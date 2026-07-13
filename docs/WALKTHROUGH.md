# Procurement AI Toolkit — a walkthrough

This repository started as a document-comparison tool. It has grown into a small
**AI-powered procurement system**: five tools that read messy vendor documents,
score and compare quotes, remember past dealings, watch renewal dates on their
own, and run a quotation from requirement to award recommendation.

This document ties those five tools together — what each one does, how they share
one design, and the three principles that make the whole thing trustworthy enough
to put in front of a procurement committee.

> **Who this is for.** A procurement or operations lead who wants to understand
> what the system does and why it's safe to rely on — and an engineer who wants to
> see how it's built. No AI background needed for the first half.

---

## The one idea behind everything

Every tool here follows a single rule:

> **The AI never makes a decision that has a right answer. Code does. The AI only
> writes the words around the decision.**

Concretely: the vendor ranking, the "which contract expires first", the "which
quote is cheapest", the recommended awardee — all of that is plain, auditable
arithmetic. A language model is only ever asked to *extract* fields, *summarise*,
or *draft* an email — never to pick the winner.

Why this matters: when someone asks *"why did we recommend this vendor?"*, the
answer is a weighted score you can show them on a spreadsheet — not "the AI said
so." A hallucinating model can word an email awkwardly; it can **never** make you
award the wrong contract or miss a renewal.

You can see this rule in the code. The vendor score
([`docdiff/quote_intelligence.py`](../docdiff/quote_intelligence.py)) is fixed
weighted math:

```python
SCORE_WEIGHTS = {
    "Cost Competitiveness": 0.40,
    "Delivery Timeline":    0.20,
    "Warranty":             0.15,
    "Payment Terms":        0.10,
    "Technical Compliance": 0.15,
}
```

The language model computes none of that. It only fills in the fields the math
runs on, and narrates the result.

---

## Two more principles that unify the code

**1. One AI engine interface, four interchangeable brains.**
Everything AI goes through a single abstraction, `AIProvider`
([`docdiff/ai_providers.py`](../docdiff/ai_providers.py)), with four
implementations:

| Engine | What it is | Needs |
|---|---|---|
| **Local rules** | Regex/heuristics, no model | Nothing — works anywhere |
| **Ollama** | A local open-source LLM | Ollama running on your PC |
| **Claude** | Anthropic's cloud API | An API key |
| **Gemini** | Google's cloud API | An API key |

Every tool picks an engine from the *same* selector and calls the *same* methods
(`extract`, `reason`, `ask`, `write`). Swapping the brain — or adding a fifth —
touches one file and never touches the scoring logic. This is why the RFQ Agent
could be built by *composing* existing parts rather than rewriting them.

**2. It always works, then works better.**
Every AI step degrades gracefully. No API key? The quote analysis uses the rules
engine; the knowledge base shows you the raw passages; the reminder emails use a
template. The cloud engines make the output *sharper*, but nothing is ever
*blocked* on having one. This is a deliberate fit for an NGO on a free hosting
tier.

---

## The system at a glance

```
                       ┌───────────────────────────────────────────┐
                       │            AIProvider (one interface)       │
                       │   Local · Ollama · Claude · Gemini          │
                       │   extract() reason() ask() write()          │
                       └───────────────▲─────────────────────────────┘
                                       │ (all tools share it)
   ┌───────────────┬──────────────┬────┴─────────┬───────────────┐
   │ AI Quote      │ Knowledge    │ AMC Monitor  │ RFQ Agent     │
   │ Analysis      │ Base (RAG)   │              │ (the capstone)│
   │               │              │              │               │
   │ extract →     │ embed →      │ date math →  │ requirement → │
   │ score →       │ retrieve →   │ classify →   │ draft RFQ →   │
   │ rank →        │ grounded     │ draft        │ score replies→│
   │ narrate       │ answer       │ reminders    │ award memo    │
   └───────┬───────┴──────┬───────┴──────┬───────┴───────┬───────┘
           │              │              │               │
           │        reuses align.py      │        reuses everything above:
           │        embedding model      │        analyze_quotes + write() +
           │                             │        the knowledge base
           │                    ┌────────┴─────────┐
           │                    │ run_amc.py  +    │  ← autonomous:
           │                    │ GitHub Action    │     a daily scheduled run
           │                    │ (daily cron)     │
           │                    └──────────────────┘
   deterministic scoring engine (quote_intelligence.py) — the shared "brain"
```

