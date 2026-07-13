"""
Benchmark Assistant — "is this price reasonable?" against your own history.

Given an item and a proposed price, it retrieves the most relevant passages from
the knowledge base (the same RAG index you built from past quotes/POs), pulls the
monetary figures out of them, and compares the proposed price to the historical
median.

Phase-0 rule, again: the DECISION — the median, the % above/below, the verdict —
is deterministic arithmetic on the numbers found in your documents. The LLM (if
any) only writes a sentence of context around it; it never sets the benchmark.
And every figure is traceable: the retrieved passages are returned so a buyer can
see exactly which documents the benchmark came from.

Honest about its limits: it benchmarks against the monetary figures in matching
documents, so it's indicative, not a like-for-like unit price. Showing the sources
is what makes it trustworthy despite that.
"""

from __future__ import annotations

import re

# Currency-marked amounts (₹ / Rs / INR ...) — the figures worth benchmarking.
_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)


def _amounts(text: str) -> list[float]:
    out = []
    for m in _AMOUNT_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def benchmark_price(kb, item: str, proposed_price: float | None = None,
                    provider=None, k: int = 6) -> dict:
    """Benchmark `proposed_price` for `item` against figures in the knowledge base.

    Returns {proposed, count, median, min, max, pct_vs_median, verdict, sources,
    narrative}. `count` is 0 when nothing comparable was found."""
    hits = kb.query(item, k=k) if (kb is not None and not kb.is_empty()) else []

    prices: list[float] = []
    sources: list[dict] = []
    for h in hits:
        vals = _amounts(h.chunk.text)
        if vals:
            prices.extend(vals)
            sources.append({"source": h.chunk.source, "snippet": h.chunk.text,
                            "amounts": vals, "score": round(h.score, 3)})

    # When a proposed price is given, keep figures of a comparable magnitude
    # (drops GST lines / tiny unit prices that would skew the median).
    comparable = prices
    if proposed_price:
        band = [p for p in prices if proposed_price * 0.2 <= p <= proposed_price * 5]
        if band:
            comparable = band

    result: dict = {"proposed": proposed_price, "sources": sources,
                    "count": len(comparable), "narrative": ""}
    if not comparable:
        result["verdict"] = ("No comparable historical prices were found in the "
                             "knowledge base for this item.")
        return result

    median = _median(comparable)
    result.update({"median": median, "min": min(comparable), "max": max(comparable)})

    if proposed_price and median:
        pct = (proposed_price - median) / median * 100.0
        result["pct_vs_median"] = pct
        if pct <= -5:
            result["verdict"] = (f"Proposed price is {abs(pct):.0f}% BELOW the "
                                 f"historical median (₹{median:,.0f}) — favourable.")
        elif pct < 5:
            result["verdict"] = ("Proposed price is in line with the historical "
                                 f"median (₹{median:,.0f}).")
        else:
            result["verdict"] = (f"Proposed price is {pct:.0f}% ABOVE the "
                                 f"historical median (₹{median:,.0f}) — review "
                                 "before accepting.")
    else:
        result["verdict"] = (f"Historical median is ₹{median:,.0f} "
                             f"(from {len(comparable)} figures).")

    if provider is not None:
        result["narrative"] = _narrate(result, item, provider)
    return result


def _narrate(result: dict, item: str, provider) -> str:
    facts = [
        f"Item: {item}",
        f"Proposed price: {result.get('proposed')}",
        f"Historical figures compared: {result['count']}",
        f"Median: {result.get('median')}",
        f"Range: {result.get('min')} to {result.get('max')}",
        f"Deterministic verdict: {result['verdict']}",
    ]
    instruction = (
        "You are a procurement analyst. In 2-3 sentences, explain this price "
        "benchmark to a buyer and suggest what to check. Use ONLY the facts below "
        "— do not invent figures. Do not contradict the deterministic verdict.\n\n"
        + "\n".join(facts)
    )
    try:
        return provider.write(instruction).strip()
    except Exception:
        return ""
