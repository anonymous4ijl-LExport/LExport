"""
LP — DIG4EL JSON converter
"""

import json
import re
import time
import xml.etree.ElementTree as ET

import streamlit as st
from lp_utils import (
    require_xml, sidebar_file_status, wizard_progress, nav_buttons,
    get_unique_attr_values,
)

st.set_page_config(page_title="LP — DIG4EL", page_icon="🌐", layout="wide")
sidebar_file_status()
root, fname = require_xml()

# ─────────────────────────────────────────────────────────────────────────────
# Session-state defaults
# ─────────────────────────────────────────────────────────────────────────────
DEFAULTS = {
    # Mode
    "dig4el_mode": "Conversational Questionnaire (CQ)",   # or "Sentence Pairs"

    # ── Shared XML structure (used by both modes) ─────────────────────────────
    "dig4el_exemple_tag": "Exemple",
    "dig4el_text_tag": "ReprésentationDeTexte",
    "dig4el_lang_attr": "langue",

    # ── CQ wizard ─────────────────────────────────────────────────────────────
    "dig4el_cq_step": 0,
    "dig4el_pivot_lang": "",
    "dig4el_target_lang": "",
    "dig4el_pivot_label": "",
    "dig4el_target_label": "",
    "dig4el_delimiters": " .,;:!?…'\u0965",
    "dig4el_interviewer": "",
    "dig4el_interviewee": "",
    "dig4el_owner_name": "",
    "dig4el_owner_orcid": "",
    "dig4el_authorization": "accessed read-only by anyone via DIG4EL tools",
    "dig4el_location": "",
    "dig4el_cq_output": "",
    # LIFT: when pivot and target are in structurally separate paths
    # (e.g. source in ./form/text and translation in ./translation/form/text)
    "dig4el_cq_lift_mode": False,
    "dig4el_cq_source_path": "",   # e.g. "./form/text"
    "dig4el_cq_source_lang": "",   # e.g. "seh"
    "dig4el_cq_transl_path": "",   # e.g. "./translation/form/text"
    "dig4el_cq_transl_lang": "",   # e.g. "pt" (pivot language in translation)

    # ── Sentence Pairs wizard ─────────────────────────────────────────────────
    "dig4el_sp_step": 0,
    # XML structure for sentence pairs (may differ from CQ — separate wrapper tag)
    "dig4el_sp_wrapper_tag": "",      # e.g. Exemple
    "dig4el_sp_source_tag": "",       # child tag whose text is the source sentence
    "dig4el_sp_source_lang": "",      # lang attr value for source (if attr-based)
    "dig4el_sp_target_tag": "",       # child tag whose text is the target sentence
    "dig4el_sp_target_lang": "",      # lang attr value for target (if attr-based)
    "dig4el_sp_use_attr": True,       # True = same tag + lang attr; False = distinct tags
    "dig4el_sp_comment_tag": "",      # child tag for comments (empty = no comments)
    # LIFT: text sub-path inside lang-bearing element (e.g. "text" for <form><text>)
    "dig4el_sp_text_subpath": "text",
    # LIFT: fully separate paths for source and target (e.g. form/text vs translation/form/text)
    "dig4el_sp_source_path": "",   # e.g. "form/text"
    "dig4el_sp_target_path": "",   # e.g. "translation/form/text"
    "dig4el_sp_source_path_lang": "",  # lang val to filter source container
    "dig4el_sp_target_path_lang": "",  # lang val to filter target container
    "dig4el_sp_output": "",
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─────────────────────────────────────────────────────────────────────────────
# Shared helper
# ─────────────────────────────────────────────────────────────────────────────
def plain_text(elem):
    """Flatten mixed-content element to plain text."""
    if elem is None:
        return ""
    parts = [elem.text or ""]
    for child in elem:
        parts.append(child.text or "")
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _split_path(p):
    """Split "./a/b/c" into ("./a/b", "c").
    Only splits when there are 2+ real tag components after ./ —
    "./quote" or "quote" returns (None, p) since there is no real container.
    """
    if not p:
        return None, p
    stripped = p.lstrip(".").lstrip("/")
    if "/" not in stripped:
        return None, p
    container, leaf = p.rsplit("/", 1)
    container = None if (not container or container == ".") else container
    return container, leaf


def _get_text_from_path(wrapper, path, lang_attr=None, lang_val=None):
    """Extract text from `wrapper` using a path like "./form/text".

    Splits the path into a container (e.g. "./form") and a leaf tag (e.g. "text").
    If lang_attr/lang_val are given, only considers containers whose lang_attr
    attribute matches lang_val (i.e. checks the lang on <form>, not on <text>).
    Falls back to plain_text on the container itself when leaf is None.
    """
    if not path:
        return ""
    container, leaf = _split_path(path)
    if container:
        for elem in wrapper.findall(container):
            if lang_attr and elem.get(lang_attr) != lang_val:
                continue
            target = elem.find(leaf) if leaf else elem
            text = plain_text(target)
            if text:
                return text
        return ""
    else:
        # bare tag name — fall back to old behaviour (no lang filter)
        el = wrapper.find(path)
        return plain_text(el)


# ─────────────────────────────────────────────────────────────────────────────
# Conversion functions
# ─────────────────────────────────────────────────────────────────────────────
def run_cq_conversion(root, cfg):
    exemple_tag  = cfg["dig4el_exemple_tag"]
    text_tag     = cfg["dig4el_text_tag"]
    lang_attr    = cfg["dig4el_lang_attr"]
    pivot_lang   = cfg["dig4el_pivot_lang"]
    target_lang  = cfg["dig4el_target_lang"]
    pivot_label  = cfg["dig4el_pivot_label"] or pivot_lang
    target_label = cfg["dig4el_target_label"] or target_lang
    delimiters   = list(cfg["dig4el_delimiters"])
    lift_mode    = cfg.get("dig4el_cq_lift_mode", False)
    source_path  = cfg.get("dig4el_cq_source_path", "")
    source_lang  = cfg.get("dig4el_cq_source_lang", "")
    transl_path  = cfg.get("dig4el_cq_transl_path", "")
    transl_lang  = cfg.get("dig4el_cq_transl_lang", "")

    data = {}
    index = 1
    # In LIFT/TEI mode, skip cit elements that are themselves translation containers
    _cq_skip_type = None
    if lift_mode and transl_path and "[@type=" in transl_path:
        _ts = transl_path.split("[@type=", 1)[1]
        _cq_skip_type = _ts.replace("'","").replace('"',"").split("]")[0].strip()
    for ex in root.iter(exemple_tag):
        if _cq_skip_type and ex.get("type") == _cq_skip_type:
            continue
        if lift_mode:
            # LIFT mode: source and translation are at separate structural paths,
            # with the lang attr on the container element (e.g. <form lang="seh">)
            # not on the text leaf. The "pivot" in CQ terms is the translation
            # (e.g. Portuguese/English) and the "target" is the documented language.
            pivot_text  = _get_text_from_path(ex, transl_path, lang_attr, transl_lang)
            target_text = _get_text_from_path(ex, source_path, lang_attr, source_lang)
        else:
            # Standard mode: same tag, different lang attr values
            pivot_text = target_text = ""
            for rt in ex.findall(text_tag):
                lang = rt.get(lang_attr, "")
                if lang == pivot_lang and not pivot_text:
                    pivot_text = plain_text(rt)
                elif lang == target_lang and not target_text:
                    target_text = plain_text(rt)
        if not pivot_text or not target_text:
            continue
        data[str(index)] = {
            "legacy index": "",
            "cq": pivot_text,
            "alternate_pivot": "",
            "translation": target_text,
            "concept_words": {},
            "comment": "",
        }
        index += 1

    return {
        "target language": target_label,
        "delimiters": delimiters,
        "pivot language": pivot_label,
        "cq_uid": str(int(time.time())),
        "data": data,
        "interviewer": cfg["dig4el_interviewer"],
        "interviewee": cfg["dig4el_interviewee"],
        "recording_uid": str(int(time.time())),
        "owner name": cfg["dig4el_owner_name"],
        "owner orcid": cfg["dig4el_owner_orcid"],
        "authorization": cfg["dig4el_authorization"],
        "location": cfg["dig4el_location"],
    }


def run_sp_conversion(root, cfg):
    wrapper_tag   = cfg["dig4el_sp_wrapper_tag"]
    use_attr      = cfg["dig4el_sp_use_attr"]
    text_tag      = cfg["dig4el_text_tag"]
    lang_attr     = cfg["dig4el_lang_attr"]
    source_tag    = cfg["dig4el_sp_source_tag"]
    source_lang   = cfg["dig4el_sp_source_lang"]
    target_tag    = cfg["dig4el_sp_target_tag"]
    target_lang   = cfg["dig4el_sp_target_lang"]
    comment_tag   = cfg["dig4el_sp_comment_tag"]
    text_subpath  = cfg.get("dig4el_sp_text_subpath", "")

    sp_src_path  = cfg.get("dig4el_sp_source_path", "")
    sp_tgt_path  = cfg.get("dig4el_sp_target_path", "")
    sp_src_plang = cfg.get("dig4el_sp_source_path_lang", "")
    sp_tgt_plang = cfg.get("dig4el_sp_target_path_lang", "")

    records = []
    for wrapper in root.iter(wrapper_tag):
        if sp_src_path and sp_tgt_path:
            # LIFT fully-separate-path mode: source and target at distinct structural paths.
            # Lang filter applied at container level via _get_text_from_path.
            source_text = _get_text_from_path(wrapper, sp_src_path, lang_attr, sp_src_plang)
            target_text = _get_text_from_path(wrapper, sp_tgt_path, lang_attr, sp_tgt_plang)
        elif use_attr:
            # Same tag, distinguished by lang attribute.
            if text_subpath:
                _sp = f"{text_tag}/{text_subpath}" if text_tag else text_subpath
                source_text = _get_text_from_path(wrapper, _sp, lang_attr, source_lang)
                target_text = _get_text_from_path(wrapper, _sp, lang_attr, target_lang)
            else:
                source_text = target_text = ""
                for rt in wrapper.findall(text_tag):
                    lang = rt.get(lang_attr, "")
                    if lang == source_lang and not source_text:
                        source_text = plain_text(rt)
                    elif lang == target_lang and not target_text:
                        target_text = plain_text(rt)
        else:
            # Distinct child tags.
            if text_subpath:
                _sp = f"{source_tag}/{text_subpath}" if source_tag else text_subpath
                _tp = f"{target_tag}/{text_subpath}" if target_tag else text_subpath
                source_text = _get_text_from_path(wrapper, _sp, lang_attr, source_lang)
                target_text = _get_text_from_path(wrapper, _tp, lang_attr, target_lang)
            else:
                source_text = plain_text(wrapper.find(source_tag))
                target_text = plain_text(wrapper.find(target_tag))

        if not source_text or not target_text:
            continue

        comment = ""
        if comment_tag:
            comment = plain_text(wrapper.find(comment_tag))

        records.append({
            "source": source_text,
            "target": target_text,
            "comments": comment,
        })

    return records


# ─────────────────────────────────────────────────────────────────────────────
# Page header + mode selector
# ─────────────────────────────────────────────────────────────────────────────
st.title("🌐 DIG4EL JSON Converter")
st.markdown(f"*Working with: **{fname}***")

mode = st.radio(
    "Output format",
    ["Conversational Questionnaire (CQ)", "Sentence Pairs"],
    index=["Conversational Questionnaire (CQ)", "Sentence Pairs"].index(
        st.session_state.dig4el_mode),
    horizontal=True,
)
if mode != st.session_state.dig4el_mode:
    st.session_state.dig4el_mode = mode
    st.rerun()

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# ── MODE A: Conversational Questionnaire ─────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.dig4el_mode == "Conversational Questionnaire (CQ)":

    CQ_STEPS = ["XML structure", "Languages", "Delimiters", "Metadata", "Convert"]
    wizard_progress(CQ_STEPS, st.session_state.dig4el_cq_step)
    step = st.session_state.dig4el_cq_step

    # ── CQ Step 0: XML structure ──────────────────────────────────────────────
    if step == 0:
        st.markdown("### XML structure")
        st.markdown(
            "Tell the converter where to find example sentences and their language labels "
            "inside your XML. The defaults below match the standard Mwotlap dictionary format."
        )

        all_tags = sorted({el.tag for el in root.iter()})

        c1, c2, c3 = st.columns(3)
        with c1:
            st.session_state.dig4el_exemple_tag = st.selectbox(
                "Example wrapper tag", all_tags,
                index=all_tags.index(st.session_state.dig4el_exemple_tag)
                      if st.session_state.dig4el_exemple_tag in all_tags else 0,
                help="Tag wrapping each example block (e.g. Exemple).",
            )
        with c2:
            exemple_children = sorted({
                child.tag
                for ex in root.iter(st.session_state.dig4el_exemple_tag)
                for child in ex
            })
            st.session_state.dig4el_text_tag = st.selectbox(
                "Text tag (inside wrapper)", exemple_children,
                index=exemple_children.index(st.session_state.dig4el_text_tag)
                      if st.session_state.dig4el_text_tag in exemple_children else 0,
                help="Tag holding the sentence text for a given language.",
            )
        with c3:
            text_attrs = sorted({
                attr
                for ex in root.iter(st.session_state.dig4el_exemple_tag)
                for rt in ex.findall(st.session_state.dig4el_text_tag)
                for attr in rt.attrib
            })
            st.session_state.dig4el_lang_attr = st.selectbox(
                "Language attribute on text tag", text_attrs,
                index=text_attrs.index(st.session_state.dig4el_lang_attr)
                      if st.session_state.dig4el_lang_attr in text_attrs else 0,
                help="Attribute identifying the language of each text element.",
            )

        n_ex = sum(1 for _ in root.iter(st.session_state.dig4el_exemple_tag))
        st.info(f"Found **{n_ex}** `<{st.session_state.dig4el_exemple_tag}>` nodes.")

        with st.expander("Preview: first 3 example nodes"):
            for i, ex in enumerate(root.iter(st.session_state.dig4el_exemple_tag)):
                if i >= 3:
                    break
                st.code(ET.tostring(ex, encoding="unicode"), language="xml")

        st.markdown("---")
        st.session_state.dig4el_cq_lift_mode = st.toggle(
            "LIFT/FLEx mode — source and translation are at separate structural paths",
            value=st.session_state.dig4el_cq_lift_mode,
            help="In LIFT/FLEx files, the source sentence and its translation live at "
                 "different paths (e.g. ./form/text vs ./translation/form/text). "
                 "Turn this on to configure them independently."
        )

        if st.session_state.dig4el_cq_lift_mode:
            st.caption(
                "Configure the path to the **source** (documented language) sentence "
                "and the path to the **pivot** (translation) sentence separately. "
                "The lang attribute is read from the container element (e.g. `<form lang=\"seh\">`), "
                "not from the text leaf."
            )
            # Enumerate all paths within example elements for the dropdowns
            def _all_paths(wrapper_iter, max_samples=20):
                paths = set()
                for i, ex in enumerate(wrapper_iter):
                    if i >= max_samples: break
                    def _walk(elem, prefix):
                        for child in elem:
                            p = f"{prefix}/{child.tag}"
                            paths.add(p)
                            _walk(child, p)
                    _walk(ex, ".")
                return sorted(paths)
            ex_paths = _all_paths(root.iter(st.session_state.dig4el_exemple_tag))
            lang_vals_lift = sorted({
                el.get(st.session_state.dig4el_lang_attr, "")
                for ex in root.iter(st.session_state.dig4el_exemple_tag)
                for el in ex.iter()
                if el.get(st.session_state.dig4el_lang_attr, "")
            })
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Source sentence** (language being documented)")
                _src_opts = ["(none)"] + ex_paths
                _cur_src = st.session_state.dig4el_cq_source_path or "(none)"
                _src_sel = st.selectbox("Source path", _src_opts,
                    index=_src_opts.index(_cur_src) if _cur_src in _src_opts else 0,
                    key="dig4el_cq_src_path_sel",
                    help="e.g. ./form/text")
                st.session_state.dig4el_cq_source_path = ("" if _src_sel == "(none)"
                                                           else _src_sel)
                _cur_sl = st.session_state.dig4el_cq_source_lang
                if lang_vals_lift:
                    st.session_state.dig4el_cq_source_lang = st.selectbox(
                        "Source language value", lang_vals_lift,
                        index=lang_vals_lift.index(_cur_sl) if _cur_sl in lang_vals_lift else 0,
                        key="dig4el_cq_src_lang_sel")
            with c2:
                st.markdown("**Translation / pivot sentence**")
                _tr_opts = ["(none)"] + ex_paths
                _cur_tr = st.session_state.dig4el_cq_transl_path or "(none)"
                _tr_sel = st.selectbox("Translation path", _tr_opts,
                    index=_tr_opts.index(_cur_tr) if _cur_tr in _tr_opts else 0,
                    key="dig4el_cq_tr_path_sel",
                    help="e.g. ./translation/form/text")
                st.session_state.dig4el_cq_transl_path = ("" if _tr_sel == "(none)"
                                                           else _tr_sel)
                _cur_tl = st.session_state.dig4el_cq_transl_lang
                if lang_vals_lift:
                    st.session_state.dig4el_cq_transl_lang = st.selectbox(
                        "Translation language value", lang_vals_lift,
                        index=lang_vals_lift.index(_cur_tl) if _cur_tl in lang_vals_lift else 0,
                        key="dig4el_cq_tr_lang_sel")

        ready = bool(
            st.session_state.dig4el_exemple_tag and (
                (not st.session_state.dig4el_cq_lift_mode
                 and st.session_state.dig4el_text_tag
                 and st.session_state.dig4el_lang_attr)
                or
                (st.session_state.dig4el_cq_lift_mode
                 and st.session_state.dig4el_cq_source_path
                 and st.session_state.dig4el_cq_transl_path)
            )
        )
        nav_buttons("dig4el_cq_step", len(CQ_STEPS), next_disabled=not ready, back=False)

    # ── CQ Step 1: Languages ──────────────────────────────────────────────────
    elif step == 1:
        st.markdown("### Languages")
        st.markdown(
            "Select which language code is the **pivot** (the questionnaire prompt language, "
            "typically English) and which is the **target** (the language being documented)."
        )

        lang_vals = sorted({
            rt.get(st.session_state.dig4el_lang_attr, "")
            for ex in root.iter(st.session_state.dig4el_exemple_tag)
            for rt in ex.findall(st.session_state.dig4el_text_tag)
            if rt.get(st.session_state.dig4el_lang_attr)
        })

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Pivot language** (prompt / source)")
            pivot = st.selectbox("Language code", lang_vals,
                index=lang_vals.index(st.session_state.dig4el_pivot_lang)
                      if st.session_state.dig4el_pivot_lang in lang_vals else 0,
                key="dig4el_pivot_sel")
            st.session_state.dig4el_pivot_lang = pivot
            st.session_state.dig4el_pivot_label = st.text_input(
                "Human-readable name", value=st.session_state.dig4el_pivot_label,
                placeholder="e.g. English")
            n_pivot = sum(1 for ex in root.iter(st.session_state.dig4el_exemple_tag)
                if any(rt.get(st.session_state.dig4el_lang_attr) == pivot
                       for rt in ex.findall(st.session_state.dig4el_text_tag)))
            st.caption(f"{n_pivot} examples have a `{pivot}` text.")

        with c2:
            st.markdown("**Target language** (language being documented)")
            target = st.selectbox("Language code", lang_vals,
                index=lang_vals.index(st.session_state.dig4el_target_lang)
                      if st.session_state.dig4el_target_lang in lang_vals
                      else (1 if len(lang_vals) > 1 else 0),
                key="dig4el_target_sel")
            st.session_state.dig4el_target_lang = target
            st.session_state.dig4el_target_label = st.text_input(
                "Human-readable name", value=st.session_state.dig4el_target_label,
                placeholder="e.g. Mwotlap")
            n_target = sum(1 for ex in root.iter(st.session_state.dig4el_exemple_tag)
                if any(rt.get(st.session_state.dig4el_lang_attr) == target
                       for rt in ex.findall(st.session_state.dig4el_text_tag)))
            st.caption(f"{n_target} examples have a `{target}` text.")

        n_both = sum(
            1 for ex in root.iter(st.session_state.dig4el_exemple_tag)
            if any(rt.get(st.session_state.dig4el_lang_attr) == pivot
                   for rt in ex.findall(st.session_state.dig4el_text_tag))
            and any(rt.get(st.session_state.dig4el_lang_attr) == target
                    for rt in ex.findall(st.session_state.dig4el_text_tag))
        )
        if pivot and target:
            st.info(f"**{n_both}** examples have both languages and will be exported.")
        if pivot == target:
            st.warning("Pivot and target language must be different.")

        ready = bool(st.session_state.dig4el_pivot_lang
                     and st.session_state.dig4el_target_lang
                     and st.session_state.dig4el_pivot_lang != st.session_state.dig4el_target_lang
                     and st.session_state.dig4el_pivot_label
                     and st.session_state.dig4el_target_label)
        nav_buttons("dig4el_cq_step", len(CQ_STEPS), next_disabled=not ready)

    # ── CQ Step 2: Delimiters ─────────────────────────────────────────────────
    elif step == 2:
        st.markdown("### Delimiters")
        st.markdown(
            "DIG4EL uses these characters to tokenise sentences. "
            "Each character in the string becomes one delimiter entry."
        )
        st.session_state.dig4el_delimiters = st.text_input(
            "Delimiter characters",
            value=st.session_state.dig4el_delimiters,
            help="Default: space, period, comma, semicolon, colon, !, ?, …, apostrophe, ।",
        )
        st.caption("Preview: " + "  |  ".join(
            f"`{c}`" for c in st.session_state.dig4el_delimiters))
        nav_buttons("dig4el_cq_step", len(CQ_STEPS))

    # ── CQ Step 3: Metadata ───────────────────────────────────────────────────
    elif step == 3:
        st.markdown("### Metadata")
        st.markdown("These fields are written as-is into the top level of the JSON output.")
        c1, c2 = st.columns(2)
        with c1:
            st.session_state.dig4el_interviewer = st.text_input(
                "Interviewer", value=st.session_state.dig4el_interviewer,
                placeholder="e.g. Alex François")
            st.session_state.dig4el_owner_name = st.text_input(
                "Owner name", value=st.session_state.dig4el_owner_name,
                placeholder="e.g. Alexandre François")
            st.session_state.dig4el_owner_orcid = st.text_input(
                "Owner ORCID", value=st.session_state.dig4el_owner_orcid,
                placeholder="e.g. 0000-0000-0000-0000")
        with c2:
            st.session_state.dig4el_interviewee = st.text_input(
                "Interviewee", value=st.session_state.dig4el_interviewee,
                placeholder="e.g. Alex François")
            st.session_state.dig4el_location = st.text_input(
                "Location", value=st.session_state.dig4el_location,
                placeholder="e.g. Vanuatu")
            st.session_state.dig4el_authorization = st.text_input(
                "Authorization", value=st.session_state.dig4el_authorization)
        nav_buttons("dig4el_cq_step", len(CQ_STEPS))

    # ── CQ Step 4: Convert ────────────────────────────────────────────────────
    elif step == 4:
        st.markdown("### Convert & Download")
        cfg = {k: st.session_state[k] for k in DEFAULTS if k != "dig4el_sp_output"}

        with st.expander("Configuration summary"):
            st.json({k: v for k, v in cfg.items()
                     if k not in ("dig4el_cq_output", "dig4el_sp_output")})

        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("← Back"):
                st.session_state.dig4el_cq_step -= 1
                st.rerun()
        with c2:
            if st.button("🚀 Run conversion", type="primary"):
                with st.spinner("Converting…"):
                    try:
                        result = run_cq_conversion(root, cfg)
                        st.session_state.dig4el_cq_output = json.dumps(
                            result, ensure_ascii=False, indent=None)
                        st.success(f"✅ Done — **{len(result['data'])}** examples exported.")
                    except Exception as e:
                        st.error(f"❌ {e}")
                        import traceback; st.code(traceback.format_exc())

        if st.session_state.dig4el_cq_output:
            tgt = st.session_state.dig4el_target_label.replace(" ", "_")
            pvt = st.session_state.dig4el_pivot_label.replace(" ", "_")
            st.download_button("⬇️ Download JSON",
                st.session_state.dig4el_cq_output.encode("utf-8"),
                file_name=f"cq_{tgt}_from_{pvt}.json", mime="application/json")
            st.markdown("#### Preview (first 3 entries)")
            preview = json.loads(st.session_state.dig4el_cq_output)
            preview["data"] = dict(list(preview["data"].items())[:3])
            st.code(json.dumps(preview, ensure_ascii=False, indent=2), language="json")


# ─────────────────────────────────────────────────────────────────────────────
# ── MODE B: Sentence Pairs ────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
else:

    SP_STEPS = ["XML structure", "Convert"]
    wizard_progress(SP_STEPS, st.session_state.dig4el_sp_step)
    step = st.session_state.dig4el_sp_step

    # ── SP Step 0: XML structure ──────────────────────────────────────────────
    if step == 0:
        st.markdown("### XML structure")
        st.markdown(
            "Configure where the converter should find translated example pairs in your XML. "
            "Each wrapper node will produce one `{source, target, comments}` entry."
        )

        all_tags = sorted({el.tag for el in root.iter()})

        # Wrapper tag
        st.session_state.dig4el_sp_wrapper_tag = st.selectbox(
            "Example wrapper tag",
            all_tags,
            index=all_tags.index(st.session_state.dig4el_sp_wrapper_tag)
                  if st.session_state.dig4el_sp_wrapper_tag in all_tags else
                  (all_tags.index("Exemple") if "Exemple" in all_tags else 0),
            help="Tag that wraps each bilingual example block.",
        )

        wrapper_children = sorted({
            child.tag
            for w in root.iter(st.session_state.dig4el_sp_wrapper_tag)
            for child in w
        }) if st.session_state.dig4el_sp_wrapper_tag else []

        # Detect whether children use a language attribute or are distinct tags
        child_has_lang_attr = bool(st.session_state.dig4el_sp_wrapper_tag and any(
            child.get(st.session_state.dig4el_lang_attr)
            for w in root.iter(st.session_state.dig4el_sp_wrapper_tag)
            for child in w
            if child.tag == st.session_state.dig4el_text_tag
        ))

        st.session_state.dig4el_sp_use_attr = st.toggle(
            "Source and target are in the same tag, distinguished by a language attribute",
            value=st.session_state.dig4el_sp_use_attr,
            help="Turn off if source and target sentences live in distinct child tags.",
        )

        st.markdown("---")

        if st.session_state.dig4el_sp_use_attr:
            # Same tag + lang attr approach (mirrors the CQ structure)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.session_state.dig4el_text_tag = st.selectbox(
                    "Text tag (shared by all languages)", wrapper_children,
                    index=wrapper_children.index(st.session_state.dig4el_text_tag)
                          if st.session_state.dig4el_text_tag in wrapper_children else 0,
                )
            with c2:
                text_attrs = sorted({
                    attr
                    for w in root.iter(st.session_state.dig4el_sp_wrapper_tag)
                    for rt in w.findall(st.session_state.dig4el_text_tag)
                    for attr in rt.attrib
                }) if st.session_state.dig4el_text_tag else []
                st.session_state.dig4el_lang_attr = st.selectbox(
                    "Language attribute", text_attrs,
                    index=text_attrs.index(st.session_state.dig4el_lang_attr)
                          if st.session_state.dig4el_lang_attr in text_attrs else 0,
                )
            with c3:
                st.write("")  # spacer

            lang_vals = sorted({
                rt.get(st.session_state.dig4el_lang_attr, "")
                for w in root.iter(st.session_state.dig4el_sp_wrapper_tag)
                for rt in w.findall(st.session_state.dig4el_text_tag)
                if rt.get(st.session_state.dig4el_lang_attr)
            }) if st.session_state.dig4el_text_tag and st.session_state.dig4el_lang_attr else []

            c1, c2 = st.columns(2)
            with c1:
                st.session_state.dig4el_sp_source_lang = st.selectbox(
                    "Source language value",
                    lang_vals,
                    index=lang_vals.index(st.session_state.dig4el_sp_source_lang)
                          if st.session_state.dig4el_sp_source_lang in lang_vals else 0,
                    help="Value of the language attribute that marks the source sentence.",
                )
            with c2:
                st.session_state.dig4el_sp_target_lang = st.selectbox(
                    "Target language value",
                    lang_vals,
                    index=lang_vals.index(st.session_state.dig4el_sp_target_lang)
                          if st.session_state.dig4el_sp_target_lang in lang_vals
                          else (1 if len(lang_vals) > 1 else 0),
                    help="Value of the language attribute that marks the target sentence.",
                )

        else:
            # Distinct child tags approach
            none_option = ["(none — leave comments empty)"]
            c1, c2 = st.columns(2)
            with c1:
                st.session_state.dig4el_sp_source_tag = st.selectbox(
                    "Source sentence tag", wrapper_children,
                    index=wrapper_children.index(st.session_state.dig4el_sp_source_tag)
                          if st.session_state.dig4el_sp_source_tag in wrapper_children else 0,
                    help="Child tag whose text is the source-language sentence.",
                )
            with c2:
                st.session_state.dig4el_sp_target_tag = st.selectbox(
                    "Target sentence tag", wrapper_children,
                    index=wrapper_children.index(st.session_state.dig4el_sp_target_tag)
                          if st.session_state.dig4el_sp_target_tag in wrapper_children else
                          (1 if len(wrapper_children) > 1 else 0),
                    help="Child tag whose text is the target-language sentence.",
                )

        # LIFT: text sub-path (for when text lives inside a child of the lang-bearing element)
        if st.session_state.dig4el_sp_use_attr or True:
            st.markdown("---")
            st.markdown("**Text sub-path (LIFT/FLEx)**")
            st.caption(
                "In LIFT/FLEx files, the sentence text is inside a `<text>` child of the "
                "lang-bearing element (e.g. `<form lang=\"seh\"><text>...</text></form>`). "
                "Enter `text` here. Leave blank for schemas where text is directly in the tag."
            )
            st.session_state.dig4el_sp_text_subpath = st.text_input(
                "Text sub-path inside source/target element",
                value=st.session_state.dig4el_sp_text_subpath,
                placeholder="e.g. text",
                key="dig4el_sp_text_subpath_inp"
            )
            st.markdown("**Or: fully separate paths** (when source and translation are "
                        "at different structural positions, e.g. LIFT `<translation>` wrapper)")
            c1, c2 = st.columns(2)
            with c1:
                st.session_state.dig4el_sp_source_path = st.text_input(
                    "Source path (overrides tag selectors above if set)",
                    value=st.session_state.dig4el_sp_source_path,
                    placeholder="e.g. form/text",
                    key="dig4el_sp_src_path_inp",
                    help="LIFT: form/text (lang filter: seh)"
                )
                st.session_state.dig4el_sp_source_path_lang = st.text_input(
                    "Source language value", value=st.session_state.dig4el_sp_source_path_lang,
                    placeholder="e.g. seh", key="dig4el_sp_src_plang_inp")
            with c2:
                st.session_state.dig4el_sp_target_path = st.text_input(
                    "Target/translation path",
                    value=st.session_state.dig4el_sp_target_path,
                    placeholder="e.g. translation/form/text",
                    key="dig4el_sp_tgt_path_inp",
                    help="LIFT: translation/form/text (lang filter: pt)"
                )
                st.session_state.dig4el_sp_target_path_lang = st.text_input(
                    "Target language value", value=st.session_state.dig4el_sp_target_path_lang,
                    placeholder="e.g. pt", key="dig4el_sp_tgt_plang_inp")

        # Comments tag (optional, shared by both approaches)
        st.markdown("---")
        comment_options = ["(none — leave comments empty)"] + wrapper_children
        current_comment = st.session_state.dig4el_sp_comment_tag
        st.session_state.dig4el_sp_comment_tag = st.selectbox(
            "Comments tag (optional)",
            comment_options,
            index=comment_options.index(current_comment)
                  if current_comment in comment_options else 0,
            help="Child tag containing a comment or note about the example. "
                 "Leave as '(none)' to output empty comments.",
        )
        if st.session_state.dig4el_sp_comment_tag == "(none — leave comments empty)":
            st.session_state.dig4el_sp_comment_tag = ""

        # Preview
        if st.session_state.dig4el_sp_wrapper_tag:
            n_w = sum(1 for _ in root.iter(st.session_state.dig4el_sp_wrapper_tag))
            st.info(f"Found **{n_w}** `<{st.session_state.dig4el_sp_wrapper_tag}>` nodes.")
            with st.expander("Preview: first 3 wrapper nodes"):
                for i, w in enumerate(root.iter(st.session_state.dig4el_sp_wrapper_tag)):
                    if i >= 3:
                        break
                    st.code(ET.tostring(w, encoding="unicode"), language="xml")

        ready = bool(st.session_state.dig4el_sp_wrapper_tag and (
            (st.session_state.dig4el_sp_use_attr
             and st.session_state.dig4el_text_tag
             and st.session_state.dig4el_lang_attr
             and st.session_state.dig4el_sp_source_lang
             and st.session_state.dig4el_sp_target_lang
             and st.session_state.dig4el_sp_source_lang != st.session_state.dig4el_sp_target_lang)
            or
            (not st.session_state.dig4el_sp_use_attr
             and st.session_state.dig4el_sp_source_tag
             and st.session_state.dig4el_sp_target_tag
             and st.session_state.dig4el_sp_source_tag != st.session_state.dig4el_sp_target_tag)
        ))
        nav_buttons("dig4el_sp_step", len(SP_STEPS), next_disabled=not ready, back=False)

    # ── SP Step 1: Convert ────────────────────────────────────────────────────
    elif step == 1:
        st.markdown("### Convert & Download")
        cfg = {k: st.session_state[k] for k in DEFAULTS}

        with st.expander("Configuration summary"):
            st.json({k: v for k, v in cfg.items()
                     if k not in ("dig4el_cq_output", "dig4el_sp_output")})

        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("← Back"):
                st.session_state.dig4el_sp_step -= 1
                st.rerun()
        with c2:
            if st.button("🚀 Run conversion", type="primary"):
                with st.spinner("Converting…"):
                    try:
                        result = run_sp_conversion(root, cfg)
                        st.session_state.dig4el_sp_output = json.dumps(
                            result, ensure_ascii=False, indent=2)
                        st.success(f"✅ Done — **{len(result)}** sentence pairs exported.")
                    except Exception as e:
                        st.error(f"❌ {e}")
                        import traceback; st.code(traceback.format_exc())

        if st.session_state.dig4el_sp_output:
            st.download_button(
                "⬇️ Download JSON",
                st.session_state.dig4el_sp_output.encode("utf-8"),
                file_name="sentence_pairs.json",
                mime="application/json",
            )
            st.markdown("#### Preview (first 3 pairs)")
            preview = json.loads(st.session_state.dig4el_sp_output)
            st.code(json.dumps(preview[:3], ensure_ascii=False, indent=2), language="json")
