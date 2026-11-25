"""Persist remote creation aliases and writes awaiting confirmation."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign_creations", sa.Column("remote_alias", sa.String(), nullable=True))
    op.add_column(
        "stream_drafts",
        sa.Column("publication_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("stream_drafts", "publication_pending")
    op.drop_column("campaign_creations", "remote_alias")
