import re
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from adrobot.db import CampaignCreation
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
    candidates.sort(key=lambda offer: (str(offer.id) != query, offer.name.casefold()))
    return candidates[:12]


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
        and (
            "action_payload" not in definition
            or flow.action_payload == definition["action_payload"]
        )
        and flow.state == "active"
        and not flow.filter_or
        and filters == definition["filters"]
        and not flow.landings
        and flow.offers == [FlowOffer.model_validate(item) for item in definition.get("offers", [])]
    )


def _checked_flows(
    tracker: Keitaro, campaign_id: int, definitions: list[dict[str, Any]]
) -> set[int]:
    positions: set[int] = set()
    definitions_by_position = {definition["position"]: definition for definition in definitions}
    for flow in tracker.streams(campaign_id):
        if flow.state == "deleted":
            continue
        definition = definitions_by_position.get(flow.position)
        if (
            definition is None
            or flow.position in positions
            or flow.campaign_id != campaign_id
            or not _matches_flow(flow, definition)
        ):
            raise AppError(409, f"Кампания #{campaign_id} уже содержит другие потоки.")
        positions.add(flow.position)
    return positions


def _existing_campaign(tracker: Keitaro, name: str, alias: str) -> Campaign | None:
    for campaign in tracker.campaigns():
        if campaign.state == "deleted":
            continue
        if campaign.alias == alias and campaign.name == name:
            return campaign
        if campaign.name == name or campaign.alias == alias:
            raise AppError(409, f"Название уже используется кампанией #{campaign.id}.")
    return None


def _validate_selection(tracker: Keitaro, data: CreateCampaign) -> None:
    references = tracker.catalogs()
    for selected, catalog, label in (
        (data.domain_id, references.domains, "домен"),
        (data.group_id, references.groups, "группа"),
        (data.traffic_source_id, references.traffic_sources, "источник"),
    ):
        if all(row.id != selected for row in catalog):
            raise AppError(422, f"Недоступен выбранный {label}.")
    tracker.active_offer(data.offer_id)


def _ensure_campaign(
    db: Session,
    tracker: Keitaro,
    operation: CampaignCreation,
    data: CreateCampaign,
    references: dict[str, int],
) -> Campaign:
    try:
        return tracker.create_campaign(
            {
                "name": data.name,
                "alias": operation.alias,
                "type": "position",
                "state": "disabled",
                **references,
            }
        )
    except KeitaroError as exc:
        if not exc.uncertain:
            db.delete(operation)
            db.commit()
            raise
        campaign = _existing_campaign(tracker, data.name, operation.alias)
        if campaign is not None:
            return campaign
        raise AppError(
            502, "Создание не подтверждено. Повторите запрос с теми же данными."
        ) from exc


def create_campaign(db: Session, tracker: Keitaro, data: CreateCampaign, prefix: str) -> Resource:
    if not data.name.startswith(prefix) or not re.fullmatch(r"test_[a-z0-9]+_.+", data.name):
        expected = prefix if prefix != "test_" else "test_фамилия_"
        raise AppError(422, f"Начните название с {expected} и добавьте имя кампании.")

    # Recover unknown POST results by alias, and use the local phase to guard activation.
    alias = "adrobot-" + sha256(data.name.encode()).hexdigest()[:24]
    operation = db.get(CampaignCreation, alias)
    submission = data.model_dump()
    if operation is not None and operation.request != submission:
        raise AppError(409, "Для этого названия уже сохранены другие параметры создания.")
    if operation is not None and operation.phase == "complete":
        campaign_id = operation.campaign_id
        try:
            campaign = tracker.campaign(campaign_id)
        except KeitaroError as exc:
            if exc.upstream_status != 404:
                raise
            raise AppError(409, f"Кампания #{campaign_id} больше не доступна в Keitaro.") from exc
        if campaign.state == "deleted":
            raise AppError(409, f"Кампания #{campaign_id} находится в архиве Keitaro.")
        return Resource(id=campaign.id, name=campaign.name)

    _validate_selection(tracker, data)
    references = data.model_dump(include={"domain_id", "group_id", "traffic_source_id"})
    found = None
    if operation is None or operation.campaign_id is None:
        found = _existing_campaign(tracker, data.name, alias)
    if operation is None:
        if found is not None:
            raise AppError(409, f"Название уже используется кампанией #{found.id}.")
        operation = CampaignCreation(alias=alias, request=submission)
        db.add(operation)
        db.commit()

    if operation.campaign_id is None:
        campaign = found or _ensure_campaign(db, tracker, operation, data, references)
        operation.campaign_id = campaign.id
        db.commit()

    _finish_creation(db, tracker, operation, data, references)
    operation.phase = "complete"
    db.commit()
    return Resource(id=operation.campaign_id, name=data.name)


def _finish_creation(
    db: Session,
    tracker: Keitaro,
    operation: CampaignCreation,
    data: CreateCampaign,
    references: dict[str, int],
) -> None:
    campaign_id = operation.campaign_id
    definitions = _flow_definitions(data)
    try:
        campaign = tracker.campaign(campaign_id)
        if (
            campaign.alias != operation.alias
            or campaign.name != data.name
            or campaign.model_dump(include=set(references)) != references
        ):
            raise AppError(409, f"Кампания #{campaign_id} уже имеет другие настройки.")

        flows = _checked_flows(tracker, campaign_id, definitions)
        if campaign.state == "active":
            if len(flows) == len(definitions):
                return
            raise AppError(409, f"Кампания #{campaign_id} активна, но настроена не полностью.")
        if campaign.state != "disabled":
            raise AppError(409, f"Кампания #{campaign_id} в неподдерживаемом состоянии.")
        if operation.phase == "activating":
            raise AppError(
                409, f"Включение кампании #{campaign_id} не подтверждено. Проверьте её в Keitaro."
            )

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

        # An ambiguous enable must never turn a subsequently stopped campaign back on.
        operation.phase = "activating"
        db.commit()
        try:
            tracker.enable_campaign(campaign_id)
        except KeitaroError as exc:
            if not exc.uncertain:
                operation.phase = "building"
                db.commit()
                raise
        if tracker.campaign(campaign_id).state != "active":
            raise AppError(502, f"Keitaro не подтвердил включение кампании #{campaign_id}.")
    except KeitaroError as exc:
        raise AppError(
            exc.status,
            f"Кампания #{campaign_id}. {exc} Повторите запрос с теми же данными, чтобы проверить результат.",
        ) from exc
