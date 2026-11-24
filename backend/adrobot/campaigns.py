import re
from hashlib import sha256
from typing import Any

from adrobot.errors import AppError, KeitaroError
from adrobot.keitaro import Keitaro
from adrobot.schema import Campaign, CreateCampaign, Flow, FlowOffer, Resource

GOOGLE_URL = "https://www.google.com/"


def search_offers(tracker: Keitaro, term: str) -> list[Resource]:
    query = term.strip().casefold()
    if not query:
        return []

    candidates = [
        offer
        for offer in tracker.offers()
        if offer.available and (query in str(offer.id) or query in offer.name.casefold())
    ]
    return sorted(candidates, key=lambda offer: (str(offer.id) != query, offer.name.casefold()))[
        :12
    ]


def _flow_definitions(data: CreateCampaign) -> list[dict[str, Any]]:
    return [
        {
            "name": f"{data.country} → Google",
            "position": 1,
            "type": "regular",
            "schema": "redirect",
            "action_type": "http",
            "action_payload": GOOGLE_URL,
            "filters": [{"name": "country", "mode": "accept", "payload": [data.country]}],
        },
        {
            "name": "Остальные → офферы",
            "position": 2,
            "type": "default",
            "schema": "landings",
            "action_type": "http",
            "filters": [],
            "landings": [],
            "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}],
        },
    ]


def _matches_flow(flow: Flow, definition: dict[str, Any]) -> bool:
    filters = [{key: rule.get(key) for key in ("name", "mode", "payload")} for rule in flow.filters]
    return (
        flow.position == definition["position"]
        and flow.name == definition["name"]
        and flow.type == definition["type"]
        and flow.routing == definition["schema"]
        and flow.action_type == definition["action_type"]
        and flow.action_payload == definition.get("action_payload", flow.action_payload)
        and flow.state == "active"
        and not flow.filter_or
        and filters == definition["filters"]
        and not flow.landings
        and flow.offers == [FlowOffer.model_validate(item) for item in definition.get("offers", [])]
    )


def _checked_flows(
    tracker: Keitaro, campaign_id: int, definitions: list[dict[str, Any]]
) -> dict[int, Flow]:
    by_position: dict[int, Flow] = {}
    definitions_by_position = {definition["position"]: definition for definition in definitions}
    for flow in tracker.streams(campaign_id):
        if flow.state == "deleted":
            continue
        definition = definitions_by_position.get(flow.position)
        if (
            definition is None
            or flow.position in by_position
            or flow.campaign_id != campaign_id
            or not _matches_flow(flow, definition)
        ):
            raise AppError(409, f"Кампания #{campaign_id} уже содержит другие потоки.")
        by_position[flow.position] = flow
    return by_position


def _existing_campaign(tracker: Keitaro, name: str, alias: str) -> Campaign | None:
    for campaign in tracker.campaigns():
        if campaign.state == "deleted":
            continue
        if campaign.alias == alias and campaign.name == name:
            return campaign
        if campaign.name == name or campaign.alias == alias:
            raise AppError(409, f"Название уже используется кампанией #{campaign.id}.")
    return None


def create_campaign(tracker: Keitaro, data: CreateCampaign, prefix: str) -> Resource:
    if not data.name.startswith(prefix) or not re.fullmatch(r"test_[a-z0-9]+_.+", data.name):
        expected = prefix if prefix != "test_" else "test_фамилия_"
        raise AppError(422, f"Начните название с {expected} и добавьте имя кампании.")

    references = tracker.catalogs()
    for selected, catalog, label in (
        (data.domain_id, references.domains, "домен"),
        (data.group_id, references.groups, "группа"),
        (data.traffic_source_id, references.traffic_sources, "источник"),
    ):
        if all(row.id != selected for row in catalog):
            raise AppError(422, f"Недоступен выбранный {label}.")
    tracker.active_offer(data.offer_id)

    # The stable alias lets the same submission resume a partial creation after a timeout.
    alias = "adrobot-" + sha256(data.name.encode()).hexdigest()[:24]
    references_payload = data.model_dump(include={"domain_id", "group_id", "traffic_source_id"})
    campaign = _existing_campaign(tracker, data.name, alias)
    if campaign is None:
        try:
            campaign = tracker.create_campaign(
                {
                    "name": data.name,
                    "alias": alias,
                    "type": "position",
                    "state": "disabled",
                    **references_payload,
                }
            )
        except KeitaroError as exc:
            if not exc.uncertain:
                raise
            campaign = _existing_campaign(tracker, data.name, alias)
            if campaign is None:
                raise AppError(
                    502, "Создание не подтверждено. Повторите запрос с теми же данными."
                ) from exc

    campaign_id = campaign.id
    definitions = _flow_definitions(data)
    try:
        campaign = tracker.campaign(campaign_id)
        if campaign.model_dump(include=set(references_payload)) != references_payload:
            raise AppError(409, f"Кампания #{campaign_id} уже имеет другие настройки.")

        flows = _checked_flows(tracker, campaign_id, definitions)
        if campaign.state == "active":
            if len(flows) == len(definitions):
                return Resource(id=campaign_id, name=campaign.name)
            raise AppError(409, f"Кампания #{campaign_id} активна, но настроена не полностью.")
        if campaign.state != "disabled":
            raise AppError(409, f"Кампания #{campaign_id} в неподдерживаемом состоянии.")

        for definition in definitions:
            if definition["position"] not in flows:
                tracker.create_stream(
                    {
                        **definition,
                        "campaign_id": campaign_id,
                        "state": "active",
                        "collect_clicks": True,
                        "filter_or": False,
                    }
                )
        if len(_checked_flows(tracker, campaign_id, definitions)) != len(definitions):
            raise AppError(502, f"Keitaro не подтвердил два потока для кампании #{campaign_id}.")

        try:
            tracker.enable_campaign(campaign_id)
        except KeitaroError as exc:
            if not exc.uncertain:
                raise
        if tracker.campaign(campaign_id).state != "active":
            raise AppError(502, f"Keitaro не подтвердил включение кампании #{campaign_id}.")
    except KeitaroError as exc:
        raise AppError(
            exc.status,
            f"Кампания #{campaign_id}. {exc} Повторите запрос с теми же данными, чтобы проверить результат.",
        ) from exc

    return Resource(id=campaign_id, name=campaign.name)
