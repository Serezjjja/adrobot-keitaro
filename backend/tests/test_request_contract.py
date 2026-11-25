import httpx
import pytest
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.errors import AppError, KeitaroError
from adrobot.keitaro import Keitaro
from adrobot.main import create_app
from tests.fakes import link


@pytest.mark.parametrize(
    "states, expected",
    [
        (["active"], True),
        (["active", None], True),
        (["active", "disabled"], False),
        (["active", "deleted"], False),
        ([None], False),
        ([], False),
    ],
)
def test_stateless_offer_detail_requires_active_catalog_without_explicit_conflict(states, expected):
    def transport(request):
        if request.url.path == "/offers":
            return httpx.Response(
                200, json=[{"id": 11, "name": "Northstar", "state": state} for state in states]
            )
        return httpx.Response(200, json={"id": 11, "name": "Northstar"})

    with httpx.Client(
        base_url="https://tracker.example",
        headers={"Api-Key": "fake-key"},
        transport=httpx.MockTransport(transport),
    ) as http:
        if expected:
            assert Keitaro(http).active_offer(11).id == 11
        else:
            with pytest.raises(AppError) as error:
                Keitaro(http).active_offer(11)
            assert error.value.status == 422


def test_batch_offer_availability_limits_results_to_requested_ids_and_skips_empty_input():
    calls = []

    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json=[
                {"id": offer_id, "name": str(offer_id), "state": "active"} for offer_id in [11, 12]
            ],
        )

    with httpx.Client(
        base_url="https://tracker.example",
        headers={"Api-Key": "fake-key"},
        transport=httpx.MockTransport(transport),
    ) as http:
        tracker = Keitaro(http)
        assert tracker.active_offer_ids(set()) == set()
        assert not calls
        assert tracker.active_offer_ids({11}) == {11}
        assert calls == ["/offers"]


