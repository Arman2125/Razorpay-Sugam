"""
Dedup guard for Meta's at-least-once WhatsApp webhook delivery — see
app/models.py's WhatsAppProcessedMessage docstring for why this exists.

mark_seen() atomically records a message_id the first time it's seen and
returns True; if that exact message_id has already been recorded, the
INSERT is a no-op (ON CONFLICT DO NOTHING) and this returns False, so the
caller can skip reprocessing a redelivered event without a race between two
near-simultaneous duplicate deliveries both thinking they're first.
"""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WhatsAppProcessedMessage


async def mark_seen(session: AsyncSession, message_id: str) -> bool:
    stmt = (
        pg_insert(WhatsAppProcessedMessage)
        .values(message_id=message_id)
        .on_conflict_do_nothing(index_elements=["message_id"])
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0
