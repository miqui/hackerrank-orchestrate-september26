"""Typed CSV loaders for the Buy-or-Wait dataset.

Stdlib only. All dates -> datetime.date, all money -> decimal.Decimal,
enum-like columns validated against the known value sets observed in the
shipped dataset. Loaders never silently coerce a blank amount to 0 — a
blank `amount` on a financial event is left as None and must be resolved
via the image-extraction pipeline (see reconstruct.py / evidence.py).
"""
from __future__ import annotations

import csv
import datetime
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"

# ---------------------------------------------------------------------------
# Enum value sets observed on the shipped dataset (used for validation only;
# unknown values raise loudly rather than being silently accepted).
# ---------------------------------------------------------------------------
EVENT_TYPES = {
    "income", "investment_sale", "debt_payment", "investment_valuation",
    "expense", "refund", "investment_purchase", "subscription",
}
EVENT_STATUSES = {"scheduled", "unrealized", "settled", "cancelled", "failed", "pending"}
EVENT_DIRECTIONS = {"credit", "debit", "non_cash"}
EVENT_FLEXIBILITIES = {"reducible_or_stoppable", "stoppable", "reducible", "fixed", ""}
REQUEST_TYPES = {
    "purchase", "travel", "education", "family_transfer", "debt_repayment",
    "investment", "housing", "emergency_expense", "other",
}
PAYMENT_METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}


class DataError(ValueError):
    """Raised when the dataset contains a value the loaders cannot trust."""


def _parse_date(value: str, field_name: str, row_id: str) -> Optional[datetime.date]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{row_id}: bad date in {field_name!r}: {value!r}") from exc


def _parse_decimal(value: str, field_name: str, row_id: str) -> Optional[Decimal]:
    value = (value or "").strip()
    if value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise DataError(f"{row_id}: bad decimal in {field_name!r}: {value!r}") from exc


def _parse_bool(value: str, field_name: str, row_id: str) -> bool:
    v = (value or "").strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no", ""):
        return False
    raise DataError(f"{row_id}: bad bool in {field_name!r}: {value!r}")


def _parse_pipe_list(value: str) -> list[str]:
    value = (value or "").strip()
    if not value:
        return []
    return [v.strip() for v in value.split("|") if v.strip()]


def _validate_enum(value: str, allowed: set[str], field_name: str, row_id: str) -> str:
    if value not in allowed:
        raise DataError(f"{row_id}: unexpected {field_name} value {value!r} (allowed={sorted(allowed)})")
    return value


def _reader(path: Path):
    with path.open(newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------

@dataclass
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: list[str]
    expense_categories_to_protect: list[str]
    expense_categories_user_is_willing_to_reduce: list[str]
    expense_categories_user_is_willing_to_stop: list[str]
    payment_methods_user_will_consider: list[str]
    max_installment_months: Optional[int]


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[Decimal]  # None means "unresolved blank; must use image"
    currency: str
    event_date: datetime.date
    settlement_date: Optional[datetime.date]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]

    @property
    def effective_date(self) -> datetime.date:
        """Settlement date when present, else event date (R10/R17 V4)."""
        return self.settlement_date or self.event_date


@dataclass
class ExchangeRate:
    rate_date: datetime.date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: datetime.date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime.datetime
    source_type: str
    message_text: str


@dataclass
class Image:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]

    @property
    def path(self) -> Path:
        return DATASET_DIR / "media" / "images" / f"{self.image_id}.png"


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: datetime.date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: datetime.date
    allows_partial_payment: bool
    request_text: str


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def load_financial_profiles(path: Path = DATASET_DIR / "financial_profiles.csv") -> dict[str, FinancialProfile]:
    out: dict[str, FinancialProfile] = {}
    for r in _reader(path):
        uid = r["user_id"]
        max_months = r["max_installment_months"].strip()
        out[uid] = FinancialProfile(
            user_id=uid,
            home_currency=r["home_currency"].strip(),
            current_available_balance=_parse_decimal(r["current_available_balance"], "current_available_balance", uid),
            minimum_balance_to_keep=_parse_decimal(r["minimum_balance_to_keep"], "minimum_balance_to_keep", uid),
            financial_priorities=_parse_pipe_list(r["financial_priorities"]),
            expense_categories_to_protect=_parse_pipe_list(r["expense_categories_to_protect"]),
            expense_categories_user_is_willing_to_reduce=_parse_pipe_list(r["expense_categories_user_is_willing_to_reduce"]),
            expense_categories_user_is_willing_to_stop=_parse_pipe_list(r["expense_categories_user_is_willing_to_stop"]),
            payment_methods_user_will_consider=_parse_pipe_list(r["payment_methods_user_will_consider"]),
            max_installment_months=int(max_months) if max_months else None,
        )
    return out


