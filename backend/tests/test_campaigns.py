import httpx
import pytest

from tests.fakes import link

CREATE_DATA = {
    "name": "test_demo_autumn",
    "country": "ca",
    "offer_id": 11,
    "domain_id": 1,
    "group_id": 2,
    "traffic_source_id": 3,
}


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


def test_creation_requires_a_name_after_a_compound_prefix(client, fake):
    prefix = "test_demo_team_"
    client.app.state.settings.campaign_prefix = prefix

    assert client.post("/api/campaigns", json={**CREATE_DATA, "name": prefix}).status_code == 422
    assert not fake.calls
    assert (
        client.post("/api/campaigns", json={**CREATE_DATA, "name": prefix + "autumn"}).status_code
        == 201
    )


def test_partial_creation_stays_disabled_and_retry_finishes(client, fake, definite_stream_failure):
    from sqlalchemy import select

    from adrobot.db import CampaignCreation

    fake.fail_stream_position = 2
    response = client.post("/api/campaigns", json=CREATE_DATA)
    assert response.status_code == 502
    assert "#8" in response.json()["detail"]
    assert fake.campaigns[8]["state"] == "disabled"
    assert [flow["position"] for flow in fake.streams.values() if flow["campaign_id"] == 8] == [1]
    with client.app.state.sessions() as db:
        operation = db.scalar(select(CampaignCreation))
        assert operation.submitted_stream_positions == [1]

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


def test_completed_creation_does_not_open_an_archived_campaign(client, fake):
    response = client.post("/api/campaigns", json=CREATE_DATA)
    campaign_id = response.json()["id"]
    fake.campaigns[campaign_id]["state"] = "deleted"
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 409

    del fake.campaigns[campaign_id]
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 409
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 1


def test_definite_creation_refusal_allows_corrected_parameters(client, fake):
    fake.reject_campaign = True
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    fake.reject_campaign = False

    corrected = client.post("/api/campaigns", json={**CREATE_DATA, "country": "AU"})

    assert corrected.status_code == 201, corrected.text
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 2


def test_unconfirmed_activation_is_not_repeated_automatically(client, fake):
    fake.timeout_during_enable = True
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    fake.timeout_during_enable = False

    retry = client.post("/api/campaigns", json=CREATE_DATA)

    assert retry.status_code == 409
    assert fake.campaigns[8]["state"] == "disabled"
    assert sum(path.endswith("/enable") for _, path, _ in fake.calls) == 1


def test_uncertain_creation_never_adopts_another_installation(client, fake, monkeypatch):
    from hashlib import sha256

    from adrobot.errors import KeitaroError

    predictable_alias = "adrobot-" + sha256(CREATE_DATA["name"].encode()).hexdigest()[:24]

    def other_installation_created(settings):
        fake.campaigns[8] = {**settings, "id": 8, "alias": predictable_alias}
        raise KeitaroError("Response lost", uncertain=True)

    monkeypatch.setattr(client.app.state.tracker, "create_campaign", other_installation_created)
    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == 409
    assert fake.campaigns[8]["state"] == "disabled"
    assert not any(flow["campaign_id"] == 8 for flow in fake.streams.values())


def test_creation_reserves_random_remote_alias_before_post(client, monkeypatch):
    from hashlib import sha256

    from adrobot.db import CampaignCreation

    local_alias = "adrobot-" + sha256(CREATE_DATA["name"].encode()).hexdigest()[:24]
    create = client.app.state.tracker.create_campaign

    def check_reservation(settings):
        with client.app.state.sessions() as db:
            operation = db.get(CampaignCreation, local_alias)
            assert operation is not None
            assert operation.remote_alias == settings["alias"]
            assert operation.remote_alias != local_alias
            assert operation.remote_alias.startswith("adrobot-")
        return create(settings)

    monkeypatch.setattr(client.app.state.tracker, "create_campaign", check_reservation)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 201


def test_creation_rejects_stream_readback_without_explicit_state(client, fake, monkeypatch):
    get = fake._get

    def omit_stream_state(request, path):
        response = get(request, path)
        if path == "campaigns/8/streams":
            return httpx.Response(
                200,
                json=[
                    {key: value for key, value in flow.items() if key != "state"}
                    for flow in response.json()
                ],
            )
        return response

    monkeypatch.setattr(fake, "_get", omit_stream_state)
    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == 409
    assert fake.campaigns[8]["state"] == "disabled"
    assert not any(path.endswith("/enable") for _, path, _ in fake.calls)


