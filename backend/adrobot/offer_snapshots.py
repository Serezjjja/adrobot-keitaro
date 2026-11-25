from adrobot.schema import FlowOffer, Offer


def offer_links(offers: list[Offer]) -> list[FlowOffer]:
    return [
        FlowOffer(
            offer_id=offer.id, share=offer.share, state="active" if offer.enabled else "disabled"
        )
        for offer in offers
        if not offer.removed
    ]


def same_links(left: list[FlowOffer], right: list[FlowOffer]) -> bool:
    return sorted(left, key=lambda link: link.offer_id) == sorted(
        right, key=lambda link: link.offer_id
    )


def archive_offer(offer: Offer) -> Offer:
    return offer.model_copy(update={"share": 0, "pinned": False, "removed": True})


def merge_remote_offers(
    published: list[Offer], remote_links: list[FlowOffer], names: dict[int, str]
) -> list[Offer]:
    previous = {offer.id: offer for offer in published}
    received = {link.offer_id: link for link in remote_links}
    offers = []

    # Порядок известных офферов сохраняется для стабильного округления после Fetch.
    for offer_id in dict.fromkeys([*previous, *received]):
        link = received.get(offer_id)
        if link is None:
            offers.append(archive_offer(previous[offer_id]))
            continue
        old = previous.get(offer_id)
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

    # Снимок отражает фактические доли Keitaro, в том числе при сумме меньше 100%.
    return offers
