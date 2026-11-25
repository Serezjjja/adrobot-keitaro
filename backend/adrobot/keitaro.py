from collections.abc import Callable
from typing import Any

import httpx
from pydantic import TypeAdapter

from adrobot.errors import AppError, KeitaroError
from adrobot.schema import Campaign, Catalogs, Flow, FlowOffer, Resource, TrackerResource

CAMPAIGNS = TypeAdapter(list[Campaign])
FLOWS = TypeAdapter(list[Flow])
RESOURCES = TypeAdapter(list[TrackerResource])


class Keitaro:
    def __init__(self, http: httpx.Client):
        self.http = http

    def _request[T](
        self,
        method: str,
        path: str,
        parse: Callable[[Any], T],
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> T:
        if not self.http.headers.get("Api-Key"):
            raise KeitaroError("Укажите KEITARO_API_KEY в настройках сервера.", upstream_status=401)

        write = method != "GET"
        try:
            response = self.http.request(method, path, json=payload, params=params)
        except httpx.RequestError as exc:
            before_send = isinstance(
                exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
            )
            raise KeitaroError(
                "Keitaro не ответил. Проверьте соединение и повторите запрос.",
                uncertain=write and not before_send,
            ) from exc

        if response.is_error or response.is_redirect:
            code = response.status_code
            message = f"Keitaro отклонил запрос (HTTP {code})."
            if code in {401, 403}:
                message = "Keitaro не принял ключ API или у ключа недостаточно прав."
            raise KeitaroError(message, upstream_status=code, uncertain=write and code >= 500)

        try:
            return parse(response.json())
        except ValueError as exc:
            raise KeitaroError(
                "Keitaro вернул данные в неожиданном формате.", uncertain=write
            ) from exc

    def campaigns(self) -> list[Campaign]:
        return self._request("GET", "campaigns", CAMPAIGNS.validate_python)

    def _get_resource[T: Resource](
        self, collection: str, resource_id: int, parse: Callable[[Any], T]
    ) -> T:
        resource = self._request("GET", f"{collection}/{resource_id}", parse)
        if resource.id != resource_id:
            raise KeitaroError(f"Keitaro вернул объект с другим ID вместо #{resource_id}.")
        return resource

    def create_campaign(self, payload: dict[str, Any]) -> Campaign:
        return self._request("POST", "campaigns", Campaign.model_validate, payload=payload)

    def enable_campaign(self, campaign_id: int) -> None:
        self._request("POST", f"campaigns/{campaign_id}/enable", lambda value: value)

    def campaign(self, campaign_id: int) -> Campaign:
        return self._get_resource("campaigns", campaign_id, Campaign.model_validate)

    def catalogs(self) -> Catalogs:
        return Catalogs(
            domains=self._catalog("domains"),
            groups=self._catalog("groups", {"type": "campaigns"}),
            traffic_sources=self._catalog("traffic_sources"),
        )

    def _catalog(self, path: str, params: dict[str, Any] | None = None) -> list[Resource]:
        return [
            Resource(id=item.id, name=item.name)
            for item in self._request("GET", path, RESOURCES.validate_python, params=params)
            if item.available
        ]

    def offers(self) -> list[TrackerResource]:
        return self._request("GET", "offers", RESOURCES.validate_python)

    def active_offer_ids(self, offer_ids: set[int]) -> set[int]:
        if not offer_ids:
            return set()
        states: dict[int, set[str | None]] = {}
        for item in self.offers():
            if item.id in offer_ids:
                states.setdefault(item.id, set()).add(item.state)

        active = set()
        for offer_id, observed in states.items():
            # Явный запрет важнее активного дубля или неполной записи каталога.
            if observed - {None, "active"}:
                continue
            if observed == {"active"}:
                active.add(offer_id)
                continue
            try:
                item = self._get_resource("offers", offer_id, TrackerResource.model_validate)
            except KeitaroError as exc:
                if exc.upstream_status == 404:
                    continue
                raise
            if item.state == "active":
                active.add(offer_id)
        return active

    def active_offer(self, offer_id: int) -> Resource:
        try:
            item = self._get_resource("offers", offer_id, TrackerResource.model_validate)
        except KeitaroError as exc:
            if exc.upstream_status == 404:
                raise AppError(422, f"Оффер #{offer_id} не найден в Keitaro.") from exc
            raise
        available = item.state == "active"
        if item.state is None:
            # Неполная карточка не подтверждает доступность без явного состояния в каталоге.
            states = {offer.state for offer in self.offers() if offer.id == offer_id}
            available = "active" in states and not states - {None, "active"}
        if not available:
            raise AppError(422, f"Оффер #{offer_id} недоступен в Keitaro.")
        return Resource(id=item.id, name=item.name)

    def streams(self, campaign_id: int) -> list[Flow]:
        flows = self._request("GET", f"campaigns/{campaign_id}/streams", FLOWS.validate_python)
        if any(flow.campaign_id != campaign_id for flow in flows):
            raise KeitaroError(f"Keitaro вернул потоки другой кампании вместо #{campaign_id}.")
        if len({flow.id for flow in flows}) != len(flows):
            raise KeitaroError(f"Keitaro вернул повторяющиеся потоки кампании #{campaign_id}.")
        return flows

    def stream(self, stream_id: int) -> Flow:
        return self._get_resource("streams", stream_id, Flow.model_validate)

    def create_stream(self, payload: dict[str, Any]) -> Flow:
        return self._request("POST", "streams", Flow.model_validate, payload=payload)

    def set_offers(self, stream_id: int, offers: list[FlowOffer]) -> None:
        self._request(
            "PUT",
            f"streams/{stream_id}",
            lambda value: value,
            payload={"offers": [offer.model_dump() for offer in offers]},
        )
