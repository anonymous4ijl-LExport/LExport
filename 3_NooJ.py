"""
LP — Wiktionary converter
"""

import io
import re
import xml.etree.ElementTree as ET

import streamlit as st
from lp_utils import (
    require_xml, sidebar_file_status, tag_selector, show_sample_values,
    wizard_progress, nav_buttons, collect_child_tags, get_unique_text_values,
    get_unique_attr_values, sample_entries, find_text, get_elem_plain_text,
    collect_corpus_ids, ensure_period, close_unbalanced, bold_lemma,
    normalize_lemma, to_relative_xpath, validate_field_coverage,
    render_coverage_report, iter_path, get_field_value, get_unique_field_values,
    collect_element_attrs, parse_field_spec,
)

st.set_page_config(page_title="LP — Wiktionary", page_icon="📖", layout="wide")

def _walk_paths(elem, prefix=None):
    for child in elem:
        path = (prefix or []) + [child.tag]
        yield path
        yield from _walk_paths(child, path)
sidebar_file_status()
root, fname = require_xml()

# ─────────────────────────────────────────────────────────────────────────────
# Session state for this page
# ─────────────────────────────────────────────────────────────────────────────
WIKT_DEFAULTS = {
    "wikt_step": 0,
    "wikt_lang_name": "",
    "wikt_lang_code": "",
    "wikt_entry_tag": "",
    "wikt_lemma_path": "",
    "wikt_ipa_path": "",
    "wikt_citation_path": "",
    "wikt_det_prefixes": "",
    "wikt_pos_path": "",
    "wikt_pos_labels": {},
    "wikt_verb_transitivity": {},
    "wikt_pos_skip": {},
    "wikt_sense_path": "",
    "wikt_subsense_path": "",
    "wikt_def_path": "",
    "wikt_def_lang_attr": "",
    "wikt_def_lang_value": "",
    "wikt_sem_label_path": "",
    "wikt_sem_label_lang_attr": "",
    "wikt_sem_label_lang_value": "",
    "wikt_sem_label_map": {},
    "wikt_example_path": "",
    "wikt_corpus_link_path": "",
    "wikt_ex_text_path": "",
    "wikt_ex_lang_attr": "",
    "wikt_ex_mlv_lang_value": "",
    "wikt_ex_eng_lang_value": "",
    "wikt_ex_translation_path": "",
    "wikt_ex_translation_lang_value": "",
    "wikt_rel_path": "",
    "wikt_rel_resolve_guid": False,
    "wikt_rel_entry_id_attr": "id",
    "wikt_rel_type_path": "",
    "wikt_rel_target_path": "",
    "wikt_synonym_value": "",
    "wikt_antonym_value": "",
    "wikt_etym_path": "",
    "wikt_etym_source_attr": "",
    "wikt_etym_comment_field_type": "",
    "wikt_etym_form_path": "",
    "wikt_etym_lang_path": "",
    "wikt_corpus_sources": {},
    "wikt_output": "",
}
for k, v in WIKT_DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─────────────────────────────────────────────────────────────────────────────
# Conversion engine
# ─────────────────────────────────────────────────────────────────────────────
def get_corpus_citation(url, corpus_sources):
    url = url.strip()
    wikilink = f"([{url} read online])"
    m = re.search(r'(pangloss-\d+)', url)
    if m:
        src = corpus_sources.get(m.group(1), {})
        author_title = src.get("author_title", "").strip()
        description = src.get("description", "").strip()
        if author_title:
            parts = [author_title] + ([description] if description else [])
            return ". ".join(parts) + ". " + wikilink
    return wikilink


def render_examples(sense_elem, lemma_base, cfg):
    ex_tag          = cfg["wikt_example_path"]
    corpus_tag      = cfg["wikt_corpus_link_path"]
    text_tag        = cfg["wikt_ex_text_path"]
    lang_attr       = cfg["wikt_ex_lang_attr"]
    mlv_val         = cfg["wikt_ex_mlv_lang_value"]
    eng_val         = cfg["wikt_ex_eng_lang_value"]
    sources         = cfg["wikt_corpus_sources"]
    transl_path     = cfg.get("wikt_ex_translation_path", "")
    transl_lang_val = cfg.get("wikt_ex_translation_lang_value", "")

    if not ex_tag:
        return ""

    lift_mode = bool(not corpus_tag and text_tag)

    lines = []
    for ex in sense_elem.findall(to_relative_xpath(ex_tag)):
        if not lift_mode:
            lien = ex.find(corpus_tag) if corpus_tag else None
            if lien is None:
                continue
            mlv_elem = eng_elem = None
            if text_tag:
                for el in ex.findall(to_relative_xpath(text_tag)):
                    val = el.get(lang_attr, "")
                    if val == mlv_val and mlv_elem is None: mlv_elem = el
                    if val == eng_val and eng_elem is None: eng_elem = el
            citation = get_corpus_citation(lien.text or "", sources)
            mlv_text = get_elem_plain_text(mlv_elem).strip() if mlv_elem is not None else ""
            eng_text = get_elem_plain_text(eng_elem).strip() if eng_elem is not None else ""
            lines.append(f"#*: {citation}")
            if mlv_text: lines.append(f"#*: {bold_lemma(mlv_text, lemma_base)}")
            if eng_text: lines.append(f"#*: {eng_text}")
        else:
            # LIFT mode: lang attr is on the container element,
            # not on the leaf text element. Split path into container + leaf.
            def _split(p):
                if not p: return None, p
                stripped = p.lstrip(".").lstrip("/")
                if "/" not in stripped: return None, p
                container, leaf = p.rsplit("/", 1)
                container = None if (not container or container == ".") else container
                return container, leaf
            src_container, src_leaf = _split(text_tag)
            src_text = ""
            if src_container:
                for form in ex.findall(to_relative_xpath(src_container)):
                    if not lang_attr or form.get(lang_attr) == mlv_val:
                        t = form.find(src_leaf) if src_leaf else form
                        src_text = get_elem_plain_text(t).strip() if t is not None else ""
                        if src_text: break
            else:
                el = ex.find(to_relative_xpath(text_tag))
                src_text = get_elem_plain_text(el).strip() if el is not None else ""
            if not src_text:
                continue
            tr_text = ""
            if transl_path:
                tr_container, tr_leaf = _split(transl_path)
                if tr_container:
                    for form in ex.findall(to_relative_xpath(tr_container)):
                        if not lang_attr or not transl_lang_val or form.get(lang_attr) == transl_lang_val:
                            t = form.find(tr_leaf) if tr_leaf else form
                            tr_text = get_elem_plain_text(t).strip() if t is not None else ""
                            if tr_text: break
                else:
                    el = ex.find(to_relative_xpath(transl_path))
                    tr_text = get_elem_plain_text(el).strip() if el is not None else ""
            lines.append(f"#*: {bold_lemma(src_text, lemma_base)}")
            if tr_text: lines.append(f"#*: {tr_text}")
    return "\n".join(lines)


