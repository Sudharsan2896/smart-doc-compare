"""
AMC Monitor — watches Annual Maintenance Contract expiry dates and decides what
needs action, then drafts the reminder.

This is the app's first *agentic* piece: instead of answering a one-off question,
it looks at a whole register of contracts, decides on its own **which** ones need
attention and **what** action each needs, and prepares the artifact (a renewal
reminder email). In a real deployment a daily scheduled run would do exactly this
and send the drafts out.

The Phase-0 rule still holds, and it's the whole point here:

    The DECISION — which contracts are expired / critical / due, and what action
    each needs — is deterministic date math (`analyze_amc`). The LLM is NEVER
    allowed to decide urgency; it only DRAFTS the reminder email from facts the
    code already computed. So a hallucinating model can word an email awkwardly,
    but it can never make you miss (or mis-prioritise) a renewal.

Everything here is pure Python (no pandas/numpy) so the engine is easy to test;
the Streamlit UI reads the register into a DataFrame and hands rows in as dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


# Status buckets, most to least urgent. Thresholds are configurable in analyze_amc.
STATUS_EXPIRED = "Expired"
STATUS_CRITICAL = "Critical"
STATUS_DUE = "Due soon"
STATUS_OK = "OK"

_ACTION = {
    STATUS_EXPIRED: "Escalate — renew immediately (contract already lapsed)",
    STATUS_CRITICAL: "Start renewal now",
    STATUS_DUE: "Plan renewal",
    STATUS_OK: "Monitor",
}

# Order used when ranking so the most urgent bucket floats to the top.
_STATUS_RANK = {STATUS_EXPIRED: 0, STATUS_CRITICAL: 1, STATUS_DUE: 2, STATUS_OK: 3}

# Header keywords for auto-detecting columns, most specific first. Order matters:
# for end_date, "expiry" is tried before the loose "end" so a real expiry column
# wins over a coincidental match.
COLUMN_KEYWORDS = {
    "asset": ["asset", "equipment", "item", "description", "descr"],
    "vendor": ["vendor", "supplier", "contractor", "party"],
    "end_date": ["expiry", "expire", "expires", "renewal", "valid until",
                 "valid", "end"],
    "value": ["value", "amount", "cost", "price"],
    "owner": ["owner", "department", "dept", "location", "custodian"],
    "contact": ["email", "e-mail", "contact"],
}


def guess_column(headers, logical: str) -> str | None:
    """Best-guess the header for a logical field. Matches keywords at a WORD
    BOUNDARY, not as a bare substring — otherwise the keyword "end" would match
    "Vendor" (v-end-or) and steal the expiry column. Returns None if nothing fits."""
    import re
    for kw in COLUMN_KEYWORDS.get(logical, []):
        pat = re.compile(r"\b" + re.escape(kw))
        for h in headers:
            if pat.search(str(h).lower()):
                return h
    return None


@dataclass
class AmcRecord:
    asset: str
    vendor: str
    end_date: date | None
    days_left: int | None
    status: str
    action: str
    value: float | None = None
    owner: str = ""
    contact: str = ""
    raw: dict = field(default_factory=dict)


def _parse_date(value):
    """Parse a cell into a date. Handles real datetime/date objects (as openpyxl
    returns for Excel date cells) and common string formats, Indian day-first
    first (so 03/07/2026 is 3 July, not 7 March)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
                "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y",
                "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _to_float(value):
    if value is None or value == "":
        return None
    import re
    m = re.search(r"[\d,]+(?:\.\d+)?", str(value))
    return float(m.group(0).replace(",", "")) if m else None


def _classify(days_left, critical_days, due_days):
    if days_left is None:
        # No readable expiry date — treat as needing attention, not "OK".
        return STATUS_CRITICAL
    if days_left < 0:
        return STATUS_EXPIRED
    if days_left <= critical_days:
        return STATUS_CRITICAL
    if days_left <= due_days:
        return STATUS_DUE
    return STATUS_OK


