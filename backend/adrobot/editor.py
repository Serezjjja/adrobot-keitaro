from sqlalchemy import select
from sqlalchemy.orm import Session

from adrobot.db import StreamDraft
from adrobot.errors import AppError, KeitaroError
from adrobot.keitaro import Keitaro
from adrobot.offer_shares import allocate, allocation_issue, validate_pinned_shares
from adrobot.offer_snapshots import archive_offer, merge_remote_offers, offer_links, same_links
from adrobot.schema import DraftChange, Flow, FlowOffer, Offer, StreamView


def _load(items: list[dict]) -> list[Offer]:
    return [Offer.model_validate(item) for item in items]


def _dump(items: list[Offer]) -> list[dict]:
    return [item.model_dump() for item in items]


def _stream_view(stream: StreamDraft) -> StreamView:
    dirty = stream.draft is not None
    offers = _load(stream.draft if dirty else stream.published) if stream.editable else []
    return StreamView(
        id=stream.id,
        name=stream.name,
        position=stream.position,
        editable=stream.editable,
        dirty=dirty,
        conflict=stream.conflict,
        missing=stream.missing,
        publication_pending=stream.publication_pending,
        version=stream.version,
        issue=allocation_issue(offers) if stream.editable else None,
        offers=offers,
        published_offers=_load(stream.published) if dirty else None,
    )


def _campaign_rows(db: Session, campaign_id: int) -> list[StreamDraft]:
    query = (
        select(StreamDraft)
        .where(StreamDraft.campaign_id == campaign_id)
        .order_by(StreamDraft.position, StreamDraft.id)
    )
    return list(db.scalars(query))


def list_streams(db: Session, campaign_id: int) -> list[StreamView]:
    return [
        _stream_view(stream)
        for stream in _campaign_rows(db, campaign_id)
        if not stream.missing or stream.draft is not None
    ]


def attached_offer_ids(db: Session, stream_id: int) -> set[int]:
    stream = db.get(StreamDraft, stream_id)
    if stream is None:
        return set()
    offers = _load(stream.draft if stream.draft is not None else stream.published)
    return {offer.id for offer in offers if not offer.removed}


def _import_flow(stream: StreamDraft, flow: Flow, names: dict[int, str]) -> None:
    stream.conflict = False
    # После смены маршрутизации на офферы прежний архив снова пригодится.
    if flow.editable:
        stream.published = _dump(merge_remote_offers(_load(stream.published), flow.offers, names))


def _present_flow(flow: Flow | None, campaign_id: int) -> bool:
    return bool(flow and flow.state != "deleted" and flow.campaign_id == campaign_id)


def _same_remote_flow(left: Flow | None, right: Flow | None, campaign_id: int) -> bool:
    if not _present_flow(left, campaign_id) and not _present_flow(right, campaign_id):
        return True
    return bool(
        _present_flow(left, campaign_id)
        and _present_flow(right, campaign_id)
        and left.routing == right.routing
        and left.state == right.state
        and same_links(left.offers, right.offers)
    )


def _require_flow_routing(flow: Flow | None) -> None:
    # Пропущенная schema не доказывает переход на другой тип маршрутизации.
    if (
        flow
        and flow.state != "deleted"
        and ("routing" not in flow.model_fields_set or not flow.routing.strip())
    ):
        raise AppError(502, "Keitaro не вернул тип потока. Загрузите потоки снова.")


def _read_corroborating_flow(
    tracker: Keitaro, stream: StreamDraft, listed: Flow | None
) -> Flow | None:
    try:
        detail = tracker.stream(stream.id)
    except KeitaroError as exc:
        if exc.upstream_status != 404:
            raise
        detail = None
    _require_flow_routing(listed)
    _require_flow_routing(detail)
    return detail


def _corroborate_pending_flow(
    tracker: Keitaro, stream: StreamDraft, listed: Flow | None
) -> Flow | None:
    desired = offer_links(_load(stream.draft))
    published = offer_links(_load(stream.published))
    # Даже совпавший с черновиком список может запаздывать: проверяем карточку перед снятием блокировки.
    detail = _read_corroborating_flow(tracker, stream, listed)
    listed_can_lag = listed is None or (
        _present_flow(listed, stream.campaign_id)
        and listed.editable
        and (same_links(listed.offers, published) or same_links(listed.offers, desired))
    )
    if (
        listed_can_lag
        and _present_flow(detail, stream.campaign_id)
        and detail.editable
        and same_links(detail.offers, desired)
    ):
        return detail
    if _same_remote_flow(listed, detail, stream.campaign_id):
        return detail if _present_flow(detail, stream.campaign_id) else None
    raise AppError(
        502, "Keitaro вернул несогласованные данные. Черновик сохранён. Загрузите потоки снова."
    )