def run_conversion(root, cfg):
    out = io.StringIO()

    lang_name    = cfg["wikt_lang_name"]
    lang_code    = cfg["wikt_lang_code"]
    entry_tag    = cfg["wikt_entry_tag"]
    lemma_path   = cfg["wikt_lemma_path"]
    ipa_path     = cfg["wikt_ipa_path"]
    cit_path     = cfg["wikt_citation_path"]
    det_prefixes = [p.strip() for p in cfg["wikt_det_prefixes"].split(",") if p.strip()]
    pos_path     = cfg["wikt_pos_path"]
    pos_labels   = cfg["wikt_pos_labels"]
    verb_trans   = cfg["wikt_verb_transitivity"]
    sense_path   = cfg["wikt_sense_path"]
    def_tag      = cfg["wikt_def_path"]
    def_la       = cfg["wikt_def_lang_attr"]
    def_lv       = cfg["wikt_def_lang_value"]
    sem_tag      = cfg["wikt_sem_label_path"]
    sem_la       = cfg["wikt_sem_label_lang_attr"]
    sem_lv       = cfg["wikt_sem_label_lang_value"]
    sem_map      = cfg["wikt_sem_label_map"]
    rel_path     = cfg["wikt_rel_path"]
    rel_type     = cfg["wikt_rel_type_path"]
    rel_tgt      = cfg["wikt_rel_target_path"]
    syn_val      = cfg["wikt_synonym_value"]
    ant_val      = cfg["wikt_antonym_value"]
    etym_path    = cfg["wikt_etym_path"]
    etym_form    = cfg["wikt_etym_form_path"]
    etym_lang    = cfg["wikt_etym_lang_path"]
    ref_templates       = cfg.get("wikt_reference_templates", [])
    subsense_path       = cfg.get("wikt_subsense_path", "")
    ex_translation_path = cfg.get("wikt_ex_translation_path", "")
    ex_transl_lang_val  = cfg.get("wikt_ex_translation_lang_value", "")
    rel_resolve_guid    = cfg.get("wikt_rel_resolve_guid", False)
    rel_entry_id_attr   = cfg.get("wikt_rel_entry_id_attr", "id")
    etym_source_attr    = cfg.get("wikt_etym_source_attr", "")
    etym_comment_field  = cfg.get("wikt_etym_comment_field_type", "")

    _guid_to_lemma = {}
    if rel_resolve_guid and entry_tag:
        for _e in root.iter(entry_tag):
            _eid  = _e.get(rel_entry_id_attr, "")
            _guid = _e.get("guid", "")
            _xl   = _e.find(to_relative_xpath(lemma_path)) if lemma_path else None
            _lem  = _xl.text.strip() if _xl is not None and _xl.text else ""
            if _lem:
                if _guid: _guid_to_lemma[_guid] = _lem
                if _eid:  _guid_to_lemma[_eid]  = _lem
                if "_" in _eid:
                    _sfx = _eid.rsplit("_", 1)[-1]
                    if _sfx: _guid_to_lemma[_sfx] = _lem

    def get_gloss(d_elem):
        if not def_tag:
            return ""
        for el in d_elem.findall(to_relative_xpath(def_tag)):
            if not def_la or el.get(def_la) == def_lv:
                raw = get_elem_plain_text(el).strip()
                if raw:
                    return ensure_period(close_unbalanced(
                        close_unbalanced(re.sub(r"\s+", " ", raw), "(", ")"), "‹", "›"))
        if subsense_path:
            for ss in d_elem.findall(to_relative_xpath(subsense_path)):
                for el in ss.findall(to_relative_xpath(def_tag)):
                    if not def_la or el.get(def_la) == def_lv:
                        raw = get_elem_plain_text(el).strip()
                        if raw:
                            return ensure_period(close_unbalanced(
                                close_unbalanced(re.sub(r"\s+", " ", raw), "(", ")"), "‹", "›"))
        return ""

    def get_sem_labels(d_elem):
        if not sem_tag:
            return []
        return [
            sem_map[el.text.strip()]
            for el in d_elem.findall(f".//{sem_tag}")
            if (not sem_la or el.get(sem_la) == sem_lv)
            and el.text and el.text.strip() in sem_map
            and sem_map[el.text.strip()]
        ]

    def build_etymology(entry):
        if not etym_path:
            return ""
        parts = []
        for e in entry.findall(to_relative_xpath(etym_path)):
            form = find_text(e, etym_form) if etym_form else ""
            lang_raw = find_text(e, etym_lang) if etym_lang else ""
            if not lang_raw and etym_source_attr:
                lang_raw = e.get(etym_source_attr, "").strip()
            if not form and etym_comment_field:
                for fld in e.findall("field"):
                    if fld.get("type") == etym_comment_field:
                        fxt = fld.find("./form/text")
                        if fxt is not None and fxt.text:
                            form = fxt.text.strip()
                            break
            lang = lang_raw
            if form:
                parts.append(f"From {lang + ' ' if lang else ''}{{{{m|und|{form}}}}}")
            elif lang:
                parts.append(f"Borrowed from {lang}.")
        return (", ".join(parts) + ".") if parts else ""

    def sense_line(lb, gloss, ex):
        return f"# {lb}{gloss}" + ("\n" + ex if ex else "")

    def build_references_section(lemma, base):
        """Build the ===References=== block from the user-configured list of
        Wiktionary reference templates (Step 9 of the wizard).

        Each template dict has "name", "template" (a string that may contain
        the literal placeholders {lemma}, {base}, {lang_code}), and "enabled".
        {base} falls back to {lemma} when there is no determinate base form
        for this entry.

        Substitution is done via plain text replacement (not str.format) so
        that the template's own literal {{...}} wikitext braces are left
        untouched.
        """
        fields = {
            "{lemma}": lemma,
            "{base}": base if base else lemma,
            "{lang_code}": lang_code,
        }
        lines = []
        for ref in ref_templates:
            if not ref.get("enabled", True):
                continue
            pattern = ref.get("template", "").strip()
            if not pattern:
                continue
            rendered = pattern
            for placeholder, value in fields.items():
                rendered = rendered.replace(placeholder, value)
            lines.append(f"* {rendered}")
        if not lines:
            return ""
        return "===References===\n" + "\n".join(lines) + "\n"

    def write_pos_sections(page, verb_senses, other_groups, lang_code):
        if verb_senses:
            trans_set = {v[0] for v in verb_senses}
            all_same  = len(trans_set) == 1
            if all_same:
                page += f"===Verb===\n{{{{head|{lang_code}|verb}}}} {{{{lb|en|{next(iter(trans_set))}}}}}\\n\\n"
                for vl, labels, gloss, ex in verb_senses:
                    lb = ("{{lb|en|" + "|".join(labels) + "}} ") if labels else ""
                    page += sense_line(lb, gloss, ex) + "\n"
            else:
                page += f"===Verb===\n{{{{head|{lang_code}|verb}}}}\n\n"
                for vl, labels, gloss, ex in verb_senses:
                    lb = "{{lb|en|" + "|".join([vl] + labels) + "}} "
                    page += sense_line(lb, gloss, ex) + "\n"
            page += "\n"

        for pos_code, senses, _, __ in other_groups:
            if not senses:
                continue
            pos_title, head_pos = pos_labels.get(pos_code, (pos_code, pos_code))
            page += f"==={pos_title}===\n{{{{head|{lang_code}|{head_pos}}}}}\n\n"
            for labels, gloss, ex in senses:
                lb = ("{{lb|en|" + "|".join(labels) + "}} ") if labels else ""
                page += sense_line(lb, gloss, ex) + "\n"
            page += "\n"
        return page

    SEP = "\n" + "=" * 80 + "\n\n"

    for entry in root.iter(entry_tag):
        lemma = find_text(entry, lemma_path)
        if not lemma:
            continue

        lemma_base = normalize_lemma(lemma)
        ipa        = find_text(entry, ipa_path) if ipa_path else ""
        citation   = find_text(entry, cit_path) if cit_path else ""

        has_det = False
        det_prefix = None
        if citation:
            for prefix in det_prefixes:
                if citation.lstrip("°").startswith(prefix):
                    has_det = True
                    det_prefix = prefix
                    break

        base = lemma_base if has_det else None

        verb_senses    = []
        verb_synonyms  = []
        verb_antonyms  = []
        other_groups   = []

        # Build attr-based field specs for pos / relations
        _pos_attr_val = cfg.get("wikt_pos_attr", "")
        pos_spec = f"{pos_path}@{_pos_attr_val}" if _pos_attr_val else pos_path
        _rel_type_attr_val = cfg.get("wikt_rel_type_attr", "")
        _rel_tgt_attr_val  = cfg.get("wikt_rel_target_attr", "")
        rel_type_spec = f"{rel_type}@{_rel_type_attr_val}" if _rel_type_attr_val else rel_type
        rel_tgt_spec  = f"{rel_tgt}@{_rel_tgt_attr_val}"  if _rel_tgt_attr_val  else rel_tgt
        _pos_skip = cfg.get("wikt_pos_skip", {})

        for group in (entry.findall("./Groupe") or [entry]):
            pos_code = get_field_value(group, pos_spec) if pos_spec else ""
            if _pos_skip.get(pos_code, False):
                continue
            senses, synonyms, antonyms = [], [], []

            for sens in (group.findall(to_relative_xpath(sense_path)) if sense_path else []):
                gloss = get_gloss(sens)
                if not gloss:
                    continue
                labels      = get_sem_labels(sens)
                ex_wikitext = render_examples(sens, lemma_base, cfg)
                senses.append((labels, gloss, ex_wikitext))

                if rel_path:
                    for rel in sens.findall(to_relative_xpath(rel_path)):
                        typ = get_field_value(rel, rel_type_spec) if rel_type_spec else ""
                        raw_tgt = get_field_value(rel, rel_tgt_spec) if rel_tgt_spec else ""
                        if rel_resolve_guid and raw_tgt in _guid_to_lemma:
                            tgt = _guid_to_lemma[raw_tgt]
                        else:
                            tgt = re.sub(r"[_‹›\[\]].*$", "", raw_tgt).strip()
                        if typ == syn_val and tgt:
                            synonyms.append(tgt)
                        elif typ == ant_val and tgt:
                            antonyms.append(tgt)

            trans_label = verb_trans.get(pos_code, "none")
            if trans_label != "none":
                for labels, gloss, ex in senses:
                    verb_senses.append((trans_label, labels, gloss, ex))
                verb_synonyms.extend(synonyms)
                verb_antonyms.extend(antonyms)
            else:
                other_groups.append((pos_code, senses, synonyms, antonyms))

        if rel_path:
            for rel in entry.findall(to_relative_xpath(rel_path)):
                typ = get_field_value(rel, rel_type_spec) if rel_type_spec else ""
                raw_tgt = get_field_value(rel, rel_tgt_spec) if rel_tgt_spec else ""
                if rel_resolve_guid and raw_tgt in _guid_to_lemma:
                    tgt = _guid_to_lemma[raw_tgt]
                else:
                    tgt = re.sub(r"[_‹›\[\]].*$", "", raw_tgt).strip()
                if typ == syn_val and tgt:
                    verb_synonyms.append(tgt)
                elif typ == ant_val and tgt:
                    verb_antonyms.append(tgt)

        etym = build_etymology(entry)

        def pron_block(ipa_str):
            return f"===Pronunciation===\n* {{{{IPA|{lang_code}|/{ipa_str}/}}}}\n\n"

        def rel_blocks(syns, ants):
            s = ""
            if syns:
                s += "===Synonyms===\n" + ", ".join(f"{{{{l|{lang_code}|{x}}}}}" for x in sorted(set(syns))) + "\n\n"
            if ants:
                s += "===Antonyms===\n" + ", ".join(f"{{{{l|{lang_code}|{x}}}}}" for x in sorted(set(ants))) + "\n\n"
            return s

        # ── Base form page ────────────────────────────────────────────────────
        if has_det:
            page = f"=={lang_name}==\n\n" + pron_block(ipa)
            if etym:
                page += f"===Etymology===\n{etym}\n\n"
            page = write_pos_sections(page, verb_senses, other_groups, lang_code)
            page += rel_blocks(verb_synonyms, verb_antonyms)
            if page.strip():
                page += build_references_section(lemma, base)
                out.write(page + SEP)

            # Determinate form page
            det_page = (
                f"=={lang_name}==\n\n"
                + pron_block(ipa)
                + f"===Etymology===\nFrom {{{{affix|{lang_code}|{det_prefix}|{base}}}}}.\n\n"
                + f"===Noun===\n{{{{head|{lang_code}|noun form}}}}\n\n"
                + f"# {{{{form of|{lang_code}|Determinate form|{base}}}}}\n\n"
            )
            det_page += build_references_section(lemma, base)
            out.write(det_page + SEP)

        # ── Regular page ──────────────────────────────────────────────────────
        else:
            page = f"=={lang_name}==\n\n" + pron_block(ipa)
            if etym:
                page += f"===Etymology===\n{etym}\n\n"
            page = write_pos_sections(page, verb_senses, other_groups, lang_code)
            page += rel_blocks(verb_synonyms, verb_antonyms)
            if page.strip():
                page += build_references_section(lemma, None)
                out.write(page + SEP)

    return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Wizard steps
