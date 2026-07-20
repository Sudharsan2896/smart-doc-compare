"""Smoke tests for the deterministic procurement engines.

These cover the parts that must never silently break: the vendor scoring/ranking,
AMC classification and column detection, RAG chunking/retrieval/round-trip, and
the RFQ award (awardee = top of the weighted score). They are all pure-Python and
run with only `pytest` installed — no numpy, pandas, or model — so they mirror CI
and stay fast. The LLM-backed paths are exercised separately with a stub provider
that checks the prompt contract, not the model output.
"""

from datetime import date
from pathlib import Path

import pytest

from docdiff.ai_providers import AIProvider, LocalHeuristicProvider
from docdiff.quote_intelligence import analyze_quotes
from docdiff.amc import analyze_amc, draft_reminder, guess_column
from docdiff.rag import KnowledgeBase
from docdiff.rfq import Requirement, draft_rfq, build_award_memo
from docdiff.summary import narrative_from_summary, summarize_changes
from docdiff.segment import segment, Segment
from docdiff.numbers import extract_numbers, diff_numbers
from docdiff.align import Pair
from docdiff.compare import compare_pairs
from docdiff.benchmark import benchmark_price, _amounts, _median
from docdiff.classify import (
    classify_item, classify_items, category_summary, UNCATEGORISED,
)

REPO = Path(__file__).resolve().parents[1]
QUOTES = REPO / "samples" / "quotes"
AMC_CSV = REPO / "samples" / "amc" / "amc_register.csv"

AMC_COLS = {"asset": "Asset", "vendor": "Vendor", "end_date": "AMC End",
            "value": "Contract Value", "owner": "Owner", "contact": "Vendor Email"}


@pytest.fixture(scope="module")
def quote_files():
    files = [(p.name, p.read_bytes()) for p in sorted(QUOTES.glob("*.txt"))]
    assert len(files) == 3, "expected the three sample quotes"
    return files


