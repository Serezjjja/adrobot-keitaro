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
        and ("type" not in flow.model_fields_set or flow.type == definition["type"])
        and flow.routing == definition["schema"]
        and flow.action_type == definition["action_type"]
        and (
            "action_payload" not in definition
            or flow.action_payload == definition["action_payload"]
        )
        and "state" in flow.model_fields_set
        and flow.state == "active"
        and flow.collect_clicks is not False
        and not flow.filter_or
        and ("filters" not in flow.model_fields_set or filters == definition["filters"])
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
        required_fields = {"type", "filters"}
        if "landings" in definition:
            required_fields.add("landings")
        if not required_fields <= flow.model_fields_set:
            # Карточка уточняет пропущенное поле, но не отменяет противоречия в списке.
            detail = tracker.stream(flow.id)
            if (
                detail.campaign_id != campaign_id
                or not required_fields <= detail.model_fields_set
                or not _matches_flow(detail, definition)
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
        # Предыдущий POST мог пройти: отказ при повторе ещё не опровергает принадлежность.
        campaign = _existing_campaign(tracker, settings["name"], operation.remote_alias)
        if campaign is not None:
            return campaign
        raise AppError(
            502, "Создание не подтверждено. Повторите запрос с теми же данными."
        ) from exc


def _validate_name(name: str, prefix: str) -> None:
    if (
        name == prefix
        or not name.startswith(prefix)
        or not re.fullmatch(r"test_[a-z0-9]+_.+", name)
    ):
        expected = prefix if prefix != "test_" else "test_фамилия_"
        raise AppError(422, f"Начните название с {expected} и добавьте имя кампании.")


def _completed_creation(tracker: Keitaro, operation: CampaignCreation) -> Resource:
    campaign_id = operation.campaign_id
    try:
        campaign = tracker.campaign(campaign_id)
    except KeitaroError as exc:
        if exc.upstream_status != 404:
            raise
        raise AppError(409, f"Кампания #{campaign_id} больше не доступна в Keitaro.") from exc
    if campaign.state == "deleted":
        raise AppError(409, f"Кампания #{campaign_id} находится в архиве Keitaro.")
    if campaign.name != operation.request["name"] or campaign.alias != (
        operation.remote_alias or operation.alias
    ):
        raise AppError(409, f"Кампания #{campaign_id} уже имеет другие настройки.")
    return Resource(id=campaign.id, name=campaign.name)


def create_campaign(db: Session, tracker: Keitaro, data: CreateCampaign, prefix: str) -> Resource:
    _validate_name(data.name, prefix)
    alias = "adrobot-" + sha256(data.name.encode()).hexdigest()[:24]
    operation = db.get(CampaignCreation, alias)
    submission = data.model_dump()
    if operation is not None and operation.request != submission:
        raise AppError(409, "Для этого названия уже сохранены другие параметры создания.")
    if operation is not None and operation.phase == "complete":
        return _completed_creation(tracker, operation)

    _validate_selection(tracker, data)
    resuming = operation is not None and operation.remote_alias is not None
    if operation is None or operation.campaign_id is None:
        if operation is None or operation.remote_alias is None:
            # Старая запись без ID не доказывает принадлежность кампании по известному alias.
            found = _existing_campaign(tracker, data.name, alias)
            if found is not None:
                raise AppError(409, f"Название уже используется кампанией #{found.id}.")
            if operation is None:
                operation = CampaignCreation(alias=alias, request=submission)
                db.add(operation)
            operation.remote_alias = "adrobot-" + uuid4().hex
            # Сохраняем случайный alias до POST, чтобы распознать кампанию при потере ответа.
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
    campaign_id = operation.campaign_id
    if campaign_id is None:
        candidate = found or _ensure_campaign(db, tracker, operation, settings, resuming=resuming)
        campaign_id = candidate.id

    try:
        campaign = None
        if operation.campaign_id is None:
            # Ответ POST может содержать чужой ID; принадлежность проверяем до его записи.
            campaign = _checked_campaign(tracker, campaign_id, settings)
            operation.campaign_id = campaign.id
            db.commit()
        _finish_creation(db, tracker, operation, data, settings, campaign=campaign)
    except KeitaroError as exc:
        raise AppError(
            exc.status,
            f"Кампания #{campaign_id}. {exc} "
            "Повторите запрос с теми же данными, чтобы проверить результат.",
        ) from exc
    operation.phase = "complete"
    db.commit()
    return Resource(id=operation.campaign_id, name=data.name)


def _checked_campaign(tracker: Keitaro, campaign_id: int, settings: dict[str, Any]) -> Campaign:
    campaign = tracker.campaign(campaign_id)
    if campaign.model_dump(include=set(settings)) != settings:
        raise AppError(409, f"Кампания #{campaign_id} уже имеет другие настройки.")
    return campaign


def _finish_creation(
    db: Session,
    tracker: Keitaro,
    operation: CampaignCreation,
    data: CreateCampaign,
    settings: dict[str, Any],
    *,
    campaign: Campaign | None = None,
) -> None:
    campaign_id = operation.campaign_id
    definitions = _flow_definitions(data)
    if campaign is None:
        campaign = _checked_campaign(tracker, campaign_id, settings)

    confirmed_positions = _checked_flows(tracker, campaign_id, definitions)
    _validate_submitted_positions(operation, definitions, confirmed_positions)
    # Учитываем найденные потоки, чтобы запоздавший список не вызвал повторный POST.
    operation.submitted_stream_positions = sorted(confirmed_positions)
    db.commit()
    if campaign.state == "active":
        if len(confirmed_positions) == len(definitions):
            return
        raise AppError(409, f"Кампания #{campaign_id} активна, но настроена не полностью.")
    if campaign.state != "disabled":
        raise AppError(409, f"Кампания #{campaign_id} в неподдерживаемом состоянии.")
    if operation.phase == "activating":
        raise AppError(
            409, f"Включение кампании #{campaign_id} не подтверждено. Проверьте её в Keitaro."
        )

    _create_missing_flows(db, tracker, operation, definitions, confirmed_positions)
    if len(_checked_flows(tracker, campaign_id, definitions)) != len(definitions):
        raise AppError(502, f"Keitaro не подтвердил два потока для кампании #{campaign_id}.")
    # Оффер мог стать недоступен за время создания потоков.
    tracker.active_offer(data.offer_id)
    _activate_campaign(db, tracker, operation, settings)


def _validate_submitted_positions(
    operation: CampaignCreation, definitions: list[dict[str, Any]], confirmed_positions: set[int]
) -> None:
    # У старой записи нет истории отправки: обе позиции могли уже попасть в Keitaro.
    submitted = (
        {definition["position"] for definition in definitions}
        if operation.submitted_stream_positions is None
        else set(operation.submitted_stream_positions)
    )
    missing = submitted - confirmed_positions
    if missing:
        positions = ", ".join(str(position) for position in sorted(missing))
        raise AppError(
            502,
            f"Кампания #{operation.campaign_id}: Keitaro не подтвердил ранее отправленные потоки "
            f"на позициях {positions}. Проверьте их в Keitaro перед продолжением.",
        )


def _create_missing_flows(
    db: Session,
    tracker: Keitaro,
    operation: CampaignCreation,
    definitions: list[dict[str, Any]],
    confirmed_positions: set[int],
) -> None:
    submitted = set(confirmed_positions)
    for definition in definitions:
        position = definition["position"]
        if position not in confirmed_positions:
            submitted.add(position)
            operation.submitted_stream_positions = sorted(submitted)
            # Записываем намерение до POST: потеря ответа не должна дублировать поток.
            db.commit()
            try:
                tracker.create_stream(
                    {
                        **definition,
                        "campaign_id": operation.campaign_id,
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


def _activate_campaign(
    db: Session, tracker: Keitaro, operation: CampaignCreation, settings: dict[str, Any]
) -> None:
    campaign_id = operation.campaign_id
    # За время создания потоков оператор мог изменить настройки или состояние кампании.
    campaign = _checked_campaign(tracker, campaign_id, settings)
    if campaign.state == "active":
        return
    if campaign.state != "disabled":
        raise AppError(409, f"Кампания #{campaign_id} в неподдерживаемом состоянии.")
    # После неопределённого enable нельзя автоматически включать остановленную кампанию.
    operation.phase = "activating"
    db.commit()
    try:
        tracker.enable_campaign(campaign_id)
    except KeitaroError as exc:
        if not exc.uncertain:
            operation.phase = "building"
            db.commit()
            raise
    if _checked_campaign(tracker, campaign_id, settings).state != "active":
        raise AppError(502, f"Keitaro не подтвердил включение кампании #{campaign_id}.")