def load_financial_events(path: Path = DATASET_DIR / "financial_events.csv") -> list[FinancialEvent]:
    out: list[FinancialEvent] = []
    for r in _reader(path):
        eid = r["event_id"]
        event_date = _parse_date(r["event_date"], "event_date", eid)
        if event_date is None:
            raise DataError(f"{eid}: missing event_date")
        out.append(FinancialEvent(
            event_id=eid,
            user_id=r["user_id"],
            event_type=_validate_enum(r["event_type"], EVENT_TYPES, "event_type", eid),
            description=r["description"],
            category=r["category"],
            direction=_validate_enum(r["direction"], EVENT_DIRECTIONS, "direction", eid),
            amount=_parse_decimal(r["amount"], "amount", eid),
            currency=r["currency"].strip(),
            event_date=event_date,
            settlement_date=_parse_date(r["settlement_date"], "settlement_date", eid),
            status=_validate_enum(r["status"], EVENT_STATUSES, "status", eid),
            linked_event_id=r["linked_event_id"].strip() or None,
            flexibility=_validate_enum(r["flexibility"], EVENT_FLEXIBILITIES, "flexibility", eid),
            minimum_allowed_amount=_parse_decimal(r["minimum_allowed_amount"], "minimum_allowed_amount", eid),
        ))
    return out


def load_exchange_rates(path: Path = DATASET_DIR / "exchange_rates.csv") -> list[ExchangeRate]:
    out: list[ExchangeRate] = []
    for i, r in enumerate(_reader(path)):
        rid = f"rate_row_{i}"
        rate_date = _parse_date(r["rate_date"], "rate_date", rid)
        if rate_date is None:
            raise DataError(f"{rid}: missing rate_date")
        out.append(ExchangeRate(
            rate_date=rate_date,
            from_currency=r["from_currency"].strip(),
            to_currency=r["to_currency"].strip(),
            rate=_parse_decimal(r["rate"], "rate", rid),
        ))
    return out


def load_payment_options(path: Path = DATASET_DIR / "request_payment_options.csv") -> list[PaymentOption]:
    out: list[PaymentOption] = []
    for r in _reader(path):
        oid = r["payment_option_id"]
        first_date = _parse_date(r["first_payment_date"], "first_payment_date", oid)
        if first_date is None:
            raise DataError(f"{oid}: missing first_payment_date")
        freq = r["payment_frequency_days"].strip()
        out.append(PaymentOption(
            payment_option_id=oid,
            request_id=r["request_id"],
            payment_method=r["payment_method"],
            payment_amount=_parse_decimal(r["payment_amount"], "payment_amount", oid),
            number_of_payments=int(r["number_of_payments"]),
            first_payment_date=first_date,
            payment_frequency_days=int(freq) if freq else None,
            financing_fee=_parse_decimal(r["financing_fee"], "financing_fee", oid) or Decimal("0"),
            total_payable_amount=_parse_decimal(r["total_payable_amount"], "total_payable_amount", oid),
        ))
    return out


def load_messages(path: Path = DATASET_DIR / "messages.csv") -> list[Message]:
    out: list[Message] = []
    for r in _reader(path):
        mid = r["message_id"]
        sent_raw = r["sent_at"].strip()
        try:
            sent_at = datetime.datetime.fromisoformat(sent_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataError(f"{mid}: bad sent_at {sent_raw!r}") from exc
        out.append(Message(
            message_id=mid,
            user_id=r["user_id"],
            request_id=r["request_id"].strip() or None,
            related_event_id=r["related_event_id"].strip() or None,
            sent_at=sent_at,
            source_type=r["source_type"],
            message_text=r["message_text"],
        ))
    return out


def load_images(path: Path = DATASET_DIR / "images.csv") -> list[Image]:
    out: list[Image] = []
    for r in _reader(path):
        out.append(Image(
            image_id=r["image_id"],
            user_id=r["user_id"],
            request_id=r["request_id"].strip() or None,
            related_event_id=r["related_event_id"].strip() or None,
        ))
    return out


def load_requests(path: Path = DATASET_DIR / "requests.csv") -> list[Request]:
    out: list[Request] = []
    for r in _reader(path):
        rid = r["request_id"]
        req_date = _parse_date(r["request_date"], "request_date", rid)
        completion_date = _parse_date(r["desired_completion_date"], "desired_completion_date", rid)
        if req_date is None or completion_date is None:
            raise DataError(f"{rid}: missing request_date or desired_completion_date")
        out.append(Request(
            request_id=rid,
            user_id=r["user_id"],
            request_date=req_date,
            request_type=_validate_enum(r["request_type"], REQUEST_TYPES, "request_type", rid),
            requested_amount=_parse_decimal(r["requested_amount"], "requested_amount", rid),
            desired_completion_date=completion_date,
            allows_partial_payment=_parse_bool(r["allows_partial_payment"], "allows_partial_payment", rid),
            request_text=r["request_text"],
        ))
    return out


def load_all(dataset_dir: Path = DATASET_DIR) -> dict:
    """Load every dataset CSV and return a dict of parsed collections."""
    return {
        "profiles": load_financial_profiles(dataset_dir / "financial_profiles.csv"),
        "events": load_financial_events(dataset_dir / "financial_events.csv"),
        "rates": load_exchange_rates(dataset_dir / "exchange_rates.csv"),
        "payment_options": load_payment_options(dataset_dir / "request_payment_options.csv"),
        "messages": load_messages(dataset_dir / "messages.csv"),
        "images": load_images(dataset_dir / "images.csv"),
        "requests": load_requests(dataset_dir / "requests.csv"),
    }
