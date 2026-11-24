from sqlalchemy import select
from sqlalchemy.orm import Session

from adrobot.db import StreamDraft
from adrobot.errors import AppError, KeitaroError
from adrobot.keitaro import Keitaro
from adrobot.schema import DraftChange, Flow, FlowOffer, Offer, StreamView


def allocate(offers: list[Offer]) -> list[Offer]:
    active = [offer for offer in offers if offer.enabled and not offer.removed]
    pinned = sum(offer.share for offer in active if offer.pinned)
    free = [offer.id for offer in active if not offer.pinned]
    if not free or pinned > 100:
        return offers

    share, remainder = divmod(100 - pinned, len(free))
    shares = {offer_id: share + (index < remainder) for index, offer_id in enumerate(free)}
    return [
        offer.model_copy(update={"share": shares[offer.id]}) if offer.id in shares else offer
        for offer in offers
    ]


def allocation_issue(offers: list[Offer]) -> str | None:
    active = [offer for offer in offers if offer.enabled and not offer.removed]
    if not active:
        return "Для публикации нужен хотя бы один активный оффер."
    if sum(offer.share for offer in active) != 100:
        return "Сумма долей должна быть 100%. Снимите часть закреплений или добавьте оффер."
    return None


def _links(offers: list[Offer]) -> list[FlowOffer]:
    return [
        FlowOffer(
            offer_id=offer.id, share=offer.share, state="active" if offer.enabled else "disabled"
        )
        for offer in offers
        if not offer.removed
    ]


def _same_links(left: list[FlowOffer], right: list[FlowOffer]) -> bool:
    return sorted(left, key=lambda link: link.offer_id) == sorted(
        right, key=lambda link: link.offer_id
    )


def _load(items: list[dict]) -> list[Offer]:
    return [Offer.model_validate(item) for item in items]


def _dump(items: list[Offer]) -> list[dict]:
    return [item.model_dump() for item in items]


def _archive(offer: Offer) -> Offer:
    return offer.model_copy(update={"share": 0, "pinned": False, "removed": True})


def _stream_view(stream: StreamDraft) -> StreamView:
    dirty = stream.draft is not None
    offers = _load(stream.draft if dirty else stream.published)
    return StreamView(
        id=stream.id,
        name=stream.name,
        position=stream.position,
        editable=stream.editable,
        dirty=dirty,
        conflict=stream.conflict,
        missing=stream.missing,
        version=stream.version,
        issue=allocation_issue(offers) if stream.editable else None,
        offers=offers,
    )


def _campaign_rows(db: Session, campaign_id: int) -> list[StreamDraft]:
    query = (
        select(StreamDraft)
        .where(StreamDraft.campaign_id == campaign_id)
        .order_by(StreamDraft.position, StreamDraft.id)
    )
    return list(db.scalars(query))


def list_streams(db: Session, campaign_id: int) -> list[StreamView]:
    return [_stream_view(stream) for stream in _campaign_rows(db, campaign_id)]


def _import_flow(stream: StreamDraft, flow: Flow, names: dict[int, str]) -> None:
    previous = {offer.id: offer for offer in _load(stream.published)}
    received = {link.offer_id: link for link in flow.offers} if flow.editable else {}
    offers = []
    removed = False

    # Preserve the known order so integer remainders don't move between offers on Fetch.
    for offer_id in dict.fromkeys([*previous, *received]):
        old = previous.get(offer_id)
        link = received.get(offer_id)
        if link is None:
            removed |= not old.removed
            offers.append(_archive(old))
            continue
        offers.append(
            Offer(
                id=offer_id,
                name=names.get(offer_id, old.name if old else f"Оффер #{offer_id}"),
                share=link.share,
                enabled=link.state == "active",
                pinned=bool(
                    old and old.pinned and old.share == link.share and link.state == "active"
                ),
            )
        )

    stream.published = _dump(offers) if flow.editable else []
    stream.conflict = False
    if removed and flow.editable:
        recalculated = allocate(offers)
        if recalculated != offers:
            stream.draft = _dump(recalculated)


