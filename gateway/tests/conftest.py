import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.twilio import dedup as twilio_dedup


@pytest.fixture(autouse=True)
def _reset_twilio_dedup_cache():
    """The MessageSid dedup cache is a module-level dict — clear it between
    tests so one test's seen SIDs can't leak into another's."""
    twilio_dedup._seen.clear()
    yield
    twilio_dedup._seen.clear()
