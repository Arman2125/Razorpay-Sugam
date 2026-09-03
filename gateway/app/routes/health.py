from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "channels": {
            "meta_whatsapp_configured": bool(
                settings.whatsapp_enabled and settings.whatsapp_access_token and settings.whatsapp_phone_number_id
            ),
            "twilio_whatsapp_configured": bool(
                settings.twilio_enabled
                and settings.twilio_account_sid
                and settings.twilio_auth_token
                and settings.twilio_whatsapp_number
            ),
        },
    }
