# hack_nation_back

FastAPI backend for the hackathon project.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Configure Postgres with `DATABASE_URL` in `.env`.

## Endpoints

- `GET /health`
- `POST /client/call`
- `POST /internal/call`
- Agent API: `POST /api/agent/campaigns`, `GET /api/agent/campaigns`
- User API: `GET /api/user/current_variant`, `POST /api/user/completed_task`
- Tester API: `POST /tester/campaigns/{campaign_id}/assignment`
- SDK API: `POST /sdk/init`, `POST /sdk/events`, `POST /sdk/tasks/complete`
- Payment API: `POST /payments/invoices`

Interactive API docs are available at `/docs` while the server is running.

### Create Agent Campaign

`POST /api/agent/campaigns`

```json
{
  "variants": [
    {
      "link": "https://example.com/a",
      "name": "Variant A"
    }
  ],
  "budget": 1000,
  "number_of_tests": 20,
  "success_event": "task_completed",
  "task": "Try to complete checkout and report if anything feels confusing."
}
```

The response includes `campaign_id`.

The response also includes a Lightning invoice that must be paid before the
campaign is available to testers:

```json
{
  "campaign_id": "...",
  "payment_invoice": "lnbc...",
  "payment_hash": "...",
  "payment_status": "pending"
}
```

### Check Agent Payment

`POST /api/agent/payment_status`

```json
{
  "campaign_id": "...",
  "payment_hash": "..."
}
```

The backend checks the platform wallet invoice status. When the payment status
becomes `settled`, the campaign is marked active and can be served to testers.

### Get Current User Variant

`GET /api/user/current_variant`

For now the backend uses hardcoded `user_id = "1"`. Each returned campaign is saved as used for that user, so repeated calls return a different campaign until none are left.

Response shape:

```json
{
  "campaign_id": "...",
  "variant": {
    "link": "https://example.com/a",
    "name": "Variant A"
  },
  "success_event": "task_completed",
  "task": "Try to complete checkout and report if anything feels confusing."
}
```

### Complete User Task

`POST /api/user/completed_task`

```json
{
  "campaign_id": "...",
  "user_id": "1",
  "metrics": {
    "duration_ms": 42000,
    "clicks": 12,
    "completed": true,
    "friction_score": 2
  },
  "success_event": "checkout_completed"
}
```
