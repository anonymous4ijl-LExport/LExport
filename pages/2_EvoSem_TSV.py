"""
LP — EvoSem TSV converter
Converts a lexical XML dictionary to a .tsv file for use with EvoSem.
"""

import csv
import io
import re
from urllib.parse import quote
import xml.etree.ElementTree as ET

import streamlit as st
from lp_utils import (
    require_xml, sidebar_file_status, tag_selector, show_sample_values,
    wizard_progress, nav_buttons, collect_child_tags, get_unique_text_values,
    sample_entries, find_text, get_elem_plain_text,
    to_relative_xpath, iter_path, get_field_value, get_unique_field_values,
    collect_element_attrs, parse_field_spec,
)

st.set_page_config(page_title="LP — EvoSem TSV", page_icon="📊", layout="wide")
sidebar_file_status()
root, fname = require_xml()

# ─────────────────────────────────────────────────────────────────────────────
# Session-state defaults (all keys prefixed "evosem_")
# ─────────────────────────────────────────────────────────────────────────────
EVOSEM_DEFAULTS = {
    "evosem_step": 0,

    # Step 1 — Entry & structure
    "evosem_entry_tag": "",
    "evosem_lemma_path": "",
    "evosem_citation_path": "",
    "evosem_pos_path": "",
    "evosem_homonym_path": "",

    # Step 2 — Etymology
    "evosem_etymon_wrapper": "",   # e.g. "Étymologie/Étymon"
    "evosem_etymon_form_tag": "",  # e.g. "ReprésentationDeForme"
    "evosem_etymon_lang_tag": "",  # e.g. "Langue"
    "evosem_etymon_source_tag": "",# e.g. "SourceÉtymon" (optional)
    "evosem_etymon_which": "last", # "last" or "first"
    # LIFT-style: read etymon form from a field child element
    "evosem_etymon_form_field_type": "",  # e.g. "comment" for <field type="comment">
    "evosem_etymon_form_field_lang": "",  # e.g. "en" for <form lang="en">
    # LIFT-style: read language/family from an attribute on the wrapper
    "evosem_etymon_lang_attr": "",        # e.g. "source" or "type"
    # Attribute-based POS reading
    "evosem_pos_attr": "",                # e.g. "value" for grammatical-info@value

    # Step 3 — Family mapping
    # Maps each XML lang code → family name shown in TSV
    "evosem_lang_map": {},
    # Lang codes to SKIP entirely (won't produce a row)
    "evosem_skip_langs": [],

    # Step 4 — Reflex
    # POS codes that should use citation form (not representation form)
    "evosem_citation_pos": [],
    # Transformations applied to reflex string
    "evosem_reflex_strip_degree": True,   # strip °
    "evosem_reflex_strip_brackets": True, # strip [ ] ‹ › ~
    # Extra strings/characters to strip from reflex (comma-separated)
    "evosem_reflex_strip_extra": "",

    # Step 5 — Definitions
    "evosem_def_tag": "",          # e.g. "ReprésentationDeTexte" or "gloss"
    "evosem_def_lang_attr": "",    # e.g. "langue" or "lang"
    "evosem_def_lang_val": "",     # e.g. "eng" or "en"
    # Path from sense to definition element (supports LIFT gloss structure)
    "evosem_def_sense_tag": "",      # e.g. "sense" (LIFT) or leave blank for Mwotlap
    "evosem_def_text_path": "",      # path within gloss/def to text, e.g. "text"
    "evosem_sci_name_tag": "",     # e.g. "NomScientifique" (optional)
    "evosem_def_strip_parens": True,
    "evosem_def_strip_style": True,
    # Extra strings/characters to strip from definitions (comma-separated)
    "evosem_def_strip_extra": "",

    # Step 6 — Link & output
    "evosem_link_base_url": "",    # e.g. "https://marama.huma-num.fr/Lex/Mwotlap/"
    "evosem_link_suffix": ".htm",  # suffix before #fragment
    # LIFT / generic: direct URL pattern using {lemma} and {lang_code} placeholders
    "evosem_link_pattern": "",       # e.g. "https://en.wiktionary.org/wiki/{lemma}"
    "evosem_language_code": "",    # e.g. "mlv"
    "evosem_canonical_name": "",   # e.g. "Mwotlap"
    "evosem_columns": [            # ordered list of column names
        "Family", "Etymon", "Language", "Canonical name",
        "Reflex", "Transliteration", "Definition", "Definition_raw",
        "Etymon link", "Link",
    ],

    "evosem_output": "",
}
for k, v in EVOSEM_DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_text(elem):
    return elem.text.strip() if elem is not None and elem.text else ""