def test_offer_search_excludes_occupied_results_before_limit(client, fake):
    from adrobot.campaigns import search_offers

    fake.offers = [
        {"id": number, "name": f"Match {number:02}", "state": "active"} for number in range(1, 14)
    ]
    tracker = client.app.state.tracker
    assert [
        offer.id for offer in search_offers(tracker, "Match", exclude_ids=set(range(1, 13)))
    ] == [13]
    assert [offer.id for offer in search_offers(tracker, "13", exclude_ids=set(range(1, 13)))] == [
        13
    ]


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


@pytest.mark.parametrize("rotation", ["weight", None])
def test_retry_requires_confirmed_positional_rotation(
    client, fake, rotation, definite_stream_failure
):
    fake.fail_stream_position = 2
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    if rotation is None:
        del fake.campaigns[8]["type"]
    else:
        fake.campaigns[8]["type"] = rotation
    fake.fail_stream_position = None
    before = len(fake.calls)

    retry = client.post("/api/campaigns", json=CREATE_DATA)

    assert retry.status_code == 409
    assert fake.campaigns[8]["state"] == "disabled"
    assert all(method == "GET" for method, _, _ in fake.calls[before:])


def test_enable_readback_requires_explicit_active_state(client, fake, monkeypatch):
    get = fake._get

    def omit_state_after_enable(request, path):
        if path == "campaigns/8" and fake.campaigns[8]["state"] == "active":
            campaign = {k: v for k, v in fake.campaigns[8].items() if k != "state"}
            return httpx.Response(200, json=campaign)
        return get(request, path)

    monkeypatch.setattr(fake, "_get", omit_state_after_enable)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502

    monkeypatch.setattr(fake, "_get", get)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 201
    assert sum(path.endswith("/enable") for _, path, _ in fake.calls) == 1


@pytest.mark.parametrize("collision", [False, True])
def test_legacy_reservation_without_id_never_adopts_predictable_alias(client, fake, collision):
    from hashlib import sha256

    from adrobot.db import CampaignCreation
    from adrobot.schema import CreateCampaign

    alias = "adrobot-" + sha256(CREATE_DATA["name"].encode()).hexdigest()[:24]
    with client.app.state.sessions() as db:
        db.add(CampaignCreation(alias=alias, request=CreateCampaign(**CREATE_DATA).model_dump()))
        db.commit()
    if collision:
        fake.campaigns[8] = {
            **fake.campaigns[7],
            "id": 8,
            "name": CREATE_DATA["name"],
            "alias": alias,
            "state": "disabled",
            "type": "position",
        }

    response = client.post("/api/campaigns", json=CREATE_DATA)

    if collision:
        assert response.status_code == 409
        assert all(method == "GET" for method, _, _ in fake.calls)
    else:
        assert response.status_code == 201
        assert fake.campaigns[response.json()["id"]]["alias"] != alias


def test_legacy_reservation_with_confirmed_id_finishes_using_old_alias(
    client, fake, definite_stream_failure
):
    from hashlib import sha256

    from adrobot.db import CampaignCreation

    fake.fail_stream_position = 2
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    alias = "adrobot-" + sha256(CREATE_DATA["name"].encode()).hexdigest()[:24]
    with client.app.state.sessions() as db:
        operation = db.get(CampaignCreation, alias)
        operation.remote_alias = None
        db.commit()
    fake.campaigns[8]["alias"] = alias
    fake.fail_stream_position = None

    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == 201
    assert response.json()["id"] == 8
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 1


def test_unconfirmed_campaign_retry_reuses_reserved_remote_alias(client, monkeypatch):
    from adrobot.errors import KeitaroError

    tracker = client.app.state.tracker
    create = tracker.create_campaign
    reserved = []

    def lose_request(settings):
        reserved.append(settings["alias"])
        raise KeitaroError("Request lost", uncertain=True)

    monkeypatch.setattr(tracker, "create_campaign", lose_request)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502

    def check_retry(settings):
        assert settings["alias"] == reserved[0]
        return create(settings)

    monkeypatch.setattr(tracker, "create_campaign", check_retry)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 201


