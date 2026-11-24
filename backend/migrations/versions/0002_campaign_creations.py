"""Remember creation attempts and whether activation has started."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_creations",
        sa.Column("alias", sa.String(), primary_key=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("campaign_creations")