def _corroborate_clean_flow(
    tracker: Keitaro, stream: StreamDraft, listed: Flow | None, campaign_id: int
) -> None:
    if stream.missing and not _present_flow(listed, campaign_id):
        return
    if (
        not stream.missing
        and stream.campaign_id == campaign_id
        and _present_flow(listed, campaign_id)
        and "routing" in listed.model_fields_set
        and listed.editable == stream.editable
        and (not listed.editable or same_links(listed.offers, offer_links(_load(stream.published))))
    ):
        return
    # Перед заменой чистого снимка сверяем изменения, чтобы старый список не сбросил закрепления.
    detail = _read_corroborating_flow(tracker, stream, listed)
    if not _same_remote_flow(listed, detail, campaign_id):
        raise AppError(502, "Keitaro вернул несогласованные данные. Загрузите потоки снова.")


def synchronize(db: Session, tracker: Keitaro, campaign_id: int) -> list[StreamView]:
    listed = {flow.id: flow for flow in tracker.streams(campaign_id)}
    for flow in listed.values():
        _require_flow_routing(flow)
    flows = {flow_id: flow for flow_id, flow in listed.items() if flow.state != "deleted"}
    existing = {stream.id: stream for stream in _campaign_rows(db, campaign_id)}
    # Один запоздавший ответ не должен снять блокировку. Сверяем публикации до изменений в SQLite.
    for stream in existing.values():
        if stream.publication_pending and stream.draft is not None:
            confirmed = _corroborate_pending_flow(tracker, stream, listed.get(stream.id))
            if confirmed is None:
                flows.pop(stream.id, None)
            else:
                flows[stream.id] = confirmed
        elif stream.draft is None:
            _corroborate_clean_flow(tracker, stream, listed.get(stream.id), campaign_id)
    # Перенос чистого потока тоже меняет снимок: проверяем его до принятия в новую кампанию.
    for flow in flows.values():
        if flow.id not in existing:
            stream = db.get(StreamDraft, flow.id)
            if stream is not None and stream.draft is None:
                _corroborate_clean_flow(tracker, stream, flow, campaign_id)
    names = (
        {offer.id: offer.name for offer in tracker.offers()}
        if any(flow.editable and flow.offers for flow in flows.values())
        else {}
    )

    for flow in flows.values():
        stream = existing.pop(flow.id, None) or db.get(StreamDraft, flow.id)
        if stream is None:
            stream = StreamDraft(id=flow.id, campaign_id=campaign_id, published=[])
            db.add(stream)
        elif stream.campaign_id != campaign_id:
            if stream.draft is not None:
                raise AppError(
                    409,
                    f"Поток #{flow.id} перенесён. Отмените черновик в кампании "
                    f"#{stream.campaign_id} и загрузите потоки снова.",
                )
            stream.campaign_id = campaign_id
        stream.name = flow.name
        stream.position = flow.position
        stream.missing = False
        stream.editable = flow.editable
        if stream.draft is None:
            _import_flow(stream, flow, names)
        else:
            _reconcile_draft(stream, flow)
        if stream not in db.new and db.is_modified(stream):
            stream.version += 1

    for stream in existing.values():
        stream.missing = True
        if stream.draft is not None:
            stream.conflict = True
        stream.publication_pending = False
        if db.is_modified(stream):
            stream.version += 1

    db.commit()
    return list_streams(db, campaign_id)


def _reconcile_draft(stream: StreamDraft, flow: Flow) -> None:
    published = offer_links(_load(stream.published))
    changed_remotely = not same_links(flow.offers, published)
    if (
        stream.publication_pending
        and flow.editable
        and same_links(flow.offers, offer_links(_load(stream.draft)))
    ):
        # Keitaro мог применить предыдущий PUT даже при потере ответа.
        stream.published = stream.draft
        stream.draft = None
        stream.conflict = False
    else:
        stream.conflict = changed_remotely or not flow.editable
    stream.publication_pending = False


def _require_stream(db: Session, stream_id: int, version: int) -> StreamDraft:
    stream = db.get(StreamDraft, stream_id)
    if stream is None:
        raise AppError(404, "Сначала загрузите потоки кампании из Keitaro.")
    if stream.version != version:
        raise AppError(409, "Поток изменился. Перечитайте его перед следующей правкой.")
    return stream


def _require_editable(stream: StreamDraft) -> None:
    _require_confirmed(stream)
    if stream.missing or stream.conflict:
        raise AppError(
            409, "Поток изменился в Keitaro. Отмените черновик и загрузите потоки снова."
        )
    if not stream.editable:
        raise AppError(422, "Этот поток не маршрутизирует трафик по офферам.")


def _require_confirmed(stream: StreamDraft) -> None:
    if stream.publication_pending:
        raise AppError(409, "Публикация не подтверждена. Загрузите потоки для сверки с Keitaro.")


def _save(db: Session, stream: StreamDraft) -> StreamView:
    if db.is_modified(stream):
        stream.version += 1
    db.commit()
    return _stream_view(stream)