def extract_text_without_style(elem):
    parts = []
    for node in elem.iter():
        if node.tag == "style":
            continue
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    return "".join(parts)

def clean_definition(text, strip_parens=True):
    text = re.sub(r'\s+', ' ', text)
    if strip_parens:
        text = re.sub(r'\(.*?\)', '', text)
    return text.strip()

# ─────────────────────────────────────────────────────────────────────────────
# Conversion engine
# ─────────────────────────────────────────────────────────────────────────────
def run_conversion(root, cfg):
    entry_tag         = cfg["evosem_entry_tag"]
    lemma_path        = cfg["evosem_lemma_path"]
    citation_path     = cfg["evosem_citation_path"]
    pos_path          = cfg["evosem_pos_path"]
    homonym_path      = cfg["evosem_homonym_path"]
    etymon_wrapper    = cfg["evosem_etymon_wrapper"]
    etymon_form_tag   = cfg["evosem_etymon_form_tag"]
    etymon_lang_tag   = cfg["evosem_etymon_lang_tag"]
    etymon_source_tag = cfg["evosem_etymon_source_tag"]
    etymon_which      = cfg["evosem_etymon_which"]
    etymon_form_field_type = cfg.get("evosem_etymon_form_field_type", "")
    etymon_form_field_lang = cfg.get("evosem_etymon_form_field_lang", "")
    etymon_lang_attr       = cfg.get("evosem_etymon_lang_attr", "")
    pos_attr               = cfg.get("evosem_pos_attr", "")
    lang_map          = cfg["evosem_lang_map"]
    skip_langs        = set(cfg["evosem_skip_langs"])
    citation_pos      = set(cfg["evosem_citation_pos"])
    strip_degree      = cfg["evosem_reflex_strip_degree"]
    strip_brackets    = cfg["evosem_reflex_strip_brackets"]
    reflex_strip_extra = [s.strip() for s in cfg.get("evosem_reflex_strip_extra", "").split(",") if s.strip()]
    def_tag           = cfg["evosem_def_tag"]
    def_lang_attr     = cfg["evosem_def_lang_attr"]
    def_lang_val      = cfg["evosem_def_lang_val"]
    def_sense_tag     = cfg.get("evosem_def_sense_tag", "")
    def_text_path     = cfg.get("evosem_def_text_path", "")
    sci_tag           = cfg["evosem_sci_name_tag"]
    strip_parens      = cfg["evosem_def_strip_parens"]
    strip_style       = cfg["evosem_def_strip_style"]
    def_strip_extra   = [s.strip() for s in cfg.get("evosem_def_strip_extra", "").split(",") if s.strip()]
    link_base         = cfg["evosem_link_base_url"].rstrip("/")
    link_suffix       = cfg["evosem_link_suffix"]
    link_pattern      = cfg.get("evosem_link_pattern", "")
    language_code     = cfg["evosem_language_code"]
    canonical_name    = cfg["evosem_canonical_name"]
    columns           = cfg["evosem_columns"]

    rows = []

    for entry in root.iter(entry_tag):
        # ── Etymology ──────────────────────────────────────────────────────
        etymons = entry.findall(f".//{etymon_wrapper}") if etymon_wrapper else []
        if not etymons:
            continue

        target_etymon = etymons[-1] if etymon_which == "last" else etymons[0]

        # Standard: child tag holds the form
        etymon_form_el = target_etymon.find(etymon_form_tag) if etymon_form_tag else None
        # LIFT fallback: form is inside <field type="..."><form lang="..."><text>
        if etymon_form_el is None and etymon_form_field_type:
            for fld in target_etymon.findall("field"):
                if fld.get("type") == etymon_form_field_type:
                    if etymon_form_field_lang:
                        fform = next((f for f in fld.findall("form")
                                      if f.get("lang") == etymon_form_field_lang), None)
                    else:
                        fform = fld.find("form")
                    if fform is not None:
                        etymon_form_el = fform.find("text")
                    break
        if etymon_form_el is None:
            continue

        etymon = (
            get_text(etymon_form_el)
            .replace("ʀ", "R")
            .replace("(?)", "")
            .replace("(", "")
            .replace(")", "")
            .replace("°", "*")
            .strip()
        )
        if not etymon:
            continue

        # ── Family ────────────────────────────────────────────────────────
        # Standard: child tag; LIFT fallback: attribute on wrapper
        lang_el = target_etymon.find(etymon_lang_tag) if etymon_lang_tag else None
        family_code = get_text(lang_el) if lang_el is not None else ""
        if not family_code and etymon_lang_attr:
            family_code = target_etymon.get(etymon_lang_attr, "").strip()
        if family_code in skip_langs:
            continue
        family = lang_map.get(family_code, family_code)
        if not family:
            continue

        # ── Etymon link ────────────────────────────────────────────────────
        etymon_link = ""
        if etymon_source_tag:
            src_el = target_etymon.find(etymon_source_tag)
            etymon_link = get_text(src_el) if src_el is not None else ""

        # ── Lemma forms ────────────────────────────────────────────────────
        repr_form = find_text(entry, lemma_path) if lemma_path else ""
        cit_form  = find_text(entry, citation_path) if citation_path else ""
        _pos_spec = f"{pos_path}@{pos_attr}" if pos_attr else pos_path
        pos_code  = get_field_value(entry, _pos_spec) if _pos_spec else ""
        homonym   = find_text(entry, homonym_path) if homonym_path else ""

        # ── Reflex ─────────────────────────────────────────────────────────
        if pos_code in citation_pos and cit_form:
            reflex = cit_form
        else:
            reflex = repr_form

        if strip_degree:
            reflex = reflex.replace("°", "")
        if strip_brackets:
            reflex = (reflex
                .replace("[", "").replace("]", "")
                .replace("‹", "").replace("›", "")
                .replace("~", ""))
        for s in reflex_strip_extra:
            reflex = reflex.replace(s, "")
        reflex = reflex.strip()

        # ── Link ───────────────────────────────────────────────────────────
        link = ""
        if link_pattern:
            # Generic pattern: {lemma}, {lang_code}, {citation} placeholders
            _lm = cit_form or repr_form
            link = (link_pattern
                    .replace("{lemma}", quote(_lm, safe=""))
                    .replace("{lang_code}", language_code)
                    .replace("{citation}", quote(cit_form, safe="")))
        elif link_base and repr_form:
            first_letter = repr_form[0].lower()
            fragment = f"ⓔ{repr_form}"
            if homonym:
                fragment += f"ⓗ{homonym}"
            link = f"{link_base}/{first_letter}{link_suffix}#{quote(fragment, safe='')}"

        # ── Definitions ────────────────────────────────────────────────────
        defs = []
        sci_names = set()

        def collect_defs(parent):
            # ── Mwotlap-style: ./Sens/Définition/def_tag ──────────────────
            if not def_sense_tag:
                for sens in parent.findall("./Sens"):
                    if def_tag:
                        for defn in sens.findall("./Définition"):
                            el = defn.find(f".//{def_tag}")
                            if el is None:
                                for child in defn:
                                    if child.tag == def_tag:
                                        el = child
                                        break
                            if el is not None:
                                if def_lang_attr and el.get(def_lang_attr) != def_lang_val:
                                    continue
                                raw = (extract_text_without_style(el)
                                       if strip_style else get_elem_plain_text(el))
                                text = clean_definition(raw, strip_parens)
                                if text:
                                    defs.append(text)
                    if sci_tag:
                        sci = sens.find(f".//{sci_tag}")
                        if sci is not None:
                            sci_text = clean_definition(get_text(sci), False)
                            if sci_text:
                                sci_names.add(sci_text)
            else:
                # ── LIFT / generic: sense_tag > def_tag[@lang_attr=lang_val] ──
                for sens in parent.findall(to_relative_xpath(def_sense_tag)):
                    for gloss in sens.findall(to_relative_xpath(def_tag)):
                        if def_lang_attr and gloss.get(def_lang_attr) != def_lang_val:
                            continue
                        # Get text: either a child "text" tag or direct text
                        if def_text_path:
                            t_el = gloss.find(def_text_path)
                            raw = get_text(t_el) if t_el is not None else ""
                        else:
                            raw = (extract_text_without_style(gloss)
                                   if strip_style else get_elem_plain_text(gloss))
                        text = clean_definition(raw, strip_parens)
                        if text:
                            defs.append(text)
                            break  # one gloss per sense

        collect_defs(entry)
        for group in entry.findall("./Groupe"):
            collect_defs(group)

        # Append scientific names not already in definitions
        for sci in sci_names:
            if not any(sci in d for d in defs):
                defs.append(sci)

        for i, d in enumerate(defs):
            for s in def_strip_extra:
                d = d.replace(s, "")
            defs[i] = d.strip()
        definition = ", ".join(defs).replace(",,", ",")
        definition = re.sub(r'‹.*?›', '|', definition)
        definition_raw = definition

        # ── Assemble row ───────────────────────────────────────────────────
        row_data = {
            "Family":         family,
            "Etymon":         etymon,
            "Language":       language_code,
            "Canonical name": canonical_name,
            "Reflex":         reflex,
            "Transliteration": "",
            "Definition":     definition,
            "Definition_raw": definition_raw,
            "Etymon link":    etymon_link,
            "Link":           link,
        }
        rows.append([row_data.get(col, "") for col in columns])

    # ── Write TSV ──────────────────────────────────────────────────────────
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue(), len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Wizard
# ─────────────────────────────────────────────────────────────────────────────
STEPS = [
    "Entry & Structure",
    "Etymology",
    "Family mapping",
    "Reflex",
    "Definitions",
    "Link & Output",
    "Convert",
]

