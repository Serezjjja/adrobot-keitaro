import pytest

from adrobot.editor import Offer, allocate, allocation_issue


def offers(*ids):
    return [Offer(id=offer_id, name=f"Offer {offer_id}", share=25) for offer_id in ids]


@pytest.mark.parametrize(("count", "expected"), [(3, [34, 33, 33]), (4, [25] * 4)])
def test_integer_shares_use_stream_order(count, expected):
    result = allocate(offers(*range(1, count + 1)))
    assert [offer.share for offer in result] == expected


def test_pinned_share_and_archived_offer():
    pinned, first, second, archived = offers(30, 10, 20, 40)
    pinned = pinned.model_copy(update={"pinned": True})
    archived = archived.model_copy(update={"removed": True, "share": 0})

    result = allocate([pinned, first, second, archived])

    assert [(offer.id, offer.share) for offer in result] == [
        (30, 25),
        (10, 38),
        (20, 37),
        (40, 0),
    ]
    assert result[0].pinned
    assert allocation_issue(result) is None


def test_disabled_offer_keeps_remote_weight_but_is_not_counted():
    first, second = offers(1, 2)
    second = second.model_copy(update={"enabled": False, "share": 15})

    result = allocate([first, second])

    assert [offer.share for offer in result] == [100, 15]
    assert allocation_issue(result) is None


def test_fully_pinned_incomplete_distribution_has_an_issue():
    first, second = offers(1, 2)
    pinned = [offer.model_copy(update={"pinned": True}) for offer in (first, second)]

    assert allocation_issue(allocate(pinned)) is not None
    assert allocation_issue([]) is not None
