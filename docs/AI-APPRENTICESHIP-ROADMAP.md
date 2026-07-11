# AI Transformation Roadmap — Procurement & Logistics @ SELCO Foundation

> A personalised roadmap for Sudharsan: moving from *user of AI* to *builder of
> AI-powered systems*, anchored to real procurement work and to the
> `smart-doc-compare` project already in this repo.
>
> This is a working document. It is opinionated on purpose — a roadmap that
> refuses to prioritise is just a list. Where I rank things, the reasoning is
> shown so you can disagree with the logic, not just the conclusion.

---

## 0. The one idea to carry through everything

**The LLM is never the source of truth for a decision that has a right answer.**

You already built this instinct into `docdiff/quote_intelligence.py`: the vendor
ranking is computed with plain weighted math (`SCORE_WEIGHTS`), and the language
model is only allowed to *explain* the result, never to *decide* it. Keep this
rule in front of every tool you build. It is the single thing that makes an AI
system safe to put in front of a procurement committee, an auditor, or a donor.

Everything below is an application of that idea to a different corner of your job.

---

## Phase 2 — Your job, audited for AI leverage

I went through the work you listed (procurement ops, vendor management, RFQ,
benchmarking, ERPNext, Excel/Power BI/Power Query, AMC tracking, logistics,
dashboards). For each repetitive task: what AI does to it, rough time saved, and
the honest risk. "Improve / Automate / Eliminate" is the McKinsey lens —
*improve* = human still drives, faster; *automate* = machine drives, human
reviews; *eliminate* = the task stops existing.

| Task | AI move | Improve / Automate / Eliminate | Time saved (est.) | Main risk |
|---|---|---|---|---|
| Comparing supplier quotes (RFQ) | Extract fields + weighted score + narrate | **Automate** (you already built the core) | 60–80% per RFQ round | Mis-read totals/units → *mitigate with confidence flags, which you have* |
| Reading clauses across contract versions | Embedding match + number-change flagging | **Automate** (this repo) | ~90% vs. line-by-line | Scanned docs need OCR (roadmap item) |
| Benchmarking prices across past POs | Retrieval over historical PO data (RAG) | **Improve → Automate** | 50% | Stale/dirty history → garbage benchmarks |
| Vendor follow-up emails / RFQ dispatch | Templated draft + merge from ERPNext | **Automate** | 70% of drafting | Tone/commitments — keep human send |
| AMC expiry tracking & reminders | Scheduled agent watches dates, alerts owner | **Eliminate** the manual check | ~100% of the *watching* | Bad source data → missed renewal |
| Extracting line items from PDF/scanned quotes | OCR + table parse (`ocr.py`, `tables.py`) | **Improve** | 40–60% | OCR errors on poor scans |
| Recurring Excel/Power Query cleanups | LLM writes the transform once; you reuse | **Improve** | One-time, then near-100% | Silent logic errors — test on a known sheet |
| Dashboard commentary ("what changed this month") | LLM narrates over the *computed* numbers | **Automate** | 80% of the write-up | Hallucinated causes — feed it only the data |
| Categorising spend / GL coding | Classifier over item descriptions | **Automate** | 60% | Edge cases — route low-confidence to human |
| Drafting procurement notes / committee summaries | LLM first draft from structured facts | **Improve** | 50% | Never let it invent numbers |

**Read the pattern:** the wins are all *extract → compute deterministically →
let AI explain*. The failures are all *let AI decide the number*. That's not a
coincidence, it's the rule from Phase 0.

**Cost/savings framing (rough, for a business case):** if RFQ comparison alone
takes you ~2 hrs × ~8 rounds/month, automating 70% of it is ~11 hrs/month back —
call it ~130 hrs/year of skilled procurement time redirected from clerical
comparison to actual negotiation and vendor development. That is the sentence you
put in the deck, not "AI is transformative."

---

## Phase 3 — Tools you can build, laddered by difficulty

Not 100 filler ideas — a curated ladder where each rung teaches the next skill.
Build roughly top-to-bottom and you learn the whole stack in order.

### Level 1 — No-code (learn: prompting, structured output)
1. **Quote-summary prompt pack** — paste a quote, get fields + red flags. Teaches you what fields matter before you automate them.
2. **AMC reminder in Power Automate / Google Sheets** — date math + email. No AI yet, but the automation muscle.
3. **Vendor email drafter** — a saved prompt that turns bullet points into a professional RFQ email.

### Level 2 — Low-code (learn: connecting tools, data in/out)
4. **ERPNext → Sheet → LLM digest** — nightly "what POs are pending, what's overdue" narrated summary.
5. **Power BI narrative tile** — LLM writes the "what changed" paragraph over your existing measures.
6. **Spend-category classifier** in a spreadsheet via an LLM formula add-in.

