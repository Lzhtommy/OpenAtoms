"""Domain model (ARCHITECTURE §4, CONTRACT §1). Portable across SQLite (lite) and Postgres (full)."""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BigId = BigInteger().with_variant(Integer, "sqlite")
Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hex32() -> str:
    return _uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------- accounts
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    plan: Mapped[str] = mapped_column(String(20), default="free")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    current_workspace_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke refresh tokens


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan: Mapped[str] = mapped_column(String(20), default="free")
    credits: Mapped[int] = mapped_column(BigId, default=0)  # cached balance; source of truth = credit_ledger
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class WorkspaceMember(TimestampMixin, Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner|admin|member|viewer


class Invitation(TimestampMixin, Base):
    __tablename__ = "invitations"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20), default="member")
    invited_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthToken(TimestampMixin, Base):
    """One-time tokens: password reset, email verification, preview tickets, oauth state."""
    __tablename__ = "auth_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # pwd_reset|verify_email|preview_ticket|oauth_state
    user_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- projects
class Folder(TimestampMixin, Base):
    __tablename__ = "folders"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))


class Chat(TimestampMixin, Base):
    __tablename__ = "chats"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=hex32)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200), default="New Project")
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready|forking|failed
    access_mode: Mapped[str] = mapped_column(String(20), default="private")  # private|secret|public
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    llm: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    runtime_status: Mapped[str] = mapped_column(String(20), default="stopped")
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)  # agent run in progress
    current_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fork_from: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"), nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(BigId, default=0)  # last event seq (lite mode counter persistence)
    timeline_index: Mapped[int] = mapped_column(Integer, default=0)  # last block_timelines.index
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # BoN/boost candidate projects are hidden children of the project the user works in (Atoms `bon.parent_chat_id`)
    parent_chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class ChatLike(TimestampMixin, Base):
    __tablename__ = "chat_likes"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), default=hex32, unique=True, index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80), default="")
    type: Mapped[str] = mapped_column(String(40), default="message")
    content: Mapped[Any] = mapped_column(Json, default=list)
    refer_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    round_id: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    prev_round_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prev_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_finished: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", Json, default=dict)
    user_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    __table_args__ = (Index("ix_messages_chat_round", "chat_id", "round_id", "attempt"),)


class ActionData(TimestampMixin, Base):
    __tablename__ = "action_datas"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), default=hex32, unique=True, index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    message_uuid: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    command_name: Mapped[str] = mapped_column(String(80))
    block_type: Mapped[str] = mapped_column(String(40))
    file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    src_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="running")
    timeline_index: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BlockTimeline(TimestampMixin, Base):
    __tablename__ = "block_timelines"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String(40))
    block_owner: Mapped[str] = mapped_column(String(40), default="")
    command_name: Mapped[str] = mapped_column(String(80), default="")
    file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    src_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")  # final content (small blocks) / truncated
    is_running: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    has_secret: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("chat_id", "index", name="uq_timeline_chat_index"),)


class Version(TimestampMixin, Base):
    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(20))  # v1, v2...
    number: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    cover: Mapped[str | None] = mapped_column(String(500), nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="building")  # building|ready|failed
    snapshot_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", Json, default=dict)

    __table_args__ = (UniqueConstraint("chat_id", "version", name="uq_version_chat"),)


class RoundState(TimestampMixin, Base):
    __tablename__ = "round_states"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    active_attempts: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)  # {"<round_id>": attempt}


class PromptQueueItem(TimestampMixin, Base):
    __tablename__ = "prompt_queue"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[Any] = mapped_column(Json, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", Json, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|sent
    user_id: Mapped[int | None] = mapped_column(BigId, nullable=True)


class QueueState(Base):
    __tablename__ = "prompt_queue_state"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(TimestampMixin, Base):
    """One attempt of one round. state holds serialized orchestrator state (plan, cursors, suspension)."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    round_id: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    trigger_message_id: Mapped[int] = mapped_column(BigId)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # queued|running|suspended|success|failed|cancelled
    suspend_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ask_human|plan_ready|questions
    suspend_message_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    state: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    agent_mode: Mapped[str] = mapped_column(String(10), default="lite")
    model: Mapped[str] = mapped_column(String(80), default="oa-mock")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Deployment(TimestampMixin, Base):
    __tablename__ = "deployments"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[str] = mapped_column(String(20))
    seq: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(20), default="static")
    status: Mapped[str] = mapped_column(String(20), default="deploying")
    is_always_latest: Mapped[bool] = mapped_column(Boolean, default=False)
    access_mode: Mapped[str] = mapped_column(String(20), default="public")  # who may open the published site
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Domain(TimestampMixin, Base):
    __tablename__ = "domains"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    hostname: Mapped[str] = mapped_column(String(255), unique=True)
    verify_status: Mapped[str] = mapped_column(String(20), default="pending")
    ssl_status: Mapped[str] = mapped_column(String(20), default="pending")


class Secret(TimestampMixin, Base):
    __tablename__ = "secrets"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(20))  # chat|workspace
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    key: Mapped[str] = mapped_column(String(200))
    value_enc: Mapped[str] = mapped_column(Text)
    env: Mapped[str] = mapped_column(String(10), default="dev")


class Integration(TimestampMixin, Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(30))  # github|supabase
    account: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    tokens_enc: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("workspace_id", "provider", name="uq_integration_ws_provider"),)


class CreditLedger(TimestampMixin, Base):
    __tablename__ = "credit_ledger"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delta: Mapped[int] = mapped_column(BigId)
    reason: Mapped[str] = mapped_column(String(40))  # signup|subscription|purchase|llm|sandbox|refund|grant
    ref: Mapped[str | None] = mapped_column(String(200), nullable=True)


class UsageEvent(TimestampMixin, Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    role: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    credits: Mapped[int] = mapped_column(Integer, default=0)


class Sandbox(TimestampMixin, Base):
    __tablename__ = "sandboxes"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)  # container id / pid
    endpoint: Mapped[str | None] = mapped_column(String(200), nullable=True)  # sandboxd base url
    token: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="stopped")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ports: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)  # {"5173": "http://127.0.0.1:54321"}


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)  # stripe session id / mock id
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))  # plan|pack
    item: Mapped[str] = mapped_column(String(40))
    amount_usd: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|paid|failed


class AppUser(TimestampMixin, Base):
    """End-users of generated apps (Cloud BaaS auth)."""
    __tablename__ = "app_users"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    env: Mapped[str] = mapped_column(String(10), default="dev")
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    platform_user_id: Mapped[int | None] = mapped_column(BigId, nullable=True)

    __table_args__ = (UniqueConstraint("chat_id", "env", "email", name="uq_app_user"),)


class OutboxEmail(TimestampMixin, Base):
    __tablename__ = "outbox_emails"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    to: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)


class Boost(TimestampMixin, Base):
    """Best-of-N run: K candidate child chats built in parallel from the same prompt; the user merges one."""
    __tablename__ = "boosts"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[int | None] = mapped_column(BigId, nullable=True)  # the user prompt in the parent chat
    card_message_id: Mapped[int | None] = mapped_column(BigId, nullable=True)  # type=boost card in the parent chat
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ready|merging|merged|aborted|failed
    items: Mapped[list[Any]] = mapped_column(Json, default=list)  # [{index, model, chat_id, status, ...}]
    recommended: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_reason: Mapped[str] = mapped_column(Text, default="")


class Automation(TimestampMixin, Base):
    """Scheduled prompt: at each cron tick the prompt is posted to the chat as a user message (queued if busy)."""
    __tablename__ = "automations"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    cron: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # sent|queued|failed
    last_message_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
