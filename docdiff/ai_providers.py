"""
AI provider abstraction for quote analysis.

The rest of the app never talks to a specific AI engine directly — it asks an
`AIProvider` to (a) read a quote's text into structured fields and (b) optionally
narrate a recommendation. Swapping the brain (no-LLM → Ollama → a cloud model
later) needs no change to the business logic in quote_intelligence.py.

Providers shipped now:
    LocalHeuristicProvider  - no LLM, pure rules/regex. Runs anywhere, including
                              the free Streamlit Cloud host. This is the default.
    OllamaProvider          - talks to a local Ollama server (http://localhost:11434)
                              if one is running (e.g. on the user's own PC). Gives
                              real LLM reasoning. Never used on the free cloud host
                              because Ollama can't run there.
    ClaudeProvider          - talks to Anthropic's cloud API (needs an API key).
                              Works everywhere, including the free cloud host, and
                              gives the sharpest extraction + reasoning. Sends quote
                              text to Anthropic, so only use it where that's allowed.
    GeminiProvider          - talks to Google's Gemini cloud API (needs an API key).
                              Same trade-off as Claude: works anywhere, sends quote
                              text to Google.

get_provider() auto-detects: Ollama if reachable, else the heuristic engine.
The cloud providers are opt-in (get_provider(prefer="claude"|"gemini")) because
they need a key and send text off-machine — the caller decides when that trade-off
is acceptable.

The two cloud providers are the same shape (subclass AIProvider, implement
available()/extract(), optionally recommend()/reason()); a third — OpenAI,
Mistral, etc. — would slot in the same way without touching the scoring engine.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

# The fields we try to pull out of every quotation.
QUOTE_FIELDS = [
    "Vendor Name", "Quotation Number", "Quotation Date", "Validity Period",
    "Payment Terms", "Delivery Timeline", "Warranty Details", "GST / Taxes",
    "Freight / Transport", "Total Value", "Additional Terms",
]


class AIProvider(ABC):
    """Interface every AI backend implements."""

    name = "base"

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def extract(self, text: str, items_df, vendor_hint: str = "") -> dict:
        """Return {field: {"value": str|None, "confidence": 0..1}} for QUOTE_FIELDS."""
        ...

    def recommend(self, context: str) -> str:
        """Optional richer narrative. Default: none (engine uses its own text)."""
        return ""

    def reason(self, quotes: list[tuple]) -> str:
        """Holistic, domain-agnostic reasoned comparison of all quotes.

        `quotes`: list of (vendor_name, raw_text). Returns markdown analysis, or
        "" if this provider can't truly reason (no LLM). Only a real LLM provider
        overrides this — it's what produces analyst-quality output for any kind of
        quotation (goods, hotels, services), not just the fixed field schema.
        """
        return ""

    def ask(self, question: str, context: str) -> str:
        """Answer a question grounded ONLY in `context` (the retrieved excerpts).

        This is the generation half of RAG. Returns "" if this provider can't do
        free-form generation (no LLM) — the caller then shows the raw excerpts
        instead, so the knowledge base still works without a key.
        """
        return ""


# Grounding rules shared by every LLM provider's ask() — kept in one place so the
# "answer only from the sources, cite them, never guess" guarantee is identical no
# matter which model runs. This is what makes the knowledge base trustworthy.
RAG_SYSTEM = (
    "You are a procurement knowledge assistant. Answer the user's question using "
    "ONLY the numbered source excerpts provided. Cite the sources you rely on "
    "inline like [1], [2]. If the answer is not contained in the sources, say you "
    "could not find it in the documents — do NOT use outside knowledge or guess. "
    "Be specific and quote figures, dates, and vendor names exactly as written."
)


def _rag_user_prompt(question: str, context: str) -> str:
    return f"SOURCES:\n{context}\n\nQUESTION: {question}"


# --- Heuristic (no-LLM) provider ---------------------------------------------
_GST_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]Z[A-Z\d]\b")
_DATE_RE = re.compile(
    r"\b(\d{1,2}[\-/ ](?:\d{1,2}|[A-Za-z]{3,9})[\-/ ]\d{2,4})\b")
_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
_VENDOR_HINT_WORDS = ("ltd", "pvt", "private limited", "inc", "llp", "industries",
                      "enterprises", "technologies", "solar", "systems", "company",
                      "corporation", "co.", "traders", "solutions",
                      "hotel", "inn", "resort", "restaurant", "lodge")
_GENERIC_SENDERS = ("general manager", "sales", "accounts", "front office manager",
                    "reservations", "admin", "info", "marketing")


def _guess_vendor(text: str, fallback: str) -> str:
    """Best-effort vendor name from an emailed/prose quote."""
    # 1. "Greetings from X" — very common in quote emails, gives clean names.
    m = re.search(r"greetings\s+from\s+([^\n!.,]{3,50})", text, re.I)
    if m:
        return m.group(1).strip().strip("!.,")
    # 2. Outlook "From <Name> <email>" — unless it's a generic role.
    m = re.search(r"(?im)^\s*from\s+([A-Z][^\n<]{2,50}?)\s*<", text)
    if m and m.group(1).strip().lower() not in _GENERIC_SENDERS:
        return m.group(1).strip()
    # 3. A line that looks like a company/venue name.
    for line in text.splitlines():
        s = line.strip()
        if 3 < len(s) < 60 and any(w in s.lower() for w in _VENDOR_HINT_WORDS):
            return s
    # 4. Fall back to the filename.
    return fallback


def _first(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def heuristic_extract(text: str, items_df, vendor_hint: str = "") -> dict:
    """Rule/regex based field extraction. Confidence reflects match strength."""
    t = text or ""
    low = t.lower()
    out: dict[str, dict] = {}

    def put(field, value, conf):
        out[field] = {"value": value, "confidence": conf if value else 0.0}

    # Vendor name from the email/prose (e.g. "Greetings from X"), else filename.
    vendor = _guess_vendor(t, vendor_hint)
    strong = vendor and vendor != vendor_hint
    put("Vendor Name", vendor or (vendor_hint or None), 0.85 if strong else 0.55)

    put("Quotation Number",
        _first(r"(?:quotation|quote|ref(?:erence)?|q\.?\s*no)[\s:.#\-]*([A-Z0-9][A-Z0-9\-/]{2,})", t),
        0.8)
    put("Quotation Date", _first(_DATE_RE.pattern, t), 0.75)
    put("Validity Period",
        _first(r"valid(?:ity)?[^.\n]*?(\d+\s*(?:days?|weeks?|months?))", t), 0.8)

    # Payment terms: capture a short phrase around the keyword.
    pay = None
    pm = re.search(r"(payment[^.\n]{0,80}|(?:100%\s*)?advance[^.\n]{0,40}|"
                   r"net\s*\d+[^.\n]{0,20}|\d+\s*days?\s*credit)", low)
    if pm:
        pay = pm.group(1).strip().capitalize()
    put("Payment Terms", pay, 0.7 if pay else 0.0)

    put("Delivery Timeline",
        _first(r"(?:delivery|deliver(?:ed)?|lead\s*time)[^.\n]*?(\d+\s*(?:days?|weeks?))", t)
        or _first(r"within\s+(\d+\s*(?:days?|weeks?))", t), 0.75)
    put("Warranty Details",
        _first(r"(\d+\s*(?:years?|yrs?|months?))\s*warranty", t)
        or _first(r"warranty[^.\n]*?(\d+\s*(?:years?|yrs?|months?))", t), 0.75)

    gst = _GST_RE.search(t)
    gst_pct = _first(r"gst[^.\n]*?(\d{1,2}\s*%)", t)
    gst_val = gst.group(0) if gst else gst_pct
    put("GST / Taxes", gst_val, 0.9 if gst else (0.65 if gst_pct else 0.0))

    freight = None
    if re.search(r"freight|transport|shipping|delivery charges", low):
        fm = re.search(r"(?:freight|transport|shipping)[^.\n]{0,40}", low)
        freight = fm.group(0).strip().capitalize() if fm else "Mentioned"
    put("Freight / Transport", freight, 0.65 if freight else 0.0)

    total = _first(r"(?:grand\s*total|total\s*(?:amount|value|payable)?)[\s:rs.₹inr]*([\d,]+(?:\.\d+)?)", t)
    if not total:
        amts = _AMOUNT_RE.findall(t)
        total = max(amts, key=lambda a: float(a.replace(",", "")) if a else 0) if amts else None
    put("Total Value", total, 0.7 if total else 0.0)

    terms = []
    for kw in ("installation", "commissioning", "training", "amc", "buyback", "penalty"):
        if kw in low:
            terms.append(kw)
    put("Additional Terms", ", ".join(terms) if terms else None, 0.6 if terms else 0.0)

    return out


class LocalHeuristicProvider(AIProvider):
    name = "Local (rules, no LLM)"

    def available(self) -> bool:
        return True

    def extract(self, text, items_df, vendor_hint=""):
        return heuristic_extract(text, items_df, vendor_hint)


# --- Ollama provider (used when running locally with Ollama) ------------------
class OllamaProvider(AIProvider):
    name = "Ollama (local LLM)"

    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host

    def available(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self.host}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def _chat(self, prompt: str, expect_json: bool = False) -> str:
        import requests
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if expect_json:
            payload["format"] = "json"
        r = requests.post(f"{self.host}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "")

    def extract(self, text, items_df, vendor_hint=""):
        prompt = (
            "You are a procurement analyst. Extract these fields from the vendor "
            "quotation text and return ONLY JSON with these exact keys: "
            + ", ".join(f'\"{f}\"' for f in QUOTE_FIELDS)
            + ". For each key use an object {\"value\": <string or null>, "
            "\"confidence\": <0..1>}. If a field is absent, value null and "
            "confidence 0.\n\nQUOTATION TEXT:\n" + (text or "")[:6000]
        )
        try:
            raw = self._chat(prompt, expect_json=True)
            data = json.loads(raw)
            result = {}
            for f in QUOTE_FIELDS:
                cell = data.get(f) or {}
                if isinstance(cell, dict):
                    result[f] = {"value": cell.get("value"),
                                 "confidence": float(cell.get("confidence") or 0.0)}
                else:
                    result[f] = {"value": cell, "confidence": 0.8 if cell else 0.0}
            return result
        except Exception:
            # Any failure → fall back so the app never breaks.
            return heuristic_extract(text, items_df, vendor_hint)

    def recommend(self, context: str) -> str:
        try:
            return self._chat(
                "You are a procurement advisor. Based on the structured comparison "
                "below, write a concise, professional recommendation (5-8 sentences) "
                "for a procurement committee. Avoid jargon.\n\n" + context
            ).strip()
        except Exception:
            return ""

    def reason(self, quotes: list[tuple]) -> str:
        blocks = []
        for i, (name, text) in enumerate(quotes, 1):
            blocks.append(f"--- QUOTE {i} (file: {name}) ---\n{(text or '')[:4500]}")
        prompt = (
            "You are an experienced procurement analyst. Compare the vendor "
            "quotations below like a professional and produce a committee-ready "
            "note in markdown.\n\n"
            "Do this:\n"
            "1. Identify each vendor and what they are quoting.\n"
            "2. Put COMPARABLE line items side by side (same product / room type / "
            "plan / occupancy). Compute effective prices INCLUDING any taxes "
            "stated (e.g. '2800+5%' = 2940).\n"
            "3. For each comparable item, say which vendor is cheaper and by how "
            "much (amount and %).\n"
            "4. Note non-price differences: inclusions, availability/quantity, "
            "warranty, validity, payment terms, location, and any missing info or "
            "risks.\n"
            "5. End with a clear recommendation of best overall value and why.\n"
            "Be specific with numbers. Use short sections and bullet points.\n\n"
            + "\n\n".join(blocks)
        )
        try:
            return self._chat(prompt).strip()
        except Exception:
            return ""

    def ask(self, question: str, context: str) -> str:
        try:
            return self._chat(
                RAG_SYSTEM + "\n\n" + _rag_user_prompt(question, context)
            ).strip()
        except Exception:
            return ""


# --- Claude provider (Anthropic cloud API) -----------------------------------
# Default to the most capable model. The caller can pass a cheaper one
# (e.g. "claude-haiku-4-5") from the UI if cost matters more than sharpness.
_CLAUDE_DEFAULT_MODEL = "claude-opus-4-8"


def _quote_output_schema() -> dict:
    """A JSON schema locking the model to {field: {value, confidence}} for every
    QUOTE_FIELD. Structured outputs guarantee we get valid, parseable JSON back."""
    field_schema = {
        "type": "object",
        "properties": {
            # value is a string when present, null when the field is absent.
            "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "confidence": {"type": "number"},
        },
        "required": ["value", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {f: field_schema for f in QUOTE_FIELDS},
        "required": list(QUOTE_FIELDS),
        "additionalProperties": False,
    }


class ClaudeProvider(AIProvider):
    name = "Claude (Anthropic cloud LLM)"

    def __init__(self, model: str = _CLAUDE_DEFAULT_MODEL, api_key: str | None = None):
        self.model = model or _CLAUDE_DEFAULT_MODEL
        self.api_key = api_key or None
        self._client_cache = None

    def available(self) -> bool:
        """True when the SDK is installed and a key is resolvable — no network call."""
        # Catch BaseException, not just Exception: a broken/partial install of an
        # optional cloud SDK (e.g. a bad native binding) can raise non-Exception
        # errors at import time, and selecting this engine must never crash the app.
        try:
            import anthropic  # noqa: F401
        except BaseException:
            return False
        import os
        return bool(self.api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def _client(self):
        if self._client_cache is None:
            import anthropic
            # Passing api_key=None lets the SDK resolve from the environment.
            self._client_cache = anthropic.Anthropic(api_key=self.api_key) \
                if self.api_key else anthropic.Anthropic()
        return self._client_cache

    @staticmethod
    def _text(message) -> str:
        """Concatenate the text blocks of a response (ignoring thinking blocks)."""
        return "".join(b.text for b in message.content if b.type == "text").strip()

    def extract(self, text, items_df, vendor_hint=""):
        try:
            client = self._client()
            prompt = (
                "You are a procurement analyst. Extract the key commercial fields "
                "from the vendor quotation below. For each field give a value "
                "(a short string, or null if the quote doesn't state it) and a "
                "confidence from 0 to 1 for how sure you are.\n\n"
                f"Vendor filename hint: {vendor_hint}\n\n"
                "QUOTATION TEXT:\n" + (text or "")[:8000]
            )
            # No thinking needed for extraction; structured outputs force valid JSON.
            resp = client.messages.create(
                model=self.model,
                max_tokens=2048,
                output_config={"format": {"type": "json_schema",
                                          "schema": _quote_output_schema()}},
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(self._text(resp))
            result = {}
            for f in QUOTE_FIELDS:
                cell = data.get(f) or {}
                result[f] = {
                    "value": cell.get("value"),
                    "confidence": float(cell.get("confidence") or 0.0),
                }
            return result
        except Exception:
            # Any failure (no key, network, schema) → heuristic so the app never breaks.
            return heuristic_extract(text, items_df, vendor_hint)

    def recommend(self, context: str) -> str:
        try:
            resp = self._client().messages.create(
                model=self.model,
                max_tokens=1024,
                system="You are a procurement advisor. Write a concise, professional "
                       "recommendation (5-8 sentences) for a procurement committee. "
                       "Avoid jargon. Do not invent numbers — use only what's given.",
                messages=[{"role": "user", "content": context}],
            )
            return self._text(resp)
        except Exception:
            return ""

    def ask(self, question: str, context: str) -> str:
        try:
            resp = self._client().messages.create(
                model=self.model,
                max_tokens=1024,
                system=RAG_SYSTEM,
                messages=[{"role": "user",
                           "content": _rag_user_prompt(question, context)}],
            )
            return self._text(resp)
        except Exception:
            return ""

    def reason(self, quotes: list[tuple]) -> str:
        if not quotes:
            return ""
        blocks = []
        for i, (name, text) in enumerate(quotes, 1):
            blocks.append(f"--- QUOTE {i} (file: {name}) ---\n{(text or '')[:6000]}")
        prompt = (
            "You are an experienced procurement analyst. Compare the vendor "
            "quotations below like a professional and produce a committee-ready "
            "note in markdown.\n\n"
            "Do this:\n"
            "1. Identify each vendor and what they are quoting.\n"
            "2. Put COMPARABLE line items side by side. Compute effective prices "
            "INCLUDING any taxes stated (e.g. '2800+5%' = 2940).\n"
            "3. For each comparable item, say which vendor is cheaper and by how "
            "much (amount and %).\n"
            "4. Note non-price differences: inclusions, quantity, warranty, "
            "validity, payment terms, and any missing info or risks.\n"
            "5. End with a clear recommendation of best overall value and why.\n"
            "Be specific with numbers. Use short sections and bullet points.\n\n"
            + "\n\n".join(blocks)
        )
        try:
            # This is genuine analysis, so let Claude think. Stream + get_final_message
            # so a long thinking+answer turn can't hit the SDK's non-stream timeout.
            with self._client().messages.stream(
                model=self.model,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                return self._text(stream.get_final_message())
        except Exception:
            return ""


# --- Gemini provider (Google cloud API) --------------------------------------
# Default to a capable general model. The caller can pass "gemini-2.5-flash"
# from the UI for lower cost, or a newer model string as they're released.
_GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"


class GeminiProvider(AIProvider):
    name = "Gemini (Google cloud LLM)"

    def __init__(self, model: str = _GEMINI_DEFAULT_MODEL, api_key: str | None = None):
        self.model = model or _GEMINI_DEFAULT_MODEL
        self.api_key = api_key or None
        self._client_cache = None

    def available(self) -> bool:
        """True when the SDK is installed and a key is resolvable — no network call."""
        # Catch BaseException: google-genai's import pulls in native crypto bindings
        # that can raise a non-Exception (e.g. pyo3 PanicException) on a broken or
        # partial install. Selecting this engine must never crash the app.
        try:
            from google import genai  # noqa: F401
        except BaseException:
            return False
        import os
        return bool(self.api_key
                    or os.environ.get("GEMINI_API_KEY")
                    or os.environ.get("GOOGLE_API_KEY"))

    def _client(self):
        if self._client_cache is None:
            from google import genai
            # Passing api_key=None lets the SDK resolve GEMINI_API_KEY/GOOGLE_API_KEY.
            self._client_cache = genai.Client(api_key=self.api_key) \
                if self.api_key else genai.Client()
        return self._client_cache

    def _generate(self, prompt: str, system: str | None = None,
                  json_out: bool = False, max_tokens: int = 2048) -> str:
        from google.genai import types
        kwargs: dict = {"max_output_tokens": max_tokens, "temperature": 0.1}
        if system:
            kwargs["system_instruction"] = system
        if json_out:
            kwargs["response_mime_type"] = "application/json"
        resp = self._client().models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(**kwargs),
        )
        return (resp.text or "").strip()

    def extract(self, text, items_df, vendor_hint=""):
        try:
            prompt = (
                "You are a procurement analyst. Extract these fields from the vendor "
                "quotation text and return ONLY JSON with these exact keys: "
                + ", ".join(f'"{f}"' for f in QUOTE_FIELDS)
                + '. For each key use an object {"value": <string or null>, '
                '"confidence": <0..1>}. If a field is absent, value null and '
                "confidence 0.\n\nVendor filename hint: " + (vendor_hint or "")
                + "\n\nQUOTATION TEXT:\n" + (text or "")[:8000]
            )
            data = json.loads(self._generate(prompt, json_out=True, max_tokens=2048))
            result = {}
            for f in QUOTE_FIELDS:
                cell = data.get(f) or {}
                if isinstance(cell, dict):
                    result[f] = {"value": cell.get("value"),
                                 "confidence": float(cell.get("confidence") or 0.0)}
                else:
                    result[f] = {"value": cell, "confidence": 0.8 if cell else 0.0}
            return result
        except Exception:
            # Any failure (no key, network, bad JSON) → heuristic so nothing breaks.
            return heuristic_extract(text, items_df, vendor_hint)

    def recommend(self, context: str) -> str:
        try:
            return self._generate(
                context,
                system="You are a procurement advisor. Write a concise, professional "
                       "recommendation (5-8 sentences) for a procurement committee. "
                       "Avoid jargon. Do not invent numbers — use only what's given.",
                max_tokens=1024,
            )
        except Exception:
            return ""

    def ask(self, question: str, context: str) -> str:
        try:
            return self._generate(
                _rag_user_prompt(question, context),
                system=RAG_SYSTEM,
                max_tokens=1024,
            )
        except Exception:
            return ""

    def reason(self, quotes: list[tuple]) -> str:
        if not quotes:
            return ""
        blocks = []
        for i, (name, text) in enumerate(quotes, 1):
            blocks.append(f"--- QUOTE {i} (file: {name}) ---\n{(text or '')[:6000]}")
        prompt = (
            "You are an experienced procurement analyst. Compare the vendor "
            "quotations below like a professional and produce a committee-ready "
            "note in markdown.\n\n"
            "Do this:\n"
            "1. Identify each vendor and what they are quoting.\n"
            "2. Put COMPARABLE line items side by side. Compute effective prices "
            "INCLUDING any taxes stated (e.g. '2800+5%' = 2940).\n"
            "3. For each comparable item, say which vendor is cheaper and by how "
            "much (amount and %).\n"
            "4. Note non-price differences: inclusions, quantity, warranty, "
            "validity, payment terms, and any missing info or risks.\n"
            "5. End with a clear recommendation of best overall value and why.\n"
            "Be specific with numbers. Use short sections and bullet points.\n\n"
            + "\n\n".join(blocks)
        )
        try:
            return self._generate(prompt, max_tokens=8000)
        except Exception:
            return ""


def get_provider(prefer: str = "auto", model: str | None = None,
                 api_key: str | None = None) -> AIProvider:
    """Return the best available provider.

    'auto'   → Ollama if reachable, else the heuristic engine.
    'claude' → the Anthropic cloud provider if a key is available, else heuristic.
    'gemini' → the Google Gemini cloud provider if a key is available, else heuristic.
    'ollama' → Ollama if reachable, else heuristic.
    """
    if prefer == "claude":
        claude = ClaudeProvider(model=model or _CLAUDE_DEFAULT_MODEL, api_key=api_key)
        if claude.available():
            return claude
        return LocalHeuristicProvider()
    if prefer == "gemini":
        gemini = GeminiProvider(model=model or _GEMINI_DEFAULT_MODEL, api_key=api_key)
        if gemini.available():
            return gemini
        return LocalHeuristicProvider()
    if prefer in ("auto", "ollama"):
        ollama = OllamaProvider()
        if ollama.available():
            return ollama
    return LocalHeuristicProvider()
