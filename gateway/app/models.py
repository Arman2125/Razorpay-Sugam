import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    """Every column below is DateTime(timezone=True) — asyncpg always
    returns a timezone-aware datetime for those on read, so the Python-side
    default/onupdate must produce an aware one too. A naive datetime.utcnow()
    here would be inconsistent with what a later SELECT returns, breaking
    any comparison against it (see merchant_auth_service.py and
    conversation_state_service.py, which do exactly that)."""
    return datetime.now(timezone.utc)


class MerchantDirectoryEntry(Base):
    """
    Local mirror of Mini-Razorpay's GET /auth/demo-merchants — the same
    "sync the external app's own phone-number directory into a local table,
    match inbound numbers against it, never hit the live API on the hot
    path" pattern Sugam AI OS uses for Playground (app_directory_entries).

    phone_number_raw is stored EXACTLY as Mini-Razorpay returned it and is
    the only value ever sent back to POST /auth/login — that endpoint does
    an exact string match on Merchant.phoneNumber, never normalized.
    phone_number_normalized (digits only, no +/91 prefix) is used solely to
    match inbound WhatsApp numbers, which arrive in an unpredictable format.
    """

    __tablename__ = "merchant_directory_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(Text, unique=True, index=True, nullable=False)
    business_name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_name: Mapped[str] = mapped_column(Text, nullable=True)
    phone_number_raw: Mapped[str] = mapped_column(Text, nullable=False)
    phone_number_normalized: Mapped[str] = mapped_column(Text, unique=True, index=True, nullable=False)
    business_type: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active | inactive
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class MerchantJwtCache(Base):
    """
    DB-backed (not purely in-memory) cache of each merchant's own Mini-Razorpay
    JWT, obtained via POST /auth/login. DB-backed so multiple gateway worker
    processes (e.g. under PM2) share one view of "is there already a valid
    token" instead of each re-logging in independently — logins are cheap and
    infrequent (7-day expiry) so this round trip costs nothing on the hot path,
    which still checks an in-process dict first (see merchant_auth_service.py).

    This is the one piece with no direct Sugam AI OS precedent: Playground
    uses a single shared service credential for every caller, but Mini-Razorpay
    has no such credential by design — each merchant's own JWT is the entire
    cross-merchant data-isolation boundary.
    """

    __tablename__ = "merchant_jwt_cache"

    merchant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    jwt_token: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ConversationState(Base):
    """
    Generic pending-clarification table — one active row per
    (whatsapp_number, capability), mutate-in-place, TTL-expired (never
    deleted, only status-flipped). Mirrors Sugam AI OS's own
    GenericActionStateRow design (built there but never wired into
    production) rather than one bespoke table per capability.

    payload holds: {"tool_name", "original_arguments", "candidates", "attempts"}.
    """

    __tablename__ = "conversation_states"
    __table_args__ = (
        UniqueConstraint(
            "whatsapp_number", "capability", "status", name="uq_conversation_states_active_per_capability"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    whatsapp_number: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)  # the MCP tool name awaiting clarification
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active|resolved|expired|cancelled
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentNotificationWatch(Base):
    """
    Tracks a pending payment whose reminder also messaged the customer
    directly on WhatsApp with a real payment link. Mini-Razorpay has no
    webhook back into this gateway, so this table — not a push notification
    — is what lets the gateway later notice (by polling the existing
    get_payment_status tool; see app/services/payment_recovery_notifier.py)
    that the customer has paid, and confirm that back to the merchant on
    the same channel/number the original reminder went out on.

    One active ("watching") row per (merchant_id, payment_id) — a second
    reminder for the same still-pending payment reuses the existing watch
    rather than creating a duplicate.
    """

    __tablename__ = "payment_notification_watches"
    __table_args__ = (
        UniqueConstraint("merchant_id", "payment_id", "status", name="uq_payment_watch_active_per_payment"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    payment_id: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(Text, nullable=False)
    customer_name: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # whatsapp | twilio | test
    merchant_whatsapp_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="watching")  # watching | notified
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class GatewayActivityLog(Base):
    """Our own audit trail — distinct from Mini-Razorpay's own Activity
    collection, which we never write to directly."""

    __tablename__ = "gateway_activity_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, nullable=False, index=True
    )
    whatsapp_number: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    merchant_id: Mapped[str] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # whatsapp | twilio | test
    incoming_message: Mapped[str] = mapped_column(Text, nullable=True)
    resolved_intent: Mapped[dict] = mapped_column(JSONB, nullable=True)
    tool_name: Mapped[str] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)  # success|ambiguous|duplicate|error|declined
    error_code: Mapped[str] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
