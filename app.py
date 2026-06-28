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
from docdiff.convert import pdf_to_word, word_tables_to_excel


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


def main():
    """Pick a tool from the sidebar and show it."""
    with st.sidebar:
        st.title("🧰 Document Toolkit")
        tool = st.radio(
            "Choose a tool",
            ["📑 Compare documents", "📄 PDF → Word", "📊 Word tables → Excel"],
        )
        st.divider()

    if tool.startswith("📑"):
        render_compare()
    elif tool.startswith("📄"):
        render_pdf_to_word()
    else:
        render_word_to_excel()


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
        "Pull every table out of a Word (.docx) document into an Excel workbook — "
        "each table becomes its own sheet."
    )

    docx_file = st.file_uploader("Upload a Word .docx", type=["docx"], key="word2excel")
    if not docx_file:
        st.info("⬆️ Upload a Word document to extract its tables.")
        return

    if not st.button("Extract tables to Excel", type="primary"):
        return

    with st.spinner("Extracting tables…"):
        try:
            xlsx_bytes, n_tables = word_tables_to_excel(docx_file.getvalue())
        except Exception as e:  # noqa: BLE001
            st.error("Sorry, that Word file couldn't be read. Make sure it's a "
                     "real .docx file (not an old .doc).")
            st.caption(f"Technical detail: {e}")
            return

    if n_tables == 0:
        st.warning("No tables were found in that document. The Excel file still "
                   "downloaded, with a note inside.")
    else:
        st.success(f"Found {n_tables} table(s). Download your Excel file below.")

    out_name = docx_file.name.rsplit(".", 1)[0] + "_tables.xlsx"
    st.download_button(
        "⬇️ Download Excel (.xlsx)",
        data=xlsx_bytes,
        file_name=out_name,
        mime=XLSX_MIME,
    )


if __name__ == "__main__":
    main()
