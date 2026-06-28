"""
Smart Document Comparison — MVP (digital contracts, fully local, no API key).

This file is ONLY the user interface. All the real work lives in the `docdiff`
package, in five stages: extract -> segment -> align -> compare -> export.

Run locally:   streamlit run app.py
Deploy free:   push this folder to GitHub, then deploy on Streamlit Community Cloud.
"""

from __future__ import annotations

import streamlit as st

from docdiff.extract import extract
from docdiff.segment import segment
from docdiff.align import align
from docdiff.compare import compare_pairs, Change
from docdiff.export import changes_to_excel
from docdiff.convert import pdf_to_word, extract_word_tables, build_tables_excel
from docdiff.reconcile import list_columns, reconcile


st.set_page_config(page_title="Document Toolkit", layout="wide")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# A colour + label for each change category, so the eye finds the loud stuff fast.
CATEGORY_STYLE = {
    "Number change":  ("🔴", "#fde2e1"),
    "Clause added":   ("🟠", "#ffe9d6"),
    "Clause removed": ("🟠", "#ffe9d6"),
    "Wording change": ("🟡", "#fff6cc"),
    "Formatting only": ("⚪", "#eef0f2"),
}


# Cache the embedding model across reruns so we only load it once per session.
@st.cache_resource(show_spinner=False)
def _warm_model():
    from docdiff.align import _load_model
    return _load_model()