def change_draft(db: Session, tracker: Keitaro, stream_id: int, change: DraftChange) -> StreamView:
    stream = _require_stream(db, stream_id, change.version)
    _require_editable(stream)
    published = _load(stream.published)
    offers = {offer.id: offer for offer in _load(stream.draft or stream.published)}
    offer = offers.get(change.offer_id)
    action = change.action

    adding = action == "add" and offer is None
    restoring = action in {"add", "restore"} and offer is not None and offer.removed
    if adding or restoring:
        resource = tracker.active_offer(change.offer_id)
        offers[resource.id] = Offer(id=resource.id, name=resource.name, share=0)
    elif offer is None or offer.removed:
        raise AppError(404, "Оффер отсутствует в потоке.")
    elif action == "remove":
        if any(saved.id == offer.id for saved in published):
            offers[offer.id] = archive_offer(offer)
        else:
            del offers[offer.id]
    elif action in {"pin", "unpin"} and offer.enabled:
        offers[offer.id] = offer.model_copy(update={"pinned": action == "pin"})
    elif action == "set_share" and offer.enabled:
        offers[offer.id] = offer.model_copy(update={"share": change.share, "pinned": True})
    else:
        raise AppError(422, "Это действие недоступно для выбранного оффера.")

    result = list(offers.values())
    if action in {"pin", "set_share"}:
        validate_pinned_shares(result, require_full=action == "set_share")
    if action in {"pin", "unpin"} and stream.draft is None:
        stream.published = _dump(result)
        return _save(db, stream)
    if action != "pin":
        result = allocate(result)
    stream.draft = None if result == published else _dump(result)
    return _save(db, stream)


def cancel_draft(db: Session, stream_id: int, version: int) -> StreamView:
    stream = _require_stream(db, stream_id, version)
    _require_confirmed(stream)
    stream.draft = None
    return _save(db, stream)


def _current_offer_flow(
    db: Session, tracker: Keitaro, stream: StreamDraft, *, publication_readback: bool = False
) -> Flow:
    try:
        flow = tracker.stream(stream.id)
    except KeitaroError as exc:
        if exc.upstream_status != 404:
            raise
        flow = None

    _require_flow_routing(flow)
    if publication_readback and (
        flow is None
        or flow.state == "deleted"
        or not flow.editable
        or flow.campaign_id != stream.campaign_id
    ):
        raise AppError(
            502, "Публикация не подтверждена. Черновик сохранён. Загрузите потоки снова."
        )

    if flow is None or flow.state == "deleted":
        stream.missing = True
        stream.conflict = True
        stream.publication_pending = False
        _save(db, stream)
        raise AppError(409, "Поток больше не доступен в Keitaro.")

    if not flow.editable or flow.campaign_id != stream.campaign_id:
        stream.conflict = True
        stream.publication_pending = False
        _save(db, stream)
        raise AppError(409, "Тип или кампания потока изменились. Загрузите потоки снова.")
    return flow


def publish_draft(db: Session, tracker: Keitaro, stream_id: int, version: int) -> StreamView:
    stream = _require_stream(db, stream_id, version)
    _require_editable(stream)
    if stream.draft is None:
        return _stream_view(stream)

    draft = _load(stream.draft)
    issue = allocation_issue(draft)
    if issue:
        raise AppError(422, issue)

    desired = offer_links(draft)
    current = _current_offer_flow(db, tracker, stream)

    if not same_links(current.offers, offer_links(_load(stream.published))):
        stream.conflict = True
        _save(db, stream)
        raise AppError(409, "Офферы изменились в Keitaro. Черновик сохранён.")

    if not same_links(current.offers, desired):
        required = {
            offer.id for offer in draft if offer.enabled and not offer.removed and offer.share > 0
        }
        available = tracker.active_offer_ids(required)
        unavailable = [
            offer
            for offer in draft
            if offer.enabled and not offer.removed and offer.share > 0 and offer.id not in available
        ]
        if unavailable:
            labels = ", ".join(f"{offer.name} (#{offer.id})" for offer in unavailable)
            raise AppError(422, f"Офферы недоступны в Keitaro: {labels}. Черновик сохранён.")
        # Фиксируем попытку до PUT, чтобы потеря ответа или перезапуск сохраняли блокировку до Fetch.
        stream.publication_pending = True
        _save(db, stream)
        try:
            tracker.set_offers(stream_id, desired)
        except KeitaroError as exc:
            if not exc.uncertain:
                stream.publication_pending = False
                _save(db, stream)
                raise
        _confirm_publication(db, tracker, stream, desired)

    stream.publication_pending = False
    stream.published = stream.draft
    stream.draft = None
    return _save(db, stream)


def _confirm_publication(
    db: Session, tracker: Keitaro, stream: StreamDraft, desired: list[FlowOffer]
) -> None:
    try:
        confirmed = _current_offer_flow(db, tracker, stream, publication_readback=True)
    except KeitaroError as exc:
        raise AppError(
            502, "Публикация не подтверждена. Черновик сохранён. Загрузите потоки снова."
        ) from exc
    if not same_links(confirmed.offers, desired):
        # Keitaro может вернуть старые доли сразу после PUT. Блокировка остаётся до Fetch.
        raise AppError(
            502, "Keitaro не подтвердил новые доли. Черновик сохранён. Загрузите потоки снова."
        )
