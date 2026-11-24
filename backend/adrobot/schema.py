from typing import Annotated, Any, Literal

import pycountry
from pydantic import BaseModel, Field, field_validator

PositiveId = Annotated[int, Field(gt=0)]
Share = Annotated[int, Field(ge=0, le=100)]
OfferState = Literal["active", "disabled"]


class Resource(BaseModel):
    id: PositiveId
    name: str


class TrackerResource(Resource):
    state: str | None = None

    @property
    def available(self) -> bool:
        return self.state in {None, "active"}


class Campaign(Resource):
    alias: str | None = None
    state: str = "active"
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class FlowOffer(BaseModel):
    offer_id: PositiveId
    share: Share
    state: OfferState = "active"


class Flow(Resource):
    campaign_id: PositiveId
    position: int = 0
    type: str = "regular"
    routing: str = Field(default="", alias="schema")
    state: str = "active"
    action_type: str | None = None
    action_payload: str | dict[str, Any] | None = None
    filter_or: bool = False
    filters: list[dict[str, Any]] = Field(default_factory=list)
    offers: list[FlowOffer] = Field(default_factory=list)
    landings: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def editable(self) -> bool:
        return self.routing == "landings"


class Catalogs(BaseModel):
    domains: list[Resource]
    groups: list[Resource]
    traffic_sources: list[Resource]


class CreateCampaign(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country: str
    offer_id: PositiveId
    domain_id: PositiveId
    group_id: PositiveId
    traffic_source_id: PositiveId

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        country = value.strip().upper()
        if pycountry.countries.get(alpha_2=country) is None:
            raise ValueError("Укажите двухбуквенный код страны ISO 3166-1.")
        return country


class Offer(Resource):
    share: Share
    pinned: bool = False
    removed: bool = False
    enabled: bool = True


class DraftVersion(BaseModel):
    version: int = Field(ge=1)


class DraftChange(DraftVersion):
    action: Literal["add", "remove", "restore", "pin", "unpin"]
    offer_id: PositiveId


class StreamView(Resource):
    position: int
    editable: bool
    dirty: bool
    conflict: bool
    missing: bool
    version: int
    issue: str | None
    offers: list[Offer]
