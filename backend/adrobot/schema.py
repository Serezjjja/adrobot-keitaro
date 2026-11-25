from typing import Annotated, Any, Literal

import pycountry
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

# IDs travel as JSON numbers and must remain exact in the React client.
MAX_SAFE_ID = 2**53 - 1
PositiveId = Annotated[int, Field(gt=0, le=MAX_SAFE_ID)]
InputId = Annotated[PositiveId, Field(strict=True)]
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
    state: str
    type: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class FlowOffer(BaseModel):
    offer_id: PositiveId
    share: Share
    state: OfferState


class Flow(Resource):
    campaign_id: PositiveId
    position: int = 0
    type: str = "regular"
    routing: str = Field(default="", alias="schema")
    state: str = "active"
    action_type: str | None = None
    action_payload: str | dict[str, Any] | None = None
    collect_clicks: bool | None = None
    filter_or: bool = False
    filters: list[dict[str, Any]] = Field(default_factory=list)
    offers: list[FlowOffer] = Field(default_factory=list)
    landings: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def editable(self) -> bool:
        return self.routing == "landings"

    @model_validator(mode="after")
    def validate_offer_links(self) -> "Flow":
        if self.editable and self.state != "deleted" and "offers" not in self.model_fields_set:
            raise ValueError("Keitaro не вернул список офферов для потока.")
        if len({link.offer_id for link in self.offers}) != len(self.offers):
            raise ValueError("Keitaro вернул повторяющиеся офферы потока.")
        return self


class Catalogs(BaseModel):
    domains: list[Resource]
    groups: list[Resource]
    traffic_sources: list[Resource]


class CreateCampaign(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country: str
    offer_id: InputId
    domain_id: InputId
    group_id: InputId
    traffic_source_id: InputId

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
    version: InputId


class DraftChange(DraftVersion):
    action: Literal["add", "remove", "restore", "pin", "unpin", "set_share"]
    offer_id: InputId
    share: int | None = Field(default=None, strict=True, ge=0, le=100, validate_default=True)

    @field_validator("share", mode="before")
    @classmethod
    def require_share_for_set_share(cls, value: Any, info: ValidationInfo) -> Any:
        if info.data.get("action") != "set_share":
            return None
        if value is None:
            raise ValueError("Укажите целую долю от 0 до 100.")
        return value


class StreamView(Resource):
    position: int
    editable: bool
    dirty: bool
    conflict: bool
    missing: bool
    publication_pending: bool = False
    version: int
    issue: str | None
    offers: list[Offer]
    published_offers: list[Offer] | None = None
