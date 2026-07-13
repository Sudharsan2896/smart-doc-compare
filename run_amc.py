#!/usr/bin/env python3
"""
Headless AMC monitor — the autonomous half of the AMC Renewal Monitor.

Runs on a schedule (see .github/workflows/amc-monitor.yml), reads an AMC register,
decides what needs action, and produces a digest of the contracts that are
expired / critical / due for renewal, each with a ready-to-send reminder draft.

By design it does NOT email vendors automatically — auto-sending outward mail from
an unattended job is exactly the kind of hard-to-reverse action a human should
approve. Instead it delivers an INTERNAL digest to the procurement inbox (and the
GitHub Actions run summary) so a person reviews the drafts and sends them.

Everything here is standard library only when using template drafts (the default),
so the scheduled job needs no pip install. Set AMC_USE_LLM=1 (and provide an LLM
key + install the SDK) to have the drafts written by Claude/Gemini instead.

Configuration (all via environment variables):
    AMC_REGISTER_PATH   path to the register (.csv or .xlsx). Default: the sample.
    AMC_CRITICAL_DAYS   flag "Critical" within N days of expiry (default 15).
    AMC_DUE_DAYS        flag "Due soon" within N days of expiry (default 45).
    AMC_COL_ASSET / _VENDOR / _END / _VALUE / _OWNER / _CONTACT
                        override auto-detected column names if your headers differ.
    AMC_USE_LLM         "1" to draft with an LLM (needs a key + SDK); else templates.
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
    AMC_EMAIL_FROM / AMC_EMAIL_TO
                        set all of host/user/password/to to email the digest.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from docdiff.amc import analyze_amc, draft_reminder, guess_column, records_to_csv

DEFAULT_REGISTER = os.path.join("samples", "amc", "amc_register.csv")

# Per-column env overrides; auto-detection (word-boundary keyword match) lives in
# docdiff.amc.guess_column so the UI and this job detect columns identically.
_COL_ENV = {
    "asset": "AMC_COL_ASSET", "vendor": "AMC_COL_VENDOR", "end_date": "AMC_COL_END",
    "value": "AMC_COL_VALUE", "owner": "AMC_COL_OWNER", "contact": "AMC_COL_CONTACT",
}


def env(name: str, default: str = "") -> str:
    """Like os.environ.get, but treats an EMPTY value as unset. GitHub Actions
    substitutes an unset `${{ vars.X }}` as an empty string, so a plain
    os.environ.get(name, default) would return "" instead of the default."""
    value = os.environ.get(name)
    return value if (value is not None and value.strip()) else default


def read_register(path: str) -> list[dict]:
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if ext == "csv":
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    if ext == "xlsx":
        import openpyxl  # only needed for Excel registers
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h) if h is not None else "" for h in rows[0]]
        return [dict(zip(headers, r)) for r in rows[1:]]
    raise SystemExit(f"Unsupported register format: {path!r} (use .csv or .xlsx)")


def build_col_map(headers: list[str]) -> dict:
    cmap: dict = {}
    for logical, env_name in _COL_ENV.items():
        override = os.environ.get(env_name)
        if override and override in headers:
            cmap[logical] = override
            continue
        found = guess_column(headers, logical)
        if found:
            cmap[logical] = found
    return cmap


def get_provider():
    """An LLM provider for drafting, only if explicitly enabled and available.
    Otherwise None → deterministic template drafts (no third-party deps)."""
    if os.environ.get("AMC_USE_LLM", "").lower() not in ("1", "true", "yes"):
        return None
    try:
        from docdiff.ai_providers import ClaudeProvider, GeminiProvider
    except Exception:
        return None
    for factory in (ClaudeProvider, GeminiProvider):
        try:
            p = factory()
            if p.available():
                print(f"Drafting with {p.name}.")
                return p
        except Exception:
            continue
    print("AMC_USE_LLM set but no usable LLM found — using template drafts.")
    return None


def build_digest(result: dict, provider) -> str:
    """A Markdown digest: summary, the action table, and a reminder draft for each
    contract that needs one. Used for the email body, the report file, and the
    GitHub Actions run summary."""
    s = result["summary"]
    today = result["today"].isoformat()
    needs = result["needs_action"]
    out = [
        f"# AMC Renewal Monitor — {today}",
        "",
        f"- 🔴 Expired: **{s['Expired']}**",
        f"- 🟠 Critical: **{s['Critical']}**",
        f"- 🟡 Due soon: **{s['Due soon']}**",
        f"- ⚪ OK: **{s['OK']}**",
        f"- 💰 Value at risk (expired + critical): **{s['at_risk_value']:,.0f}**",
        "",
        f"**{len(needs)} of {s['total']} contract(s) need action.**",
        "",
    ]
    if not needs:
        out.append("_Nothing needs action today._")
        return "\n".join(out)

    out += ["| Status | Days left | Asset | Vendor | Expiry | Action |",
            "|---|---|---|---|---|---|"]
    for r in needs:
        dl = "—" if r.days_left is None else r.days_left
        exp = r.end_date.isoformat() if r.end_date else "—"
        out.append(f"| {r.status} | {dl} | {r.asset} | {r.vendor} | {exp} | {r.action} |")

    out += ["", "---", "", "## Reminder drafts (review before sending)", ""]
    for r in needs:
        out.append(f"### {r.asset} — {r.vendor} ({r.status})")
        out.append("")
        out.append("```")
        out.append(draft_reminder(r, provider))
        out.append("```")
        out.append("")
    return "\n".join(out)


def maybe_send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("AMC_EMAIL_TO")
    if not (host and user and password and to):
        print("SMTP not fully configured — skipping email "
              "(digest is still written to the report and the run summary).")
        return False
    port = int(env("SMTP_PORT", "587"))
    sender = env("AMC_EMAIL_FROM", user)

    import smtplib
    import ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(user, password)
            server.send_message(msg)
        print(f"Digest emailed to {to}.")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"Email send failed: {e}")
        return False


def _write_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(markdown + "\n")
        except Exception:
            pass


def main() -> int:
    path = env("AMC_REGISTER_PATH", DEFAULT_REGISTER)
    if not os.path.exists(path):
        print(f"AMC register not found: {path}", file=sys.stderr)
        return 1

    rows = read_register(path)
    if not rows:
        print(f"Register {path} has no data rows.", file=sys.stderr)
        return 1

    col_map = build_col_map(list(rows[0].keys()))
    missing = [k for k in ("asset", "vendor", "end_date") if k not in col_map]
    if missing:
        print(f"Could not identify required column(s): {missing}. "
              f"Set AMC_COL_* env vars. Headers seen: {list(rows[0].keys())}",
              file=sys.stderr)
        return 1

    critical_days = int(env("AMC_CRITICAL_DAYS", "15"))
    due_days = int(env("AMC_DUE_DAYS", "45"))
    provider = get_provider()

    result = analyze_amc(rows, col_map, today=date.today(),
                         critical_days=critical_days, due_days=due_days)
    digest = build_digest(result, provider)
    needs = result["needs_action"]

    # Always: print to logs, write report artifacts, add to the run summary.
    print(digest)
    with open("amc_digest.md", "w", encoding="utf-8") as f:
        f.write(digest)
    with open("amc_action_report.csv", "wb") as f:
        f.write(records_to_csv(result))
    _write_step_summary(digest)

    # Deliver the internal digest by email when SMTP is configured.
    today = result["today"].isoformat()
    subject = (f"AMC Monitor: {len(needs)} contract(s) need attention ({today})"
               if needs else f"AMC Monitor: all clear ({today})")
    maybe_send_email(subject, digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