# ─────────────────────────────────────────────────────────────────────────────
STEPS = [
    "Language", "Pronunciation", "POS", "Definitions",
    "Examples", "Relations", "Etymology", "Sources", "References", "Convert",
]

st.title("📖 Wiktionary Converter")
st.markdown(f"*Working with: **{fname}***")
wizard_progress(STEPS, st.session_state.wikt_step)

step = st.session_state.wikt_step

# ── Step 0: Language & entry structure ───────────────────────────────────────
if step == 0:
    st.markdown("### Language & entry structure")
    all_tags = sorted({el.tag for el in root.iter()})

    c1, c2 = st.columns(2)
    with c1:
        st.session_state.wikt_lang_name = st.text_input(
            "Language name", value=st.session_state.wikt_lang_name,
            placeholder="e.g. English")
    with c2:
        st.session_state.wikt_lang_code = st.text_input(
            "Wiktionary language code", value=st.session_state.wikt_lang_code,
            placeholder="e.g. eng")

    tag_selector("Entry tag (wraps each dictionary entry)", "wikt_entry_tag", all_tags)

    if st.session_state.wikt_entry_tag:
        count = sum(1 for _ in root.iter(st.session_state.wikt_entry_tag))
        st.info(f"Found **{count}** `<{st.session_state.wikt_entry_tag}>` entries.")
        child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)
        tag_selector("Lemma tag (the headword text)", "wikt_lemma_path", child_tags)
        if st.session_state.wikt_lemma_path:
            show_sample_values(root, st.session_state.wikt_entry_tag, st.session_state.wikt_lemma_path)
        with st.expander("Preview: first 2 entries (raw XML)"):
            for s in sample_entries(root, st.session_state.wikt_entry_tag):
                st.code(s[:1500], language="xml")

    ready = bool(st.session_state.wikt_lang_name and st.session_state.wikt_lang_code
                 and st.session_state.wikt_entry_tag and st.session_state.wikt_lemma_path)
    nav_buttons("wikt_step", len(STEPS), next_label="Next →", next_disabled=not ready, back=False)

