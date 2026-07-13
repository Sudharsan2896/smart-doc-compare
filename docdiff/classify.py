"""
Spend Classifier — sort purchase line items into spend categories.

Given item descriptions (from a PO register, invoice list, or a pasted column),
it assigns each to a spend category so procurement analytics and GL coding stop
being manual. Deterministic keyword rules do the bulk; anything they can't place
is optionally sent to an LLM — but the LLM must choose from the SAME fixed list of
categories, so it can classify but never invent a category. Whatever still can't
be placed is flagged "Uncategorised" for manual review, never guessed.

Phase-0 rule: the set of allowed categories and the keyword rules are fixed code.
The LLM's decision space is bounded to that list; low confidence routes to a human.
"""

from __future__ import annotations

# Category -> keywords. Order matters only for display; scoring counts hits.
CATEGORY_KEYWORDS = {
    "Solar & Energy": ["solar", "panel", "pv module", "inverter", "battery",
                       "ups", "generator", "dg set", "kva"],
    "IT & Electronics": ["computer", "laptop", "desktop", "printer", "server",
                         "router", "software", "license", "monitor", "biometric",
                         "cctv", "projector"],
    "Office Supplies": ["stationery", "paper", "toner", "cartridge", "pen",
                        "file", "register", "envelope"],
    "Furniture": ["chair", "table", "desk", "cupboard", "furniture", "almirah",
                  "rack", "shelf"],
    "Vehicles & Logistics": ["vehicle", "transport", "freight", "fuel", "diesel",
                             "petrol", "tyre", "logistics", "courier", "shipping"],
    "Civil & Construction": ["cement", "brick", "construction", "civil", "paint",
                            "plumbing", "electrical work", "flooring", "roofing"],
    "Water & Sanitation": ["water", "ro system", "purifier", "pump", "sanitation",
                          "filter", "borewell"],
    "Medical & Health": ["medical", "medicine", "health", "diagnostic", "hospital",
                        "vaccine", "surgical"],
    "Professional Services": ["consultant", "consultancy", "audit", "legal",
                            "training", "professional", "advisory"],
    "Maintenance / AMC": ["amc", "annual maintenance", "maintenance", "repair",
                         "service contract", "servicing"],
    "Utilities": ["electricity", "power bill", "internet", "telephone", "mobile",
                 "utility", "broadband"],
}

UNCATEGORISED = "Uncategorised"
_LLM_CONFIDENCE = 0.6


def _classify_rules(description: str) -> tuple[str | None, float]:
    text = (description or "").lower()
    if not text.strip():
        return None, 0.0
    best_cat, best_hits = None, 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best_cat, best_hits = cat, hits
    if best_cat is None:
        return None, 0.0
    # More keyword hits -> more confidence.
    conf = 0.9 if best_hits >= 2 else 0.7
    return best_cat, conf


def _classify_llm(description: str, provider) -> str | None:
    """Ask the LLM to pick ONE category from the fixed list. Returns a valid
    category or None — never a made-up one."""
    cats = list(CATEGORY_KEYWORDS.keys())
    instruction = (
        "Classify the purchase item below into exactly ONE of these spend "
        "categories. Reply with ONLY the category name, exactly as written, and "
        "nothing else. If none fit, reply 'Uncategorised'.\n\n"
        "Categories:\n" + "\n".join(f"- {c}" for c in cats)
        + f"\n\nItem: {description}"
    )
    try:
        raw = (provider.write(instruction) or "").strip()
    except Exception:
        return None
    # Accept only an exact (case-insensitive) match against the allowed list.
    for c in cats:
        if raw.lower() == c.lower() or raw.lower().startswith(c.lower()):
            return c
    return None


def classify_item(description: str, provider=None) -> dict:
    cat, conf = _classify_rules(description)
    method = "rules"
    if cat is None and provider is not None:
        llm_cat = _classify_llm(description, provider)
        if llm_cat:
            cat, conf, method = llm_cat, _LLM_CONFIDENCE, "ai"
    if cat is None:
        cat, conf, method = UNCATEGORISED, 0.0, "none"
    return {"description": description, "category": cat,
            "confidence": conf, "method": method}


def classify_items(descriptions, provider=None) -> list[dict]:
    return [classify_item(d, provider) for d in descriptions]


def category_summary(results: list[dict]) -> dict:
    """Counts per category, for a quick spend-mix view."""
    counts: dict = {}
    for r in results:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def results_to_csv(results: list[dict]) -> bytes:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Description", "Category", "Confidence", "Method"])
    for r in results:
        w.writerow([r["description"], r["category"], r["confidence"], r["method"]])
    return buf.getvalue().encode("utf-8")
