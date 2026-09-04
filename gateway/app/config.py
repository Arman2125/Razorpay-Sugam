"""
Typed settings for the gateway, mirroring Sugam AI OS's config.py discipline:
one pydantic-settings field per env var, dedicated validators for URL-shaped
settings, and every optional/pilot flag stored as a raw string parsed by a
shared fail-closed helper (a malformed flag must never crash startup).
"""

import sys
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_GATEWAY_APP_DIR = Path(__file__).resolve().parent


def parse_bool_fail_closed(raw: str) -> bool:
    """Never raises. Unrecognized/empty input means False — the same
    'no value set -> safest behavior' convention Sugam AI OS uses for every
    pilot flag on its own Settings class."""
    return (raw or "").strip().lower() in {"true", "1", "yes", "on"}


def _validate_api_base_url(value: str, var_name: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{var_name} must not be empty.")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{var_name} must be an absolute http(s) URL, got: {value!r}")
    normalized = value.rstrip("/")
    if not normalized.endswith("/api"):
        raise ValueError(
            f"{var_name} must be the API origin ending in /api (e.g. 'http://localhost:5000/api'), "
            f"got: {value!r} — a bare host:port or a frontend URL (e.g. :5173) is not the API origin."
        )
    return normalized


class Settings(BaseSettings):
    mini_razorpay_base_url: str = "http://localhost:5000/api"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/razorpay_sugam"
    database_url_sync: str = "postgresql+psycopg://postgres:postgres@localhost:5432/razorpay_sugam"

    # OpenAI GPT is the sole reasoning / intent-understanding / tool-selection
    # engine (see intent_service.py and llm_fallback_formatter.py) — never
    # swapped for Gemini. Gemini's only role in this codebase is audio/video
    # understanding (see gemini_* below and
    # app/services/media_understanding_service.py); it never sees a plain
    # text message and never selects a tool.
    openai_api_key: str = ""
    openai_model: str = ""

    # Gemini — audio/video understanding only: turns a WhatsApp voice note
    # or video into a plain-text transcript/description that is then handed
    # to the exact same OpenAI-driven pipeline as typed text. See
    # app/services/media_understanding_service.py.
    gemini_api_key: str = ""
    gemini_model: str = ""

    llm_fallback_enabled_raw: str = Field(default="false", validation_alias="LLM_FALLBACK_ENABLED_RAW")

    whatsapp_enabled_raw: str = Field(default="false", validation_alias="WHATSAPP_ENABLED_RAW")
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_graph_api_version: str = "v21.0"

    # /test/message exercises the full pipeline (identity resolution, the
    # real LLM call, and real MCP/Mini-Razorpay tool calls) with no
    # authentication of its own — safe for local development, but it must
    # not exist in production unless explicitly turned on. Same fail-closed
    # convention as every other optional flag above: unset/malformed means
    # disabled.
    enable_test_endpoint_raw: str = Field(default="false", validation_alias="ENABLE_TEST_ENDPOINT_RAW")

    mcp_server_path: str = str(
        (_GATEWAY_APP_DIR / ".." / ".." / "mcp-servers" / "mini-razorpay-mcp" / "server.py").resolve()
    )
    mcp_server_python_path: str = sys.executable
    mcp_call_timeout_seconds: int = 45

    # How often to poll Mini-Razorpay (via the existing get_payment_status
    # tool) for payments a reminder auto-notified a customer about, to
    # detect when they've paid and confirm it back to the merchant on
    # WhatsApp. Mini-Razorpay has no webhook out to this gateway, so
    # polling is the only way to close this loop without modifying it —
    # see app/services/payment_recovery_notifier.py.
    payment_recovery_poll_interval_seconds: int = 20

    merchant_directory_sync_interval_seconds: int = 300
    jwt_cache_safety_margin_seconds: int = 300
    conversation_state_ttl_seconds: int = 600
    conversation_state_max_attempts: int = 3

    # Generic multi-turn conversation memory (app/services/conversation_history_service.py)
    # — the actual OpenAI-format transcript per WhatsApp number, replayed to
    # the LLM on every message so it can reason over what it already asked/
    # was told, rather than seeing each message in isolation. Bounded on both
    # axes (time and turn count) so context never grows unbounded; tool
    # results are additionally truncated before being stored so one large
    # list response doesn't balloon every subsequent turn's token cost.
    conversation_history_ttl_seconds: int = 1800
    conversation_history_max_turns: int = 20
    conversation_history_max_tool_result_chars: int = 2000

    log_level: str = "INFO"
    python_env: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("mini_razorpay_base_url")
    @classmethod
    def _check_mini_razorpay_base_url(cls, v: str) -> str:
        return _validate_api_base_url(v, "MINI_RAZORPAY_BASE_URL")

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(f"DATABASE_URL must use the asyncpg driver (postgresql+asyncpg://...), got: {v!r}")
        return v

    @field_validator("database_url_sync")
    @classmethod
    def _check_database_url_sync(cls, v: str) -> str:
        if not v.startswith("postgresql+psycopg://") and not v.startswith("postgresql://"):
            raise ValueError(f"DATABASE_URL_SYNC must use the psycopg driver, got: {v!r}")
        return v

    @property
    def llm_fallback_enabled(self) -> bool:
        return parse_bool_fail_closed(self.llm_fallback_enabled_raw)

    @property
    def whatsapp_enabled(self) -> bool:
        return parse_bool_fail_closed(self.whatsapp_enabled_raw)

    @property
    def enable_test_endpoint(self) -> bool:
        return parse_bool_fail_closed(self.enable_test_endpoint_raw)


settings = Settings()