# ── Step 1: Pronunciation ─────────────────────────────────────────────────────
elif step == 1:
    st.markdown("### Pronunciation")
    child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)

    tag_selector("IPA transcription tag", "wikt_ipa_path", child_tags, allow_none=True)
    if st.session_state.wikt_ipa_path:
        show_sample_values(root, st.session_state.wikt_entry_tag, st.session_state.wikt_ipa_path)

    tag_selector("Citation form tag (if it has additional morphological material)", "wikt_citation_path", child_tags, allow_none=True)

    st.markdown("**Determinate form prefixes** (comma-separated, leave blank if not applicable)")
    st.session_state.wikt_det_prefixes = st.text_input(
        "Prefixes", value=st.session_state.wikt_det_prefixes,
        placeholder="")

    nav_buttons("wikt_step", len(STEPS))

# ── Step 2: POS ───────────────────────────────────────────────────────────────
elif step == 2:
    st.markdown("### Part of speech")
    child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)

    tag_selector("POS tag", "wikt_pos_path", child_tags, allow_none=True)

    if st.session_state.wikt_pos_path:
        pos_values = get_unique_text_values(root, st.session_state.wikt_entry_tag, st.session_state.wikt_pos_path)
        st.info(f"Found **{len(pos_values)}** unique POS codes.")
        TRANS_OPTIONS = ["(not a verb)", "intransitive", "transitive", "transitive and intransitive"]
        pos_labels = st.session_state.wikt_pos_labels.copy()
        verb_trans = st.session_state.wikt_verb_transitivity.copy()

        pos_skip = st.session_state.wikt_pos_skip.copy()
        st.caption(
            "**head= category** fills automatically as the lowercase of **Section title**. "
            "Override it manually if needed. Tick **Skip** to exclude a POS from the output entirely."
        )
        for code in pos_values:
            st.markdown(f"---\n**`{code}`**")
            c1, c2, c3, c4 = st.columns([3, 3, 3, 1])
            with c1:
                title = st.text_input("Section title", value=pos_labels.get(code, ("",""))[0],
                                      key=f"wikt_pt_{code}", placeholder="e.g. Noun")
            with c2:
                # Auto-fill head= as lowercase of title; user can override
                saved_head = pos_labels.get(code, ("",""))[1]
                default_head = saved_head if saved_head else title.lower()
                head = st.text_input("head= category", value=default_head,
                                     key=f"wikt_ph_{code}", placeholder="e.g. noun")
            with c3:
                cur = verb_trans.get(code, "(not a verb)")
                trans = st.selectbox("Verb transitivity", TRANS_OPTIONS,
                                     index=TRANS_OPTIONS.index(cur) if cur in TRANS_OPTIONS else 0,
                                     key=f"wikt_pv_{code}")
            with c4:
                skip = st.checkbox("Skip", value=pos_skip.get(code, False),
                                   key=f"wikt_pskip_{code}",
                                   help="Exclude entries with this POS from the output.")
            pos_labels[code] = (title, head)
            verb_trans[code] = trans if trans != "(not a verb)" else "none"
            pos_skip[code] = skip

        st.session_state.wikt_pos_labels = pos_labels
        st.session_state.wikt_verb_transitivity = verb_trans
        st.session_state.wikt_pos_skip = pos_skip

    nav_buttons("wikt_step", len(STEPS))