@pytest.fixture(scope="module")
def amc_rows():
    import csv
    with open(AMC_CSV, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --- quote scoring -----------------------------------------------------------
def test_quote_ranking_order(quote_files):
    analysis = analyze_quotes(quote_files, LocalHeuristicProvider())
    order = [r["name"] for r in analysis["ranking"]]
    # SunPower (cheapest) ranks above GreenVolt, both above the gap-ridden Bright.
    assert order[0].startswith("SunPower")
    assert order[1].startswith("GreenVolt")
    assert order[2].startswith("Bright")
    # The recommendation must not be empty and must name the cheapest vendor.
    assert "SunPower" in analysis["recommendation"]


def test_quote_risk_flags_missing_fields(quote_files):
    analysis = analyze_quotes(quote_files, LocalHeuristicProvider())
    risk_text = " ".join(analysis["risk"])
    assert "Bright" in risk_text  # the quote missing GST/delivery/warranty


# --- AMC engine --------------------------------------------------------------
def test_amc_classification_and_value_at_risk(amc_rows):
    res = analyze_amc(amc_rows, AMC_COLS, today=date(2026, 7, 11))
    s = res["summary"]
    assert (s["Expired"], s["Critical"], s["Due soon"], s["OK"]) == (2, 3, 2, 2)
    assert s["at_risk_value"] == 237000.0
    assert len(res["needs_action"]) == 7
    # Most urgent (expired, most negative days) sorts first.
    assert res["records"][0].status == "Expired"


def test_amc_guess_column_word_boundary():
    headers = ["Asset", "Vendor", "AMC Start", "AMC End", "Contract Value",
               "Owner", "Vendor Email"]
    # "end" must not match "Vendor" (v-END-or); it must map to the real column.
    assert guess_column(headers, "end_date") == "AMC End"
    assert guess_column(headers, "vendor") == "Vendor"
    assert guess_column(headers, "contact") == "Vendor Email"


def test_amc_blank_date_is_critical():
    rows = [{"Asset": "UPS", "Vendor": "Acme", "AMC End": ""}]
    res = analyze_amc(rows, {"asset": "Asset", "vendor": "Vendor",
                             "end_date": "AMC End"}, today=date(2026, 7, 11))
    assert res["records"][0].status == "Critical"


def test_amc_template_reminder_mentions_asset_and_vendor():
    rows = [{"Asset": "Solar Pump", "Vendor": "GreenVolt", "AMC End": "30/05/2026"}]
    res = analyze_amc(rows, {"asset": "Asset", "vendor": "Vendor",
                             "end_date": "AMC End"}, today=date(2026, 7, 11))
    email = draft_reminder(res["records"][0], provider=None)
    assert "Subject:" in email
    assert "Solar Pump" in email and "GreenVolt" in email


# --- RAG knowledge base ------------------------------------------------------
def _build_kb():
    kb = KnowledgeBase()
    for p in sorted(QUOTES.glob("*.txt")):
        kb.add_document(p.read_bytes(), p.name)
    kb.build()
    return kb


def test_rag_retrieval_finds_right_vendor():
    kb = _build_kb()
    hits = kb.query("What warranty did GreenVolt offer?", k=3)
    assert any(h.chunk.source == "quote_greenvolt.txt" for h in hits)


def test_rag_roundtrip_preserves_query_results():
    kb = _build_kb()
    blob = kb.to_bytes()
    assert blob[:1] == b"{"  # JSON, not pickle
    kb2 = KnowledgeBase.from_bytes(blob)
    q = "delivery timeline"
    before = [(h.chunk.source, round(h.score, 4)) for h in kb.query(q, k=3)]
    after = [(h.chunk.source, round(h.score, 4)) for h in kb2.query(q, k=3)]
    assert before == after
    assert len(kb2.chunks) == len(kb.chunks)


# --- RFQ agent ---------------------------------------------------------------
def test_rfq_award_is_top_of_score(quote_files):
    analysis = analyze_quotes(quote_files, LocalHeuristicProvider())
    req = Requirement(title="Solar equipment", items="panels; inverter")
    memo = build_award_memo(req, analysis, provider=None)
    assert memo["awardee"] == analysis["ranking"][0]["name"]
    assert memo["used_llm"] is False
    assert memo["awardee"] in memo["memo"]


def test_rfq_draft_has_subject_and_vendor():
    req = Requirement(title="Solar panels", items="330Wp x40")
    draft = draft_rfq(req, "GreenVolt Energy", provider=None)
    assert draft.startswith("Subject:")
    assert "GreenVolt Energy" in draft


# --- doc-compare AI summary layer --------------------------------------------
def test_summary_narrative_fallback_and_grounding():
    summary = {"total": 3, "commercial": ["Pricing changed from 100 to 120."],
               "legal": [], "operational": [], "risks": ["Warranty period reduced."]}
    empty = {"total": 0, "commercial": [], "legal": [], "operational": [], "risks": []}

    # No LLM -> "" (the rule-based summary is shown on its own).
    assert narrative_from_summary(summary, None) == ""

    # Capture the prompt to assert on it OUTSIDE write() — narrative_from_summary
    # swallows exceptions (graceful fallback), so asserting inside write() would be
    # silently turned into an empty result instead of a test failure.
    captured = {}

    class StubLLM(AIProvider):
        name = "stub"

        def available(self):
            return True

        def extract(self, *a, **k):
            return {}

        def write(self, instruction):
            captured["prompt"] = instruction
            return "Cost rose and warranty shrank; verify both before signing."

    # Empty summary -> "" even with an LLM (nothing to narrate); write() not called.
    assert narrative_from_summary(empty, StubLLM()) == ""
    assert "prompt" not in captured

    out = narrative_from_summary(summary, StubLLM())
    assert "warranty" in out.lower()
    assert "Pricing changed from 100 to 120" in captured["prompt"]  # grounded
    assert "invent" in captured["prompt"].lower()                    # no-invent rule


# --- benchmark assistant -----------------------------------------------------
def test_benchmark_helpers():
    assert _amounts("Total Rs 3,38,000 and GST Rs 60,840") == [338000.0, 60840.0]
    assert _median([10, 20, 30]) == 20
    assert _median([10, 20, 30, 40]) == 25


def test_benchmark_verdict_is_deterministic():
    kb = KnowledgeBase()
    kb.add_text("Solar panel 330Wp total Rs 3,00,000", "hist1.txt")
    kb.add_text("Solar panel 330Wp grand total Rs 4,00,000", "hist2.txt")
    kb.build()
    res = benchmark_price(kb, "solar panel", proposed_price=500000.0, provider=None)
    assert res["count"] >= 1
    assert res["median"] > 0
    assert "ABOVE" in res["verdict"]      # 500k vs median 350k
    assert res["narrative"] == ""          # no LLM -> no narrative


# --- spend classifier --------------------------------------------------------
def test_classify_rules():
    assert classify_item("330Wp solar panel and inverter")["category"] == "Solar & Energy"
    assert classify_item("Dell laptop i5")["category"] == "IT & Electronics"
    r = classify_item("xyzzy unknown thing", provider=None)
    assert r["category"] == UNCATEGORISED and r["confidence"] == 0.0


def test_classify_llm_is_bounded_to_the_category_list():
    class StubLLM(AIProvider):
        name = "stub"

        def available(self):
            return True

        def extract(self, *a, **k):
            return {}

        def write(self, instruction):
            assert "Categories:" in instruction
            return "Furniture"  # a valid category

    r = classify_item("mysterious item", provider=StubLLM())
    assert r["category"] == "Furniture" and r["method"] == "ai"

    class BadLLM(StubLLM):
        def write(self, instruction):
            return "Spaceships"  # not an allowed category

    r2 = classify_item("mysterious item", provider=BadLLM())
    assert r2["category"] == UNCATEGORISED  # invalid LLM answer is rejected


def test_category_summary_counts():
    summ = category_summary(classify_items(["solar panel", "laptop", "solar inverter"]))
    assert summ.get("Solar & Energy") == 2


# --- headless AMC runner (CI regression) -------------------------------------
def test_env_treats_empty_as_unset(monkeypatch):
    """GitHub Actions injects an unset `${{ vars.X }}` as an EMPTY string, so a
    plain os.environ.get(name, default) returns "" instead of the default. env()
    must fall back to the default for empty/whitespace values."""
    import run_amc
    monkeypatch.delenv("AMC_TEST_X", raising=False)
    assert run_amc.env("AMC_TEST_X", "d") == "d"       # unset -> default
    monkeypatch.setenv("AMC_TEST_X", "")
    assert run_amc.env("AMC_TEST_X", "d") == "d"       # empty -> default (the bug)
    monkeypatch.setenv("AMC_TEST_X", "   ")
    assert run_amc.env("AMC_TEST_X", "d") == "d"       # whitespace -> default
    monkeypatch.setenv("AMC_TEST_X", "v")
    assert run_amc.env("AMC_TEST_X", "d") == "v"       # set -> value


def test_amc_runner_survives_empty_register_env(monkeypatch, tmp_path):
    """Reproduces the CI failure: AMC_REGISTER_PATH="" must fall back to the
    default sample and the run must succeed (exit 0)."""
    import os
    import run_amc
    monkeypatch.setenv("AMC_REGISTER_PATH", "")   # the empty-string CI condition
    monkeypatch.setenv("AMC_CRITICAL_DAYS", "")    # would have crashed int("")
    monkeypatch.chdir(tmp_path)                     # write outputs into a temp dir
    # DEFAULT_REGISTER is repo-relative; point it at the real sample by absolute path.
    monkeypatch.setattr(run_amc, "DEFAULT_REGISTER", str(AMC_CSV))
    assert run_amc.main() == 0
    assert (tmp_path / "amc_digest.md").exists()


# --- document-comparison pipeline (deterministic parts, no ML stack) ---------
def test_segment_splits_by_clause_number():
    # segment() uses clause mode only when >=3 clause markers are present,
    # otherwise it treats the text as prose (paragraph mode).
    text = ("1. Payment terms are net 30.\n"
            "2. Delivery within 45 days.\n"
            "3. Warranty is 12 months.")
    segs = segment(text)
    assert len(segs) == 3
    assert segs[0].label.startswith("1")
    assert "Payment" in segs[0].text


def test_extract_numbers_types_and_values():
    nums = extract_numbers("Fee is $1,250.00, discount 30% on 42 units")
    units = {n.unit for n in nums}
    assert "money" in units and "percent" in units
    assert next(n for n in nums if n.unit == "money").value == 1250.0


def test_diff_numbers_detects_change():
    changes = diff_numbers("Payment within 30 days", "Payment within 45 days")
    assert any("30" in c.old and "45" in c.new for c in changes)


def test_compare_pairs_number_change():
    old = Segment("1", "Payment within 30 days")
    new = Segment("1", "Payment within 45 days")
    changes = compare_pairs([Pair(old=old, new=new, similarity=0.9)])
    assert len(changes) == 1
    assert changes[0].category == "Number change"
    assert changes[0].number_changes


def test_compare_pairs_added_removed_and_identical():
    s = Segment("2", "New confidentiality clause")
    assert compare_pairs([Pair(None, s, 0.0)])[0].category == "Clause added"
    assert compare_pairs([Pair(s, None, 0.0)])[0].category == "Clause removed"
    same = Segment("3", "Unchanged clause")
    assert compare_pairs([Pair(same, same, 1.0)]) == []   # identical -> nothing


def test_compare_pairs_wording_change():
    old = Segment("4", "The vendor shall deliver the goods")
    new = Segment("4", "The supplier shall deliver the goods")
    changes = compare_pairs([Pair(old=old, new=new, similarity=0.8)])
    assert changes[0].category == "Wording change"


def test_summarize_changes_buckets_and_risk():
    changes = compare_pairs([
        Pair(Segment("1", "Payment within 30 days"),
             Segment("1", "Payment within 45 days"), 0.9),
        Pair(Segment("2", "Warranty period is 24 months"),
             Segment("2", "Warranty period is 12 months"), 0.9),
    ])
    summary = summarize_changes(changes)
    assert summary["total"] == 2
    assert summary["commercial"]                              # payment change
    assert any("arranty" in r for r in summary["risks"])     # warranty reduced


# --- provider abstraction fallbacks ------------------------------------------
def test_local_provider_generation_methods_return_empty():
    p = LocalHeuristicProvider()
    assert p.available() is True
    assert p.ask("q", "ctx") == ""      # -> RAG shows raw passages
    assert p.write("draft") == ""        # -> AMC/RFQ use templates
    assert p.reason([]) == ""
    assert isinstance(p.extract("some text", None, "hint"), dict)


def test_rfq_llm_path_receives_grounded_contract(quote_files):
    analysis = analyze_quotes(quote_files, LocalHeuristicProvider())
    awardee = analysis["ranking"][0]["name"]

    class StubLLM(AIProvider):
        name = "stub"

        def available(self):
            return True

        def extract(self, *a, **k):
            return {}

        def write(self, instruction):
            # The memo instruction must pin the awardee and forbid invention.
            assert awardee in instruction
            assert "do NOT override" in instruction
            assert "do not invent" in instruction.lower()
            return f"# Memo\nRecommend {awardee}."

    req = Requirement(title="Solar", items="panels")
    memo = build_award_memo(req, analysis, provider=StubLLM())
    assert memo["used_llm"] is True
    assert memo["awardee"] == awardee