def _human_size(num_bytes: int) -> str:
    """Format a byte count as a friendly size, e.g. '912 KB' or '1.4 MB'."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _show_uploaded_files(files):
    """Show an 'Uploaded Files' panel (name / type / size) + a success message.

    Accepts a list that may contain None entries (for not-yet-filled uploaders);
    those are skipped. The drag-and-drop uploaders themselves are Streamlit's
    built-in file_uploader, which already supports both drag-drop and Browse.
    """
    files = [f for f in files if f is not None]
    if not files:
        return
    st.markdown("#### Uploaded Files")
    st.table([
        {
            "File Name": f.name,
            "File Type": (f.name.rsplit(".", 1)[-1].upper() if "." in f.name else "—"),
            "File Size": _human_size(getattr(f, "size", 0)),
        }
        for f in files
    ])
    st.success(f"✅ {len(files)} file(s) uploaded successfully.")


def _render_ocr_report(label: str, pages: list):
    """Feature 1: per-page OCR confidence table, summary, warnings, and export."""
    from docdiff.ocr import (
        classify_confidence, summarize_confidence, build_ocr_report_excel,
    )

    st.subheader(f"OCR Confidence Report — {label}")
    st.table([
        {
            "Page": f"Page {p['page']}",
            "Confidence": f"{p['confidence']:.0f}%",
            "Status": classify_confidence(p["confidence"]),
        }
        for p in pages
    ])

    s = summarize_confidence(pages)
    cols = st.columns(4)
    cols[0].metric("Total pages", s["total_pages"])
    cols[1].metric("Average confidence", f"{s['average']:.0f}%")
    if s["lowest"]:
        cols[2].metric("Lowest page",
                       f"Pg {s['lowest']['page']} · {s['lowest']['confidence']:.0f}%")
    if s["highest"]:
        cols[3].metric("Highest page",
                       f"Pg {s['highest']['page']} · {s['highest']['confidence']:.0f}%")

    # Warnings (both can apply, per the spec's two thresholds).
    if any(p["confidence"] < 75 for p in pages):
        st.warning("⚠ Some pages have low OCR confidence and should be manually reviewed.")
    if any(p["confidence"] < 60 for p in pages):
        st.error("⚠ Critical OCR quality issues detected.")

    st.download_button(
        f"⬇️ Download OCR Confidence Report ({label})",
        data=build_ocr_report_excel(pages),
        file_name=f"ocr_confidence_{label.lower()}.xlsx",
        mime=XLSX_MIME,
        key=f"ocr_dl_{label}",
    )


def _copy_button(text: str, key: str = "copy"):
    """A 'Copy Summary' button that copies `text` to the clipboard (client-side JS,
    with an execCommand fallback for restricted iframes)."""
    import json
    import streamlit.components.v1 as components

    payload = json.dumps(text)
    components.html(
        f"""
        <button id="{key}_btn" style="padding:8px 16px;border:none;border-radius:6px;
            background:#1F2937;color:#fff;cursor:pointer;font-size:14px;">
            📋 Copy Summary</button>
        <span id="{key}_msg" style="margin-left:10px;color:#15803d;font-size:13px;"></span>
        <script>
          const txt = {payload};
          document.getElementById("{key}_btn").onclick = async () => {{
            try {{
              await navigator.clipboard.writeText(txt);
            }} catch (e) {{
              const ta = document.createElement("textarea");
              ta.value = txt; document.body.appendChild(ta); ta.select();
              document.execCommand("copy"); ta.remove();
            }}
            document.getElementById("{key}_msg").innerText = "Copied!";
          }};
        </script>
        """,
        height=48,
    )


def _render_executive_summary(changes):
    """Feature 2: business-friendly summary of the differences (rule-based)."""
    from docdiff.summary import summarize_changes, summary_to_text

    summary = summarize_changes(changes)

    st.subheader("🧾 Executive Summary")
    st.markdown(f"**{summary['total']} differences detected.**")

    sections = [
        ("commercial", "Commercial Changes"),
        ("legal", "Legal Changes"),
        ("operational", "Operational Changes"),
        ("risks", "⚠️ Risk Indicators"),
    ]
    any_shown = False
    for key, title in sections:
        bullets = summary.get(key, [])
        if bullets:
            any_shown = True
            st.markdown(f"**{title}**")
            st.markdown("\n".join(f"- {b}" for b in bullets))

    if not any_shown:
        st.caption("No commercial, legal, or operational changes were identified "
                   "beyond minor wording.")

    _copy_button(summary_to_text(summary), key="exec_summary")


def main():
    """Pick a tool from the sidebar and show it."""
    with st.sidebar:
        st.title("🧰 Document Toolkit")
        tool = st.radio(
            "Choose a tool",
            ["📑 Compare documents", "📄 PDF → Word", "📊 Word tables → Excel",
             "🔀 Reconcile data"],
        )
        st.divider()

    if tool.startswith("📑"):
        render_compare()
    elif tool.startswith("📄"):
        render_pdf_to_word()
    elif tool.startswith("📊"):
        render_word_to_excel()
    else:
        render_reconcile()


def render_compare():
    st.title("📑 Smart Document Comparison")
    st.caption(
        "Compares two contracts by **meaning**, not just text. Matches clauses "
        "even if they were reordered, and flags changed **numbers** loudly. "
        "Runs fully local — no AI API key, nothing leaves this app."
    )

    # ---------------- Sidebar settings ----------------
    with st.sidebar:
        st.header("Settings")
        threshold = st.slider(
            "Clause-match sensitivity",
            min_value=0.40, max_value=0.90, value=0.60, step=0.05,
            help="Higher = clauses must be more similar to count as 'the same "
                 "clause reworded'. Lower = matches looser paraphrases.",
        )
        show_formatting = st.checkbox(
            "Show formatting-only changes", value=False,
            help="Hide trivial spacing/punctuation differences by default.",
        )
        st.divider()
        st.caption("Supported files: PDF (incl. scanned, via OCR), Word (.docx), "
                   "Excel (.xlsx), CSV (.csv), text (.txt), Markdown (.md), and "
                   "images (.png/.jpg/.tiff, read via OCR).")

    # ---------------- File uploaders ----------------
    file_types = ["pdf", "docx", "xlsx", "csv", "txt", "md",
                  "png", "jpg", "jpeg", "tiff", "tif", "bmp"]
    col1, col2 = st.columns(2)
    with col1:
        old_file = st.file_uploader("Original document", type=file_types, key="old")
    with col2:
        new_file = st.file_uploader("Revised document", type=file_types, key="new")

    _show_uploaded_files([old_file, new_file])

    if not (old_file and new_file):
        st.info("⬆️ Upload both documents to begin.")
        return

    if not st.button("Compare documents", type="primary"):
        return

    # ---------------- Pipeline ----------------
    with st.spinner("Reading documents… (scanned files are OCR'd, which can take a moment)"):
        old_x = extract(old_file.getvalue(), old_file.name)
        new_x = extract(new_file.getvalue(), new_file.name)

    for label, x in (("Original", old_x), ("Revised", new_x)):
        if x.looks_scanned:
            st.warning(f"**{label}:** {x.note}")
        else:
            st.caption(f"{label}: {x.note}")
        if x.ocr_pages:
            _render_ocr_report(label, x.ocr_pages)

    with st.spinner("Splitting into clauses…"):
        old_segs = segment(old_x.text)
        new_segs = segment(new_x.text)

    if not old_segs or not new_segs:
        st.error("Couldn't find any text to compare. If these are scanned PDFs or "
                 "images, the scan may be too low-quality for OCR to read — try a "
                 "clearer copy.")
        return

    with st.spinner("Loading the local meaning model (first run downloads ~90 MB)…"):
        _warm_model()

    with st.spinner("Matching clauses by meaning…"):
        pairs, used_model = align(old_segs, new_segs, threshold=threshold)
        changes = compare_pairs(pairs)

    if not used_model:
        st.warning("The local meaning model couldn't load, so I used a simpler "
                   "text-similarity match. Results are still useful but less "
                   "smart about paraphrasing.")

    _render_results(changes, old_segs, new_segs, show_formatting)


def _render_results(changes, old_segs, new_segs, show_formatting):
    # Apply the "hide trivia" filter.
    visible = [c for c in changes if show_formatting or c.category != "Formatting only"]

    # ---------- Summary metrics ----------
    counts = {cat: 0 for cat in CATEGORY_STYLE}
    for c in changes:
        counts[c.category] = counts.get(c.category, 0) + 1

    st.subheader("Summary")
    m = st.columns(6)
    m[0].metric("Clauses (orig)", len(old_segs))
    m[1].metric("Clauses (revised)", len(new_segs))
    m[2].metric("🔴 Number changes", counts["Number change"])
    m[3].metric("🟠 Added/removed", counts["Clause added"] + counts["Clause removed"])
    m[4].metric("🟡 Wording", counts["Wording change"])
    m[5].metric("⚪ Formatting", counts["Formatting only"])

    if not visible:
        st.success("No material changes found. 🎉")
        return

    # ---------- Executive summary (business-friendly, rule-based) ----------
    _render_executive_summary(visible)

    # ---------- Export ----------
    st.download_button(
        "⬇️ Download Excel report",
        data=changes_to_excel(visible),
        file_name="document_changes.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # ---------- Ranked change list ----------
    st.subheader(f"Ranked changes ({len(visible)})")
    st.caption("Most important at the top: number changes, then added/removed "
               "clauses, then wording, then formatting.")

    for c in visible:
        icon, color = CATEGORY_STYLE.get(c.category, ("•", "#eef0f2"))
        header = f"{icon} **{c.category}** — clause `{c.label}`"
        if c.category == "Wording change":
            header += f"  ·  {int(c.similarity * 100)}% similar"

        with st.expander(header, expanded=(c.category == "Number change")):
            if c.number_changes:
                st.markdown("**Figures that changed:**")
                rows = [
                    {"Old": nc.old, "New": nc.new, "Change": nc.description}
                    for nc in c.number_changes
                ]
                st.table(rows)

            st.markdown(
                f"<div style='background:{color};color:#1a1a1a;padding:10px;"
                f"border-radius:6px'>{c.diff_html}</div>",
                unsafe_allow_html=True,
            )


def render_pdf_to_word():
    st.title("📄 PDF → Word")
    st.caption(
        "Turn a **digital** PDF into an editable Word (.docx) file, keeping text, "
        "layout and tables. (Scanned/photographed PDFs need OCR, coming later.)"
    )

    pdf_file = st.file_uploader("Upload a PDF", type=["pdf"], key="pdf2word")
    if not pdf_file:
        st.info("⬆️ Upload a PDF to convert.")
        return

    _show_uploaded_files([pdf_file])

    if not st.button("Convert to Word", type="primary"):
        return

    with st.spinner("Converting… (large PDFs can take a minute)"):
        try:
            docx_bytes = pdf_to_word(pdf_file.getvalue())
        except Exception as e:  # noqa: BLE001 — show a friendly message, not a crash
            st.error(
                "Sorry, that PDF couldn't be converted. It may be scanned (an "
                "image rather than real text), password-protected, or corrupted."
            )
            st.caption(f"Technical detail: {e}")
            return

    out_name = pdf_file.name.rsplit(".", 1)[0] + ".docx"
    st.success("Done! Download your Word file below.")
    st.download_button(
        "⬇️ Download Word (.docx)",
        data=docx_bytes,
        file_name=out_name,
        mime=DOCX_MIME,
    )


def render_word_to_excel():
    st.title("📊 Word tables → Excel")
    st.caption(
        "Extract tables from a Word (.docx) document into Excel. **Preview** the "
        "tables first, choose which to keep, rename their sheets, and optionally "
        "merge tables together before exporting."
    )

    docx_file = st.file_uploader("Upload a Word .docx", type=["docx"], key="word2excel")
    if not docx_file:
        st.info("⬆️ Upload a Word document to extract its tables.")
        return

    _show_uploaded_files([docx_file])

    # --- Read the tables for preview ---
    try:
        tables = extract_word_tables(docx_file.getvalue())
    except Exception as e:  # noqa: BLE001
        st.error("Sorry, that Word file couldn't be read. Make sure it's a "
                 "real .docx file (not an old .doc).")
        st.caption(f"Technical detail: {e}")
        return

    if not tables:
        st.warning("No tables were found in that document.")
        return

    layout = st.radio(
        "How should the tables be saved?",
        ["Separate sheet per table", "All tables on one sheet"],
        help="'Separate sheet per table' puts each kept table on its own Excel tab. "
             "'All tables on one sheet' stacks them on a single tab, each under its "
             "sheet name as a label.",
    )
    separate_sheets = layout.startswith("Separate")

    # --- Preview each table with keep / sheet-name / merge controls ---
    import pandas as pd
    st.subheader("Tables detected")

    keeps: list[bool] = []
    names: list[str] = []
    merges: list[bool] = []
    for i, rows in enumerate(tables, start=1):
        n_rows = len(rows)
        n_cols = len(rows[0]) if rows else 0
        st.markdown(f"**Table {i}** · {n_rows} rows × {n_cols} columns")
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("(this table is empty)")

        c1, c2, c3 = st.columns([1, 2, 2])
        keep = c1.checkbox("Keep", value=True, key=f"w2e_keep_{i}")
        name = c2.text_input("Sheet name", value=f"Table_{i}", key=f"w2e_name_{i}")
        merge = c3.checkbox(
            "Merge with previous", value=False, key=f"w2e_merge_{i}",
            disabled=(i == 1),
            help="Add this table's rows onto the previous kept table, in the same sheet.",
        )
        keeps.append(keep)
        names.append(name)
        merges.append(merge)
        st.divider()

    # --- Turn the user's choices into export specs (apply keep, then merge) ---
    specs: list[dict] = []
    for i, rows in enumerate(tables):
        if not keeps[i]:
            continue
        if merges[i] and specs:
            specs[-1]["rows"].extend(rows)          # merge into previous kept group
        else:
            specs.append({"name": names[i].strip() or f"Table_{i + 1}",
                          "rows": list(rows)})

    total_found = len(tables)
    total_selected = sum(1 for k in keeps if k)
    a, b = st.columns(2)
    a.metric("Total tables found", total_found)
    b.metric("Total tables selected", total_selected)

    if total_selected == 0:
        st.warning("No tables selected. Tick at least one 'Keep' to export.")
        return

    if not st.button("Generate Excel", type="primary"):
        return

    with st.spinner("Building Excel…"):
        try:
            xlsx_bytes = build_tables_excel(specs, separate_sheets=separate_sheets)
        except Exception as e:  # noqa: BLE001
            st.error("Something went wrong while building the Excel file.")
            st.caption(f"Technical detail: {e}")
            return

    out_name = docx_file.name.rsplit(".", 1)[0] + "_tables.xlsx"
    unit = "sheet" if separate_sheets else "section"
    st.success(f"Done! Exporting {len(specs)} {unit}(s) from {total_selected} kept table(s).")
    st.download_button(
        "⬇️ Download Excel (.xlsx)",
        data=xlsx_bytes,
        file_name=out_name,
        mime=XLSX_MIME,
    )


def render_reconcile():
    st.title("🔀 Reconcile data")
    st.caption(
        "Compare an **old** export against a **new** one, record by record, keyed "
        "on an ID column. Finds records that are **missing**, **changed**, or "
        "**newly added**, and exports a full Excel report. Works with CSV and "
        "Excel (.xlsx) files (reconciliation needs tabular data with an ID column, "
        "so PDFs/Word/images don't apply here)."
    )

    c1, c2 = st.columns(2)
    with c1:
        old_file = st.file_uploader("Original (old) data — CSV or Excel",
                                    type=["csv", "xlsx"], key="rec_old")
    with c2:
        new_file = st.file_uploader("Revised (new) data — CSV or Excel",
                                    type=["csv", "xlsx"], key="rec_new")

    _show_uploaded_files([old_file, new_file])

    if not (old_file and new_file):
        st.info("⬆️ Upload both files to begin.")
        return

    # Read the old file's columns so the user can pick the ID column from a list.
    try:
        cols = list_columns(old_file.getvalue(), old_file.name)
    except Exception as e:  # noqa: BLE001
        st.error("Couldn't read the original file as a table. Make sure it's a "
                 "proper CSV or Excel file.")
        st.caption(f"Technical detail: {e}")
        return

    if not cols:
        st.error("The original file has no columns to compare.")
        return

    default_idx = cols.index("name") if "name" in cols else 0
    id_column = st.selectbox(
        "Which column is the unique ID?", cols, index=default_idx,
        help="The column that uniquely identifies each record (e.g. an ID or "
             "reference number).",
    )

    with st.expander("Advanced options"):
        prefix = st.text_input(
            "ID prefix to ignore in the new file (optional)", value="",
            help="If IDs in the new file were given a prefix (e.g. 'OLD-'), enter "
                 "it so those records still match the old file.",
        )
        exclude = st.multiselect(
            "Columns to ignore when comparing values", cols, default=[],
            help="Pick columns whose differences you don't care about (e.g. "
                 "timestamps). Common system fields are ignored automatically.",
        )

    if not st.button("Reconcile", type="primary"):
        return

    with st.spinner("Reconciling…"):
        try:
            xlsx_bytes, summary = reconcile(
                old_file.getvalue(), old_file.name,
                new_file.getvalue(), new_file.name,
                id_column=id_column, prefix_to_strip=prefix.strip(),
                exclude_columns=exclude,
            )
        except ValueError as e:
            st.error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            st.error("Something went wrong while reconciling the two files.")
            st.caption(f"Technical detail: {e}")
            return

    st.success("Done! Download the full report below.")
    m = st.columns(4)
    m[0].metric("Records in old", summary.get("Total in Old", 0))
    m[1].metric("Records in new", summary.get("Total in New", 0))
    m[2].metric("Missing in new", summary.get("Missing in New", 0))
    m[3].metric("Field mismatches", summary.get("Total Field-Level Mismatches", 0))

    st.download_button(
        "⬇️ Download reconciliation report (.xlsx)",
        data=xlsx_bytes,
        file_name="reconciliation_report.xlsx",
        mime=XLSX_MIME,
    )


if __name__ == "__main__":
    main()
