"""In-process de-duplication of Twilio's MessageSid. Twilio retries a
webhook delivery whenever it doesn't get a prompt 2xx (slow LLM/MCP calls,
transient errors), and without this the same inbound message would be run
through the full pipeline — and replied to — twice.

Process-local and in-memory by design: this gateway runs as a single
uvicorn worker today (see README/RUNBOOK), so a plain TTL-bounded dict is
the smallest correct mechanism. A multi-worker or multi-instance deployment
would need a shared store (e.g. Redis) instead."""

import time

_TTL_SECONDS = 600
_seen: dict[str, float] = {}


def seen_before(message_sid: str) -> bool:
    _sweep()
    if message_sid in _seen:
        return True
    _seen[message_sid] = time.time()
    return False


def _sweep() -> None:
    cutoff = time.time() - _TTL_SECONDS
    for sid in [s for s, seen_at in _seen.items() if seen_at < cutoff]:
        del _seen[sid]