def test_creation_requires_explicit_linked_offer_state_before_enabling(client, fake, monkeypatch):
    get = fake._get

    def omit_disabled_link_state(request, path):
        response = get(request, path)
        if path == "campaigns/8/streams":
            flows = response.json()
            for flow in flows:
                for offer in flow.get("offers", []):
                    fake.streams[flow["id"]]["offers"][0]["state"] = "disabled"
                    offer.pop("state", None)
            return httpx.Response(200, json=flows)
        return response

    monkeypatch.setattr(fake, "_get", omit_disabled_link_state)
    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == 502
    assert fake.campaigns[8]["state"] == "disabled"
    assert not any(path.endswith("/enable") for _, path, _ in fake.calls)

    monkeypatch.setattr(fake, "_get", get)
    fallback = next(
        flow for flow in fake.streams.values() if flow["campaign_id"] == 8 and flow.get("offers")
    )
    fallback["offers"][0]["state"] = "active"
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 201


@pytest.mark.parametrize("refused", [False, True])
def test_new_reservation_posts_without_a_redundant_campaign_list(
    client, fake, monkeypatch, refused
):
    get = fake._get
    list_calls = 0

    def transient_second_list(request, path):
        nonlocal list_calls
        if path == "campaigns":
            list_calls += 1
            posted = any(
                method == "POST" and route == "campaigns" for method, route, _ in fake.calls
            )
            if list_calls == 2 and not posted:
                return httpx.Response(503, json={"error": "temporary outage"})
        return get(request, path)

    monkeypatch.setattr(fake, "_get", transient_second_list)
    fake.reject_campaign = refused
    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == (502 if refused else 201)
    assert list_calls == 1
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 1
    fake.reject_campaign = False
    retried = client.post(
        "/api/campaigns", json={**CREATE_DATA, "country": "AU"} if refused else CREATE_DATA
    )
    assert retried.status_code == 201
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == (
        2 if refused else 1
    )


@pytest.mark.parametrize("reveal_on_duplicate", [False, True])
def test_duplicate_rejection_after_uncertain_creation_preserves_remote_ownership(
    client, fake, monkeypatch, reveal_on_duplicate
):
    from adrobot.errors import KeitaroError

    tracker = client.app.state.tracker
    create = tracker.create_campaign
    get = fake._get
    visible = False
    submitted_aliases = []

    def stale_campaign_list(request, path):
        response = get(request, path)
        if path == "campaigns" and not visible:
            return httpx.Response(200, json=[row for row in response.json() if row["id"] != 8])
        return response

    def create_or_reject_duplicate(settings):
        nonlocal visible
        submitted_aliases.append(settings["alias"])
        if len(submitted_aliases) > 1:
            assert settings["alias"] == submitted_aliases[0]
            visible = reveal_on_duplicate
            raise KeitaroError("Duplicate alias", upstream_status=422)
        return create(settings)

    monkeypatch.setattr(fake, "_get", stale_campaign_list)
    monkeypatch.setattr(tracker, "create_campaign", create_or_reject_duplicate)
    fake.timeout_after_campaign = True
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    assert fake.campaigns[8]["state"] == "disabled"

    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == (
        201 if reveal_on_duplicate else 502
    )
    visible = True
    response = client.post("/api/campaigns", json=CREATE_DATA)

    assert response.status_code == 201, response.text
    assert response.json()["id"] == 8
    assert fake.campaigns[8]["state"] == "active"
    assert len(submitted_aliases) == 2
    assert len(fake.campaigns) == 2
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in fake.calls) == 1


@pytest.fixture
def definite_stream_failure(client, fake, monkeypatch):
    from adrobot.errors import KeitaroError

    create = client.app.state.tracker.create_stream

    def reject_stream(payload):
        if payload["position"] == fake.fail_stream_position:
            raise KeitaroError("Invalid stream", upstream_status=422)
        return create(payload)

    monkeypatch.setattr(client.app.state.tracker, "create_stream", reject_stream)