@pytest.mark.parametrize("detail_state", [{}, {"state": None}])
@pytest.mark.parametrize("catalog_state", ["disabled", None])
def test_create_rejects_offer_without_confirmed_active_state(
    settings, fake, detail_state, catalog_state
):
    fake.offers[0]["state"] = catalog_state

    def transport(request):
        if request.url.path.endswith("/offers/11"):
            return httpx.Response(200, json={"id": 11, "name": "Northstar", **detail_state})
        return fake(request)

    payload = {
        "name": "test_demo_offer_state",
        "country": "CA",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 2,
        "traffic_source_id": 3,
    }
    with TestClient(create_app(settings, httpx.MockTransport(transport))) as client:
        response = client.post("/api/campaigns", json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Оффер #11 недоступен в Keitaro."
        assert not any(method == "POST" for method, _, _ in fake.calls)


@pytest.mark.parametrize(
    "states, detail_state, expected, detail_requested",
    [
        (["active"], "disabled", {11}, False),
        (["active", "disabled"], "active", set(), False),
        (["disabled"], "active", set(), False),
        ([None], "active", {11}, True),
        (["active", None], "active", {11}, True),
        (["active", None], None, set(), True),
        ([None], None, set(), True),
        ([None], "disabled", set(), True),
        ([], "active", set(), False),
    ],
)
def test_batch_offer_availability_requires_uncontradicted_active_state(
    states, detail_state, expected, detail_requested
):
    paths = []

    def transport(request):
        paths.append(request.url.path)
        if request.url.path == "/offers":
            return httpx.Response(
                200, json=[{"id": 11, "name": "Northstar", "state": state} for state in states]
            )
        assert request.url.path == "/offers/11"
        return httpx.Response(200, json={"id": 11, "name": "Northstar", "state": detail_state})

    with httpx.Client(
        base_url="https://tracker.example",
        headers={"Api-Key": "fake-key"},
        transport=httpx.MockTransport(transport),
    ) as http:
        assert Keitaro(http).active_offer_ids({11}) == expected
    assert ("/offers/11" in paths) == detail_requested


@pytest.mark.parametrize("status", [404, 403, 503])
def test_batch_offer_availability_handles_detail_failures(status):
    def transport(request):
        if request.url.path == "/offers":
            return httpx.Response(200, json=[{"id": 11, "name": "Northstar"}])
        return httpx.Response(status, json={"error": "unavailable"})

    with httpx.Client(
        base_url="https://tracker.example",
        headers={"Api-Key": "fake-key"},
        transport=httpx.MockTransport(transport),
    ) as http:
        if status == 404:
            assert Keitaro(http).active_offer_ids({11}) == set()
        else:
            with pytest.raises(KeitaroError) as error:
                Keitaro(http).active_offer_ids({11})
            assert error.value.upstream_status == status


def test_tracker_api_uses_clean_base_path(settings):
    urls = []
    config = Settings(
        **(
            settings.model_dump(exclude={"keitaro_base_url"})
            | {"keitaro_base_url": "https://tracker.example/prefix/?unused=1#fragment"}
        )
    )

    def respond(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=[])

    with TestClient(create_app(config, httpx.MockTransport(respond))) as client:
        assert client.get("/api/campaigns").status_code == 200
    assert urls == ["https://tracker.example/prefix/admin_api/v1/campaigns"]


def test_create_and_draft_require_integer_json_inputs(client, fake):
    data = {
        "name": "test_demo_strict",
        "country": "CA",
        "offer_id": 11,
        "domain_id": True,
        "group_id": 2,
        "traffic_source_id": 3,
    }
    assert client.post("/api/campaigns", json=data).status_code == 422
    assert not fake.calls

    state = client.post("/api/campaigns/7/sync").json()[1]
    path = f"/api/streams/{state['id']}"
    assert (
        client.patch(
            f"{path}/draft", json={"action": "add", "offer_id": "14", "version": state["version"]}
        ).status_code
        == 422
    )
    assert client.post(f"{path}/publish", json={"version": True}).status_code == 422


def test_offer_search_excludes_attached_offers_before_limiting_results(client, fake):
    fake.offers.extend(
        {"id": offer_id, "name": f"Campaign demo {offer_id:02}", "state": "active"}
        for offer_id in range(21, 34)
    )
    fake.streams[102]["offers"] = [link(offer_id, 8) for offer_id in range(21, 33)]
    assert client.post("/api/campaigns/7/sync").status_code == 200

    result = client.get("/api/offers", params={"q": "Campaign demo", "stream_id": 102})
    assert result.status_code == 200
    assert result.json() == [{"id": 33, "name": "Campaign demo 33"}]
    assert len(client.get("/api/offers", params={"q": "Campaign demo"}).json()) == 12


@pytest.mark.parametrize("value", [2**53, 2**63])
def test_api_rejects_ids_outside_browser_safe_integer_range(client, value):
    assert client.get(f"/api/campaigns/{value}/streams").status_code == 422
    assert client.get("/api/offers", params={"q": "Atlas", "stream_id": value}).status_code == 422


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
def test_enable_can_retry_when_connection_fails_before_sending(settings, fake, failure):
    connection_ready = False

    def transport(request):
        if request.url.path.endswith("/enable") and not connection_ready:
            raise failure("Request never reached Keitaro", request=request)
        return fake(request)

    payload = {
        "name": "test_demo_enable_retry",
        "country": "CA",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 2,
        "traffic_source_id": 3,
    }
    with TestClient(create_app(settings, httpx.MockTransport(transport))) as client:
        assert client.post("/api/campaigns", json=payload).status_code == 502
        assert not any(path.endswith("/enable") for _, path, _ in fake.calls)

        connection_ready = True
        result = client.post("/api/campaigns", json=payload)
        assert result.status_code == 201, result.text
        assert fake.campaigns[result.json()["id"]]["state"] == "active"
        assert sum(path.endswith("/enable") for _, path, _ in fake.calls) == 1


@pytest.mark.parametrize(
    "problem",
    [
        "wrong_campaign",
        "duplicate_stream",
        "duplicate_offer",
        "missing_offers",
        "missing_link_state",
    ],
)
def test_invalid_stream_list_leaves_existing_snapshot_untouched(settings, fake, problem):
    invalid_response = None

    def transport(request):
        if invalid_response is not None and request.url.path.endswith("/campaigns/7/streams"):
            return httpx.Response(200, json=invalid_response)
        return fake(request)

    with TestClient(create_app(settings, httpx.MockTransport(transport))) as client:
        snapshot = client.post("/api/campaigns/7/sync").json()
        stream = fake.streams[102]
        if problem == "wrong_campaign":
            invalid_response = [{**stream, "campaign_id": 8, "offers": []}]
        elif problem == "duplicate_stream":
            invalid_response = [stream, {**stream, "offers": []}]
        elif problem == "duplicate_offer":
            invalid_response = [{**stream, "offers": [link(11, 40), link(11, 60)]}]
        elif problem == "missing_link_state":
            invalid_response = [{**stream, "offers": [{"offer_id": 11, "share": 100}, link(12, 0)]}]
        else:
            invalid_response = [{key: value for key, value in stream.items() if key != "offers"}]

        assert client.post("/api/campaigns/7/sync").status_code == 502
        assert client.get("/api/campaigns/7/streams").json() == snapshot


def test_add_rejects_an_offer_response_with_a_different_id(settings, fake):
    def transport(request):
        if request.url.path.endswith("/offers/14"):
            return httpx.Response(200, json={"id": 15, "name": "Daylight", "state": "active"})
        return fake(request)

    with TestClient(create_app(settings, httpx.MockTransport(transport))) as client:
        snapshot = client.post("/api/campaigns/7/sync").json()
        response = client.patch(
            "/api/streams/102/draft",
            json={"action": "add", "offer_id": 14, "version": snapshot[1]["version"]},
        )
        assert response.status_code == 502
        assert client.get("/api/campaigns/7/streams").json() == snapshot


def test_publish_rejects_preflight_response_for_a_different_stream(settings, fake):
    def transport(request):
        if request.method == "GET" and request.url.path.endswith("/streams/102"):
            return httpx.Response(200, json={**fake.streams[102], "id": 103})
        return fake(request)

    with TestClient(create_app(settings, httpx.MockTransport(transport))) as client:
        snapshot = client.post("/api/campaigns/7/sync").json()
        draft = client.patch(
            "/api/streams/102/draft",
            json={"action": "add", "offer_id": 14, "version": snapshot[1]["version"]},
        ).json()
        response = client.post("/api/streams/102/publish", json={"version": draft["version"]})
        assert response.status_code == 502
        assert client.get("/api/campaigns/7/streams").json()[1] == draft
        assert not any(method == "PUT" for method, _, _ in fake.calls)


def test_completed_creation_rejects_response_for_a_different_campaign(settings, fake):
    requested_id = None

    def transport(request):
        if requested_id is not None and request.url.path.endswith(f"/campaigns/{requested_id}"):
            return httpx.Response(200, json={**fake.campaigns[requested_id], "id": 99})
        return fake(request)

    payload = {
        "name": "test_demo_campaign_id",
        "country": "CA",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 2,
        "traffic_source_id": 3,
    }
    with TestClient(create_app(settings, httpx.MockTransport(transport))) as client:
        created = client.post("/api/campaigns", json=payload)
        assert created.status_code == 201
        requested_id = created.json()["id"]
        assert client.post("/api/campaigns", json=payload).status_code == 502
