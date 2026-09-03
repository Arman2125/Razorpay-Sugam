# Connecting real WhatsApp

Two independent WhatsApp channels exist — Meta WhatsApp Cloud API and Twilio
WhatsApp. Either, both, or neither can be enabled; they share the same
downstream pipeline but have entirely separate webhooks, signature schemes,
and outbound senders.

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

Set `WHATSAPP_ENABLED_RAW=false` and restart — outbound sends immediately stop attempting real Graph API calls (they just log what would have been sent), with zero code changes needed.

## Twilio WhatsApp

The webhook code (`gateway/app/routes/twilio_webhook.py`, `gateway/app/twilio/`) is complete and ready — it just needs a real Twilio account with a WhatsApp-enabled sender to go live. This channel is additive: enabling it does not affect the Meta channel above, and vice versa.

### Steps

1. Create/use a Twilio account at [twilio.com](https://www.twilio.com) and open the Console.
2. From the Console dashboard, note:
   - **Account SID** → `TWILIO_ACCOUNT_SID`
   - **Auth Token** → `TWILIO_AUTH_TOKEN`
3. Get a WhatsApp-enabled sender number — the Twilio Sandbox for WhatsApp for development, or a provisioned WhatsApp sender for production → `TWILIO_WHATSAPP_NUMBER` (plain E.164, e.g. `+14155238886`; the `whatsapp:` prefix is added by the code, not the `.env` value).
4. Set all three in `gateway/.env`, plus `TWILIO_ENABLED_RAW=true`.
5. Expose the gateway publicly (e.g. `ngrok http 8100` during development, or your real production domain).
6. In the Twilio Console (Messaging → Try it out → WhatsApp sandbox settings, or your production sender's configuration):
   - "When a message comes in" webhook URL: `https://<your-public-url>/webhook/twilio/whatsapp`
   - Method: `HTTP POST`
7. **If you're behind a reverse proxy that doesn't forward the original scheme/host** (a common issue with some ngrok setups, load balancers, etc.), Twilio's signature is computed over the exact public URL it called — if that won't match what `request.url` sees inside the app, set `TWILIO_WEBHOOK_URL_OVERRIDE` to the real public webhook URL (e.g. `https://<your-public-url>/webhook/twilio/whatsapp`) so signature validation checks against the right value instead. Leave it unset if there's no proxy/scheme mismatch.
8. Restart the gateway. Send a WhatsApp message to your Twilio sandbox/production number from the merchant's registered phone (`+919876543210` or `+919876543211` for the demo merchants) — it should reach `process_user_message()` exactly the same way `/test/message` and the Meta channel do.

### Verifying the webhook without live Twilio credentials

`gateway/app/twilio/webhook_security.py`'s `verify_signature()` can be checked in isolation with a synthetic auth token:

```python
from twilio.request_validator import RequestValidator
from app.twilio.webhook_security import verify_signature

token = "test_auth_token"
url = "https://example.com/webhook/twilio/whatsapp"
params = {"From": "whatsapp:+919876543210", "Body": "hi", "MessageSid": "SM123"}
sig = RequestValidator(token).compute_signature(url, params)
assert verify_signature(url, params, sig, token) is True
```

The full automated suite (`gateway/tests/`) covers signature validation, message parsing, `MessageSid` de-duplication, outbound sending, and the route's end-to-end wiring — all mocked, no live Twilio account or database needed:

```bash
cd gateway
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m pytest tests/ -v
```

### Rolling back

Set `TWILIO_ENABLED_RAW=false` and restart — outbound sends immediately stop attempting real Twilio API calls (they just log what would have been sent), with zero code changes needed. The webhook route stays mounted and will still `403` unsigned/invalid requests and `200` valid ones (it just won't send a reply) if left exposed.
