"""
Homepage
"""

import xml.etree.ElementTree as ET
import streamlit as st

def _strip_namespaces(root):
    had_ns = False
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
            had_ns = True
        el.attrib = {
            (k.split("}", 1)[1] if "}" in k else k): v
            for k, v in el.attrib.items()
        }
    return had_ns


st.set_page_config(
    page_title="LExport",
    page_icon="📚",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared session-state initialisation
# Every page can read st.session_state.xml_root and st.session_state.xml_filename
# ─────────────────────────────────────────────────────────────────────────────
if "xml_root" not in st.session_state:
    st.session_state.xml_root = None
if "xml_filename" not in st.session_state:
    st.session_state.xml_filename = ""

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar: show upload status on every page
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.session_state.xml_root is not None:
        st.success(f"✅ **{st.session_state.xml_filename}**")
        if st.button("Upload a different file"):
            st.session_state.xml_root = None
            st.session_state.xml_filename = ""
            st.rerun()
    else:
        st.warning("No file loaded yet.")

# ─────────────────────────────────────────────────────────────────────────────
# Home page content
# ─────────────────────────────────────────────────────────────────────────────
st.title("LExport")
st.markdown(
    "Welcome to **LExport**, a toolkit for converting lexical XML dictionaries "
    "into various output formats. Upload your XML file once here, then use "
    "the sidebar to navigate to any converter."
)

st.markdown("---")

# ── Upload widget ─────────────────────────────────────────────────────────────
st.markdown("### Upload your XML dictionary")

if st.session_state.xml_root is not None:
    st.success(
        f"✅ **{st.session_state.xml_filename}** is loaded and ready. "
        "Use the sidebar to navigate to a converter."
    )
else:
    uploaded = st.file_uploader("Choose an XML dictionary file", type=["xml", "lift"])
    if uploaded:
        try:
            raw = uploaded.read()
            root = ET.fromstring(raw.decode("utf-8"))
            had_ns = _strip_namespaces(root)
            st.session_state.xml_root = root
            st.session_state.xml_filename = uploaded.name
            if had_ns:
                st.info("XML namespaces stripped automatically (TEI file).")
            st.success(f"✅ **{uploaded.name}** parsed successfully.")
            st.rerun()
        except ET.ParseError as e:
            st.error(f"❌ Could not parse XML: {e}")

st.markdown("---")
with st.expander("ℹ️ TEI P5 dictionary files"):
    st.markdown(
        "TEI XML files using xmlns=http://www.tei-c.org/ns/1.0 are fully supported. "
        "Namespace prefixes are stripped automatically on upload so wizard "
        "dropdowns show plain tag names as usual."
    )
    st.table({
        "Wizard field": ["Entry tag", "Lemma path", "IPA", "POS tag",
            "Sense wrapper", "Definition tag", "Example wrapper",
            "Example text / lang attr", "Translation path (LIFT mode)",
            "Relation wrapper", "Relation target",
            "Variant wrapper", "Variant form tag",
            "Etymology wrapper", "Etymology form", "Etymology language"],
        "TEI tag / path": ["entry", "./form[@type=lemma]/orth  (disambiguate when prompted)",
            "pron", "pos", "sense", "def",
            "cit -> disambiguate to ./sense/cit[@type=example]",
            "quote  /  lang", "./cit[@type=translation]/quote",
            "xr (type attr: syn/ant)", "ref",
            "form -> disambiguate to ./form[@type=variant]", "orth",
            "etym", "mentioned", "lang"],
    })

with st.expander("ℹ️ LIFT dictionary files"):
    st.markdown(
        "LIFT (Lexicon Interchange FormaT) is the XML dictionary format used by "
        "FieldWorks Language Explorer (FLEx), WeSay, and Lexique Pro. A LIFT export "
        "is plain XML, so it works here too — upload the `.lift` file directly "
        "(the uploader accepts a `.lift` extension as well as `.xml`), or rename a "
        "copy to `.xml` if you prefer. Full technical details are in SIL's "
        "[Technical Notes on LIFT used in FLEx](https://downloads.languagetechnology.org/fieldworks/Documentation/Technical%20Notes%20on%20LIFT%20used%20in%20FLEx.pdf)."
    )
    st.markdown(
        "Unlike TEI, LIFT has no namespace to strip, but it leans heavily on "
        "**attributes** rather than nested tags: the language of a form lives in a "
        "`lang` attribute, a category value sits in a `value` attribute on "
        "`grammatical-info`, and the actual text is usually one level deeper, inside "
        "a `<text>` child of `<form>`. Keep that in mind when the wizard asks for a "
        "tag vs. an attribute vs. a sub-path."
    )
    st.table({
        "Wizard field": ["Entry tag", "Lemma path", "IPA", "POS tag / attribute",
            "Sense wrapper", "Definition tag", "Example wrapper",
            "Example text / lang attr", "Translation path (LIFT mode)",
            "Relation wrapper", "Relation target",
            "Variant wrapper", "Variant form tag",
            "Etymology wrapper", "Etymology form", "Etymology language"],
        "LIFT tag / path": ["entry", "./lexical-unit/form/text  (disambiguate by lang attr)",
            "./pronunciation/form/text  (lang ending in -fonipa)",
            "grammatical-info  (attribute: value)", "sense",
            "gloss/text  (or ./definition/form/text for the fuller definition)",
            "example",
            "form/text  /  lang attr on form", "./translation/form/text",
            "relation  (attribute: type)", "ref  (attribute holding the target id/guid)",
            "variant  (direct child of entry)", "form/text",
            "etymology  (attributes: source, type)", "form/text",
            "source  (attribute on the etymology element)"],
    })

# ── Available converters ──────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### Available converters")

converters = [
    {
        "icon": "📖",
        "name": "Wiktionary",
        "desc": (
            "Convert your XML dictionary to Wiktionary wikitext. "
        ),
        "page": "pages/1_Wiktionary.py",
    },
    {
        "icon": "📊",
        "name": "EvoSem TSV",
        "desc": (
            "Export your dictionary as a tab-separated values file (.tsv) "
            "for use with EvoSem."
        ),
        "page": "pages/2_EvoSem_TSV.py",
    },
    {
        "icon": "📝",
        "name": "NooJ Dictionary",
        "desc": (
            "Convert your XML dictionary to NooJ's .dic format. "
        ),
        "page": "pages/3_NooJ.py",
    },
    {
        "icon": "🌐",
        "name": "DIG4EL DCQs",
        "desc": (
            "Export example sentences and their translations as a "
            "DIG4EL DCQ/SP JSON file."
        ),
        "page": "pages/4_DIG4EL.py",
    },
]

cols = st.columns(len(converters))
for col, c in zip(cols, converters):
    with col:
        st.markdown(
            f"""
            <div style="border:1px solid #ddd; border-radius:8px; padding:1.2rem;
                        height:100%; background:#fafafa;">
                <h3 style="margin-top:0">{c['icon']} {c['name']}</h3>
                <p style="color:#555; font-size:0.9rem">{c['desc']}</p>
                <p style="font-size:0.8rem; color:#888">
                    → Use the sidebar to navigate there
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("---")
st.markdown(
    "<small style='color:#aaa'>LExport</small>",
    unsafe_allow_html=True,
)
