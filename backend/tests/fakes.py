import json
from copy import deepcopy

import httpx


def link(offer_id: int, share: int) -> dict:
    return {"offer_id": offer_id, "share": share, "state": "active"}


class FakeKeitaro:
    def __init__(self):
        self.catalogs = {
            "domains": [{"id": 1, "name": "campaigns.example", "state": "active"}],
            "groups": [{"id": 2, "name": "Тестовые кампании"}],
            "traffic_sources": [{"id": 3, "name": "Google Ads"}],
        }
        self.offers = [
            {"id": 11, "name": "Northstar", "state": "active"},
            {"id": 12, "name": "Atlas", "state": "active"},
            {"id": 13, "name": "Meridian", "state": "active"},
            {"id": 14, "name": "Horizon", "state": "active"},
            {"id": 15, "name": "Daylight", "state": "active"},
            {"id": 16, "name": "Inactive", "state": "disabled"},
        ]
        self.campaigns = {
            7: {
                "id": 7,
                "name": "test_demo_summer",
                "alias": "demo-summer",
                "state": "active",
                "domain_id": 1,
                "group_id": 2,
                "traffic_source_id": 3,
            }
        }
        self.streams = {
            101: {
                "id": 101,
                "name": "CA → Google",
                "campaign_id": 7,
                "position": 1,
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": "https://www.google.com/",
                "filters": [{"name": "country", "mode": "accept", "payload": ["CA"]}],
            },
            102: {
                "id": 102,
                "name": "Остальные → офферы",
                "campaign_id": 7,
                "position": 2,
                "type": "default",
                "schema": "landings",
                "action_type": "http",
                "offers": [link(11, 25), link(12, 38), link(13, 37)],
                "landings": [{"landing_id": 42, "share": 100}],
            },
        }
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_stream_position: int | None = None
        self.timeout_after_put = False
        self.timeout_after_campaign = False
        self.timeout_during_enable = False
        self.delete_after_put = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Api-Key") == "fake-key"
        path = request.url.path.removeprefix("/admin_api/v1/")
        method = request.method
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, deepcopy(payload)))

        if method == "GET":
            return self._get(request, path)
        if method == "POST" and path == "campaigns":
            campaign_id = max(self.campaigns) + 1
            campaign = {**payload, "id": campaign_id}
            self.campaigns[campaign_id] = campaign
            if self.timeout_after_campaign:
                raise httpx.ReadTimeout("Response lost", request=request)
            return httpx.Response(200, json=campaign)
        if method == "POST" and path == "streams":
            if payload["position"] == self.fail_stream_position:
                return httpx.Response(503, json={"error": "temporarily unavailable"})
            stream_id = max(self.streams) + 1
            stream = {**payload, "id": stream_id}
            self.streams[stream_id] = stream
            return httpx.Response(200, json=stream)
        if method == "POST" and path.endswith("/enable"):
            campaign = self.campaigns[int(path.split("/")[1])]
            if self.timeout_during_enable:
                raise httpx.ReadTimeout("Activation not confirmed", request=request)
            campaign["state"] = "active"
            return httpx.Response(200, json=campaign)
        if method == "PUT" and path.startswith("streams/"):
            assert set(payload) == {"offers"}
            stream = self.streams[int(path.split("/")[1])]
            stream["offers"] = payload["offers"]
            if self.delete_after_put:
                stream["state"] = "deleted"
            if self.timeout_after_put:
                raise httpx.ReadTimeout("Response lost", request=request)
            return httpx.Response(200, json=stream)
        raise AssertionError(f"Unexpected request: {method} {path}")

    def _get(self, request: httpx.Request, path: str) -> httpx.Response:
        if path in self.catalogs:
            if path == "groups":
                assert request.url.params["type"] == "campaigns"
            return httpx.Response(200, json=self.catalogs[path])
        if path == "offers":
            return httpx.Response(200, json=self.offers)
        if path == "campaigns":
            return httpx.Response(200, json=list(self.campaigns.values()))

        collection, object_id, *extra = path.split("/")
        object_id = int(object_id)
        if collection == "campaigns":
            value = self.campaigns.get(object_id)
            if extra == ["streams"] and value:
                value = [flow for flow in self.streams.values() if flow["campaign_id"] == object_id]
        elif collection == "streams":
            value = self.streams.get(object_id)
        elif collection == "offers":
            value = next((offer for offer in self.offers if offer["id"] == object_id), None)
        else:
            raise AssertionError(f"Unexpected GET: {path}")
        if value is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=value)