---

## Tool 1 · AI Quote Analysis

**Problem.** Vendors send quotes in every format — PDF, Word, scanned images,
Excel, pasted email text. Comparing them by hand means re-typing figures into a
spreadsheet and hoping you didn't miss a tax line.

**How it works.** Upload two or more quotes in any format. The tool
([`analyze_quotes`](../docdiff/quote_intelligence.py)):

1. **Reads** each file (`extract.py`, OCR for scans).
2. **Extracts** the commercial fields — total, delivery, warranty, GST, payment
   terms — via the chosen engine.
3. **Scores** each vendor on the weighted procurement score (the deterministic
   part).
4. **Ranks** them and writes a plain-English commercial / technical / risk
   breakdown and a recommendation.

**The trust boundary in action.** The LLM extracts "Total: ₹4,33,060"; the *code*
decides that makes GreenVolt rank #2. Try the three files in
[`samples/quotes/`](../samples/quotes/): the free rules engine even mis-reads one
vendor's "Sub Total" as the total — switch to Claude or Gemini and the figure
corrects itself, while the *ranking method* stays identical. That's the whole
philosophy in one visible example.

---

## Tool 2 · Knowledge Base (RAG)

**Problem.** "What warranty did we get from this vendor last time? What did we pay
in 2024?" That knowledge is scattered across old quotes and contracts nobody can
search.

**How it works.** This is **RAG** — Retrieval-Augmented Generation
([`docdiff/rag.py`](../docdiff/rag.py)):

1. **Retrieve** — every past document is split into passages and turned into a
   "meaning fingerprint" using the *same local embedding model* that powers clause
   alignment (`align.py`, `all-MiniLM-L6-v2`). Your question is matched to the
   closest passages **on the machine** — nothing leaves it for the search step.
   (If the model can't load, it falls back to classic keyword search, so it always
   works.)
2. **Augment & Generate** — the top passages are handed to the LLM, which answers
   using *only* them and **cites the source document**. No key? You get the ranked
   passages to read yourself — so it can never hallucinate.

**A real engineering detail worth seeing.** Early on, a query like "GreenVolt
warranty" retrieved the wrong passage, because the vendor name sat in a *different*
chunk than the warranty line. The fix — *contextual chunk headers*, prepending the
document's identity to every passage at index time — is the single most common
real-world RAG problem, solved in the code. Knowledge bases live or die on
chunking.

**Persistence.** The free host wipes memory between sessions, so the tool has a
**Download / Load** knowledge base button. It serialises to **JSON, not pickle** —
because a knowledge-base file can be uploaded by a user, and loading a pickle can
execute arbitrary code. (See the note on trust below.)

---

## Tool 3 · AMC Monitor — and its autonomous twin

**Problem.** Annual Maintenance Contracts lapse quietly. A missed renewal means a
coverage gap on a solar plant at a rural health centre.

**How it works.** Upload your AMC register (Excel/CSV). The monitor
([`docdiff/amc.py`](../docdiff/amc.py)) computes days-to-expiry for each contract,
classifies it 🔴 Expired / 🟠 Critical / 🟡 Due soon / ⚪ OK, ranks by urgency,
totals the **value at risk**, and drafts a renewal reminder email for each contract
that needs one. The urgency decision is date math; the LLM only drafts the email.

**The autonomous part.** [`run_amc.py`](../run_amc.py) +
[`.github/workflows/amc-monitor.yml`](../.github/workflows/amc-monitor.yml) turn
this from *interactive* into *self-running*. Every morning a GitHub Action reads
the register and delivers a digest — to the Action's run summary, an artifact, and
(optionally) an email to the procurement inbox.

**A deliberate safety choice.** The scheduled job **does not email vendors.**
Auto-sending outward mail from an unattended process is hard to reverse, so the
digest goes to *you* — a human reviews the drafts and sends them. Automate the
*preparation*; keep a person on the *irreversible action*. Setup is in
[`docs/amc-automation.md`](./amc-automation.md).

---

## Tool 4 · RFQ Agent — the capstone

**Problem.** Running a quotation is a whole workflow: write the RFQ, send it,
collect replies, compare them, and justify an award. Each step is a different tool
today.

