# 🧭 AI Procurement Assistant

[![Tests](https://github.com/Sudharsan2896/ai-procurement-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/Sudharsan2896/ai-procurement-assistant/actions/workflows/tests.yml)

An AI-powered toolkit for a real procurement function: read messy vendor
documents in any format, score and compare quotes, remember past dealings, watch
renewal dates on their own, and run a quotation from requirement to award
recommendation.

Every tool works **free and offline with no API key** — a local model and rule
engines do the baseline. Plug in **Claude, Gemini, or a local Ollama** model and
the same tools get sharper. Nothing is ever *blocked* on having a key.

> **The one idea behind everything:** the AI never makes a decision that has a
> right answer — code does; the AI only writes the words around it. Vendor
> rankings, renewal urgency, the recommended awardee: all auditable arithmetic. A
> model only extracts fields, summarises, and drafts. See the
> **[full walkthrough](docs/WALKTHROUGH.md)** for how it all fits together.

---

## The tools

Pick **Document Tools** or **Procurement Toolkit** in the sidebar.

### Procurement Toolkit
| Tool | What it does |
|---|---|
| 🧭 **RFQ Agent** | Runs a quotation end to end: capture requirement → draft RFQs → score replies → award memo, with two human approval gates |
| 🤖 **AI Quote Analysis** | Upload 2–10 quotes in any format; extracts terms, scores on a weighted procurement score, ranks, and recommends |
| 🔎 **Knowledge Base (RAG)** | A searchable memory of past quotes/POs/contracts; ask questions in plain English and get answers that **cite the source** |
| 📈 **Benchmark Assistant** | "Is this price reasonable?" — benchmarks a quote against figures in your own history |
| 🏷️ **Spend Classifier** | Sorts purchase line items into spend categories for analytics / GL coding |
| 🔔 **AMC Monitor** | Flags Annual Maintenance Contracts that are expired / due, ranked by urgency, and drafts renewal reminders — with a [daily autonomous run](docs/amc-automation.md) |
| 🧮 **Quote Comparison** · ✅ **PO vs Invoice Validator** | Tabular quote comparison and PO/invoice reconciliation |

### Document Tools
| Tool | What it does |
|---|---|
| 📑 **Compare documents** | Compares two contracts by **meaning**, not just text — matches clauses even when reordered, flags changed numbers loudly, ranks the important changes first, with an optional AI "why this matters" insight |
| 📄 **PDF → Word** · 📊 **Word tables → Excel** · 🔀 **Reconcile data** | Format conversion and data reconciliation utilities |

---

## Four interchangeable AI engines

Every AI step goes through one interface, so you can swap the "brain" without
touching any scoring logic:

| Engine | Needs | Notes |
|---|---|---|
| **Local rules** | nothing | Works anywhere, no key — the default |
| **Ollama** | Ollama running locally | A local open-source LLM, fully private |
| **Claude** | `ANTHROPIC_API_KEY` | Anthropic's cloud API |
| **Gemini** | `GEMINI_API_KEY` | Google's cloud API |

The local embedding model for meaning-based search (`all-MiniLM-L6-v2`, ~90 MB)
downloads once and runs on-device — document text never leaves the machine for
the *search* step. Only the optional cloud engines send text to a provider.

---

## Run it on your own computer

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the link it prints (usually http://localhost:8501). Everything works with
**no key**; to use a cloud engine, select it in a tool and paste a key (or set
`ANTHROPIC_API_KEY` / `GEMINI_API_KEY`).

Try it end to end with the bundled sample data: `samples/quotes/` (three vendor
quotes) and `samples/amc/amc_register.csv`.

## Deploy free (Streamlit Community Cloud)

Push to GitHub, then at **share.streamlit.io** create a new app pointing at this
repo with the main file `app.py`. It works out of the box with no configuration;
add any API keys as Streamlit **secrets** if you want the cloud engines.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The deterministic engines are covered by a fast, dependency-light suite (no
model/key needed) that runs in CI on every push. See
**[docs/VERIFY.md](docs/VERIFY.md)** for a checklist to verify the live LLM and
embedding paths after deploying.

---

## Project layout

```
app.py                   Streamlit UI — one engine selector, one tool per screen
docdiff/
  ai_providers.py        The four AI engines behind one interface
  quote_intelligence.py  Deterministic weighted vendor scoring (analyze_quotes)
  rag.py                 Knowledge base: chunking, retrieval, grounded Q&A
  benchmark.py           Price benchmarking against history
  classify.py            Spend classification (rules + bounded LLM)
  amc.py                 AMC classification, ranking, reminder drafting
  rfq.py                 RFQ requirement, draft, and award memo
  summary.py             Change summary + optional AI narrative
  align.py               Local embedding model (shared by compare + RAG)
  extract.py ocr.py tables.py   Read text/tables from any file format
  compare.py segment.py numbers.py export.py   Document-comparison pipeline
run_amc.py               Headless AMC monitor for the daily scheduled run
tests/                   Deterministic-engine smoke tests
samples/                 Sample quotes + AMC register
.github/workflows/       tests.yml (CI) · amc-monitor.yml (daily AMC run)
docs/                    Walkthrough, AMC automation, verify checklist, roadmap
```

---

## Docs

- **[Walkthrough](docs/WALKTHROUGH.md)** — how the tools fit together and the design principles
- **[AMC automation](docs/amc-automation.md)** — set up the daily renewal-monitor run
- **[Verify checklist](docs/VERIFY.md)** — prove the live LLM/embedding paths after deploy
- **[Apprenticeship roadmap](docs/AI-APPRENTICESHIP-ROADMAP.md)** — where this goes next