def synchronize(db: Session, tracker: Keitaro, campaign_id: int) -> list[StreamView]:
    flows = [flow for flow in tracker.streams(campaign_id) if flow.state != "deleted"]
    names = (
        {offer.id: offer.name for offer in tracker.offers()}
        if any(flow.editable and flow.offers for flow in flows)
        else {}
    )
    existing = {stream.id: stream for stream in _campaign_rows(db, campaign_id)}

    for flow in flows:
        stream = existing.pop(flow.id, None)
        if stream is None:
            stream = StreamDraft(id=flow.id, campaign_id=campaign_id, published=[])
            db.add(stream)
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
        if not stream.missing:
            stream.missing = True
            stream.version += 1

    db.commit()
    return list_streams(db, campaign_id)


def _reconcile_draft(stream: StreamDraft, flow: Flow) -> None:
    published = _links(_load(stream.published))
    changed_remotely = not _same_links(flow.offers, published)
    if flow.editable and changed_remotely and _same_links(flow.offers, _links(_load(stream.draft))):
        # A previous PUT may have succeeded even when its response never reached us.
        stream.published = stream.draft
        stream.draft = None
        stream.conflict = False
    else:
        stream.conflict = changed_remotely or not flow.editable


def _require_stream(db: Session, stream_id: int, version: int) -> StreamDraft:
    stream = db.get(StreamDraft, stream_id)
    if stream is None:
        raise AppError(404, "Сначала загрузите потоки кампании из Keitaro.")
    if stream.version != version:
        raise AppError(409, "Поток изменился. Перечитайте его перед следующей правкой.")
    return stream


def _require_editable(stream: StreamDraft) -> None:
    if stream.missing or stream.conflict:
        raise AppError(
            409, "Поток изменился в Keitaro. Отмените черновик и загрузите потоки снова."
        )
    if not stream.editable:
        raise AppError(422, "Этот поток не маршрутизирует трафик по офферам.")


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

    if action == "add" and offer is None:
        resource = tracker.active_offer(change.offer_id)
        offers[resource.id] = Offer(id=resource.id, name=resource.name, share=0)
    elif action in {"add", "restore"} and offer is not None and offer.removed:
        resource = tracker.active_offer(offer.id)
        offers[offer.id] = Offer(id=offer.id, name=resource.name, share=0)
    elif offer is None or offer.removed:
        raise AppError(404, "Оффер отсутствует в потоке.")
    elif action == "remove":
        if any(saved.id == offer.id for saved in published):
            offers[offer.id] = _archive(offer)
        else:
            del offers[offer.id]
    elif action in {"pin", "unpin"} and offer.enabled:
        offers[offer.id] = offer.model_copy(update={"pinned": action == "pin"})
    else:
        raise AppError(422, "Это действие недоступно для выбранного оффера.")

    result = allocate(list(offers.values()))
    stream.draft = None if result == published else _dump(result)
    return _save(db, stream)


def cancel_draft(db: Session, stream_id: int, version: int) -> StreamView:
    stream = _require_stream(db, stream_id, version)
    stream.draft = None
    return _save(db, stream)


def publish_draft(db: Session, tracker: Keitaro, stream_id: int, version: int) -> StreamView:
    stream = _require_stream(db, stream_id, version)
    _require_editable(stream)
    if stream.draft is None:
        return _stream_view(stream)

    draft = _load(stream.draft)
    issue = allocation_issue(draft)
    if issue:
        raise AppError(422, issue)

    desired = _links(draft)
    try:
        current = tracker.stream(stream_id)
    except KeitaroError as exc:
        if exc.upstream_status != 404:
            raise
        stream.missing = True
        _save(db, stream)
        raise AppError(409, "Поток больше не доступен в Keitaro.") from exc

    if not current.editable or current.campaign_id != stream.campaign_id:
        stream.conflict = True
        _save(db, stream)
        raise AppError(409, "Тип или кампания потока изменились. Загрузите потоки снова.")

    if not _same_links(current.offers, desired):
        if not _same_links(current.offers, _links(_load(stream.published))):
            stream.conflict = True
            _save(db, stream)
            raise AppError(409, "Офферы изменились в Keitaro. Черновик сохранён.")

        try:
            tracker.set_offers(stream_id, desired)
        except KeitaroError as exc:
            if not exc.uncertain:
                raise
        try:
            confirmed = tracker.stream(stream_id)
        except KeitaroError as exc:
            raise AppError(
                502, "Публикация не подтверждена. Черновик сохранён. Загрузите потоки снова."
            ) from exc
        if not _same_links(confirmed.offers, desired):
            raise AppError(502, "Keitaro не подтвердил новые доли. Черновик сохранён.")

    stream.published = stream.draft
    stream.draft = None
    return _save(db, stream)
