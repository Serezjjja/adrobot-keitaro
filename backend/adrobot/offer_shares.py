from adrobot.errors import AppError
from adrobot.schema import Offer


def _active_offers(offers: list[Offer]) -> list[Offer]:
    return [offer for offer in offers if offer.enabled and not offer.removed]


def allocate(offers: list[Offer]) -> list[Offer]:
    active = _active_offers(offers)
    pinned = sum(offer.share for offer in active if offer.pinned)
    free = [offer.id for offer in active if not offer.pinned]
    if not free or pinned > 100:
        return offers

    share, remainder = divmod(100 - pinned, len(free))
    # Остаток получают первые свободные офферы, чтобы округление учитывало порядок потока.
    shares = {offer_id: share + (index < remainder) for index, offer_id in enumerate(free)}
    return [
        offer.model_copy(update={"share": shares[offer.id]}) if offer.id in shares else offer
        for offer in offers
    ]


def allocation_issue(offers: list[Offer]) -> str | None:
    active = _active_offers(offers)
    if not active:
        return "Для публикации нужен хотя бы один активный оффер."
    if sum(offer.share for offer in active) != 100:
        return "Сумма долей должна быть 100%. Снимите часть закреплений или добавьте оффер."
    return None


def validate_pinned_shares(offers: list[Offer], *, require_full: bool) -> None:
    active = _active_offers(offers)
    pinned_total = sum(offer.share for offer in active if offer.pinned)
    if pinned_total > 100:
        raise AppError(422, "Сумма закреплённых долей больше 100%. Уменьшите долю.")
    if require_full and pinned_total < 100 and all(offer.pinned for offer in active):
        raise AppError(
            422,
            "Сумма закреплённых долей должна быть 100%. Снимите закрепление или измените долю.",
        )
