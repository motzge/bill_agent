"""Business validation for bill_agent.

Input is an already schema-valid InvoiceData (Pydantic passed), output is a
ValidationResult: either clean, or carrying every reason why a human needs
to look at this invoice. Reasons are collected, not fail-fast -- the person
doing the review should see the full picture, not just the first problem.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from config import GROSS_TOTAL_TOLERANCE
from models import UID_PATTERN, InvoiceData

logger = logging.getLogger(__name__)

#Standard AT (0/10/13/20) and DE (0/7/19) VAT rates. Anything else on a supplier invoice for this client is almost certainly an extraction error.
PLAUSIBLE_VAT_RATES = frozenset(
    Decimal(rate) for rate in ("0", "7", "10", "13", "19", "20")
)

_TERM_DAYS = re.compile(r"(\d{1,3})\s*Tage", re.IGNORECASE)
_TERM_IMMEDIATE = re.compile(r"\b(sofort|prompt)\b", re.IGNORECASE)


@dataclass
class ValidationResult:
    invoice: InvoiceData  #possibly enriched (derived due_date)
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reasons


def validate(invoice: InvoiceData, today: date | None = None) -> ValidationResult:
    """Run all business rules. Empty reasons == safe to write to Excel."""
    today = today or date.today()
    invoice = _resolve_due_date(invoice)

    reasons: list[str] = []
    reasons.extend(_check_totals(invoice))
    reasons.extend(_check_uid(invoice))
    reasons.extend(_check_plausibility(invoice, today))
    if invoice.due_date is None:
        reasons.append(
            "Fälligkeitsdatum fehlt und ist aus dem Zahlungsziel "
            f"({invoice.payment_term_raw!r}) nicht ableitbar"
        )

    result = ValidationResult(invoice=invoice, reasons=reasons)
    if result.ok:
        logger.info("Validation passed: %s", invoice.invoice_number)
    else:
        logger.warning(
            "Escalating %s: %s", invoice.invoice_number, " | ".join(reasons)
        )
    return result


def _resolve_due_date(invoice: InvoiceData) -> InvoiceData:
    """Derive due_date from invoice_date + payment term, deterministically.

    The LLM is told NOT to compute dates; date math belongs here where it
    is testable. Unknown term wording simply stays unresolved and turns
    into an escalation reason in validate().
    """
    if invoice.due_date is not None or invoice.payment_term_raw is None:
        return invoice

    term = invoice.payment_term_raw
    if _TERM_IMMEDIATE.search(term):
        due = invoice.invoice_date
    else:
        #terms like "2% Skonto binnen 10 Tagen, 30 Tage netto" contain two day counts - the NET due date is always the longest one mentioned
        matches = _TERM_DAYS.findall(term)
        if not matches:
            return invoice
        due = invoice.invoice_date + timedelta(days=max(int(m) for m in matches))

    logger.debug("Derived due_date %s from term %r", due, term)
    return invoice.model_copy(update={"due_date": due})


def _check_totals(invoice: InvoiceData) -> list[str]:
    reasons = []
    expected = invoice.net_total + invoice.tax_total
    diff = abs(expected - invoice.gross_total)
    if diff > GROSS_TOTAL_TOLERANCE:
        reasons.append(
            f"Summenprüfung fehlgeschlagen: Netto {invoice.net_total} + USt {invoice.tax_total} "
            f"= {expected}, laut Rechnung Brutto {invoice.gross_total} (Differenz {diff})"
        )
    for item in invoice.vat_items:
        computed_tax = (item.net * item.rate / 100).quantize(Decimal("0.01"))
        if abs(computed_tax - item.tax) > GROSS_TOTAL_TOLERANCE:
            reasons.append(
                f"USt-Position {item.rate}%: ausgewiesene Steuer {item.tax} passt nicht zu "
                f"Netto {item.net} × {item.rate}% = {computed_tax}"
            )
    return reasons


def _check_uid(invoice: InvoiceData) -> list[str]:
    uid = invoice.supplier_uid
    if uid is not None and not UID_PATTERN.match(uid):
        return [f"UID-Nummer hat ungültiges Format: {uid!r}"]
    return []


def _check_plausibility(invoice: InvoiceData, today: date) -> list[str]:
    reasons = []
    if invoice.gross_total <= 0:
        reasons.append(
            f"Bruttobetrag {invoice.gross_total} <= 0 (Gutschrift oder Extraktionsfehler)"
        )
    if invoice.invoice_date > today:
        reasons.append(f"Rechnungsdatum {invoice.invoice_date} liegt in der Zukunft")
    if invoice.due_date is not None and invoice.due_date < invoice.invoice_date:
        reasons.append(
            f"Fälligkeit {invoice.due_date} liegt vor dem Rechnungsdatum {invoice.invoice_date}"
        )
    for item in invoice.vat_items:
        if item.rate not in PLAUSIBLE_VAT_RATES:
            reasons.append(f"Ungewöhnlicher USt-Satz: {item.rate}%")
    return reasons