st.title("📊 EvoSem TSV Converter")
st.markdown(f"*Working with: **{fname}***")
wizard_progress(STEPS, st.session_state.evosem_step)

step = st.session_state.evosem_step

# ── Step 0: Entry & structure ─────────────────────────────────────────────────
if step == 0:
    st.markdown("### Entry & structure")
    all_tags = sorted({el.tag for el in root.iter()})

    tag_selector("Entry tag (wraps each dictionary entry)", "evosem_entry_tag", all_tags)

    if st.session_state.evosem_entry_tag:
        count = sum(1 for _ in root.iter(st.session_state.evosem_entry_tag))
        st.info(f"Found **{count}** `<{st.session_state.evosem_entry_tag}>` entries.")
        child_tags = collect_child_tags(root, st.session_state.evosem_entry_tag)

        tag_selector("Lemma / representation form tag", "evosem_lemma_path", child_tags)
        if st.session_state.evosem_lemma_path:
            show_sample_values(root, st.session_state.evosem_entry_tag,
                               st.session_state.evosem_lemma_path)

        tag_selector("Citation form tag (e.g. with article)", "evosem_citation_path",
                     child_tags, allow_none=True)
        tag_selector("POS tag", "evosem_pos_path", child_tags, allow_none=True)
        if st.session_state.evosem_pos_path:
            _pos_attrs = collect_element_attrs(root, st.session_state.evosem_entry_tag,
                                              st.session_state.evosem_pos_path)
            if _pos_attrs:
                _pa_opts = ["(text content)"] + list(_pos_attrs.keys())
                _cur_pa  = st.session_state.evosem_pos_attr or "(text content)"
                _pa_sel  = st.selectbox("POS value is in", _pa_opts,
                    index=_pa_opts.index(_cur_pa) if _cur_pa in _pa_opts else 0,
                    format_func=lambda x: f"attribute `{x}`" if x != "(text content)" else "text content",
                    key="evosem_pos_attr_sel",
                    help="LIFT/FLEx: pick the attribute holding the POS value (e.g. value).")
                st.session_state.evosem_pos_attr = "" if _pa_sel == "(text content)" else _pa_sel
        tag_selector("Homonym number tag (optional)", "evosem_homonym_path",
                     child_tags, allow_none=True)

        with st.expander("Preview: first 2 entries (raw XML)"):
            for s in sample_entries(root, st.session_state.evosem_entry_tag):
                st.code(s[:1500], language="xml")

    ready = bool(st.session_state.evosem_entry_tag and st.session_state.evosem_lemma_path)
    nav_buttons("evosem_step", len(STEPS), next_disabled=not ready, back=False)

