from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StreamDraft(Base):
    __tablename__ = "stream_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    campaign_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer)
    editable: Mapped[bool] = mapped_column(Boolean)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    published: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    draft: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON(none_as_null=True))

    __table_args__ = (Index("ix_stream_drafts_campaign_id", "campaign_id"),)
