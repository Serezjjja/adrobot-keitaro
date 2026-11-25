"""Remember submitted stream positions before calling Keitaro."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_creations",
        sa.Column(
            "submitted_stream_positions", sa.JSON(), nullable=True, server_default=sa.text("'[]'")
        ),
    )
    # Older attempts with a confirmed campaign ID may already have sent stream requests.
    creations = sa.table(
        "campaign_creations", sa.column("campaign_id"), sa.column("submitted_stream_positions")
    )
    op.execute(
        creations.update()
        .where(creations.c.campaign_id.is_not(None))
        .values(submitted_stream_positions=sa.null())
    )


def downgrade() -> None:
    op.drop_column("campaign_creations", "submitted_stream_positions")