# ── Step 3: Definitions ───────────────────────────────────────────────────────
elif step == 3:
    st.markdown("### Definitions & senses")
    child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)

    tag_selector("Sense wrapper tag", "wikt_sense_path", child_tags, allow_none=True)
    tag_selector("Subsense tag (optional, e.g. LIFT subsense)", "wikt_subsense_path",
                 child_tags, allow_none=True,
                 help_text="For LIFT/FLEx: glosses from subsenses are flattened into the parent sense.")
    tag_selector("Definition text tag", "wikt_def_path", child_tags, allow_none=True)

    if st.session_state.wikt_def_path:
        attrs = {}
        for el in root.iter(st.session_state.wikt_def_path):
            for k, v in el.attrib.items():
                attrs.setdefault(k, set()).add(v)
        if attrs:
            st.markdown("**Filter by language attribute** (to select English glosses only)")
            c1, c2 = st.columns(2)
            attr_keys = list(attrs.keys())
            with c1:
                chosen_attr = st.selectbox("Attribute name", ["(none)"] + attr_keys, key="wikt_def_attr_sel",
                    index=(["(none)"] + attr_keys).index(st.session_state.wikt_def_lang_attr)
                    if st.session_state.wikt_def_lang_attr in attr_keys else 0)
                st.session_state.wikt_def_lang_attr = "" if chosen_attr == "(none)" else chosen_attr
            with c2:
                if st.session_state.wikt_def_lang_attr:
                    vals = sorted(attrs.get(st.session_state.wikt_def_lang_attr, []))
                    chosen_val = st.selectbox("Attribute value", vals, key="wikt_def_val_sel",
                        index=vals.index(st.session_state.wikt_def_lang_value)
                        if st.session_state.wikt_def_lang_value in vals else 0)
                    st.session_state.wikt_def_lang_value = chosen_val

    st.markdown("---")
    st.markdown("**Semantic / usage labels** (optional)")
    tag_selector("Semantic label tag", "wikt_sem_label_path", child_tags, allow_none=True)

    if st.session_state.wikt_sem_label_path:
        sem_codes = get_unique_text_values(root, st.session_state.wikt_entry_tag, st.session_state.wikt_sem_label_path)
        sem_map = st.session_state.wikt_sem_label_map.copy()
        if sem_codes:
            st.markdown(f"Found **{len(sem_codes)}** label codes. Map each to a Wiktionary label (blank = skip):")
            cols = st.columns(3)
            for i, code in enumerate(sem_codes):
                with cols[i % 3]:
                    sem_map[code] = st.text_input(f"`{code}`", value=sem_map.get(code, ""),
                                                  key=f"wikt_sem_{code}", placeholder="e.g. figurative")
            st.session_state.wikt_sem_label_map = sem_map

    nav_buttons("wikt_step", len(STEPS))

