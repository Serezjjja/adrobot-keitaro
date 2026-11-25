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
    # Give the extra percentage points to the first free offers in stable stream order.
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
    if not flow.editable:
        # A redirect can become an offer flow again. Keep the prior archive for that case.
        return

    previous = {offer.id: offer for offer in _load(stream.published)}
    received = {link.offer_id: link for link in flow.offers}
    offers = []

    # Preserve the known order so integer remainders don't move between offers on Fetch.
    for offer_id in dict.fromkeys([*previous, *received]):
        old = previous.get(offer_id)
        link = received.get(offer_id)
        if link is None:
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

    # Keep the tracker's actual shares on Fetch, even when they sum to less than 100.
    stream.published = _dump(offers)


def _corroborate_pending_flow(
    tracker: Keitaro, stream: StreamDraft, listed: Flow | None
) -> Flow | None:
    desired = _links(_load(stream.draft))

    def present(flow: Flow | None) -> bool:
        return bool(flow and flow.state != "deleted" and flow.campaign_id == stream.campaign_id)

    def confirms(flow: Flow | None) -> bool:
        return bool(present(flow) and flow.editable and _same_links(flow.offers, desired))

    # A matching draft confirms the write. Otherwise require agreement before unlocking edits.
    if confirms(listed):
        return listed
    try:
        detail = tracker.stream(stream.id)
    except KeitaroError as exc:
        if exc.upstream_status != 404:
            raise
        detail = None
    if confirms(detail):
        return detail
    if not present(listed) and not present(detail):
        return None
    if (
        present(listed)
        and present(detail)
        and listed.routing == detail.routing
        and listed.state == detail.state
        and _same_links(listed.offers, detail.offers)
    ):
        return detail
    raise AppError(
        502, "Keitaro вернул несогласованные данные. Черновик сохранён. Загрузите потоки снова."
    )


def synchronize(db: Session, tracker: Keitaro, campaign_id: int) -> list[StreamView]:
    flows = {flow.id: flow for flow in tracker.streams(campaign_id) if flow.state != "deleted"}
    existing = {stream.id: stream for stream in _campaign_rows(db, campaign_id)}
    # Corroborate before changing any rows: one stale endpoint must not unlock a PUT.
    for stream in existing.values():
        if stream.publication_pending and stream.draft is not None:
            confirmed = _corroborate_pending_flow(tracker, stream, flows.get(stream.id))
            if confirmed is None:
                flows.pop(stream.id, None)
            else:
                flows[stream.id] = confirmed
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
    published = _links(_load(stream.published))
    changed_remotely = not _same_links(flow.offers, published)
    if (
        stream.publication_pending
        and flow.editable
        and _same_links(flow.offers, _links(_load(stream.draft)))
    ):
        # A previous PUT may have succeeded even when its response never reached us.
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
            offers[offer.id] = _archive(offer)
        else:
            del offers[offer.id]
    elif action in {"pin", "unpin"} and offer.enabled:
        offers[offer.id] = offer.model_copy(update={"pinned": action == "pin"})
    elif action == "set_share" and offer.enabled:
        offers[offer.id] = offer.model_copy(update={"share": change.share, "pinned": True})
    else:
        raise AppError(422, "Это действие недоступно для выбранного оффера.")

    if action in {"pin", "set_share"}:
        active = [item for item in offers.values() if item.enabled and not item.removed]
        pinned_total = sum(item.share for item in active if item.pinned)
        if pinned_total > 100:
            raise AppError(422, "Сумма закреплённых долей больше 100%. Уменьшите долю.")
        if action == "set_share" and pinned_total < 100 and all(item.pinned for item in active):
            raise AppError(
                422,
                "Сумма закреплённых долей должна быть 100%. Снимите закрепление или измените долю.",
            )

    result = list(offers.values())
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

    desired = _links(draft)
    current = _current_offer_flow(db, tracker, stream)

    if not _same_links(current.offers, _links(_load(stream.published))):
        stream.conflict = True
        _save(db, stream)
        raise AppError(409, "Офферы изменились в Keitaro. Черновик сохранён.")

    if not _same_links(current.offers, desired):
        available = {offer.id for offer in tracker.offers() if offer.available}
        unavailable = [
            offer
            for offer in draft
            if offer.enabled and not offer.removed and offer.share > 0 and offer.id not in available
        ]
        if unavailable:
            labels = ", ".join(f"{offer.name} (#{offer.id})" for offer in unavailable)
            raise AppError(422, f"Офферы недоступны в Keitaro: {labels}. Черновик сохранён.")
        # Commit before the remote write: a lost response or process restart must not
        # unlock Cancel or a second publication before an authoritative Fetch.
        stream.publication_pending = True
        _save(db, stream)
        try:
            tracker.set_offers(stream_id, desired)
        except KeitaroError as exc:
            if not exc.uncertain:
                stream.publication_pending = False
                _save(db, stream)
                raise
        try:
            confirmed = _current_offer_flow(db, tracker, stream, publication_readback=True)
        except KeitaroError as exc:
            raise AppError(
                502, "Публикация не подтверждена. Черновик сохранён. Загрузите потоки снова."
            ) from exc
        if not _same_links(confirmed.offers, desired):
            # Even an accepted PUT can be followed by a stale immediate read.
            # Only explicit Fetch may unlock a publication that was not confirmed.
            raise AppError(
                502, "Keitaro не подтвердил новые доли. Черновик сохранён. Загрузите потоки снова."
            )

    stream.publication_pending = False
    stream.published = stream.draft
    stream.draft = None
    return _save(db, stream)
