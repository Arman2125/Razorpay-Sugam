import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import AsyncSessionLocal
from app.mcp.mini_razorpay_mcp_client import close_persistent_mcp_session
from app.routes import health, test_message, whatsapp_webhook
from app.services import directory_sync_service, payment_recovery_notifier

logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Sync once, synchronously, before accepting traffic — a fresh DB
    # shouldn't be empty for the first real message.
    try:
        async with AsyncSessionLocal() as session:
            await directory_sync_service.sync_once(session)
    except Exception:
        logger.exception("Initial merchant directory sync failed — will retry on the periodic loop")

    sync_task = asyncio.create_task(
        directory_sync_service.run_periodic(AsyncSessionLocal, settings.merchant_directory_sync_interval_seconds)
    )
    recovery_poll_task = asyncio.create_task(
        payment_recovery_notifier.run_periodic(AsyncSessionLocal, settings.payment_recovery_poll_interval_seconds)
    )

    try:
        yield
    finally:
        sync_task.cancel()
        recovery_poll_task.cancel()
        for task in (sync_task, recovery_poll_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await close_persistent_mcp_session()


app = FastAPI(title="Razorpay Sugam Gateway", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
if settings.enable_test_endpoint:
    app.include_router(test_message.router)
app.include_router(whatsapp_webhook.router)