# ── Step 4: Examples ──────────────────────────────────────────────────────────
elif step == 4:
    st.markdown("### Usage examples")
    st.info(
        "For **corpus-linked** dictionaries, configure the corpus link tag below. For **LIFT** files, leave that as *(none)* and configure the source and translation paths instead."
    )
    child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)

    tag_selector("Example wrapper tag", "wikt_example_path", child_tags, allow_none=True)
    tag_selector("Corpus link tag (contains the URL/DOI)", "wikt_corpus_link_path", child_tags, allow_none=True)

    if st.session_state.wikt_example_path:
        ex_child_tags = sorted({child.tag for ex in root.iter(st.session_state.wikt_example_path) for child in ex})
        tag_selector("Example text tag (used for all languages)", "wikt_ex_text_path", ex_child_tags, allow_none=True)

        if st.session_state.wikt_ex_text_path:
            ex_attrs = {}
            for el in root.iter(st.session_state.wikt_ex_text_path):
                for k, v in el.attrib.items():
                    ex_attrs.setdefault(k, set()).add(v)
            if ex_attrs:
                attr_keys = list(ex_attrs.keys())
                lang_attr = st.selectbox("Language attribute on text tags", attr_keys, key="wikt_ex_lang_attr_sel")
                st.session_state.wikt_ex_lang_attr = lang_attr
                lang_vals = sorted(ex_attrs.get(lang_attr, []))
                c1, c2 = st.columns(2)
                with c1:
                    mlv = st.selectbox("Source language value", lang_vals, key="wikt_mlv_sel",
                        index=lang_vals.index(st.session_state.wikt_ex_mlv_lang_value)
                        if st.session_state.wikt_ex_mlv_lang_value in lang_vals else 0)
                    st.session_state.wikt_ex_mlv_lang_value = mlv
                with c2:
                    eng = st.selectbox("Translation language value", lang_vals, key="wikt_eng_sel",
                        index=lang_vals.index(st.session_state.wikt_ex_eng_lang_value)
                        if st.session_state.wikt_ex_eng_lang_value in lang_vals else 0)
                    st.session_state.wikt_ex_eng_lang_value = eng

        if not st.session_state.wikt_corpus_link_path and st.session_state.wikt_ex_text_path:
            st.markdown("---")
            st.markdown("**Translation path (LIFT / no corpus link)**")
            st.caption("Path from each example element to its translation text. In LIFT/FLEx typically ./translation/form/text")
            _ex_sample = list(iter_path(root, st.session_state.wikt_example_path,
                                        st.session_state.wikt_entry_tag))[:20]
            _tp_opts = ["(none)"] + sorted({
                "./" + "/".join(p)
                for ex in _ex_sample
                for p in _walk_paths(ex)
            })
            _cur_tp = st.session_state.wikt_ex_translation_path or "(none)"
            _tp_sel = st.selectbox("Translation text path", _tp_opts,
                index=_tp_opts.index(_cur_tp) if _cur_tp in _tp_opts else 0,
                key="wikt_ex_transl_path_sel")
            st.session_state.wikt_ex_translation_path = "" if _tp_sel == "(none)" else _tp_sel
            if st.session_state.wikt_ex_translation_path and st.session_state.wikt_ex_lang_attr:
                _tlv = sorted({
                    el.get(st.session_state.wikt_ex_lang_attr, "")
                    for ex in iter_path(root, st.session_state.wikt_example_path,
                                        st.session_state.wikt_entry_tag)
                    for el in ex.findall(to_relative_xpath(st.session_state.wikt_ex_translation_path))
                    if el.get(st.session_state.wikt_ex_lang_attr, "")
                })
                if _tlv:
                    _cur_tlv = st.session_state.wikt_ex_translation_lang_value
                    st.session_state.wikt_ex_translation_lang_value = st.selectbox(
                        "Translation language value", _tlv,
                        index=_tlv.index(_cur_tlv) if _cur_tlv in _tlv else 0,
                        key="wikt_ex_transl_lang_sel")

    nav_buttons("wikt_step", len(STEPS))