# ── Step 1: Etymology ─────────────────────────────────────────────────────────
elif step == 1:
    st.markdown("### Etymology")
    st.markdown(
        "Only entries **with at least one etymon** will produce a TSV row. "
        "Entries without etymology are skipped."
    )
    child_tags = collect_child_tags(root, st.session_state.evosem_entry_tag)

    # Etymon wrapper — accept compound paths like "Étymologie/Étymon"
    st.markdown("**Etymon wrapper path** (relative to each entry, e.g. `Étymologie/Étymon`)")
    wrapper = st.text_input(
        "Etymon wrapper path",
        value=st.session_state.evosem_etymon_wrapper,
        placeholder="e.g. Étymologie/Étymon",
        label_visibility="collapsed",
    )
    st.session_state.evosem_etymon_wrapper = wrapper

    if wrapper:
        # Detect child tags inside etymon elements
        etym_child_tags = sorted({
            child.tag
            for el in root.findall(f".//{wrapper}")
            for child in el
        })
        if etym_child_tags:
            tag_selector("Etymon form tag (the reconstructed form)", "evosem_etymon_form_tag",
                         etym_child_tags, allow_none=True)
            tag_selector("Language tag (source language code)", "evosem_etymon_lang_tag",
                         etym_child_tags, allow_none=True)
            tag_selector("Etymon source/link tag (optional)", "evosem_etymon_source_tag",
                         etym_child_tags, allow_none=True)

        # LIFT: form and language from attributes / field children
        _etym_attrs = {}
        for el in root.findall(f".//{wrapper}"):
            for k, v in el.attrib.items():
                if v.strip(): _etym_attrs.setdefault(k, set()).add(v.strip())
        if _etym_attrs:
            st.markdown("**LIFT-style etymology (attributes on the wrapper element)**")
            _la_opts = ["(none)"] + list(_etym_attrs.keys())
            _cur_la  = st.session_state.evosem_etymon_lang_attr or "(none)"
            _la_sel  = st.selectbox(
                "Source language / family from attribute (e.g. source or type)",
                _la_opts, index=_la_opts.index(_cur_la) if _cur_la in _la_opts else 0,
                key="evosem_etym_lang_attr_sel")
            st.session_state.evosem_etymon_lang_attr = "" if _la_sel == "(none)" else _la_sel

        _etym_field_types = sorted({
            f.get("type", "")
            for el in root.findall(f".//{wrapper}")
            for f in el.findall("field") if f.get("type", "")
        })
        if _etym_field_types:
            st.markdown("**LIFT-style etymon form (inside a `<field>` child)**")
            _ff_opts = ["(none)"] + _etym_field_types
            _cur_ff  = st.session_state.evosem_etymon_form_field_type or "(none)"
            _ff_sel  = st.selectbox(
                "Field type containing the source form (e.g. comment)",
                _ff_opts, index=_ff_opts.index(_cur_ff) if _cur_ff in _ff_opts else 0,
                key="evosem_etym_form_field_sel")
            st.session_state.evosem_etymon_form_field_type = "" if _ff_sel == "(none)" else _ff_sel
            if st.session_state.evosem_etymon_form_field_type:
                _ffl_vals = sorted({
                    f.get("lang", "")
                    for el in root.findall(f".//{wrapper}")
                    for fld in el.findall("field")
                    if fld.get("type") == st.session_state.evosem_etymon_form_field_type
                    for f in fld.findall("form") if f.get("lang", "")
                })
                if _ffl_vals:
                    _cur_ffl = st.session_state.evosem_etymon_form_field_lang
                    st.session_state.evosem_etymon_form_field_lang = st.selectbox(
                        "Language of the form inside that field",
                        _ffl_vals,
                        index=_ffl_vals.index(_cur_ffl) if _cur_ffl in _ffl_vals else 0,
                        key="evosem_etym_form_field_lang_sel")

        # Show sample etymon
        sample_etymons = root.findall(f".//{wrapper}")[:3]
        if sample_etymons:
            with st.expander("Preview: sample etymons"):
                for e in sample_etymons:
                    st.code(ET.tostring(e, encoding="unicode"), language="xml")

        st.markdown("---")
        st.markdown("**Which etymon to use when an entry has multiple?**")
        which = st.radio(
            "Which etymon",
            ["last", "first"],
            index=["last","first"].index(st.session_state.evosem_etymon_which),
            format_func=lambda x: "Last etymon (most recent ancestor)" if x == "last"
                                  else "First etymon (most distant ancestor)",
            horizontal=True,
            label_visibility="collapsed",
        )
        st.session_state.evosem_etymon_which = which

    nav_buttons("evosem_step", len(STEPS))

