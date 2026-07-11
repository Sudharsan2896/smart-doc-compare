"""
RFQ Agent — the end-to-end procurement loop, composing the other pieces.

The flow this drives:

    1. Capture a REQUIREMENT (what to buy, how much, by when).
    2. DRAFT an RFQ email to each vendor (provider.write, or a template).
       → human gate: you review and send them.
    3. INGEST the vendor replies and SCORE them  (quote_intelligence.analyze_quotes
       — reused verbatim; the weighted procurement score is the SAME engine as the
       AI Quote Analysis tool).
    4. Optionally fold in HISTORICAL CONTEXT retrieved from the knowledge base
       (RAG) — "what did we get from these vendors last time?".
    5. Produce an AWARD RECOMMENDATION MEMO.
       → human gate: approval required before award.

The Phase-0 rule is the backbone here, and it matters most at the award step:

    The RECOMMENDED AWARDEE is the vendor at the top of the deterministic weighted
    score (analyze_quotes), NOT a vendor the LLM chose. The LLM only writes the
    memo prose around that decision, from facts the code computed, and is told not
    to override the ranking or invent figures. So the award you can defend to a
    committee always traces to the score, not to a model's opinion.

This module is deliberately decoupled: it takes the already-computed `analysis`
dict rather than importing the scorer, so it stays pure-Python and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Requirement:
    title: str
    items: str          # free-text list of items / specifications (multiline)
    quantity: str = ""
    needed_by: str = ""
    budget: str = ""
    notes: str = ""
    buyer: str = "Procurement Team, SELCO Foundation"


def _req_block(req: Requirement) -> str:
    lines = [f"Requirement: {req.title}",
             f"Items / specifications:\n{req.items}"]
    if req.quantity:
        lines.append(f"Quantity: {req.quantity}")
    if req.needed_by:
        lines.append(f"Required by: {req.needed_by}")
    if req.budget:
        lines.append(f"Indicative budget: {req.budget}")
    if req.notes:
        lines.append(f"Notes: {req.notes}")
    return "\n".join(lines)


def _rfq_template(req: Requirement, vendor: str) -> str:
    parts = [
        f"Subject: Request for Quotation — {req.title}",
        "",
        f"Dear {vendor},",
        "",
        f"{req.buyer} invites your quotation for the following requirement:",
        "",
        f"Items / specifications:\n{req.items}",
    ]
    if req.quantity:
        parts.append(f"\nQuantity: {req.quantity}")
    if req.needed_by:
        parts.append(f"Required by: {req.needed_by}")
    if req.budget:
        parts.append(f"Indicative budget: {req.budget}")
    parts.append(
        "\nPlease include unit prices, GST / taxes, delivery timeline, warranty, "
        "payment terms, and the validity period of your offer.")
    if req.notes:
        parts.append(f"\nNotes: {req.notes}")
    if req.needed_by:
        parts.append(f"\nKindly send your quotation by {req.needed_by}.")
    parts.append(f"\nThank you,\n{req.buyer}")
    return "\n".join(parts)


def draft_rfq(req: Requirement, vendor: str, provider=None) -> str:
    """Draft an RFQ email to one vendor. The LLM writes it from the requirement
    facts; without an LLM a deterministic template is used."""
    template = _rfq_template(req, vendor)
    if provider is None:
        return template
    instruction = (
        f"Write a professional Request for Quotation (RFQ) email from "
        f"'{req.buyer}' to the vendor '{vendor}', based ONLY on the details below. "
        "Ask the vendor to quote unit prices, GST/taxes, delivery timeline, "
        "warranty, payment terms, and offer validity. Keep it under 180 words, "
        "start with a 'Subject:' line, and do NOT invent specifications, "
        "quantities, or dates.\n\n" + _req_block(req)
    )
    try:
        drafted = provider.write(instruction)
    except Exception:
        drafted = ""
    return drafted or template


def _comparison_rows(analysis: dict) -> list[dict]:
    """Flatten analyze_quotes output into one row per vendor for the memo table."""
    by_name = {v["name"]: v for v in analysis.get("vendors", [])}
    score_by_name = {s["name"]: s["total_score"] for s in analysis.get("scores", [])}
    rows = []
    for r in analysis.get("ranking", []):
        v = by_name.get(r["name"], {})
        rows.append({
            "vendor": r["name"],
            "total": v.get("total"),
            "delivery_days": v.get("delivery_days"),
            "warranty_months": v.get("warranty_months"),
            "score": score_by_name.get(r["name"], r.get("total_score")),
        })
    return rows


def _facts_text(req: Requirement, analysis: dict, history: str = "") -> str:
    """The grounded fact sheet handed to the LLM for the memo — requirement,
    the ranked comparison, risks, and any retrieved history. Nothing invented."""
    rows = _comparison_rows(analysis)
    lines = [_req_block(req), "", "Ranked comparison (highest weighted score first):"]
    for i, row in enumerate(rows, 1):
        total = f"{row['total']:,.0f}" if row["total"] else "n/a"
        deliv = f"{int(row['delivery_days'])}d" if row["delivery_days"] else "n/a"
        warr = f"{int(row['warranty_months'])}mo" if row["warranty_months"] else "n/a"
        score = f"{row['score']:.0f}/100" if row["score"] is not None else "n/a"
        lines.append(f"  {i}. {row['vendor']} — total {total}, delivery {deliv}, "
                     f"warranty {warr}, weighted score {score}")
    risks = analysis.get("risk", [])
    if risks:
        lines.append("\nRisk / documentation notes:")
        lines += [f"  - {b}" for b in risks]
    if history.strip():
        lines.append("\nHistorical context from past records (may be partial):")
        lines.append(history.strip())
    return "\n".join(lines)


def _deterministic_memo(req: Requirement, analysis: dict, awardee: str) -> str:
    """A committee-ready memo assembled from the computed facts — works with no
    LLM, and is the fallback for the LLM version."""
    rows = _comparison_rows(analysis)
    out = [
        f"# Award Recommendation — {req.title}",
        "",
        f"**Requirement:** {req.items.strip()}"
        + (f"  \n**Quantity:** {req.quantity}" if req.quantity else "")
        + (f"  \n**Required by:** {req.needed_by}" if req.needed_by else ""),
        "",
        f"**{len(rows)} quotation(s) were evaluated on the weighted procurement "
        "score (cost, delivery, warranty, payment, technical compliance).**",
        "",
        "| Rank | Vendor | Total | Delivery | Warranty | Score |",
        "|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(rows, 1):
        total = f"{row['total']:,.0f}" if row["total"] else "—"
        deliv = f"{int(row['delivery_days'])}d" if row["delivery_days"] else "—"
        warr = f"{int(row['warranty_months'])}mo" if row["warranty_months"] else "—"
        score = f"{row['score']:.0f}" if row["score"] is not None else "—"
        out.append(f"| {i} | {row['vendor']} | {total} | {deliv} | {warr} | {score} |")

    out += ["", f"## Recommendation: **{awardee}**", ""]
    if rows:
        top = rows[0]
        why = []
        if top["total"]:
            cheapest = min((r for r in rows if r["total"]), key=lambda r: r["total"],
                           default=None)
            if cheapest and cheapest["vendor"] == awardee:
                why.append("lowest total cost")
        if top["warranty_months"]:
            best_w = max((r for r in rows if r["warranty_months"]),
                         key=lambda r: r["warranty_months"], default=None)
            if best_w and best_w["vendor"] == awardee:
                why.append(f"longest warranty ({int(top['warranty_months'])} months)")
        reason = (" It offers the " + " and the ".join(why) + "." if why
                  else " It ranks highest on the overall weighted score.")
        out.append(f"{awardee} ranks highest at {top['score']:.0f}/100 and offers "
                   f"the best overall value.{reason}")
    risks = analysis.get("risk", [])
    if risks:
        out += ["", "## Risks / documentation gaps to close before award", ""]
        out += [f"- {b}" for b in risks]
    out += ["", "_Award is subject to committee approval._"]
    return "\n".join(out)


def build_award_memo(req: Requirement, analysis: dict, provider=None,
                     history: str = "") -> dict:
    """Produce the award recommendation. The awardee is decided by the score
    (deterministic); the LLM only writes the narrative. Returns
    {awardee, memo, used_llm}."""
    ranking = analysis.get("ranking", [])
    if not ranking:
        return {"awardee": None,
                "memo": "No quotations could be analysed — nothing to recommend.",
                "used_llm": False}

    awardee = ranking[0]["name"]
    deterministic = _deterministic_memo(req, analysis, awardee)

    llm_memo = ""
    if provider is not None:
        instruction = (
            "You are a procurement analyst writing an award recommendation memo for "
            f"a committee. Recommend **{awardee}** — this is the vendor with the "
            "highest weighted procurement score; do NOT override this choice, even "
            "if another vendor looks cheaper on one axis. Explain the trade-offs, "
            "cite figures exactly as given, and list any risks to close before "
            "award. Use ONLY the facts below — do not invent prices, dates, or "
            "terms. Write in clear markdown with a short recommendation up front.\n\n"
            + _facts_text(req, analysis, history)
        )
        try:
            llm_memo = provider.write(instruction)
        except Exception:
            llm_memo = ""

    return {"awardee": awardee,
            "memo": llm_memo or deterministic,
            "used_llm": bool(llm_memo)}