def analyze_amc(rows: list[dict], col_map: dict, today: date | None = None,
                critical_days: int = 15, due_days: int = 45) -> dict:
    """Score an AMC register.

    rows    : list of {column_name: value} (e.g. DataFrame.to_dict("records")).
    col_map : maps logical fields to actual column names. Required keys:
              "asset", "vendor", "end_date". Optional: "value", "owner", "contact".
    Returns {records, needs_action, summary, today}.
    """
    today = today or date.today()
    records: list[AmcRecord] = []

    def cell(row, key):
        col = col_map.get(key)
        return row.get(col) if col else None

    for row in rows:
        end_date = _parse_date(cell(row, "end_date"))
        days_left = (end_date - today).days if end_date else None
        status = _classify(days_left, critical_days, due_days)
        records.append(AmcRecord(
            asset=str(cell(row, "asset") or "").strip() or "(unnamed asset)",
            vendor=str(cell(row, "vendor") or "").strip() or "(unknown vendor)",
            end_date=end_date,
            days_left=days_left,
            status=status,
            action=_ACTION[status],
            value=_to_float(cell(row, "value")),
            owner=str(cell(row, "owner") or "").strip(),
            contact=str(cell(row, "contact") or "").strip(),
            raw=row,
        ))

    # Rank: most urgent bucket first, then soonest expiry within the bucket.
    def sort_key(r: AmcRecord):
        days = r.days_left if r.days_left is not None else -10_000
        return (_STATUS_RANK[r.status], days)

    records.sort(key=sort_key)
    needs_action = [r for r in records if r.status != STATUS_OK]

    at_risk_value = sum(
        r.value for r in records
        if r.status in (STATUS_EXPIRED, STATUS_CRITICAL) and r.value
    )
    summary = {
        "total": len(records),
        STATUS_EXPIRED: sum(1 for r in records if r.status == STATUS_EXPIRED),
        STATUS_CRITICAL: sum(1 for r in records if r.status == STATUS_CRITICAL),
        STATUS_DUE: sum(1 for r in records if r.status == STATUS_DUE),
        STATUS_OK: sum(1 for r in records if r.status == STATUS_OK),
        "at_risk_value": at_risk_value,
    }
    return {"records": records, "needs_action": needs_action,
            "summary": summary, "today": today}


def records_to_csv(result: dict) -> bytes:
    """A downloadable action report — every contract with its status and action,
    most urgent first. Pure stdlib, so it always works."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Status", "Days left", "Asset", "Vendor", "Expiry", "Action",
                "Contract value", "Owner", "Vendor contact"])
    for r in result["records"]:
        w.writerow([
            r.status,
            "" if r.days_left is None else r.days_left,
            r.asset, r.vendor,
            r.end_date.isoformat() if r.end_date else "",
            r.action,
            "" if r.value is None else r.value,
            r.owner, r.contact,
        ])
    return buf.getvalue().encode("utf-8")


def _facts_block(r: AmcRecord) -> str:
    lines = [
        f"Asset / equipment: {r.asset}",
        f"Vendor: {r.vendor}",
        f"AMC expiry date: {r.end_date.isoformat() if r.end_date else 'NOT STATED'}",
        f"Days until expiry: {r.days_left if r.days_left is not None else 'unknown'}",
        f"Status: {r.status}",
    ]
    if r.value:
        lines.append(f"Contract value: {r.value:,.0f}")
    if r.owner:
        lines.append(f"Internal owner: {r.owner}")
    if r.contact:
        lines.append(f"Vendor contact: {r.contact}")
    return "\n".join(lines)


def _template_email(r: AmcRecord, sender: str) -> str:
    """Deterministic reminder email — works with no LLM, and is the fallback."""
    if r.days_left is not None and r.days_left < 0:
        timing = (f"expired on {r.end_date.isoformat()} "
                  f"({abs(r.days_left)} days ago)")
        ask = ("As the contract has already lapsed, please treat this as urgent "
               "and confirm renewal terms at the earliest to avoid a coverage gap.")
    elif r.end_date:
        timing = f"is due to expire on {r.end_date.isoformat()} ({r.days_left} days away)"
        ask = ("Kindly confirm the renewal terms and share an updated quotation so "
               "we can process the renewal before expiry.")
    else:
        timing = "has no expiry date on record"
        ask = ("Kindly confirm the current AMC status, coverage period, and renewal "
               "terms so we can update our records.")

    contact_line = f"\nVendor contact on file: {r.contact}" if r.contact else ""
    return (
        f"Subject: AMC Renewal — {r.asset} ({r.vendor})\n\n"
        f"Dear {r.vendor},\n\n"
        f"The Annual Maintenance Contract for {r.asset} {timing}. {ask}\n\n"
        f"Please reply to this email with the renewal quotation and revised terms. "
        f"If anything has changed on the service scope or pricing, do let us know.\n\n"
        f"Thank you,\n{sender}"
        f"{contact_line}"
    )


def draft_reminder(r: AmcRecord, provider=None, sender: str = "Procurement Team, "
                   "SELCO Foundation") -> str:
    """Draft a renewal reminder email. The LLM (if any) writes it from the exact
    computed facts; without an LLM, a deterministic template is used."""
    template = _template_email(r, sender)
    if provider is None:
        return template
    instruction = (
        "Write a short, courteous AMC (Annual Maintenance Contract) renewal "
        f"reminder email from '{sender}', addressed to the vendor. Request an "
        "updated renewal quotation and confirmation of terms, and reflect the "
        "urgency implied by the status. Keep it under 150 words. Use ONLY the "
        "facts below — do NOT invent any dates, figures, or terms. Start with a "
        "'Subject:' line.\n\n" + _facts_block(r)
    )
    try:
        drafted = provider.write(instruction)
    except Exception:
        drafted = ""
    return drafted or template