# ── Step 5: Semantic relations ────────────────────────────────────────────────
elif step == 5:
    st.markdown("### Semantic relations (synonyms & antonyms)")
    child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)

    tag_selector("Relation wrapper tag", "wikt_rel_path", child_tags, allow_none=True)
    if st.session_state.wikt_rel_path:
        _ref_attrs = collect_element_attrs(root, st.session_state.wikt_entry_tag,
                                           st.session_state.wikt_rel_path)
        if any(a in _ref_attrs for a in ("ref", "guid")):
            st.session_state.wikt_rel_resolve_guid = st.checkbox(
                "Resolve relation targets from internal IDs (LIFT/FLEx)",
                value=st.session_state.wikt_rel_resolve_guid,
                help="Tick this to convert GUID-based refs to human-readable lemmas.")
            if st.session_state.wikt_rel_resolve_guid:
                _id_opts = sorted(_ref_attrs.keys())
                _cur_id  = st.session_state.wikt_rel_entry_id_attr
                st.session_state.wikt_rel_entry_id_attr = st.selectbox(
                    "Entry ID attribute (matched against relation target)",
                    _id_opts,
                    index=_id_opts.index(_cur_id) if _cur_id in _id_opts else 0,
                    key="wikt_rel_id_attr_sel")
        rel_child_tags = sorted({child.tag
            for rel in iter_path(root, st.session_state.wikt_rel_path,
                                 st.session_state.wikt_entry_tag)
            for child in rel})
        tag_selector("Relation type tag", "wikt_rel_type_path", rel_child_tags, allow_none=True)
        tag_selector("Relation target tag", "wikt_rel_target_path", rel_child_tags, allow_none=True)
        if st.session_state.wikt_rel_type_path:
            _rel_type_spec = (f"{st.session_state.wikt_rel_type_path}@{st.session_state.wikt_rel_type_attr}"
                              if st.session_state.get("wikt_rel_type_attr") else st.session_state.wikt_rel_type_path)
            type_vals = get_unique_field_values(root, st.session_state.wikt_entry_tag, _rel_type_spec)
            c1, c2 = st.columns(2)
            with c1:
                opts = ["(none)"] + type_vals
                syn = st.selectbox("Value meaning 'synonym'", opts, key="wikt_syn_sel",
                    index=opts.index(st.session_state.wikt_synonym_value) if st.session_state.wikt_synonym_value in opts else 0)
                st.session_state.wikt_synonym_value = "" if syn == "(none)" else syn
            with c2:
                ant = st.selectbox("Value meaning 'antonym'", opts, key="wikt_ant_sel",
                    index=opts.index(st.session_state.wikt_antonym_value) if st.session_state.wikt_antonym_value in opts else 0)
                st.session_state.wikt_antonym_value = "" if ant == "(none)" else ant

    nav_buttons("wikt_step", len(STEPS))

# ── Step 6: Etymology ─────────────────────────────────────────────────────────
elif step == 6:
    st.markdown("### Etymology (optional)")
    child_tags = collect_child_tags(root, st.session_state.wikt_entry_tag)

    tag_selector("Etymon wrapper tag", "wikt_etym_path", child_tags, allow_none=True)
    if st.session_state.wikt_etym_path:
        etym_child_tags = sorted({child.tag
            for e in iter_path(root, st.session_state.wikt_etym_path,
                               st.session_state.wikt_entry_tag)
            for child in e})
        tag_selector("Form tag (source/reconstructed form)", "wikt_etym_form_path", etym_child_tags, allow_none=True)
        tag_selector("Language tag (source language name)", "wikt_etym_lang_path", etym_child_tags, allow_none=True)
        _etym_wrapper_attrs = collect_element_attrs(root, st.session_state.wikt_entry_tag,
                                                    st.session_state.wikt_etym_path)
        if _etym_wrapper_attrs:
            _src_opts = ["(none)"] + list(_etym_wrapper_attrs.keys())
            _cur_src  = st.session_state.wikt_etym_source_attr or "(none)"
            _src_sel  = st.selectbox(
                "Source language attribute on etymology element (LIFT: e.g. source)",
                _src_opts, index=_src_opts.index(_cur_src) if _cur_src in _src_opts else 0,
                key="wikt_etym_src_attr_sel")
            st.session_state.wikt_etym_source_attr = "" if _src_sel == "(none)" else _src_sel
        _etym_fld_types = sorted({
            f.get("type", "")
            for e in iter_path(root, st.session_state.wikt_etym_path,
                               st.session_state.wikt_entry_tag)
            for f in e.findall("field") if f.get("type", "")
        })
        if _etym_fld_types:
            _cf_opts = ["(none)"] + _etym_fld_types
            _cur_cf  = st.session_state.wikt_etym_comment_field_type or "(none)"
            _cf_sel  = st.selectbox(
                "Field type containing the source form (LIFT: e.g. comment)",
                _cf_opts, index=_cf_opts.index(_cur_cf) if _cur_cf in _cf_opts else 0,
                key="wikt_etym_comment_field_sel")
            st.session_state.wikt_etym_comment_field_type = "" if _cf_sel == "(none)" else _cf_sel

    nav_buttons("wikt_step", len(STEPS))

