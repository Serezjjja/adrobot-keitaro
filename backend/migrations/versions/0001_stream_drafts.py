"""Store the published offer snapshot and the pending draft for each stream."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("editable", sa.Boolean(), nullable=False),
        sa.Column("missing", sa.Boolean(), nullable=False),
        sa.Column("conflict", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published", sa.JSON(), nullable=False),
        sa.Column("draft", sa.JSON(none_as_null=True), nullable=True),
    )
    op.create_index("ix_stream_drafts_campaign_id", "stream_drafts", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_stream_drafts_campaign_id", table_name="stream_drafts")
    op.drop_table("stream_drafts")
