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
