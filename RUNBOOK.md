# Connecting real WhatsApp

The Meta WhatsApp Cloud API is the sole supported WhatsApp channel. It can
be enabled or left disabled; the webhook route mounts regardless, and
outbound sends stay inert until it's explicitly turned on.

## Meta WhatsApp Cloud API

The webhook code (`gateway/app/routes/whatsapp_webhook.py`, `gateway/app/whatsapp/`) is complete and ready — it just needs real Meta Business API credentials to go live.

### Steps

1. Create a Meta App at [developers.facebook.com](https://developers.facebook.com) with the WhatsApp product added.
2. From the app dashboard, note:
   - **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - **Temporary/permanent access token** → `WHATSAPP_ACCESS_TOKEN`
   - **App secret** (App Settings → Basic) → `WHATSAPP_APP_SECRET`
3. Choose a random string as your verify token → `WHATSAPP_VERIFY_TOKEN` (used only during webhook setup below).
4. Set all four in `gateway/.env`, plus `WHATSAPP_ENABLED_RAW=true`.
5. Expose the gateway publicly (e.g. `ngrok http 8100` during development, or your real production domain).
6. In the Meta app dashboard's WhatsApp → Configuration:
   - Callback URL: `https://<your-public-url>/webhook/whatsapp`
   - Verify token: the same string you put in `WHATSAPP_VERIFY_TOKEN`
   - Subscribe to the `messages` webhook field.
7. Restart the gateway. Send a WhatsApp message to your test number from the merchant's registered phone (`+919876543210` or `+919876543211` for the demo merchants) — it should reach `process_user_message()` exactly the same way `/test/message` does.

### Verifying the webhook without live Meta credentials

`gateway/app/whatsapp/webhook_security.py`'s `verify_signature()` can be checked in isolation with a synthetic secret — see the snippet used during initial verification:

```python
import hmac, hashlib
from app.whatsapp.webhook_security import verify_signature

secret = "test_app_secret"
body = b'{"hello":"world"}'
sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
assert verify_signature(body, sig, secret) is True
```

### Rolling back

Set `WHATSAPP_ENABLED_RAW=false` and restart — outbound sends immediately stop attempting real Graph API calls (they just log what would have been sent), with zero code changes needed. The webhook route stays mounted and will still return the verify challenge / `200` valid POSTs (it just won't send a reply) if left exposed.
