"""PDF documents (ReportLab), returned as bytes: invoice, payment receipt, invoice report.
Built-in PDF fonts have no rupee glyph, so 'Rs.' is used."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..models import Invoice, InvoiceStatus, Payment, PaymentStatus

GREEN = colors.HexColor("#1f6f43")
styles = getSampleStyleSheet()


def _build(story, title: str, page=A4) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=title)
    doc.build(story)
    return buf.getvalue()


def _kv_table(rows, widths):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                           ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _grid(rows, widths, right_from=1, bold_last=False):
    t = Table(rows, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
             ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
             ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8.5)]
    if bold_last:
        style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    return t


def _paid_payment(invoice: Invoice) -> Payment | None:
    return next((p for p in invoice.payments if p.status == PaymentStatus.succeeded), None)


def build_invoice_pdf(invoice: Invoice) -> bytes:
    c = invoice.contract
    pay = _paid_payment(invoice)
    s = [Paragraph("GridWeave AI", styles["Title"]),
         Paragraph("Virtual Power Purchase Agreement - Energy Invoice", styles["Heading3"]), Spacer(1, 6 * mm)]
    status = "OVERDUE" if invoice.is_overdue else invoice.status.value.upper()
    s.append(_kv_table([
        ["Invoice No:", invoice.invoice_number, "Status:", status],
        ["Issued on:", f"{invoice.issued_at:%d %b %Y}", "Due date:", f"{invoice.due_date:%d %b %Y}" if invoice.due_date else "-"],
        ["Seller (plant):", c.plant.name, "Buyer:", c.buyer.org_name],
        ["Contract ID:", str(c.id), "Billing period:",
         f"{invoice.period_start:%d %b %Y} to {invoice.period_end:%d %b %Y}"],
    ], [30 * mm, 52 * mm, 30 * mm, 58 * mm]))
    s.append(Spacer(1, 7 * mm))
    s.append(_grid([
        ["Description", "Energy (kWh)", "Rate (Rs./kWh)", "Amount (Rs.)"],
        ["Solar energy delivered (metered)", f"{invoice.kwh_billed:,.3f}", f"{invoice.price_per_kwh:.2f}", f"{invoice.subtotal:,.2f}"],
        [f"Tax @ {invoice.tax_percent:.1f}%", "", "", f"{invoice.tax_amount:,.2f}"],
        ["TOTAL", "", "", f"{invoice.total:,.2f}"],
    ], [68 * mm, 32 * mm, 36 * mm, 34 * mm], bold_last=True))
    s.append(Spacer(1, 7 * mm))
    if pay:
        s.append(Paragraph("Payment received", styles["Heading4"]))
        s.append(_kv_table([
            ["Receipt No:", pay.receipt_number, "Method:", (pay.method or "-").upper()],
            ["Transaction ID:", pay.provider_payment_id, "Paid on:", f"{pay.completed_at:%d %b %Y %H:%M} UTC"],
        ], [30 * mm, 62 * mm, 25 * mm, 53 * mm]))
    elif invoice.status == InvoiceStatus.issued:
        s.append(Paragraph("Payment pending. Please pay through the GridWeave AI platform by the due date.", styles["Normal"]))
    s += [Spacer(1, 8 * mm), Paragraph("Generated automatically by GridWeave AI. Not a GST tax invoice.", styles["Italic"])]
    return _build(s, f"Invoice {invoice.invoice_number}")


def build_receipt_pdf(payment: Payment) -> bytes:
    inv = payment.invoice
    c = inv.contract
    s = [Paragraph("GridWeave AI", styles["Title"]), Paragraph("Payment Receipt", styles["Heading3"]),
         Spacer(1, 6 * mm),
         _kv_table([
             ["Receipt No:", payment.receipt_number, "Date:", f"{payment.completed_at:%d %b %Y %H:%M} UTC"],
             ["Invoice No:", inv.invoice_number, "Paid by:", c.buyer.org_name],
             ["Paid to (plant):", c.plant.name, "Method:", (payment.method or "-").upper()],
             ["Transaction ID:", payment.provider_payment_id, "Gateway order:", payment.gateway_order_id],
         ], [30 * mm, 52 * mm, 30 * mm, 58 * mm]),
         Spacer(1, 7 * mm),
         _grid([["Description", "Amount (Rs.)"],
                [f"Energy invoice {inv.invoice_number} ({inv.kwh_billed:,.3f} kWh)", f"{payment.amount:,.2f}"],
                ["TOTAL PAID", f"{payment.amount:,.2f}"]], [130 * mm, 40 * mm], bold_last=True),
         Spacer(1, 8 * mm),
         Paragraph(f"Payment mode: {payment.provider}. This receipt confirms the payment above was received.", styles["Italic"])]
    return _build(s, f"Receipt {payment.receipt_number}")


def build_report_pdf(title: str, period: str, summary: dict, invoices: list[Invoice]) -> bytes:
    s = [Paragraph("GridWeave AI", styles["Title"]), Paragraph(f"{title} ({period})", styles["Heading3"]),
         Spacer(1, 4 * mm)]
    s.append(_grid([
        ["Invoices", "Energy (kWh)", "Billed (Rs.)", "Paid (Rs.)", "Outstanding (Rs.)", "Overdue (Rs.)", "Collected %"],
        [str(summary["invoice_count"]), f"{summary['total_kwh']:,.2f}", f"{summary['total_billed']:,.2f}",
         f"{summary['amount_paid']:,.2f}", f"{summary['amount_outstanding']:,.2f}",
         f"{summary['amount_overdue']:,.2f}", f"{summary['collection_rate_percent']:.1f}"],
    ], [28 * mm, 34 * mm, 36 * mm, 36 * mm, 40 * mm, 36 * mm, 30 * mm], right_from=0))
    s.append(Spacer(1, 6 * mm))
    rows = [["Invoice No", "Contract", "Period", "kWh", "Total (Rs.)", "Status", "Due", "Paid on"]]
    for i in invoices:
        rows.append([i.invoice_number, str(i.contract_id), f"{i.period_start:%d %b} - {i.period_end:%d %b %Y}",
                     f"{i.kwh_billed:,.2f}", f"{i.total:,.2f}", "OVERDUE" if i.is_overdue else i.status.value.upper(),
                     f"{i.due_date:%d %b %Y}" if i.due_date else "-", f"{i.paid_at:%d %b %Y}" if i.paid_at else "-"])
    if len(rows) == 1:
        s.append(Paragraph("No invoices in this period.", styles["Normal"]))
    else:
        s.append(_grid(rows, [34 * mm, 18 * mm, 48 * mm, 26 * mm, 30 * mm, 24 * mm, 28 * mm, 28 * mm], right_from=3))
    return _build(s, title, landscape(A4))
