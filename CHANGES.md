# v3: real payments, transaction history, receipts and invoice reports (on top of v2)

## NEW files
`app/services/payment_service.py`, `app/routers/webhooks.py`, `app/routers/reports.py`, `app/services/reports.py`,
`alembic/versions/0002_payments_and_invoice_due_date.py`, `tests/test_payments.py`, `PAYMENTS.md`

## CHANGED files (replace whole file)
| File | What changed |
|---|---|
| `requirements.txt` | httpx now a runtime dependency |
| `.env.example` | Razorpay keys, payment terms |
| `app/config.py` | Razorpay settings (refuses to start in razorpay mode without keys) |
| `app/models.py` | `payments` table extended (provider, transaction id, method, attempts, receipt no...), `webhook_events` table, `invoices.due_date` |
| `app/schemas.py` | Payment order / verify / payment out, invoice due date + overdue flag |
| `app/services/payments.py` | Simulator + real Razorpay gateway (orders, signature check, capture) |
| `app/services/pdf.py` | Invoice PDF (due date, payment block), receipt PDF, report PDF |
| `app/services/billing.py` | Sets invoice due date |
| `app/routers/billing.py` | pay, verify, confirm (simulator), payments list/detail/receipt |
| `app/main.py` | Registers reports + webhooks routers |

Run `alembic upgrade head` after copying.