# ── Step 2: Family mapping ────────────────────────────────────────────────────
elif step == 2:
    st.markdown("### Family mapping")
    st.markdown(
        "Each unique language/family code found in the etymon language tag is listed below. "
        "For each one:\n"
        "- **Map it** to the family name that should appear in the TSV `Family` column\n"
        "- **Skip it** to exclude entries with that code from the output entirely\n\n"
        "Leave the name blank and uncheck Skip to pass the code through as-is."
    )

    lang_tag = st.session_state.evosem_etymon_lang_tag
    if not lang_tag:
        st.warning("No language tag selected in the previous step.")
    else:
        # Also collect langs from etymon_lang_attr if set
        _lang_attr = st.session_state.get("evosem_etymon_lang_attr", "")
        _wrapper   = st.session_state.evosem_etymon_wrapper
        if _lang_attr:
            unique_langs = sorted({
                el.get(_lang_attr, "").strip()
                for el in root.findall(f".//{_wrapper}")
                if el.get(_lang_attr, "").strip()
            })
        else:
            unique_langs = get_unique_text_values(
                root, st.session_state.evosem_entry_tag, lang_tag
            )
        st.info(f"Found **{len(unique_langs)}** unique language codes.")

        lang_map   = st.session_state.evosem_lang_map.copy()
        skip_langs = list(st.session_state.evosem_skip_langs)

        # Header
        hc1, hc2, hc3 = st.columns([2, 3, 1])
        hc1.markdown("**XML code**")
        hc2.markdown("**Family name in TSV**")
        hc3.markdown("**Skip**")
        st.markdown("---")

        for code in unique_langs:
            c1, c2, c3 = st.columns([2, 3, 1])
            with c1:
                st.markdown(f"`{code}`")
            with c2:
                mapped = st.text_input(
                    "Family name",
                    value=lang_map.get(code, ""),
                    key=f"evosem_lm_{code}",
                    placeholder="e.g. Oceanic",
                    label_visibility="collapsed",
                )
                lang_map[code] = mapped
            with c3:
                skipped = st.checkbox(
                    "Skip",
                    value=code in skip_langs,
                    key=f"evosem_skip_{code}",
                    label_visibility="collapsed",
                )
                if skipped and code not in skip_langs:
                    skip_langs.append(code)
                elif not skipped and code in skip_langs:
                    skip_langs.remove(code)

        st.session_state.evosem_lang_map   = lang_map
        st.session_state.evosem_skip_langs = skip_langs

    nav_buttons("evosem_step", len(STEPS))

