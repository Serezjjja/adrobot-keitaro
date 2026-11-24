from copy import deepcopy

from fastapi.testclient import TestClient
from httpx import MockTransport

from adrobot.config import Settings
from adrobot.main import create_app
from tests.fakes import link

CREATE_DATA = {
    "name": "test_demo_autumn",
    "country": "ca",
    "offer_id": 11,
    "domain_id": 1,
    "group_id": 2,
    "traffic_source_id": 3,
}


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


def test_creation_confirms_two_flows_and_reuses_same_campaign(client, fake):
    response = client.post("/api/campaigns", json=CREATE_DATA)
    assert response.status_code == 201, response.text
    campaign = response.json()
    assert client.post("/api/campaigns", json=CREATE_DATA).json() == campaign

    created = fake.campaigns[campaign["id"]]
    assert created["state"] == "active"
    assert (created["domain_id"], created["group_id"], created["traffic_source_id"]) == (1, 2, 3)
    geo, fallback = [
        flow for flow in fake.streams.values() if flow["campaign_id"] == campaign["id"]
    ]
    assert (geo["position"], geo["type"], geo["schema"]) == (1, "regular", "redirect")
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["CA"]}]
    assert geo["action_payload"] == "https://www.google.com/"
    assert (fallback["position"], fallback["type"], fallback["schema"]) == (
        2,
        "default",
        "landings",
    )
    assert fallback["offers"] == [link(11, 100)]
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 1


def test_partial_creation_stays_disabled_and_retry_finishes(client, fake):
    fake.fail_stream_position = 2
    response = client.post("/api/campaigns", json=CREATE_DATA)
    assert response.status_code == 502
    assert "#8" in response.json()["detail"]
    assert fake.campaigns[8]["state"] == "disabled"
    assert [flow["position"] for flow in fake.streams.values() if flow["campaign_id"] == 8] == [1]

    fake.fail_stream_position = None
    retry = client.post("/api/campaigns", json=CREATE_DATA)
    assert retry.status_code == 201
    assert retry.json()["id"] == 8
    assert fake.campaigns[8]["state"] == "active"
    assert [flow["position"] for flow in fake.streams.values() if flow["campaign_id"] == 8] == [
        1,
        2,
    ]


def test_campaign_timeout_checks_stable_alias_before_creating_again(client, fake):
    fake.timeout_after_campaign = True

    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == 201, response.text
    assert len(fake.campaigns) == 2
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 1


def test_repeat_submission_does_not_enable_campaign_stopped_by_operator(client, fake):
    created = client.post("/api/campaigns", json=CREATE_DATA).json()
    fake.campaigns[created["id"]]["state"] = "disabled"
    before = len(fake.calls)

    repeated = client.post("/api/campaigns", json=CREATE_DATA)

    assert repeated.status_code == 201
    assert repeated.json() == created
    assert fake.campaigns[created["id"]]["state"] == "disabled"
    assert all(method == "GET" for method, _, _ in fake.calls[before:])


def test_unconfirmed_activation_is_not_repeated_automatically(client, fake):
    fake.timeout_during_enable = True
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    fake.timeout_during_enable = False

    retry = client.post("/api/campaigns", json=CREATE_DATA)

    assert retry.status_code == 409
    assert fake.campaigns[8]["state"] == "disabled"
    assert sum(path.endswith("/enable") for _, path, _ in fake.calls) == 1


def test_validation_does_not_write_to_tracker(client, fake):
    for invalid in [
        {"country": "XX"},
        {"name": "random"},
        {"offer_id": 0},
        {"offer_id": 16},
        {"group_id": 50},
    ]:
        response = client.post("/api/campaigns", json={**CREATE_DATA, **invalid})
        assert response.status_code == 422, response.text
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_search_returns_active_offers_by_id_or_name(client):
    assert client.get("/api/offers", params={"q": "HORIZON"}).json() == [
        {"id": 14, "name": "Horizon"}
    ]
    assert client.get("/api/offers", params={"q": "11"}).json()[0]["id"] == 11
    assert client.get("/api/offers", params={"q": "16"}).json() == []


def test_draft_is_local_and_cancel_is_exact(client, fake):
    redirect, published = sync(client)
    assert not redirect["editable"] and redirect["offers"] == []
    original = deepcopy(fake.streams)
    draft = change(client, published, "add", 14)
    assert draft["dirty"]
    assert shares(draft) == {11: 25, 12: 25, 13: 25, 14: 25}
    assert client.get("/api/campaigns/7/streams").json()[1] == draft

    reverted = cancel(client, draft)
    assert not reverted["dirty"]
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
    assert publish(client, conflicted).status_code == 409
    assert not cancel(client, conflicted)["dirty"]

    _, refreshed = sync(client)
    assert not refreshed["conflict"]
    assert shares(refreshed) == {11: 50, 12: 50, 13: 0}
    assert refreshed["offers"][2]["removed"]
    assert refreshed["dirty"]
    assert all(method != "PUT" for method, _, _ in fake.calls)


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
    assert all(method != "PUT" for method, _, _ in fake.calls)

    cancel(client, missing)
    assert [stream["id"] for stream in sync(client)] == [101]


def test_deleted_stream_does_not_confirm_put(client, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    fake.delete_after_put = True

    assert publish(client, draft).status_code == 409
    assert client.get("/api/campaigns/7/streams").json()[1]["dirty"]


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
    cancel(client, conflicted)
    _, redirected = sync(client)
    assert not redirected["dirty"] and not redirected["conflict"]


def test_configuration_key_stays_server_side_and_draft_survives_restart(client, database_url, fake):
    _, stream = sync(client)
    draft = change(client, stream, "add", 14)
    settings = Settings(database_url=database_url, keitaro_api_key="fake-key")
    with TestClient(create_app(settings, MockTransport(fake))) as restarted:
        assert restarted.get("/api/campaigns/7/streams").json()[1] == draft
        assert restarted.get("/api/settings").json() == {"campaign_prefix": "test_"}
