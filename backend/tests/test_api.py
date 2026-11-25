from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from httpx import MockTransport
from pydantic import HttpUrl

from adrobot.main import create_app
from tests.fakes import link


def sync(client, campaign_id=7):
    response = client.post(f"/api/campaigns/{campaign_id}/sync")
    assert response.status_code == 200, response.text
    return response.json()


def change(client, stream, action, offer_id):
    response = client.patch(
        f"/api/streams/{stream['id']}/draft",
        json={"action": action, "offer_id": offer_id, "version": stream["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def publish(client, stream):
    return client.post(f"/api/streams/{stream['id']}/publish", json={"version": stream["version"]})


def cancel(client, stream):
    response = client.delete(
        f"/api/streams/{stream['id']}/draft", params={"version": stream["version"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def shares(stream):
    return {offer["id"]: offer["share"] for offer in stream["offers"]}


def set_share(client, stream, offer_id, share):
    return client.patch(
        f"/api/streams/{stream['id']}/draft",
        json={
            "action": "set_share",
            "offer_id": offer_id,
            "version": stream["version"],
            "share": share,
        },
    )


def test_set_share_pins_and_distributes_remainder_locally(client, fake):
    _, original = sync(client)
    pinned = change(client, original, "pin", 11)
    before_calls = list(fake.calls)
    response = set_share(client, pinned, 12, 30)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert shares(draft) == {11: 25, 12: 30, 13: 45}
    assert [offer["pinned"] for offer in draft["offers"]] == [True, True, False]
    assert draft["published_offers"] == pinned["offers"]
    assert fake.calls == before_calls
    assert cancel(client, draft)["offers"] == pinned["offers"]


def test_set_share_same_value_pins_and_zero_is_allowed(client, fake):
    _, stream = sync(client)
    response = set_share(client, stream, 11, 25)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["dirty"] and draft["offers"][0]["pinned"]
    response = set_share(client, draft, 11, 0)
    assert response.status_code == 200, response.text
    assert shares(response.json()) == {11: 0, 12: 50, 13: 50}
    assert fake.streams[102]["offers"] == [link(11, 25), link(12, 38), link(13, 37)]


def test_set_share_rejects_all_locked_below_100_and_can_publish_exact_total(client, fake):
    _, stream = sync(client)
    first = set_share(client, stream, 11, 25).json()
    second = set_share(client, first, 12, 30).json()
    before_calls = list(fake.calls)
    rejected = set_share(client, second, 13, 40)
    assert rejected.status_code == 422
    assert "100%" in rejected.json()["detail"]
    assert client.get("/api/campaigns/7/streams").json()[1] == second
    assert fake.calls == before_calls
    response = set_share(client, second, 13, 45)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert all(offer["pinned"] for offer in draft["offers"])
    published = publish(client, draft)
    assert published.status_code == 200, published.text
    assert not published.json()["dirty"]
    assert fake.streams[102]["offers"] == [link(11, 25), link(12, 30), link(13, 45)]


def test_set_share_rejects_impossible_locked_totals_without_saving(client, fake):
    _, stream = sync(client)
    pinned = set_share(client, stream, 11, 80).json()
    before_calls = list(fake.calls)
    response = set_share(client, pinned, 12, 30)
    assert response.status_code == 422
    assert "100%" in response.json()["detail"]
    assert client.get("/api/campaigns/7/streams").json()[1] == pinned
    assert fake.calls == before_calls

    fake.streams[102]["offers"] = [link(11, 100)]
    cancel(client, pinned)
    _, single = sync(client)
    before_calls = list(fake.calls)
    response = set_share(client, single, 11, 80)
    assert response.status_code == 422
    assert "100%" in response.json()["detail"]
    assert client.get("/api/campaigns/7/streams").json()[1] == single
    assert fake.calls == before_calls
    response = set_share(client, single, 11, 100)
    assert response.status_code == 200, response.text
    assert response.json()["offers"][0]["pinned"]


@pytest.mark.parametrize("invalid", [None, True, False, "40", 2.5, -1, 101])
def test_set_share_rejects_invalid_inputs(client, fake, invalid):
    _, stream = sync(client)
    before_calls = list(fake.calls)
    assert set_share(client, stream, 11, invalid).status_code == 422
    assert client.get("/api/campaigns/7/streams").json()[1] == stream
    assert fake.calls == before_calls


def test_set_share_requires_share_and_respects_stream_guards(client, fake):
    _, stream = sync(client)
    payload = {"action": "set_share", "offer_id": 11, "version": stream["version"]}
    assert client.patch("/api/streams/102/draft", json=payload).status_code == 422
    draft = set_share(client, stream, 11, 30).json()
    assert set_share(client, stream, 11, 20).status_code == 409
    fake.streams[102]["offers"] = [link(11, 100)]
    _, conflicted = sync(client)
    assert conflicted["conflict"]
    assert set_share(client, conflicted, 11, 20).status_code == 409
    assert client.get("/api/campaigns/7/streams").json()[1] == conflicted
    assert draft["dirty"]


def test_set_share_rejects_disabled_and_archived_offers(client, fake):
    fake.streams[102]["offers"][1]["state"] = "disabled"
    _, stream = sync(client)
    assert set_share(client, stream, 12, 20).status_code == 422
    archived = change(client, stream, "remove", 13)
    assert set_share(client, archived, 13, 20).status_code in (404, 422)
    assert client.get("/api/campaigns/7/streams").json()[1] == archived
    assert all(method != "PUT" for method, _, _ in fake.calls)


def test_draft_is_local_and_cancel_is_exact(client, fake):
    redirect, published = sync(client)
    assert not redirect["editable"] and redirect["offers"] == []
    assert redirect["published_offers"] is None
    assert published["published_offers"] is None
    original = deepcopy(fake.streams)
    draft = change(client, published, "add", 14)
    assert draft["dirty"]
    assert draft["published_offers"] == published["offers"]
    assert shares(draft) == {11: 25, 12: 25, 13: 25, 14: 25}
    assert client.get("/api/campaigns/7/streams").json()[1] == draft

    reverted = cancel(client, draft)
    assert not reverted["dirty"]
    assert reverted["published_offers"] is None
    assert reverted["offers"] == published["offers"]
    assert fake.streams == original


def test_publish_archives_and_restores_with_pinned_weight(client, fake):
    _, stream = sync(client)
    redirect = deepcopy(fake.streams[101])
    landings = deepcopy(fake.streams[102]["landings"])
    stream = change(client, stream, "pin", 11)
    stream = change(client, stream, "add", 14)
    stream = change(client, stream, "remove", 12)
    assert shares(stream) == {11: 25, 12: 0, 13: 38, 14: 37}
    assert fake.streams[102]["offers"] == [link(11, 25), link(12, 38), link(13, 37)]

    first_push = publish(client, stream)
    assert first_push.status_code == 200, first_push.text
    published = first_push.json()
    assert not published["dirty"]
    assert next(offer for offer in published["offers"] if offer["id"] == 12)["removed"]
    assert fake.streams[102]["offers"] == [link(11, 25), link(13, 38), link(14, 37)]

    restored = change(client, published, "restore", 12)
    second_push = publish(client, restored)
    assert second_push.status_code == 200, second_push.text
    assert shares(second_push.json()) == {11: 25, 12: 25, 13: 25, 14: 25}
    assert fake.streams[101] == redirect
    assert fake.streams[102]["landings"] == landings


def test_fetch_keeps_draft_and_detects_remote_removal(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    fake.streams[102]["offers"].pop()
    _, conflicted = sync(client)

    assert conflicted["dirty"] and conflicted["conflict"]
    assert conflicted["offers"] == draft["offers"]
    assert conflicted["published_offers"] == stream["offers"]
    assert publish(client, conflicted).status_code == 409
    assert not cancel(client, conflicted)["dirty"]

    _, refreshed = sync(client)
    assert not refreshed["conflict"]
    assert shares(refreshed) == {11: 25, 12: 38, 13: 0}
    assert refreshed["offers"][2]["removed"]
    assert not refreshed["dirty"]
    assert all(method != "PUT" for method, _, _ in fake.calls)


def test_fetch_pin_restore_publish_after_two_external_removals(client, fake):
    fake.streams[102]["offers"] = [link(offer_id, 25) for offer_id in (11, 12, 13, 14)]
    sync(client)
    fake.streams[102]["offers"] = [link(11, 25), link(12, 25)]

    _, fetched = sync(client)
    assert not fetched["dirty"]
    assert shares(fetched) == {11: 25, 12: 25, 13: 0, 14: 0}
    assert [offer["id"] for offer in fetched["offers"] if offer["removed"]] == [13, 14]

    pinned = change(client, fetched, "pin", 11)
    assert not pinned["dirty"] and pinned["offers"][0]["pinned"]
    assert shares(pinned) == shares(fetched)
    assert publish(client, pinned).status_code == 200
    draft = change(client, pinned, "restore", 13)
    assert draft["dirty"] and draft["issue"] is None
    expected = {11: 25, 12: 38, 13: 37, 14: 0}
    assert shares(draft) == expected
    assert all(method == "GET" for method, _, _ in fake.calls)

    response = publish(client, draft)
    assert response.status_code == 200, response.text
    assert not response.json()["dirty"]
    desired = [link(11, 25), link(12, 38), link(13, 37)]
    assert fake.calls[-2:] == [
        ("PUT", "streams/102", {"offers": desired}),
        ("GET", "streams/102", {}),
    ]
    assert fake.streams[102]["offers"] == desired
    _, confirmed = sync(client)
    assert shares(confirmed) == expected and confirmed["offers"] == draft["offers"]


def test_publish_refuses_stale_version_and_remote_changes(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    assert publish(client, stream).status_code == 409

    fake.streams[102]["offers"] = [link(11, 100)]
    assert publish(client, draft).status_code == 409
    assert client.get("/api/campaigns/7/streams").json()[1]["dirty"]
    assert all(method != "PUT" for method, _, _ in fake.calls)


def test_publish_confirms_timeout_with_readback(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    fake.timeout_after_put = True

    response = publish(client, draft)

    assert response.status_code == 200, response.text
    assert not response.json()["dirty"]
    assert sum(method == "PUT" for method, _, _ in fake.calls) == 1


def test_deleted_stream_keeps_draft_until_cancel_without_writing(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    fake.streams[102]["state"] = "deleted"

    assert publish(client, draft).status_code == 409
    _, missing = sync(client)
    assert missing["missing"] and missing["dirty"]
    assert missing["published_offers"] == stream["offers"]
    assert all(method != "PUT" for method, _, _ in fake.calls)

    cancel(client, missing)
    assert [stream["id"] for stream in sync(client)] == [101]


def test_deleted_stream_does_not_confirm_put(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    fake.delete_after_put = True

    assert publish(client, draft).status_code == 502
    pending = client.get("/api/campaigns/7/streams").json()[1]
    assert pending["dirty"] and pending["publication_pending"]
    _, missing = sync(client)
    assert missing["dirty"] and missing["missing"]
    assert not missing["publication_pending"]


def test_fetch_of_moved_stream_preserves_archive_and_requires_cancel_in_old_campaign(client, fake):
    _, stream = sync(client)
    archived = publish(client, change(client, stream, "remove", 12)).json()
    change(client, archived, "add", 14)
    fake.campaigns[8] = {**fake.campaigns[7], "id": 8}
    fake.streams[102]["campaign_id"] = 8

    assert client.post("/api/campaigns/8/sync").status_code == 409
    _, missing = sync(client)
    assert missing["dirty"] and missing["missing"]
    cancel(client, missing)

    [moved] = sync(client, 8)
    assert moved["offers"] == archived["offers"]
    assert not moved["missing"] and not moved["dirty"]
    assert [row["id"] for row in sync(client, 7)] == [101]
    assert client.get("/api/campaigns/8/streams").json() == [moved]


def test_schema_change_hides_offers_temporarily_and_preserves_archive(client, fake):
    _, stream = sync(client)
    assert publish(client, change(client, stream, "remove", 12)).status_code == 200
    fake.streams[102]["schema"] = "redirect"

    _, redirected = sync(client)
    assert not redirected["editable"] and not redirected["offers"]

    fake.streams[102]["schema"] = "landings"
    _, restored = sync(client)
    archived = next(offer for offer in restored["offers"] if offer["id"] == 12)
    assert archived["removed"] and archived["share"] == 0


def test_schema_change_allows_cancel_of_draft(client, fake):
    _, stream = sync(client)
    change(client, stream, "add", 14)
    fake.streams[102]["schema"] = "redirect"

    _, conflicted = sync(client)
    assert conflicted["conflict"] and conflicted["dirty"] and not conflicted["editable"]
    assert conflicted["offers"] == []
    assert conflicted["published_offers"] == stream["offers"]
    cancel(client, conflicted)
    _, redirected = sync(client)
    assert not redirected["dirty"] and not redirected["conflict"]


def test_configuration_key_stays_server_side_and_draft_survives_restart(client, settings, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    with TestClient(create_app(settings, MockTransport(fake))) as restarted:
        assert restarted.get("/api/campaigns/7/streams").json()[1] == draft
        assert restarted.get("/api/settings").json() == {
            "campaign_prefix": "test_demo_",
            "keitaro_admin_url": "https://tracker.example/admin/",
        }


def test_clean_pin_and_unpin_are_local_preferences_without_reallocation(client, fake):
    fake.streams[102]["offers"] = [link(11, 25), link(12, 25)]
    _, stream = sync(client)
    before_calls = list(fake.calls)
    pinned = change(client, stream, "pin", 11)
    assert not pinned["dirty"]
    assert pinned["published_offers"] is None
    assert pinned["offers"][0]["pinned"]
    unpinned = change(client, pinned, "unpin", 11)
    assert not unpinned["dirty"]
    assert shares(unpinned) == {11: 25, 12: 25}
    assert fake.calls == before_calls


@pytest.mark.parametrize("existing_draft", [False, True])
def test_pin_rejects_locked_subtotal_above_100_without_saving(
    client, fake, settings, existing_draft
):
    fake.streams[102]["offers"] = [link(11, 60), link(12, 60)]
    _, stream = sync(client)
    pinned = change(client, stream, "pin", 11)
    if existing_draft:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from adrobot.db import StreamDraft

        engine = create_engine(settings.database_url)
        try:
            with Session(engine) as db:
                # A draft persisted by an older version can retain imported shares.
                saved = db.get(StreamDraft, 102)
                saved.draft = pinned["offers"]
                db.commit()
        finally:
            engine.dispose()
        pinned = client.get("/api/campaigns/7/streams").json()[1]
    before_calls = list(fake.calls)
    response = client.patch(
        "/api/streams/102/draft",
        json={"action": "pin", "offer_id": 12, "version": pinned["version"]},
    )
    assert response.status_code == 422, response.text
    assert "100%" in response.json()["detail"]
    assert client.get("/api/campaigns/7/streams").json()[1] == pinned
    assert fake.calls == before_calls
    unpinned = change(client, pinned, "unpin", 11)
    assert not unpinned["offers"][0]["pinned"]


def test_clean_pin_all_below_100_preserves_imported_shares(client, fake):
    fake.streams[102]["offers"] = [link(11, 25), link(12, 25)]
    _, stream = sync(client)
    before_calls = list(fake.calls)
    pinned = change(client, change(client, stream, "pin", 11), "pin", 12)
    assert not pinned["dirty"]
    assert shares(pinned) == {11: 25, 12: 25}
    assert all(offer["pinned"] for offer in pinned["offers"])
    assert fake.calls == before_calls


@pytest.mark.parametrize("operation", ["fetch", "publish"])
def test_matching_external_edit_never_confirms_an_unstarted_publication(client, fake, operation):
    _, stream = sync(client)
    draft = set_share(client, stream, 11, 40).json()
    fake.streams[102]["offers"] = [link(11, 40), link(12, 30), link(13, 30)]
    if operation == "fetch":
        _, result = sync(client)
    else:
        assert publish(client, draft).status_code == 409
        result = client.get("/api/campaigns/7/streams").json()[1]
    assert result["dirty"] and result["conflict"]
    assert result["offers"] == draft["offers"]
    assert all(method != "PUT" for method, _, _ in fake.calls)


@pytest.mark.parametrize("remote", ["desired", "base", "different", "deleted", "redirect", "moved"])
def test_uncertain_publication_survives_restart_and_only_fetch_unlocks(
    client, fake, settings, monkeypatch, remote
):
    import httpx

    _, stream = sync(client)
    draft = set_share(client, stream, 11, 40).json()
    original_get = fake._get

    def failed_readback(request, path):
        if path == "streams/102" and any(method == "PUT" for method, _, _ in fake.calls):
            raise httpx.ReadTimeout("readback unavailable", request=request)
        return original_get(request, path)

    monkeypatch.setattr(fake, "_get", failed_readback)
    assert publish(client, draft).status_code == 502
    pending = client.get("/api/campaigns/7/streams").json()[1]
    assert pending["publication_pending"] and pending["dirty"]
    assert pending["version"] > draft["version"]
    with TestClient(create_app(settings, MockTransport(fake))) as restarted:
        assert restarted.get("/api/campaigns/7/streams").json()[1] == pending
        assert set_share(restarted, pending, 11, 50).status_code == 409
        assert (
            restarted.delete(
                "/api/streams/102/draft", params={"version": pending["version"]}
            ).status_code
            == 409
        )
        assert publish(restarted, pending).status_code == 409
        assert sum(method == "PUT" for method, _, _ in fake.calls) == 1

        def failed_fetch(request, path):
            if path == "campaigns/7/streams":
                return httpx.Response(503, json={"error": "unavailable"})
            return original_get(request, path)

        monkeypatch.setattr(fake, "_get", failed_fetch)
        assert restarted.post("/api/campaigns/7/sync").status_code == 502
        assert restarted.get("/api/campaigns/7/streams").json()[1] == pending
        if remote == "base":
            fake.streams[102]["offers"] = [link(11, 25), link(12, 38), link(13, 37)]
        elif remote == "different":
            fake.streams[102]["offers"] = [link(11, 100)]
        elif remote == "deleted":
            del fake.streams[102]
        elif remote == "redirect":
            fake.streams[102]["schema"] = "redirect"
        elif remote == "moved":
            fake.streams[102]["campaign_id"] = 8
        monkeypatch.setattr(fake, "_get", original_get)
        _, reconciled = sync(restarted)
        assert not reconciled["publication_pending"]
        if remote == "desired":
            assert not reconciled["dirty"]
            assert reconciled["offers"] == draft["offers"]
        else:
            assert reconciled["dirty"]
            assert reconciled["published_offers"] == stream["offers"]
            assert reconciled["conflict"] == (remote != "base")
            if reconciled["editable"]:
                assert reconciled["offers"] == draft["offers"]


def test_publication_marker_and_version_are_committed_before_remote_put(
    client, fake, settings, monkeypatch
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from adrobot.db import StreamDraft
    from adrobot.keitaro import Keitaro

    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    actual_set_offers = Keitaro.set_offers
    checked = []

    def checked_set_offers(tracker, stream_id, desired):
        engine = create_engine(settings.database_url)
        try:
            with Session(engine) as db:
                persisted = db.get(StreamDraft, stream_id)
                assert persisted.publication_pending
                assert persisted.version > draft["version"]
                assert persisted.draft == draft["offers"]
                checked.append(True)
        finally:
            engine.dispose()
        actual_set_offers(tracker, stream_id, desired)

    monkeypatch.setattr(Keitaro, "set_offers", checked_set_offers)
    result = publish(client, draft)
    assert result.status_code == 200, result.text
    assert checked == [True]
    assert not result.json()["publication_pending"]


def test_pinning_existing_draft_is_cancelled_with_draft(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    pinned = change(client, draft, "pin", 11)
    assert pinned["dirty"] and pinned["offers"][0]["pinned"]
    assert pinned["published_offers"] == stream["offers"]
    assert cancel(client, pinned)["offers"] == stream["offers"]


@pytest.mark.parametrize("unavailable", ["disabled", "deleted", "missing"])
def test_publish_revalidates_positive_share_offer_availability(client, fake, unavailable):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    if unavailable == "missing":
        fake.offers = [offer for offer in fake.offers if offer["id"] != 14]
    else:
        next(offer for offer in fake.offers if offer["id"] == 14)["state"] = unavailable
    before_calls = len(fake.calls)
    response = publish(client, draft)
    assert response.status_code == 422, response.text
    assert "14" in response.json()["detail"]
    assert "недоступ" in response.json()["detail"].lower()
    assert client.get("/api/campaigns/7/streams").json()[1] == draft
    assert not draft["publication_pending"]
    assert all(method != "PUT" for method, _, _ in fake.calls[before_calls:])
    assert sum(path == "offers" for _, path, _ in fake.calls[before_calls:]) == 1


@pytest.mark.parametrize("excluded", ["archived", "disabled", "zero"])
def test_publish_availability_check_ignores_links_without_traffic(client, fake, excluded):
    if excluded == "disabled":
        fake.streams[102]["offers"][0]["state"] = "disabled"
    _, stream = sync(client)
    if excluded == "archived":
        draft = change(client, stream, "remove", 11)
    elif excluded == "disabled":
        draft = change(client, stream, "add", 14)
    else:
        draft = set_share(client, stream, 11, 0).json()
    fake.offers = [offer for offer in fake.offers if offer["id"] != 11]
    before_calls = len(fake.calls)
    response = publish(client, draft)
    assert response.status_code == 200, response.text
    assert not response.json()["dirty"]
    assert sum(path == "offers" for _, path, _ in fake.calls[before_calls:]) == 1


@pytest.mark.parametrize(
    "readback", ["base", "different", "missing", "deleted", "redirect", "moved"]
)
@pytest.mark.parametrize("timeout", [False, True])
def test_nonconfirming_publication_readback_stays_locked_until_fetch(
    client, fake, settings, monkeypatch, readback, timeout
):
    import httpx

    _, stream = sync(client)
    draft = set_share(client, stream, 11, 40).json()
    stale_flow = deepcopy(fake.streams[102])
    if readback == "different":
        stale_flow["offers"] = [link(11, 100)]
    elif readback == "deleted":
        stale_flow["state"] = "deleted"
    elif readback == "redirect":
        stale_flow["schema"] = "redirect"
    elif readback == "moved":
        stale_flow["campaign_id"] = 8
    actual_get = fake._get

    def stale_readback(request, path):
        if path == "streams/102" and any(method == "PUT" for method, _, _ in fake.calls):
            if readback == "missing":
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=stale_flow)
        return actual_get(request, path)

    fake.timeout_after_put = timeout
    monkeypatch.setattr(fake, "_get", stale_readback)
    assert publish(client, draft).status_code == 502
    assert fake.streams[102]["offers"] == [link(11, 40), link(12, 30), link(13, 30)]
    pending = client.get("/api/campaigns/7/streams").json()[1]
    assert pending["publication_pending"] and pending["dirty"]
    assert pending["offers"] == draft["offers"]
    with TestClient(create_app(settings, MockTransport(fake))) as restarted:
        assert restarted.get("/api/campaigns/7/streams").json()[1] == pending
        assert (
            restarted.delete(
                "/api/streams/102/draft", params={"version": pending["version"]}
            ).status_code
            == 409
        )
        assert set_share(restarted, pending, 11, 50).status_code == 409
        assert publish(restarted, pending).status_code == 409
        assert sum(method == "PUT" for method, _, _ in fake.calls) == 1
        monkeypatch.setattr(fake, "_get", actual_get)
        _, confirmed = sync(restarted)
        assert not confirmed["dirty"] and not confirmed["publication_pending"]
        assert confirmed["offers"] == draft["offers"]
        assert confirmed["offers"][0]["pinned"]


@pytest.mark.parametrize("listed", ["base", "missing"])
@pytest.mark.parametrize("timeout", [False, True])
def test_pending_fetch_uses_confirming_detail_when_campaign_list_is_stale(
    client, fake, monkeypatch, listed, timeout
):
    import httpx

    _, stream = sync(client)
    base = deepcopy(fake.streams[102])
    draft = set_share(client, stream, 11, 40).json()
    original_get = fake._get
    detail_reads = []

    def stale_list_and_first_detail(request, path):
        if any(method == "PUT" for method, _, _ in fake.calls):
            if path == "streams/102":
                detail_reads.append(True)
                if len(detail_reads) == 1:
                    return httpx.Response(200, json=base)
            if path == "campaigns/7/streams":
                rows = [fake.streams[101]]
                if listed == "base":
                    rows.append(base)
                return httpx.Response(200, json=rows)
        return original_get(request, path)

    fake.timeout_after_put = timeout
    monkeypatch.setattr(fake, "_get", stale_list_and_first_detail)
    assert publish(client, draft).status_code == 502
    _, confirmed = sync(client)
    assert len(detail_reads) == 2
    assert not confirmed["dirty"] and not confirmed["publication_pending"]
    assert confirmed["offers"] == draft["offers"]
    assert confirmed["offers"][0]["pinned"]


@pytest.mark.parametrize("detail", ["different", "missing", "redirect", "moved"])
def test_pending_fetch_disagreement_preserves_marker_and_draft(client, fake, monkeypatch, detail):
    import httpx

    _, stream = sync(client)
    base = deepcopy(fake.streams[102])
    draft = set_share(client, stream, 11, 40).json()
    original_get = fake._get

    def stale_reads(request, path):
        if any(method == "PUT" for method, _, _ in fake.calls):
            if path == "campaigns/7/streams":
                return httpx.Response(200, json=[fake.streams[101], base])
            if path == "streams/102":
                value = deepcopy(base)
                if detail == "missing":
                    return httpx.Response(404, json={"error": "missing"})
                if detail == "different":
                    value["offers"] = [link(11, 100)]
                elif detail == "redirect":
                    value["schema"] = "redirect"
                else:
                    value["campaign_id"] = 8
                return httpx.Response(200, json=value)
        return original_get(request, path)

    monkeypatch.setattr(fake, "_get", stale_reads)
    assert publish(client, draft).status_code == 502
    pending = client.get("/api/campaigns/7/streams").json()[1]
    assert client.post("/api/campaigns/7/sync").status_code == 502
    assert client.get("/api/campaigns/7/streams").json()[1] == pending
    assert pending["publication_pending"] and pending["offers"] == draft["offers"]
    assert (
        client.delete("/api/streams/102/draft", params={"version": pending["version"]}).status_code
        == 409
    )


@pytest.mark.parametrize(
    "outcome", ["rejected", "base", "different", "deleted", "redirect", "moved"]
)
def test_rejected_put_or_fetch_after_nonconfirmation_unlocks_preserved_draft(
    client, fake, settings, outcome
):
    import httpx

    _, stream = sync(client)
    draft = change(client, stream, "add", 14)

    def transport(request):
        if request.method == "PUT" and outcome == "rejected":
            return httpx.Response(422, json={"error": "invalid offers"})
        response = fake(request)
        if request.method == "PUT":
            if outcome == "base":
                fake.streams[102]["offers"] = [link(11, 25), link(12, 38), link(13, 37)]
            elif outcome == "different":
                fake.streams[102]["offers"] = [link(11, 100)]
            elif outcome == "deleted":
                del fake.streams[102]
            elif outcome == "redirect":
                fake.streams[102]["schema"] = "redirect"
            elif outcome == "moved":
                fake.streams[102]["campaign_id"] = 8
        return response

    with TestClient(create_app(settings, MockTransport(transport))) as api:
        assert publish(api, draft).status_code in (409, 502)
        result = api.get("/api/campaigns/7/streams").json()[1]
        if outcome != "rejected":
            assert result["publication_pending"] and result["dirty"]
            _, result = sync(api)
        assert not result["publication_pending"]
        assert result["dirty"]
        assert result["offers"] == ([] if outcome == "redirect" else draft["offers"])
        assert result["conflict"] == (outcome not in {"rejected", "base"})
        assert cancel(api, result)["offers"] == ([] if outcome == "redirect" else stream["offers"])


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            "https://user:password@tracker.example:8443/base/?token=secret#fragment",
            "https://tracker.example:8443/base/admin/",
        ),
        ("http://[::1]:8080", "http://[::1]:8080/admin/"),
    ],
)
def test_settings_admin_url_strips_sensitive_url_parts(settings, fake, configured, expected):
    config = settings.model_copy(update={"keitaro_base_url": HttpUrl(configured)})
    with TestClient(create_app(config, MockTransport(fake))) as api:
        assert api.get("/api/settings").json() == {
            "campaign_prefix": "test_demo_",
            "keitaro_admin_url": expected,
        }
