# Real payments, transactions and invoice reports

## What you get
| Feature | Endpoint |
|---|---|
| Start a payment (creates a real Razorpay order) | `POST /billing/invoices/{id}/pay` |
| Confirm after Checkout (signature + status + amount verified) | `POST /billing/payments/verify` |
| Razorpay tells the server about every result (reliable path) | `POST /webhooks/razorpay` |
| Transaction history (buyer: paid, seller: received, admin: all) | `GET /billing/payments`, `GET /billing/payments/{id}` |
| Payment receipt PDF | `GET /billing/payments/{id}/receipt` |
| Invoice PDF (shows due date, PAID / OVERDUE, transaction id) | `GET /billing/invoices/{id}/pdf` |
| Summary: billed, paid, outstanding, overdue, collection % | `GET /reports/summary?start=&end=` |
| Month-wise report | `GET /reports/monthly` |
| Excel-friendly exports | `GET /reports/invoices.csv`, `GET /reports/transactions.csv` |
| Printable invoice report | `GET /reports/invoices.pdf?start=&end=` |

Every report is automatically limited to the caller: a seller sees only invoices of their plants, a buyer only their own, admin sees everything.

## How a payment works
1. Buyer clicks Pay -> frontend calls `POST /billing/invoices/{id}/pay`. The server creates a Razorpay **order** for the exact invoice amount (in paise) and returns `key_id`, `amount_paise`, `gateway_order_id`.
2. Frontend opens **Razorpay Checkout** (card / UPI / netbanking). The buyer pays on Razorpay's page - your server never sees card details.
3. Checkout returns `razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature` -> frontend calls `POST /billing/payments/verify`.
4. Server checks: signature is valid (HMAC with the secret key) -> asks Razorpay for the real payment -> amount and order match -> captures if only "authorized" -> marks the invoice **paid**, stores method, transaction id and receipt number.
5. **Webhook** does the same server-to-server, so the invoice is still marked paid if the buyer closes the browser. Events are stored with their id, so duplicates are ignored.

Safety built in: forged signatures rejected, wrong amount rejected, one live order per invoice (no double charge), double payments flagged "refund required", failed attempts counted (the buyer can retry), everything written to the audit log.

## Set up Razorpay test mode (free, no real money)
1. Create an account at razorpay.com and stay in **Test Mode** (toggle in the dashboard).
2. Settings -> **API Keys** -> Generate Test Key. Copy the Key Id (`rzp_test_...`) and Key Secret.
3. Settings -> **Webhooks** -> Add URL `https://<your-public-url>/webhooks/razorpay`, choose a secret, tick `payment.captured`, `payment.failed`, `order.paid`.
   Running locally? Expose port 8000 with ngrok (`ngrok http 8000`) and use that URL.
4. Put in `.env`:
```
PAYMENT_MODE=razorpay
RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxx
RAZORPAY_WEBHOOK_SECRET=the_secret_from_step_3
```
5. Run `alembic upgrade head`, restart the server, and pay an invoice with Razorpay's **test card / test UPI** details (listed in Razorpay's test-mode documentation).

Going live later = complete Razorpay KYC, then replace the test keys with `rzp_live_` keys. No code change.

## Frontend code for Yash (Razorpay Checkout)
```html
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
```
```js
async function payInvoice(invoiceId) {
  const order = await api.post(`/billing/invoices/${invoiceId}/pay`);   // Bearer token as usual
  const rzp = new Razorpay({
    key: order.key_id,
    amount: order.amount_paise,
    currency: order.currency,
    order_id: order.gateway_order_id,
    name: "GridWeave AI",
    description: `Invoice ${order.invoice_number}`,
    theme: { color: "#1f6f43" },
    handler: async (resp) => {
      // resp = { razorpay_order_id, razorpay_payment_id, razorpay_signature }
      const payment = await api.post("/billing/payments/verify", resp);
      showReceipt(payment.id);   // GET /billing/payments/{id}/receipt
    },
  });
  rzp.on("payment.failed", (e) => alert(e.error.description));  // buyer can press Pay again
  rzp.open();
}
```
In `PAYMENT_MODE=test` the order response has `provider: "test"` and `key_id: null`; use `POST /billing/payments/confirm` with `{"gateway_order_id": ..., "outcome": "success"}` for demos without a Razorpay account.
