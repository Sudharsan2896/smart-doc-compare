# Sample vendor quotations

Three ready-made quotes for the **AI Quote Analysis** tool. They're for the same
imaginary procurement — solar equipment for a rural health centre — so the app
has something real to compare, rank, and flag.

Upload any two (or all three) under **Procurement Toolkit → AI Quote Analysis**.
Try each of the four engines (Local rules / Ollama / Claude / Gemini) on the same
files to see how the analysis sharpens.

## The three quotes (a deliberate spread)

| File | Vendor | Stated total | Character |
|---|---|---|---|
| `quote_sunpower.txt` | SunPower Solar Systems | ₹3,98,840 | Cheapest, but **100% advance** and short warranty |
| `quote_greenvolt.txt` | GreenVolt Energy Solutions | ₹4,33,060 | Priciest, but **best terms** — 30-day credit, 12-yr warranty, AMC + training |
| `quote_bright.txt` | Bright Renewables | ₹4,13,000 | **Missing info** — no GST, no delivery date, no warranty |

So there's no single obvious winner: cheapest ≠ best value. That's the point —
watch how the weighted score (cost, delivery, warranty, payment, technical)
trades these off, and how the Risk tab flags Bright's gaps.

## What to watch for (Local rules vs. an LLM engine)

These are plain-text files on purpose — open them in any editor to see exactly
what the tool reads, and tweak them to test your own scenarios.

One thing to look for: the **Local rules** engine reads SunPower's total as
₹3,38,000 (it matches the "Sub Total" line, because "Sub Total" contains the
word "Total"). Re-run the same file through **Claude** or **Gemini** and the
total parses correctly as the Grand Total ₹3,98,840. That single difference is a
clean illustration of what the cloud LLM engines buy you over the free rules
engine — and of why the *ranking math itself* stays in code (so a better reader
improves the inputs, but never silently changes how the winner is chosen).