@pytest.mark.parametrize("lost_response", [False, True])
def test_submitted_stream_is_never_reposted_when_readback_is_stale(
    client, fake, monkeypatch, lost_response
):
    from sqlalchemy import select

    from adrobot.db import CampaignCreation
    from adrobot.errors import KeitaroError

    tracker = client.app.state.tracker
    create = tracker.create_stream
    get = fake._get
    stale = True

    def create_with_recorded_intent(payload):
        with client.app.state.sessions() as db:
            operation = db.scalar(select(CampaignCreation))
            assert payload["position"] in operation.submitted_stream_positions
        result = create(payload)
        if lost_response and payload["position"] == 1:
            raise KeitaroError("Response lost", uncertain=True)
        return result

    def stale_stream_list(request, path):
        if path == "campaigns/8/streams" and stale:
            return httpx.Response(200, json=[])
        return get(request, path)

    monkeypatch.setattr(tracker, "create_stream", create_with_recorded_intent)
    monkeypatch.setattr(fake, "_get", stale_stream_list)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    writes = sum(method == "POST" and path == "streams" for method, path, _ in fake.calls)
    assert writes == (1 if lost_response else 2)
    retry = client.post("/api/campaigns", json=CREATE_DATA)
    assert retry.status_code == 502
    assert "#8" in retry.json()["detail"]
    assert sum(method == "POST" and path == "streams" for method, path, _ in fake.calls) == writes
    stale = False
    monkeypatch.setattr(tracker, "create_stream", create)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 201
    assert sum(method == "POST" and path == "streams" for method, path, _ in fake.calls) == 2


@pytest.mark.parametrize("before_posts", [False, True])
def test_transient_stream_readback_failure_retries_without_duplicate_posts(
    client, fake, monkeypatch, before_posts
):
    get = fake._get
    failed = False

    def fail_final_readback_once(request, path):
        nonlocal failed
        if (
            path == "campaigns/8/streams"
            and not failed
            and (before_posts or any(flow["campaign_id"] == 8 for flow in fake.streams.values()))
        ):
            failed = True
            return httpx.Response(503, json={"error": "temporary"})
        return get(request, path)

    monkeypatch.setattr(fake, "_get", fail_final_readback_once)
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 201
    assert sum(method == "POST" and path == "streams" for method, path, _ in fake.calls) == 2


def test_migrated_known_campaign_requires_both_flows_before_resuming(
    client, fake, monkeypatch, definite_stream_failure
):
    from sqlalchemy import select

    from adrobot.db import CampaignCreation

    fake.fail_stream_position = 2
    assert client.post("/api/campaigns", json=CREATE_DATA).status_code == 502
    fake.fail_stream_position = None
    with client.app.state.sessions() as db:
        operation = db.scalar(select(CampaignCreation))
        operation.submitted_stream_positions = None
        db.commit()
    before = len(fake.calls)
    get = fake._get
    stale = True

    def stale_first_list(request, path):
        if path == "campaigns/8/streams" and stale:
            return httpx.Response(200, json=[])
        return get(request, path)

    monkeypatch.setattr(fake, "_get", stale_first_list)
    missing_both = client.post("/api/campaigns", json=CREATE_DATA)
    assert missing_both.status_code == 502
    assert "1, 2" in missing_both.json()["detail"]
    stale = False
    missing_second = client.post("/api/campaigns", json=CREATE_DATA)
    assert missing_second.status_code == 502
    assert "2" in missing_second.json()["detail"]
    assert all(method == "GET" for method, _, _ in fake.calls[before:])
    with client.app.state.sessions() as db:
        assert db.scalar(select(CampaignCreation)).submitted_stream_positions is None
    fake.streams[104] = {
        "id": 104,
        "campaign_id": 8,
        "name": "Остальные → офферы",
        "position": 2,
        "type": "default",
        "schema": "landings",
        "action_type": "http",
        "state": "active",
        "filters": [],
        "landings": [],
        "offers": [link(11, 100)],
    }
    complete = client.post("/api/campaigns", json=CREATE_DATA)
    assert complete.status_code == 201
    assert not any(
        method == "POST" and path == "streams" for method, path, _ in fake.calls[before:]
    )
    assert fake.campaigns[8]["state"] == "active"