# ── Step 3: Reflex ────────────────────────────────────────────────────────────
elif step == 3:
    st.markdown("### Reflex")
    st.markdown(
        "The **Reflex** column contains the attested form of the word in the target language. "
        "For some POS categories (typically nouns) the citation form is used instead of "
        "the bare representation form."
    )

    if st.session_state.evosem_pos_path:
        pos_values = get_unique_text_values(
            root, st.session_state.evosem_entry_tag, st.session_state.evosem_pos_path
        )
        st.markdown("**Which POS codes should use the citation form as reflex?**")
        citation_pos = st.multiselect(
            "POS codes using citation form",
            options=pos_values,
            default=[p for p in st.session_state.evosem_citation_pos if p in pos_values],
            help="For all other POS codes, the representation form is used.",
            label_visibility="collapsed",
        )
        st.session_state.evosem_citation_pos = citation_pos
    else:
        st.info("No POS tag configured (Step 1). The representation form will always be used.")

    st.markdown("---")
    st.markdown("**Reflex string cleaning**")
    st.session_state.evosem_reflex_strip_degree = st.checkbox(
        "Strip `°` prefix",
        value=st.session_state.evosem_reflex_strip_degree,
    )
    st.session_state.evosem_reflex_strip_brackets = st.checkbox(
        "Strip `[ ]`, `‹ ›`, `~`",
        value=st.session_state.evosem_reflex_strip_brackets,
    )
    st.session_state.evosem_reflex_strip_extra = st.text_input(
        "Also strip these strings (comma-separated)",
        value=st.session_state.evosem_reflex_strip_extra,
        placeholder="e.g. *, -, '",
        help="Each token is removed from the reflex string as a plain literal. "
             "Entries are trimmed of surrounding spaces.",
    )

    nav_buttons("evosem_step", len(STEPS))