**How it works.** The RFQ Agent ([`docdiff/rfq.py`](../docdiff/rfq.py)) runs the
loop end to end by **composing everything above**:

1. **Capture** the requirement (what, how much, by when).
2. **Draft** an RFQ email per vendor — using the same `write()` method the AMC
   monitor uses. → **🚦 Gate 1: you review and send.**
3. **Compare replies** — uploaded quotes go straight through `analyze_quotes`, the
   *same* scoring engine as Tool 1.
4. **Historical context** — if you've loaded a knowledge base this session, the
   agent retrieves what you got from these vendors before and folds it into the
   memo.
5. **Award memo** — a committee-ready recommendation. → **🚦 Gate 2: committee
   approval before award.**

**Why the capstone proves the design.** The RFQ Agent added almost no new *logic* —
it orchestrates parts that already existed behind clean interfaces. And at the
highest-stakes step, *which vendor wins*, the recommended awardee is
deterministically the top of the weighted score; the LLM is explicitly told to
recommend that vendor and **not** override the ranking. The award you present
always traces to a number, and the two human gates keep the irreversible actions
(sending mail, awarding a contract) with a person.

---

## A note on trust and security

Building this surfaced three decisions worth stating plainly, because they're what
makes the system safe rather than just clever:

- **Decisions are auditable code, not model output.** (The rule above.)
- **Optional dependencies fail safe.** Selecting a cloud engine on a machine with a
  broken install can raise errors *below* the normal exception layer — the code
  catches those at the import probe so a missing/partial SDK falls back instead of
  crashing the app.
- **Uploaded data is treated as untrusted.** The knowledge-base file loads from
  JSON, never pickle, so a hostile file can't run code. The daily monitor never
  auto-mails vendors. Where data crosses from code into a user's file or an
  outbound message, a human or a safe format sits in between.

---

## Running it

**Locally**

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the printed URL, pick **Procurement Toolkit** in the sidebar, and choose a
tool. Every tool works with the **Local rules** engine and no API key. To use
Claude or Gemini, select it and paste a key (or set `ANTHROPIC_API_KEY` /
`GEMINI_API_KEY`).

**Deploy free** — push to GitHub and deploy on Streamlit Community Cloud (main
file `app.py`). Set any API keys as Streamlit *secrets*.

**The daily AMC job** — enable it by merging to your default branch; configure
email and register path per [`docs/amc-automation.md`](./amc-automation.md).

**Sample data** — [`samples/quotes/`](../samples/quotes/) (three vendor quotes) and
[`samples/amc/amc_register.csv`](../samples/amc/) let you try every tool end to end
without your own files.

---

## Module map

```
docdiff/
  ai_providers.py     The four AI engines behind one interface (extract/reason/ask/write)
  quote_intelligence.py  The deterministic scoring engine (analyze_quotes, SCORE_WEIGHTS)
  rag.py              Knowledge base: chunking, retrieval (embeddings + keyword), grounded Q&A
  benchmark.py        "Is this price reasonable?" — median from history via RAG
  classify.py         Spend classifier: keyword rules + LLM bounded to a fixed list
  amc.py              AMC classification, ranking, reminder drafting
  rfq.py              RFQ requirement, draft, and award memo (composes the above)
  summary.py          Rule-based change summary + optional grounded AI narrative
  align.py            The local embedding model (shared by clause-compare and RAG)
  extract.py / ocr.py / tables.py   Read text/tables out of any file format
  compare.py / segment.py / numbers.py / export.py   The original document-comparison pipeline
app.py                The Streamlit UI (one provider selector, one render_* per tool)
run_amc.py            Headless AMC monitor for the scheduled job
.github/workflows/{amc-monitor,tests}.yml   Daily automation + CI
```

---

## What this demonstrates

A working AI system for a real procurement function, built in layers that each
stand on their own:

- **Four generation engines** behind one swappable interface.
- **Grounded, cited retrieval** (RAG) over past documents, with safe persistence.
- **An autonomous, scheduled monitor** that watches dates without being asked.
- **An end-to-end agent** that runs a quotation, with human approval gates.

All of it sharing one scoring brain, and all of it keeping the decisions that
matter in auditable code. See [`docs/AI-APPRENTICESHIP-ROADMAP.md`](./AI-APPRENTICESHIP-ROADMAP.md)
for where this goes next.
