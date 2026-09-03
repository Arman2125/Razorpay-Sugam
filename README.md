# Razorpay Sugam

A WhatsApp-style conversational front end for [Mini-Razorpay](../mini-razorpay/) — lets a merchant manage their payments by chatting in natural language ("Rahul Sharma ke liye ₹5,000 ka payment link bana do").

Modeled on the architectural patterns used by Sugam AI OS (a separate, existing system), reimplemented here from scratch and scoped down to what Mini-Razorpay actually needs: one "merchant" identity (not a multi-role permission matrix), one external application (not a multi-app registry), and a direct, fixed model split (no LLM router dependency) — OpenAI GPT for all reasoning/intent/tool-selection, Gemini solely for turning a voice note or video into text first.

> This project never reads from or writes to Sugam AI OS's own codebase or database. It's a standalone system that borrows the same proven patterns.

## Architecture

The Meta WhatsApp Cloud API webhook feeds the channel-agnostic pipeline,
which also has a direct entrypoint via `POST /test/message` (no WhatsApp
setup at all) — both call the exact same `process_user_message()`:

```
Meta WhatsApp Cloud API
   ↓ POST /webhook/whatsapp
   ↓ X-Hub-Signature-256 (app secret)
   ↓ app/whatsapp/*
                  ↓
         message_processor.py        (orchestrator — the "never guess" boundary)
                  ↓ resolve identity (local DB lookup, phone number → merchant)
         identity_service.py  ←synced by←  directory_sync_service.py  ← GET /api/auth/demo-merchants
                  ↓ get per-merchant JWT (cached)
         merchant_auth_service.py  → POST /api/auth/login
                  ↓ understand intent (native OpenAI GPT tool-calling — the ONLY
                  ↓  reasoning/tool-selection engine; Gemini never reaches here)
         intent_service.py
                  ↓ execute via MCP (persistent stdio subprocess)
         gateway/app/mcp/mini_razorpay_mcp_client.py → mcp-servers/mini-razorpay-mcp/server.py
                                                             ↓
                                                    mini_razorpay_client.py (httpx)
                                                             ↓
                                               Mini-Razorpay real API → MongoDB
                  ↓ ambiguity? → conversation_state_service.py — numbered clarifying question
                  ↓ format reply → response_formatting.py
                  ↓
         app/whatsapp/client.py (Meta Graph API)
```

The webhook owns only its own transport concerns — signature verification,
request-shape parsing, and outbound sending. Everything from merchant
identity resolution onward is shared, unmodified code.

## Prerequisites

- Mini-Razorpay running locally at `http://localhost:5000` (see `../mini-razorpay/README.md`)
- PostgreSQL running locally, with a `razorpay_sugam` database created
- Python 3.11
- An OpenAI or Gemini API key

## Setup

```bash
# 1. MCP server's own venv
cd mcp-servers/mini-razorpay-mcp
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. Gateway's own venv
cd ../../gateway
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# edit .env: set DATABASE_URL/DATABASE_URL_SYNC to your real Postgres
# credentials, and OPENAI_API_KEY + OPENAI_MODEL (or the Gemini equivalents)

# 4. Run migrations
./.venv/Scripts/python.exe -m alembic upgrade head

# 5. Verify prerequisites
./.venv/Scripts/python.exe ../scripts/check_prereqs.py

# 6. Start the gateway
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

## Testing without WhatsApp

```bash
curl -X POST http://127.0.0.1:8100/test/message \
  -H "Content-Type: application/json" \
  -d '{"user_id": "+919876543210", "message": "what payments are overdue?"}'
```

`user_id` stands in for the WhatsApp sender's phone number — use one of Mini-Razorpay's seeded demo merchant numbers (`+919876543210` / `+919876543211`) to get a real, authenticated response.

See `RUNBOOK.md` for connecting real Meta WhatsApp credentials later, and `scripts/mcp_smoke_test.py` / `scripts/e2e_scenarios.py` for end-to-end verification against the real backend.

## Supported WhatsApp channel

| | Meta WhatsApp Cloud API |
|---|---|
| Inbound webhook | `POST /webhook/whatsapp` |
| Request shape | JSON (`entry[].changes[].value.messages[]`) |
| Signature header | `X-Hub-Signature-256` (HMAC-SHA256, app secret) |
| Outbound sender | `app/whatsapp/client.py` → Graph API |
| Enable flag | `WHATSAPP_ENABLED_RAW` |

## What this is not

- Not a fork or copy of Sugam AI OS's code — a fresh, independent implementation of the same patterns.
- Not connected to real WhatsApp yet (`WHATSAPP_ENABLED_RAW=false` by default) — the webhook is complete and ready, just inert until real credentials are supplied.
- Not a replacement for Mini-Razorpay's own dashboard — this is an additional, conversational way to reach the same real API.
