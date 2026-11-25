import re
from hashlib import sha256
from typing import AbstractSet, Any
from uuid import uuid4

from sqlalchemy.orm import Session

from adrobot.db import CampaignCreation
from adrobot.errors import AppError, KeitaroError
from adrobot.keitaro import Keitaro
from adrobot.schema import Campaign, CreateCampaign, Flow, FlowOffer, Resource

GOOGLE_URL = "https://www.google.com/"


def search_offers(
    tracker: Keitaro, term: str, *, exclude_ids: AbstractSet[int] = frozenset()
) -> list[Resource]:
    query = term.strip().casefold()
    if not query:
        return []

    candidates = [
        offer
        for offer in tracker.offers()
        if offer.available
        and offer.id not in exclude_ids
        and (query in str(offer.id) or query in offer.name.casefold())
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
        and "state" in flow.model_fields_set
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
    found = None
    for campaign in tracker.campaigns():
        if campaign.state == "deleted":
            continue
        if campaign.alias == alias and campaign.name == name:
            found = campaign
        elif campaign.name == name or campaign.alias == alias:
            raise AppError(409, f"Название уже используется кампанией #{campaign.id}.")
    return found


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
    settings: dict[str, Any],
    *,
    resuming: bool,
) -> Campaign:
    try:
        return tracker.create_campaign({**settings, "state": "disabled"})
    except KeitaroError as exc:
        if not exc.uncertain and not resuming:
            db.delete(operation)
            db.commit()
            raise
        # A prior POST may have succeeded even if this retry is definitely rejected.
        # Keep its ownership token until the remote list confirms what happened.
        campaign = _existing_campaign(tracker, settings["name"], operation.remote_alias)
        if campaign is not None:
            return campaign
        raise AppError(
            502, "Создание не подтверждено. Повторите запрос с теми же данными."
        ) from exc


def create_campaign(db: Session, tracker: Keitaro, data: CreateCampaign, prefix: str) -> Resource:
    if (
        data.name == prefix
        or not data.name.startswith(prefix)
        or not re.fullmatch(r"test_[a-z0-9]+_.+", data.name)
    ):
        expected = prefix if prefix != "test_" else "test_фамилия_"
        raise AppError(422, f"Начните название с {expected} и добавьте имя кампании.")

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
        if campaign.name != data.name or campaign.alias != (operation.remote_alias or alias):
            raise AppError(409, f"Кампания #{campaign_id} уже имеет другие настройки.")
        return Resource(id=campaign.id, name=campaign.name)

    _validate_selection(tracker, data)
    resuming = operation is not None and operation.remote_alias is not None
    if operation is None or operation.campaign_id is None:
        if operation is None or operation.remote_alias is None:
            # Old reservations without a confirmed ID cannot prove remote ownership.
            # Check their predictable alias only for conflicts, never for adoption.
            found = _existing_campaign(tracker, data.name, alias)
            if found is not None:
                raise AppError(409, f"Название уже используется кампанией #{found.id}.")
            if operation is None:
                operation = CampaignCreation(alias=alias, request=submission)
                db.add(operation)
            operation.remote_alias = "adrobot-" + uuid4().hex
            db.commit()

        else:
            found = _existing_campaign(tracker, data.name, operation.remote_alias)
    else:
        found = None

    settings = {
        **data.model_dump(include={"name", "domain_id", "group_id", "traffic_source_id"}),
        "alias": operation.remote_alias or alias,
        "type": "position",
    }
    if operation.campaign_id is None:
        campaign = found or _ensure_campaign(db, tracker, operation, settings, resuming=resuming)
        operation.campaign_id = campaign.id
        db.commit()

    try:
        _finish_creation(db, tracker, operation, data, settings)
    except KeitaroError as exc:
        raise AppError(
            exc.status,
            f"Кампания #{operation.campaign_id}. {exc} "
            "Повторите запрос с теми же данными, чтобы проверить результат.",
        ) from exc
    operation.phase = "complete"
    db.commit()
    return Resource(id=operation.campaign_id, name=data.name)


def _finish_creation(
    db: Session,
    tracker: Keitaro,
    operation: CampaignCreation,
    data: CreateCampaign,
    settings: dict[str, Any],
) -> None:
    campaign_id = operation.campaign_id
    definitions = _flow_definitions(data)
    campaign = tracker.campaign(campaign_id)
    if campaign.model_dump(include=set(settings)) != settings:
        raise AppError(409, f"Кампания #{campaign_id} уже имеет другие настройки.")

    flows = _checked_flows(tracker, campaign_id, definitions)
    # A migrated attempt has no trustworthy dispatch history. Both positions may
    # already have been sent, so only their confirmed presence permits resuming.
    submitted = (
        {definition["position"] for definition in definitions}
        if operation.submitted_stream_positions is None
        else set(operation.submitted_stream_positions)
    )
    missing = submitted - flows
    if missing:
        positions = ", ".join(str(position) for position in sorted(missing))
        raise AppError(
            502,
            f"Кампания #{campaign_id}: Keitaro не подтвердил ранее отправленные потоки "
            f"на позициях {positions}. Проверьте их в Keitaro перед продолжением.",
        )
    # Record observed progress as well as intent; a stale list must never cause another POST.
    submitted |= flows
    operation.submitted_stream_positions = sorted(submitted)
    db.commit()
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
        position = definition["position"]
        if position not in flows:
            submitted.add(position)
            operation.submitted_stream_positions = sorted(submitted)
            db.commit()
            try:
                tracker.create_stream(
                    {
                        **definition,
                        "campaign_id": campaign_id,
                        "state": "active",
                        "collect_clicks": True,
                        "filter_or": False,
                    }
                )
            except KeitaroError as exc:
                if not exc.uncertain:
                    submitted.remove(position)
                    operation.submitted_stream_positions = sorted(submitted)
                    db.commit()
                raise
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