# ── Step 4: Definitions ───────────────────────────────────────────────────────
elif step == 4:
    st.markdown("### Definitions")
    child_tags = collect_child_tags(root, st.session_state.evosem_entry_tag)

    tag_selector("Definition text tag", "evosem_def_tag", child_tags, allow_none=True)

    if st.session_state.evosem_def_tag:
        # LIFT / generic: sense wrapper and text sub-tag
        st.caption(
            "For **LIFT/FLEx** files, the definition tag is typically `gloss` and lives "
            "directly inside a `sense` element, with the text inside a `text` child. "
            "Configure the sense wrapper and text path below if needed."
        )
        c1, c2 = st.columns(2)
        with c1:
            _sense_opts = ["(Mwotlap-style: built-in)"] + child_tags
            _cur_st = st.session_state.evosem_def_sense_tag or "(Mwotlap-style: built-in)"
            _st_sel = st.selectbox("Sense wrapper tag (LIFT: e.g. sense)", _sense_opts,
                index=_sense_opts.index(_cur_st) if _cur_st in _sense_opts else 0,
                key="evosem_def_sense_sel")
            st.session_state.evosem_def_sense_tag = (
                "" if _st_sel == "(Mwotlap-style: built-in)" else _st_sel)
        with c2:
            st.session_state.evosem_def_text_path = st.text_input(
                "Text sub-path within definition element (e.g. text)",
                value=st.session_state.evosem_def_text_path,
                placeholder="e.g. text (blank = use element text directly)",
                help="In LIFT, gloss contains a <text> child. Leave blank for schemas "
                     "where the definition element contains text directly."
            )
        attrs = {}
        for el in root.iter(st.session_state.evosem_def_tag):
            for k, v in el.attrib.items():
                attrs.setdefault(k, set()).add(v)

        if attrs:
            st.markdown("**Filter definitions by language attribute**")
            attr_keys = list(attrs.keys())
            c1, c2 = st.columns(2)
            with c1:
                chosen_attr = st.selectbox(
                    "Attribute name", ["(none)"] + attr_keys, key="evosem_def_attr",
                    index=(["(none)"] + attr_keys).index(st.session_state.evosem_def_lang_attr)
                    if st.session_state.evosem_def_lang_attr in attr_keys else 0,
                )
                st.session_state.evosem_def_lang_attr = (
                    "" if chosen_attr == "(none)" else chosen_attr
                )
            with c2:
                if st.session_state.evosem_def_lang_attr:
                    vals = sorted(attrs.get(st.session_state.evosem_def_lang_attr, []))
                    chosen_val = st.selectbox(
                        "Language value", vals, key="evosem_def_val",
                        index=vals.index(st.session_state.evosem_def_lang_val)
                        if st.session_state.evosem_def_lang_val in vals else 0,
                    )
                    st.session_state.evosem_def_lang_val = chosen_val

    st.markdown("---")
    st.markdown("**Scientific names** (optional — appended to definition if not already present)")
    tag_selector("Scientific name tag", "evosem_sci_name_tag", child_tags, allow_none=True)

    st.markdown("---")
    st.markdown("**Definition cleaning**")
    st.session_state.evosem_def_strip_parens = st.checkbox(
        "Strip parenthetical content `(...)`",
        value=st.session_state.evosem_def_strip_parens,
    )
    st.session_state.evosem_def_strip_style = st.checkbox(
        "Strip `<style>` tags (keep only plain text)",
        value=st.session_state.evosem_def_strip_style,
    )
    st.session_state.evosem_def_strip_extra = st.text_input(
        "Also strip these strings (comma-separated)",
        value=st.session_state.evosem_def_strip_extra,
        placeholder="e.g. cf., (see also), [Bot.]",
        help="Each token is removed from every definition string as a plain literal. "
             "Entries are trimmed of surrounding spaces.",
    )

    nav_buttons("evosem_step", len(STEPS))

