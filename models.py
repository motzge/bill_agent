"""Data models for bill_agent.

Pydantic is the trust boundary: everything the LLM returns is untrusted
text until it survives InvoiceData validation. All amounts are Decimal --
never float -- because 0.1 + 0.2 != 0.3 has no place in accounting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

#Austrian / German VAT IDs. Checked in validation.py, NOT here - see note on supplier_uid below.
UID_PATTERN = re.compile(r"^(ATU\d{8}|DE\d{9})$")

_GERMAN_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_CODE_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```\s*$")
_NULLISH = {"", "null", "none", "n/a", "-"}


class SourceKind(str, Enum):
    DIGITAL_PDF = "digital_pdf"
    SCANNED_PDF = "scanned_pdf"
    IMAGE = "image"


@dataclass(frozen=True)
class InvoiceSource:
    """One file from input/, plus extracted text once ingest/ocr ran."""

    path: Path
    kind: SourceKind
    text: str | None = None


#normalizers (mode="before": they clean raw LLM strings, then Pydantic does the actual type validation on the cleaned value) 


def _empty_to_none(value: object) -> object:
    """LLM models love writing "null" or "" instead of JSON null."""
    if isinstance(value, str) and value.strip().lower() in _NULLISH:
        return None
    return value


def _normalize_amount(value: object) -> object:
    """Accept German number formats: '1.234,56', '1234,56', '1 234,56 EUR'.

    Rules: strip currency noise; if both separators appear, the LAST one is
    the decimal separator; a lone comma is a decimal comma (German invoices);
    multiple dots without comma are thousands separators.
    """
    if not isinstance(value, str):
        return value
    v = value.strip().replace("\u20ac", "").replace("EUR", "").replace(" ", "")
    if "," in v and "." in v:
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")  #1.234,56 -> 1234.56
        else:
            v = v.replace(",", "")  #1,234.56 -> 1234.56
    elif "," in v:
        v = v.replace(",", ".")  #1234,56 -> 1234.56
    elif v.count(".") > 1:
        v = v.replace(".", "")  #1.234.567 -> 1234567
    return v


def _normalize_date(value: object) -> object:
    """Accept '14.07.2026' next to ISO '2026-07-14'."""
    if isinstance(value, str):
        match = _GERMAN_DATE.match(value.strip())
        if match:
            day, month, year = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    return value


#schema


class VatItem(BaseModel):
    """One VAT bucket. Invoices can mix rates (e.g. 20% parts + 10% freight)."""

    model_config = ConfigDict(extra="ignore")

    rate: Decimal = Field(ge=0, le=100)
    net: Decimal
    tax: Decimal

    _amounts = field_validator("rate", "net", "tax", mode="before")(_normalize_amount)


class Skonto(BaseModel):
    """Early-payment discount: percent off if paid within days."""

    model_config = ConfigDict(extra="ignore")

    percent: Decimal = Field(gt=0, le=100)
    days: int = Field(gt=0)

    _percent = field_validator("percent", mode="before")(_normalize_amount)


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    invoice_number: str = Field(min_length=1)
    invoice_date: date
    supplier_name: str = Field(min_length=1)
    vat_items: list[VatItem] = Field(min_length=1)
    gross_total: Decimal
    due_date: date | None = None  #None is allowed here; validation.py decides
    payment_term_raw: str | None = None
    # Deliberately NOT regex-validated here: a schema failure triggers the repair prompt, and a repair prompt asked to "fix" a UID would just invent a valid-looking one. Bad UIDs go straight to escalation instead.
    supplier_uid: str | None = None
    skonto: Skonto | None = None

    _nullish = field_validator(
        "due_date", "payment_term_raw", "supplier_uid", "skonto", mode="before"
    )(_empty_to_none)
    _dates = field_validator("invoice_date", "due_date", mode="before")(_normalize_date)
    _gross = field_validator("gross_total", mode="before")(_normalize_amount)

    @property
    def net_total(self) -> Decimal:
        return sum((item.net for item in self.vat_items), Decimal("0"))

    @property
    def tax_total(self) -> Decimal:
        return sum((item.tax for item in self.vat_items), Decimal("0"))


#entry point for untrusted LLM output

def invoice_from_llm_output(raw: str) -> InvoiceData:
    """Parse raw LLM text into a validated InvoiceData.

    Raises ValueError (bad/absent JSON) or pydantic.ValidationError (schema
    violation). The caller decides whether that means repair or escalation.
    """
    cleaned = _CODE_FENCE.sub("", raw.strip())
    data = json.loads(cleaned)  #raises json.JSONDecodeError (a ValueError)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")
    return InvoiceData.model_validate(data)