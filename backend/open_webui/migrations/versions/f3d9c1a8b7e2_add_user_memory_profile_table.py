"""Add user memory profile table

Revision ID: f3d9c1a8b7e2
Revises: 018012973d35
Create Date: 2026-02-10 03:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "f3d9c1a8b7e2"
down_revision = "018012973d35"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_memory_profile",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("memory_summary", sa.JSON(), nullable=True),
        sa.Column("pending_manual_memory", sa.JSON(), nullable=True),
        sa.Column("last_processed_chat_updated_at", sa.BigInteger(), nullable=True),
        sa.Column("last_run_at", sa.BigInteger(), nullable=True),
        sa.Column("next_run_at", sa.BigInteger(), nullable=True),
        sa.Column("frequency", sa.String(), nullable=False, server_default="daily"),
        sa.Column("time_of_day", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("recent_days", sa.Integer(), nullable=True),
        sa.Column("first_n_messages", sa.Integer(), nullable=True),
        sa.Column("last_n_messages", sa.Integer(), nullable=True),
        sa.Column("user_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade():
    op.drop_table("user_memory_profile")
