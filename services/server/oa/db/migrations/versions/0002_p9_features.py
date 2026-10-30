"""P9 features: Best-of-N boosts, automations, chats.parent_chat_id

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
Json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.add_column("chats", sa.Column("parent_chat_id", sa.String(32), nullable=True))
    op.create_index("ix_chats_parent_chat_id", "chats", ["parent_chat_id"])

    op.create_table(
        "boosts",
        sa.Column("id", BigId, primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.String(32), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", BigId, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("message_id", BigId, nullable=True),
        sa.Column("card_message_id", BigId, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("items", Json, nullable=False),
        sa.Column("recommended", sa.Integer, nullable=True),
        sa.Column("selected", sa.Integer, nullable=True),
        sa.Column("judge_reason", sa.Text, nullable=False),
        *_ts(),
    )
    op.create_index("ix_boosts_chat_id", "boosts", ["chat_id"])
    op.create_index("ix_boosts_created_at", "boosts", ["created_at"])

    op.create_table(
        "automations",
        sa.Column("id", BigId, primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.String(32), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", BigId, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", BigId, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("cron", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_message_id", BigId, nullable=True),
        sa.Column("run_count", sa.Integer, nullable=False),
        *_ts(),
    )
    op.create_index("ix_automations_chat_id", "automations", ["chat_id"])
    op.create_index("ix_automations_workspace_id", "automations", ["workspace_id"])
    op.create_index("ix_automations_next_run_at", "automations", ["next_run_at"])
    op.create_index("ix_automations_created_at", "automations", ["created_at"])


def downgrade() -> None:
    op.drop_table("automations")
    op.drop_table("boosts")
    op.drop_index("ix_chats_parent_chat_id", "chats")
    op.drop_column("chats", "parent_chat_id")