# ── Step 5: Link & Output ─────────────────────────────────────────────────────
elif step == 5:
    st.markdown("### Link & Output configuration")

    c1, c2 = st.columns(2)
    with c1:
        st.session_state.evosem_language_code = st.text_input(
            "Language code (for the `Language` column)",
            value=st.session_state.evosem_language_code,
            placeholder="e.g. mlv",
        )
    with c2:
        st.session_state.evosem_canonical_name = st.text_input(
            "Canonical language name (for the `Canonical name` column)",
            value=st.session_state.evosem_canonical_name,
            placeholder="e.g. Mwotlap",
        )

    st.markdown("---")
    st.markdown("**Entry link URL** (optional)")
    st.markdown(
        "Configure an optional link to each entry's online version. "
        "Choose between the **Mwotlap-style** pattern (letter-based URL with fragment) "
        "or a **generic pattern** using `{lemma}` and optionally `{lang_code}` or "
        "`{citation}` placeholders (e.g. for a Wiktionary link)."
    )
    c1, c2 = st.columns([3, 1])
    with c1:
        st.session_state.evosem_link_base_url = st.text_input(
            "Base URL (Mwotlap-style: letter-page + fragment)",
            value=st.session_state.evosem_link_base_url,
            placeholder="e.g. https://marama.huma-num.fr/Lex/Mwotlap",
        )
    with c2:
        st.session_state.evosem_link_suffix = st.text_input(
            "File suffix",
            value=st.session_state.evosem_link_suffix,
            placeholder=".htm",
        )
    if st.session_state.evosem_link_base_url:
        example = (f"{st.session_state.evosem_link_base_url.rstrip('/')}/"
                   f"a{st.session_state.evosem_link_suffix}#%E2%93%94example")
        st.caption(f"Example link: `{example}`")
    st.session_state.evosem_link_pattern = st.text_input(
        "Generic link pattern (overrides Base URL above if set)",
        value=st.session_state.evosem_link_pattern,
        placeholder="e.g. https://en.wiktionary.org/wiki/{lemma}",
        help="Available placeholders: {lemma} (citation or repr form), "
             "{lang_code} (from step 5), {citation} (citation form). "
             "The lemma is URL-encoded automatically."
    )

    st.markdown("---")
    st.markdown("**Output columns** (drag to reorder, uncheck to remove)")
    DEFAULT_COLS = [
        "Family", "Etymon", "Language", "Canonical name",
        "Reflex", "Transliteration", "Definition", "Definition_raw",
        "Etymon link", "Link",
    ]
    current_cols = st.session_state.evosem_columns
    selected_cols = st.multiselect(
        "Columns (order matters)",
        options=DEFAULT_COLS,
        default=[c for c in current_cols if c in DEFAULT_COLS],
        help="Select and reorder the columns you want in the output.",
    )
    st.session_state.evosem_columns = selected_cols

    nav_buttons("evosem_step", len(STEPS))

# ── Step 6: Convert & Download ────────────────────────────────────────────────
elif step == 6:
    st.markdown("### Convert & Download")

    cfg = {k: st.session_state[k] for k in EVOSEM_DEFAULTS}

    with st.expander("Configuration summary"):
        st.markdown(f"- **Entry tag:** `{cfg['evosem_entry_tag']}`")
        st.markdown(f"- **Etymon path:** `{cfg['evosem_etymon_wrapper']}`")
        st.markdown(f"- **Etymon used:** {cfg['evosem_etymon_which']}")
        st.markdown(f"- **Languages mapped:** {sum(1 for v in cfg['evosem_lang_map'].values() if v)}")
        st.markdown(f"- **Languages skipped:** {len(cfg['evosem_skip_langs'])}")
        st.markdown(f"- **Citation-form POS:** {cfg['evosem_citation_pos'] or '(none)'}")
        st.markdown(f"- **Definition tag:** `{cfg['evosem_def_tag']}`  "
                    f"lang `{cfg['evosem_def_lang_attr']}={cfg['evosem_def_lang_val']}`")
        st.markdown(f"- **Scientific name tag:** `{cfg['evosem_sci_name_tag'] or '(none)'}`")
        st.markdown(f"- **Output columns:** {cfg['evosem_columns']}")

    c1, c2 = st.columns([1, 5])
    with c1:
        if st.button("← Back"):
            st.session_state.evosem_step -= 1
            st.rerun()
    with c2:
        if st.button("🚀 Run conversion", type="primary"):
            with st.spinner("Converting…"):
                try:
                    result, n_rows = run_conversion(root, cfg)
                    st.session_state.evosem_output = result
                    st.success(f"✅ Done — **{n_rows:,}** rows generated.")
                except Exception as e:
                    st.error(f"❌ {e}")
                    import traceback; st.code(traceback.format_exc())

    if st.session_state.evosem_output:
        lang = st.session_state.evosem_canonical_name.replace(" ", "_") or "dictionary"
        st.download_button(
            "⬇️ Download .tsv",
            st.session_state.evosem_output.encode("utf-8"),
            file_name=f"{lang}.tsv",
            mime="text/tab-separated-values",
        )
        st.markdown("#### Preview (first 20 rows)")
        lines = st.session_state.evosem_output.splitlines()
        preview = "\n".join(lines[:21])
        st.code(preview, language=None)