# ── Step 7: Corpus sources ────────────────────────────────────────────────────
elif step == 7:
    st.markdown("### Corpus source citations")
    corpus_tag = st.session_state.wikt_corpus_link_path
    if not corpus_tag:
        st.info("No corpus link tag configured. Skipping.")
    else:
        corpus_ids = collect_corpus_ids(root, corpus_tag)
        if not corpus_ids:
            st.info("No Pangloss-style corpus IDs found.")
        else:
            st.markdown(
                f"Found **{len(corpus_ids)}** corpus sources. "
                "Fill in metadata for citation formatting (leave blank for bare URL)."
            )
            sources = st.session_state.wikt_corpus_sources.copy()
            for pid, example_url in corpus_ids.items():
                st.markdown(f"---\n**`{pid}`** — <small>{example_url}</small>", unsafe_allow_html=True)
                c1, c2 = st.columns(2)
                with c1:
                    at = st.text_input("Author, ''Title''", value=sources.get(pid, {}).get("author_title", ""),
                                       key=f"wikt_at_{pid}", placeholder="e.g. Jane Smith, ''The Story''")
                with c2:
                    desc = st.text_input("Description", value=sources.get(pid, {}).get("description", ""),
                                         key=f"wikt_desc_{pid}", placeholder="")
                sources[pid] = {"author_title": at, "description": desc}
            st.session_state.wikt_corpus_sources = sources

    nav_buttons("wikt_step", len(STEPS))

# ── Step 8: References ────────────────────────────────────────────────────────
elif step == 8:
    st.markdown("### Reference templates")
    st.markdown(
        "Define which Wiktionary reference template(s) should appear in the "
        "**===References===** section of every generated entry. You can use "
        "one of Wiktionary's existing language-specific reference templates — "
        "browse what's available at "
        "[Category:Reference templates by language]"
        "(https://en.wiktionary.org/wiki/Category:Reference_templates_by_language) "
        "— or write your own template call."
    )
    st.caption(
        "Each row is rendered as its own `*` bullet line. Use `{lemma}` for the "
        "entry headword and `{base}` for the determinate/citation base form "
        "(falls back to `{lemma}` when there is no base form). `{lang_code}` "
        "inserts the language code from Step 1."
    )

    if not st.session_state.get("wikt_reference_templates"):
        default_code = st.session_state.wikt_lang_code or "lang_code"
        st.session_state.wikt_reference_templates = [
            {
                "name": "Lexicon dictionary",
                "template": f"{{{{R:{default_code}:lex|{{lemma}}}}}}",
                "enabled": True,
            }
        ]

    templates = st.session_state.wikt_reference_templates

    remove_idx = None
    for i, ref in enumerate(templates):
        st.markdown(f"---\n**Reference {i + 1}**")
        c1, c2, c3, c4 = st.columns([3, 5, 1, 1])
        with c1:
            ref["name"] = st.text_input(
                "Label (for your reference only)", value=ref.get("name", ""),
                key=f"wikt_ref_name_{i}", placeholder="e.g. my dictionary")
        with c2:
            ref["template"] = st.text_input(
                "Template call", value=ref.get("template", ""),
                key=f"wikt_ref_tpl_{i}",
                placeholder="")
        with c3:
            ref["enabled"] = st.checkbox(
                "On", value=ref.get("enabled", True), key=f"wikt_ref_on_{i}")
        with c4:
            if st.button("🗑️", key=f"wikt_ref_del_{i}") and len(templates) > 1:
                remove_idx = i

    if remove_idx is not None:
        templates.pop(remove_idx)
        st.rerun()

    if st.button("+ Add another reference template"):
        templates.append({"name": "", "template": "", "enabled": True})
        st.rerun()

    st.session_state.wikt_reference_templates = templates

    if any(r.get("enabled") and r.get("template", "").strip() for r in templates):
        st.markdown("**Preview** (using a placeholder lemma):")
        preview_fields = {
            "{lemma}": "example",
            "{base}": "example",
            "{lang_code}": st.session_state.wikt_lang_code or "lang_code",
        }
        preview_lines = []
        for ref in templates:
            if not ref.get("enabled") or not ref.get("template", "").strip():
                continue
            rendered = ref["template"]
            for placeholder, value in preview_fields.items():
                rendered = rendered.replace(placeholder, value)
            preview_lines.append("* " + rendered)
        st.code("===References===\n" + "\n".join(preview_lines), language=None)

    nav_buttons("wikt_step", len(STEPS))

# ── Step 9: Convert & download ────────────────────────────────────────────────
elif step == 9:
    st.markdown("### Convert & Download")

    cfg = {k: st.session_state[k] for k in WIKT_DEFAULTS}
    cfg["wikt_reference_templates"] = st.session_state.get("wikt_reference_templates", [])

    with st.expander("Configuration summary"):
        st.json({k: v for k, v in cfg.items() if not isinstance(v, dict) or len(str(v)) < 200})

    c1, c2 = st.columns([1, 5])
    with c1:
        if st.button("← Back"):
            st.session_state.wikt_step -= 1
            st.rerun()
    with c2:
        if st.button("🚀 Run conversion", type="primary"):
            with st.spinner("Converting…"):
                try:
                    result = run_conversion(root, cfg)
                    st.session_state.wikt_output = result
                    st.success(f"✅ Done — {len(result):,} characters generated.")
                except Exception as e:
                    st.error(f"❌ {e}")
                    import traceback; st.code(traceback.format_exc())

    if st.session_state.wikt_output:
        lang = st.session_state.wikt_lang_name.replace(" ", "_")
        st.download_button("⬇️ Download wikitext", st.session_state.wikt_output.encode("utf-8"),
                           file_name=f"{lang}_wiktionary.txt", mime="text/plain")
        st.markdown("#### Preview (first 3000 characters)")
        st.code(st.session_state.wikt_output[:3000], language=None)
