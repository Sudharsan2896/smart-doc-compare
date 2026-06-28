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


st.set_page_config(page_title="Smart Document Comparison", layout="wide")

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
        st.caption("Supported files: PDF, Word (.docx), Excel (.xlsx), "
                   "text (.txt), Markdown (.md). "
                   "Scanned PDFs (OCR) and the optional AI summary come later.")

    # ---------------- File uploaders ----------------
    file_types = ["pdf", "docx", "xlsx", "txt", "md"]
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
    with st.spinner("Reading documents…"):
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
        st.error("Couldn't find any text to compare. If these are scanned PDFs, "
                 "OCR support is coming in a later version.")
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


if __name__ == "__main__":
    main()
