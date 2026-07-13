# Verifying the live paths

The automated tests (`pytest`) cover every **deterministic** engine — scoring,
ranking, AMC classification, RAG chunking + save/load, RFQ award, spend
classification, benchmark math. They run with no API key and no ML model, which
is exactly why they're fast and reliable in CI.

Two categories of behaviour can't be proven that way and need a one-time manual
check on a real deploy:

1. **The cloud LLM paths** — Claude / Gemini actually returning good text for
   `extract` / `reason` / `ask` / `write`.
2. **The embedding retrieval path** — the local model (`all-MiniLM-L6-v2`)
   powering meaning-based RAG search (CI only exercises the keyword fallback).

Work through this once after deploying.

## Setup

```bash
pip install -r requirements.txt          # includes sentence-transformers, anthropic, google-genai
streamlit run app.py
```

Provide at least one key when prompted (or as a Streamlit secret):
`ANTHROPIC_API_KEY` or `GEMINI_API_KEY`.

## Checklist

### Embedding retrieval (no key needed — just the model)
- [ ] Open **🔎 Knowledge Base (RAG)**, add the three files in `samples/quotes/`.
- [ ] The caption should read **"retrieval: meaning-based embeddings"** (not
      "keyword"). If it says keyword, `sentence-transformers` didn't install/load —
      check the logs; the app still works, just on keyword search.
- [ ] Ask *"Which vendor offers the longest warranty?"* — a meaning-based query
      that keyword search handles poorly. With embeddings it should retrieve the
      GreenVolt warranty passage.

### Claude / Gemini extraction + reasoning
- [ ] Open **🤖 AI Quote Analysis**, pick **Claude** (or Gemini), enter your key.
- [ ] Upload the three `samples/quotes/`. Confirm the caption shows the cloud
      engine, and — the key check — **SunPower's total reads ₹3,98,840** (the Grand
      Total), not ₹3,38,000. The local rules engine gets this wrong; a working LLM
      corrects it. This single number confirms extraction is live and better.
- [ ] Confirm the **AI Reasoned Analysis** tab has a written comparison.

### Grounded Q&A (RAG generation)
- [ ] In the Knowledge Base, with a cloud engine selected, ask *"What warranty did
      GreenVolt offer?"* — the answer should state **12 years on modules** and
      **cite the source** `quote_greenvolt.txt`. If it invents a number or cites
      nothing, the grounding prompt isn't taking effect.

### Drafting (`write`)
- [ ] **🔔 AMC Monitor** → upload `samples/amc/amc_register.csv` → expand a
      contract → **Draft reminder**. With a cloud engine the email should read
      naturally and use the exact expiry date; with no engine it falls back to the
      template.
- [ ] **🧭 RFQ Agent** → define a requirement → **Draft RFQ emails** → confirm the
      draft is coherent and doesn't invent specs.
- [ ] **📑 Compare documents** → compare `samples/contract_v1.txt` and
      `contract_v2.txt` → open **🧠 Add an AI insight**, pick a cloud engine →
      confirm a short "why this matters" narrative appears under the rule-based
      summary.

### The award decision is still deterministic (the important invariant)
- [ ] In the RFQ Agent, after analysing replies, note the recommended awardee.
      Switch the engine (Claude ↔ Gemini ↔ Local) and re-run the memo. **The
      awardee must not change** — it's the top of the weighted score. Only the memo
      *wording* should differ. If the awardee changes with the engine, something has
      let the LLM into the decision — that's a bug worth stopping for.

## What "good" looks like

- The awardee, the ranking, the AMC urgency, the benchmark median, and the spend
  category set are **identical regardless of engine** — those are code.
- The prose (extraction of messy fields, summaries, emails, memos) gets **sharper**
  with a cloud engine and still **works** without one.