### Level 3 — Beginner coding (learn: Python, calling a model)
7. **Extend `smart-doc-compare`**: add a real Claude/Gemini provider next to Ollama in `ai_providers.py` (the architecture already supports it — this is a ~1-file change and a perfect first PR).
8. **RFQ intake bot** — a script that watches an email/folder, parses each quote with your existing `parse_quote_file`, and appends to a running comparison sheet.
9. **PO anomaly flagger** — script over historical POs: flag prices >X% off the running median for that item.

### Level 4 — Intermediate coding (learn: RAG, vector DBs, retrieval)
10. **Procurement knowledge base (RAG)** — embed your past contracts, policies, and vendor history; ask it "what warranty did we get from Vendor X last time?" This is `align.py`'s embedding idea, scaled up with a vector store (Chroma/FAISS).
11. **Benchmark assistant** — retrieval over historical prices to answer "is this quote reasonable?" with citations to the source PO.
12. **Contract clause library** — semantic search over all your clauses so you can reuse the strongest terms.

### Level 5 — Advanced AI systems (learn: agents, MCP, multi-step autonomy)
13. **RFQ agent** — given a requirement, drafts the RFQ, dispatches to vendors, ingests replies, runs your comparison engine, and produces a committee-ready recommendation memo. Human approves at two gates (send, and award).
14. **AMC autonomous monitor** — an agent that owns the renewal calendar, escalates, and drafts renewal paperwork.
15. **MCP server for ERPNext** — expose procurement data as tools so any AI assistant (including this one) can query POs, vendors, and stock safely with permissioned access.

**The honest ladder rule:** don't skip to Level 5. Ship #7 (a real provider in
this repo) first. It's small, it's real, and it teaches the provider-abstraction
pattern that everything above depends on.

---

## Phase 6 — Ranked: what to actually build first (the VC lens)

Scored on *impact × ROI × speed-to-ship × how much it teaches you*. Weighted
toward "ship something real this month."

| Rank | Build | Why it wins | Effort |
|---|---|---|---|
| 🥇 1 | **Claude/Gemini provider in this repo** (idea #7) | Turns your working tool into a *sharp* tool; tiny effort; unlocks Level 4/5; a real PR you can show | ½ day |
| 🥈 2 | **AI "why this change matters" summary layer** (the repo's own roadmap item #3) | Highest user-visible value on a tool you already deployed; safe because it narrates computed facts | 1–2 days |
| 🥉 3 | **Procurement RAG knowledge base** (idea #10) | Highest *career* leverage — RAG is the skill everyone wants; directly reusable at SELCO | 1 week |
| 4 | **AMC autonomous monitor** (idea #14) | Highest *operational* payoff (eliminates a recurring chore) | 1 week |
| 5 | **RFQ agent** (idea #13) | Biggest ambition/impact, but do it *last* — it composes everything above | 2–3 weeks |

**If you build one thing this week:** #1. **If you build one thing this quarter
that changes your career:** #3.

---

## Phase 7 — Your 5-year skills arc

Procurement roles don't get "replaced by AI" — the clerical *layer* of them does,
and the people who direct the AI move up. You are already positioning correctly by
building tools instead of just using them.

- **What AI eats in procurement:** manual quote comparison, data entry into ERP, first-draft correspondence, routine spend classification, dashboard write-ups.
- **What AI creates:** AI-tool ownership, prompt/agent design for ops, data-quality stewardship (RAG is only as good as your history), vendor-relationship work that can't be automated, and *judgment on the AI's output* — someone has to be accountable for the award decision.
- **Your edge:** you have the domain (procurement + NGO + solar/livelihood context) *and* the builder instinct. That combination is rarer than either skill alone.

**Roadmap:**

| Year | Focus | Concrete milestone |
|---|---|---|
| 2026 (now) | Ship real tools; solidify Python + LLM APIs + RAG | This repo has a cloud provider + summary layer; you've built one RAG system |
| 2027 | Agents & orchestration (LangGraph / MCP); own an automated workflow end-to-end at SELCO | AMC or RFQ agent live for your team |
| 2028 | Systems thinking: data platform + governance for AI in an NGO | You're the person who says *how* AI gets used org-wide |
| 2029 | Lead: design AI-enabled procurement/ops for SELCO-like orgs; mentor others | A reusable "AI ops kit" other NGOs adopt |
| 2030 | Strategy: AI-enabled operator in the social-impact sector | You architect, others build |

**Skills to bank, in order:** Python fluency → LLM API patterns (structured
output, function calling) → RAG + vector stores → agents (LangGraph/CrewAI) →
MCP → evaluation & governance. Learn each one *by building the corresponding tool
above*, never in the abstract.

---

## How we use this doc

Pick a rank-1 or rank-2 item from Phase 6 and we build it together in this repo —
and I teach the underlying AI concept (embeddings, RAG, agents, MCP, function
calling) at the exact moment you need it, using your own code as the textbook.
That's the apprenticeship. The phases above are the map; the repo is the terrain.

*Next suggested move: implement the Claude/Gemini provider in `docdiff/ai_providers.py` — smallest real step, and it opens everything else.*
