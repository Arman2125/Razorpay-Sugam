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

## Mini-Razorpay capabilities exposed via MCP

`mcp-servers/mini-razorpay-mcp/server.py` exposes Mini-Razorpay's REST API as MCP tools. Every tool takes an `access_token` argument — the caller's per-merchant JWT, injected server-side by `message_processor.py` from `merchant_auth_service.py`, never something the LLM invents, sees as a credential, or can override with a different merchant's identity. No tool anywhere in this server accepts a `merchantId`/`merchant_id` argument.

| Domain | READ tools | WRITE tools |
|---|---|---|
| Payments | `search_payments`, `get_payment`, `get_payment_status`, `get_pending_payments`, `get_pending_payments_priority`, `get_overdue_payments`, `get_payments_summary` | `update_payment_status` |
| Reminders | — | `send_payment_reminder` |
| Payment Links | `get_payment_links`, `get_payment_link` | `create_payment_link`, `cancel_payment_link` |
| Customers | `search_customers`, `get_customer` | — |
| Refunds | `get_refund`, `get_refunds`, `get_payment_refunds`, `get_refundable_amount` | `create_refund` |
| Orders | `get_order`, `get_orders` | `create_order`, `update_order_status` |
| Invoices | `get_invoice`, `get_invoices` | `create_invoice`, `update_invoice`, `issue_invoice`, `mark_invoice_paid`, `cancel_invoice` |
| Subscriptions | `get_subscription`, `get_subscriptions` | `create_subscription`, `pause_subscription`, `resume_subscription`, `cancel_subscription`, `process_due_subscriptions` |
| Analytics | `get_analytics` | — |
| Settlements | `get_settlements_summary`, `get_settlement` | — |
| Activity/Audit | `get_activity` | — |

Financial semantics worth calling out explicitly:

- **Mini-Razorpay is the sole source of truth for financial state.** Sugam never computes a refundable balance, a subscription's next billing date, or any other financial figure itself — it only ever relays what Mini-Razorpay's API returns, and never accesses MongoDB directly.
- **Refunds** can never exceed a payment's refundable balance — Mini-Razorpay rejects an over-refund with `REFUND_EXCEEDS_BALANCE`; Sugam surfaces that rejection as-is rather than retrying with a smaller guessed amount. A refund never changes the underlying payment's own status.
- **Orders/Invoices** follow Mini-Razorpay's own lifecycle state machines exactly (e.g. an invoice can only be edited while `draft`, and `issue_invoice`/`mark_invoice_paid`/`cancel_invoice` are thin wrappers around the same `PATCH .../status` endpoint with a fixed target status) — Sugam never invents a transition Mini-Razorpay doesn't allow.
- **Subscriptions have no automatic billing.** Mini-Razorpay runs no background scheduler or cron job — a subscription's due cycle is only ever billed when `process_due_subscriptions` is explicitly invoked (itself merchant-scoped and safe to call repeatedly; already-billed cycles are never double-billed). Listing subscriptions, checking their status, or asking what's due (`get_subscriptions`, `get_subscription`) never bills anything — `intent_service.py`'s system prompt explicitly instructs the model to only call `process_due_subscriptions` on a clear, explicit request to process/run/bill due subscriptions, never as a side effect of a read question.
- **Customer-name ambiguity** on `create_payment_link`, `create_order`, `create_invoice`, and `create_subscription` is resolved the same deterministic way for all four: Mini-Razorpay itself returns `409 AMBIGUOUS_CUSTOMER` with a candidate list when a name matches more than one customer, the MCP client converts that into `{"ambiguous": true, "candidates": [...]}`, and `conversation_state_service.py` stores it and asks the user to pick a number — never resolved by the LLM guessing.

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
