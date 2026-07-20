"""
LP shared utilities
"""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import streamlit as st


# ─────────────────────────────────────────────────────────────────────────────
# XML introspection
# ─────────────────────────────────────────────────────────────────────────────

def collect_all_tags(root):
    """Return a sorted list of all unique tag names in the document."""
    return sorted({el.tag for el in root.iter()})


def collect_child_tags(root, entry_tag):
    """
    Return all unique tag names found as descendants of entry_tag elements.
    """
    tags = set()
    for entry in root.iter(entry_tag):
        for child in entry.iter():
            if child is not entry:
                tags.add(child.tag)
    return sorted(tags)


def get_unique_text_values(root, entry_tag, child_tag):
    """Collect all unique text values of child_tag within entry_tag elements."""
    values = set()
    for entry in root.iter(entry_tag):
        for el in entry.findall(f".//{child_tag}"):
            if el.text and el.text.strip():
                values.add(el.text.strip())
    return sorted(values)


def get_unique_attr_values(root, tag, attr):
    """Collect all unique values of attribute `attr` on elements named `tag`."""
    return sorted({el.get(attr) for el in root.iter(tag) if el.get(attr)})


# ─────────────────────────────────────────────────────────────────────────────
# Attribute-aware field reading
# ─────────────────────────────────────────────────────────────────────────────
# Some XML schemas (notably LIFT/FLEx) store key values in element *attributes*
# rather than text content — e.g. <grammatical-info value="Noun"/> or
# <relation type="Synonyms" ref="abc123"/>. The helpers below treat a field
# spec as either:
#   "tag"          — bare tag name or disambiguated path; read text content
#   "tag@attr"     — read the named attribute of the matched element instead
#
# This single string format is stored in session_state and cfg the same way
# as a plain tag or path, so the rest of the pipeline needs no structural
# change — only the reading step is switched.

def parse_field_spec(spec):
    """
    Split a field spec string into (path_part, attr_part).
    "grammatical-info@value"  -> ("grammatical-info", "value")
    "./sense/gloss/text"      -> ("./sense/gloss/text", None)
    "text"                    -> ("text", None)
    """
    if spec and "@" in spec:
        path_part, attr_part = spec.rsplit("@", 1)
        return path_part.strip(), attr_part.strip()
    return spec, None


def get_field_value(elem, spec):
    """
    Read a single value from `elem` using a field spec.
    Replaces find_text() at call sites that may use attribute specs.

    - "child_tag"        -> text of first matching descendant
    - "./path/tag"       -> text of first element at that path
    - "tag@attr"         -> value of `attr` on first matching `tag`
    - "./path/tag@attr"  -> value of `attr` on first element at that path
    - "@attr"            -> value of `attr` on `elem` itself (no child lookup)
    """
    if not spec or elem is None:
        return ""
    path_part, attr_part = parse_field_spec(spec)
    # "@attr" with empty path -> read attribute directly from elem itself
    if attr_part and not path_part:
        return (elem.get(attr_part) or "").strip()
    xpath = to_relative_xpath(path_part)
    if not xpath:
        return ""
    el = elem.find(xpath)
    if el is None:
        return ""
    if attr_part:
        return (el.get(attr_part) or "").strip()
    return (el.text or "").strip()


def get_unique_field_values(root, entry_tag, spec):
    """
    Like get_unique_text_values(), but honours attribute specs.
    Returns sorted list of distinct non-empty values found across all entries.
    """
    if not spec:
        return []
    path_part, attr_part = parse_field_spec(spec)
    xpath = to_relative_xpath(path_part) if path_part else None
    if not xpath:
        return []
    values = set()
    for entry in root.iter(entry_tag):
        for el in entry.findall(xpath):
            if attr_part:
                v = el.get(attr_part, "").strip()
            else:
                v = (el.text or "").strip()
            if v:
                values.add(v)
    return sorted(values)


def collect_element_attrs(root, entry_tag, tag_or_path):
    """
    Return a dict {attr_name: sorted list of distinct values} for all
    attributes found on elements matching `tag_or_path` within entries.
    Useful for surfacing which attributes are available on a given tag so
    the user can choose one as a field source.
    """
    path_part, _ = parse_field_spec(tag_or_path)
    xpath = to_relative_xpath(path_part)
    if not xpath:
        return {}
    attrs = defaultdict(set)
    for entry in root.iter(entry_tag):
        for el in entry.findall(xpath):
            for k, v in el.attrib.items():
                if v.strip():
                    attrs[k].add(v.strip())
    return {k: sorted(v) for k, v in attrs.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Path disambiguation
# ─────────────────────────────────────────────────────────────────────────────
# LExport matches XML elements primarily by tag name. This works well for
# most lexicographic XML, but a tag name can be reused at different
# structural depths or in different semantic contexts within a single entry
# (e.g. <Exemple> used both for a usage example under a sense, and for an
# illustrative form under an etymology). The functions below let a page
# detect this and offer the user a specific relative path instead of
# silently matching every occurrence.
#
# A "path" produced here (e.g. "./Sens/Exemple") is a valid relative XPath
# expression and can be passed directly to ElementTree's find()/findall()
# from an entry element. Use to_relative_xpath() to safely convert any
# tag_selector() return value (bare tag name OR disambiguated path) into
# something find()/findall() can use, since bare tag names still need the
# ".//" prefix for backward-compatible "anywhere in subtree" matching.

def to_relative_xpath(tag_or_path):
    """
    Normalize a tag_selector() value into a valid relative XPath string
    usable with elem.find(...) / elem.findall(...).

    - Falsy input -> None (caller should treat as "not configured").
    - A value containing "/" is treated as an already-disambiguated
      relative path (e.g. "./Sens/Exemple") and used as-is.
    - A bare tag name keeps the original "anywhere in subtree" behaviour
      via ".//tag", for backward compatibility with existing configs.
    """
    if not tag_or_path:
        return None
    if "/" in tag_or_path:
        return tag_or_path
    return f".//{tag_or_path}"


def iter_path(root, tag_or_path, entry_tag=None):
    """
    Like root.iter(tag), but also accepts a disambiguated relative path
    (e.g. "./Sens/Exemple") as produced by tag_selector() once a tag has
    been disambiguated.

    root.iter(tag) only accepts a literal tag name; passing it a path
    string silently returns nothing, since ElementTree treats it as a tag
    name to match exactly rather than an XPath expression. This caused a
    real regression: once a tag_selector becomes ambiguity-aware and starts
    returning paths, any code still calling root.iter(that_value) directly
    would break silently (empty results, "(none)" in dependent dropdowns).

    Behaviour:
    - Bare tag name (no "/"): identical to root.iter(tag_or_path).
    - Disambiguated path (contains "/"): requires `entry_tag`, the document
      is scanned entry by entry and elem.findall(path) is applied relative
      to each entry, yielding the same elements root.iter() would have
      yielded for an unambiguous tag at that same structural position.

    Returns a generator of matching elements, mirroring root.iter()'s API.
    """
    if not tag_or_path:
        return
    if "/" not in tag_or_path:
        yield from root.iter(tag_or_path)
        return
    if not entry_tag:
        # No entry context to resolve a relative path against; nothing we
        # can safely do other than yield nothing (same failure mode as
        # before, but documented rather than silent).
        return
    xpath = to_relative_xpath(tag_or_path)
    for entry in root.iter(entry_tag):
        yield from entry.findall(xpath)


def find_tag_paths(root, entry_tag, target_tag):
    """
    Enumerate all distinct relative paths (from each entry's root) at which
    `target_tag` occurs, with a count of entries/occurrences at each path.

    Returns a dict {relative_path: occurrence_count}, e.g.:
        {"./Sens/Exemple": 412, "./Etymologie/Exemple": 18}

    A single key means the tag is structurally unambiguous within entries
    (always at the same position) and a plain tag_selector is sufficient.
    Multiple keys mean the tag is reused in different contexts and the
    compiler should be offered a choice of path.
    """
    path_counts = defaultdict(int)

    def walk(elem, path_so_far):
        for child in elem:
            child_path = path_so_far + [child.tag]
            if child.tag == target_tag:
                path_counts["./" + "/".join(child_path)] += 1
            walk(child, child_path)

    for entry in root.iter(entry_tag):
        walk(entry, [])

    return dict(path_counts)


# ─────────────────────────────────────────────────────────────────────────────
# Uniformity validation
# ─────────────────────────────────────────────────────────────────────────────
# LExport's wizard asks the compiler, once, which XML nodes correspond to
# which output components. It does not by itself verify that the chosen
# mapping is actually populated consistently across every entry: an
# attribute value, semantic label, or relation type that is present on most
# entries but missing or differently spelled on a minority will silently
# produce incomplete output rather than an error. validate_field_coverage()
# lets a page check this before conversion and surface a clear report.

def validate_field_coverage(root, entry_tag, field_specs, lemma_path=None):
    """
    Check how uniformly a set of configured tag/attribute mappings is
    populated across all entries in the document.

    field_specs: list of dicts, each describing one field to check:
        {
            "label": "English gloss",       # human-readable name for the report
            "path": "./Sens/Def",           # tag name or relative path (required)
            "attr": "langue",               # optional: only count elements where
            "value": "eng",                 #   el.get(attr) == value
            "required": True,               # whether missing entries should be
                                             #   flagged as a warning (vs info)
        }
    lemma_path: tag name or path used to fetch a human-readable identifier
        for entries missing a field, for the report's "sample_missing" list.
        Falls back to no samples if not given or not found.

    Returns a list of report dicts:
        {
            "label", "total_entries", "matching_entries",
            "missing_entries", "missing_pct", "sample_missing", "required",
        }
    """
    entries = list(root.iter(entry_tag))
    total = len(entries)
    report = []

    for spec in field_specs:
        path = to_relative_xpath(spec.get("path"))
        attr = spec.get("attr")
        value = spec.get("value")
        if not path:
            continue

        matching = 0
        missing_samples = []
        for entry in entries:
            found = False
            for el in entry.findall(path):
                if attr:
                    if el.get(attr) == value:
                        found = True
                        break
                else:
                    if el.text and el.text.strip():
                        found = True
                        break
            if found:
                matching += 1
            elif lemma_path and len(missing_samples) < 5:
                lemma_el = entry.find(to_relative_xpath(lemma_path))
                if lemma_el is not None and lemma_el.text and lemma_el.text.strip():
                    missing_samples.append(lemma_el.text.strip())

        missing = total - matching
        report.append({
            "label": spec["label"],
            "total_entries": total,
            "matching_entries": matching,
            "missing_entries": missing,
            "missing_pct": round(100 * missing / total, 1) if total else 0.0,
            "sample_missing": missing_samples,
            "required": spec.get("required", True),
        })
    return report


def render_coverage_report(report, expanded_threshold=0.0):
    """
    Render a validate_field_coverage() report as Streamlit UI: a warning
    panel for fields with missing coverage, an expander listing affected
    sample entries, and a clean confirmation when coverage is complete.

    expanded_threshold: if missing_pct for any field exceeds this value,
    the report expander defaults to open (otherwise collapsed).
    """
    if not report:
        return

    any_missing = any(r["missing_entries"] > 0 for r in report)
    max_missing_pct = max((r["missing_pct"] for r in report), default=0.0)

    if not any_missing:
        st.success("✅ All configured fields are present on every entry.")
        return

    label = "⚠️ Coverage check — some fields are missing on some entries"
    with st.expander(label, expanded=(max_missing_pct > expanded_threshold)):
        for r in report:
            if r["missing_entries"] == 0:
                st.markdown(f"✅ **{r['label']}** — present on all {r['total_entries']} entries.")
                continue

            severity = st.warning if r.get("required", True) else st.info
            severity(
                f"**{r['label']}** — missing on **{r['missing_entries']}** of "
                f"**{r['total_entries']}** entries ({r['missing_pct']}%)."
            )
            if r["sample_missing"]:
                sample_str = ", ".join(f"`{s}`" for s in r["sample_missing"])
                more = " (showing first 5)" if r["missing_entries"] > 5 else ""
                st.caption(f"e.g. {sample_str}{more}")


def sample_entries(root, entry_tag, n=2):
    """Return up to n sample entries as XML strings."""
    return [
        ET.tostring(entry, encoding="unicode")
        for i, entry in enumerate(root.iter(entry_tag))
        if i < n
    ]


def find_text(entry, path):
    """Find text of first descendant matching tag or relative path `path`."""
    xpath = to_relative_xpath(path)
    if not xpath:
        return ""
    el = entry.find(xpath)
    return el.text.strip() if el is not None and el.text else ""


def get_elem_plain_text(elem):
    """Extract plain text from an element, flattening all child tags."""
    parts = [elem.text or ""]
    for child in elem:
        parts.append(child.text or "")
        parts.append(child.tail or "")
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Shared UI components
# ─────────────────────────────────────────────────────────────────────────────

def require_xml():
    """
    Call at the top of every converter page.
    Shows an error and stops execution if no XML file is loaded.
    Returns the xml_root if available.
    """
    root = st.session_state.get("xml_root")
    fname = st.session_state.get("xml_filename", "")
    if root is None:
        st.warning("⚠️ No XML file loaded. Please go to the **Home** page and upload your file first.")
        st.stop()
    return root, fname


def sidebar_file_status():
    """Render the file status widget in the sidebar (call from each page)."""
    with st.sidebar:
        st.markdown("## 📚 LExport")
        st.markdown("---")
        fname = st.session_state.get("xml_filename", "")
        if st.session_state.get("xml_root") is not None:
            st.success(f"✅ **{fname}**")
        else:
            st.warning("No file loaded.")
            st.page_link("app.py", label="← Upload a file", icon="📂")


def tag_selector(label, key, tags, help_text="", allow_none=False,
                  root=None, entry_tag=None):
    """
    Render a selectbox for choosing a tag. Persists choice to session_state[key].
    Returns the chosen value (or "" if none selected).

    Disambiguation (optional): if both `root` and `entry_tag` are supplied,
    and the chosen tag name occurs at more than one distinct structural
    path within entries, a second selectbox appears letting the compiler
    pick the specific path (e.g. "./Sens/Exemple" vs "./Etymologie/Exemple").
    In that case session_state[key] stores the disambiguated relative path
    rather than the bare tag name; pass it through to_relative_xpath()
    before use in find()/findall() either way, since it transparently
    handles both bare tag names and full paths.
    """
    options = (["(none)"] + list(tags)) if allow_none else list(tags)
    current = st.session_state.get(key, "")
    # If session_state holds a disambiguated path, match it back to its tag
    # for the purposes of the selectbox (which only lists bare tag names).
    current_tag = current.rsplit("/", 1)[-1] if current else current
    default_idx = options.index(current_tag) if current_tag in options else 0
    choice = st.selectbox(label, options, index=default_idx, help=help_text, key=f"_sel_{key}")
    value = "" if choice == "(none)" else choice

    if value and root is not None and entry_tag is not None:
        paths = find_tag_paths(root, entry_tag, value)
        if len(paths) > 1:
            sorted_paths = sorted(paths.items(), key=lambda kv: -kv[1])
            path_options = [p for p, _ in sorted_paths]
            path_labels = {
                p: f"{p}  ({n} occurrence{'s' if n != 1 else ''})"
                for p, n in sorted_paths
            }
            st.caption(
                f"⚠️ `<{value}>` appears at **{len(paths)}** different positions "
                "within entries. Choose which one you mean:"
            )
            current_path = current if current in path_options else path_options[0]
            chosen_path = st.selectbox(
                f"Which `<{value}>`?",
                path_options,
                index=path_options.index(current_path),
                format_func=lambda p: path_labels[p],
                key=f"_path_{key}",
            )
            value = chosen_path

    st.session_state[key] = value
    return value


def show_sample_values(root, entry_tag, child_tag, n=5):
    """Show a caption with n sample text values for a given child tag."""
    samples = []
    for entry in root.iter(entry_tag):
        v = find_text(entry, child_tag)
        if v:
            samples.append(v)
        if len(samples) >= n:
            break
    if samples:
        st.caption(f"Sample values: {', '.join(samples)}")


def wizard_progress(steps, current_step):
    """Render a compact horizontal progress indicator."""
    cols = st.columns(len(steps))
    for i, (col, name) in enumerate(zip(cols, steps)):
        if i < current_step:
            col.markdown(
                f"<div style='text-align:center;color:#4CAF50;font-size:0.72rem'>✓<br>{name}</div>",
                unsafe_allow_html=True,
            )
        elif i == current_step:
            col.markdown(
                f"<div style='text-align:center;color:#1976D2;font-weight:bold;font-size:0.72rem'>●<br>{name}</div>",
                unsafe_allow_html=True,
            )
        else:
            col.markdown(
                f"<div style='text-align:center;color:#bbb;font-size:0.72rem'>○<br>{name}</div>",
                unsafe_allow_html=True,
            )
    st.markdown("---")


def nav_buttons(step_key, total_steps, next_label="Next →", next_disabled=False, back=True):
    """Render Back / Next navigation buttons, updating session_state[step_key]."""
    col1, col2 = st.columns([1, 5])
    with col1:
        if back and st.session_state.get(step_key, 0) > 0:
            if st.button("← Back", key=f"back_{step_key}_{st.session_state[step_key]}"):
                st.session_state[step_key] -= 1
                st.rerun()
    with col2:
        if st.button(next_label, disabled=next_disabled, type="primary",
                     key=f"next_{step_key}_{st.session_state[step_key]}"):
            st.session_state[step_key] += 1
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Text utilities
# ─────────────────────────────────────────────────────────────────────────────

def ensure_period(text):
    if not text:
        return text
    text = text.rstrip()
    return text if text[-1] in ".!?" else text + "."


def close_unbalanced(text, o, c):
    diff = text.count(o) - text.count(c)
    return text + c * diff if diff > 0 else text


def bold_lemma(text, base):
    if not base:
        return text
    return re.sub(re.escape(base), lambda m: f"'''{m.group()}'''",
                  text, count=1, flags=re.IGNORECASE)


def normalize_lemma(text):
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"‹([^›]+)›", r"\1", text)
    return text


def collect_corpus_ids(root, corpus_link_tag):
    """Return dict of {pangloss_id: example_url} from corpus link elements."""
    ids = {}
    for el in root.iter(corpus_link_tag):
        if el.text:
            url = el.text.strip()
            m = re.search(r'(pangloss-\d+)', url)
            if m:
                pid = m.group(1)
                if pid not in ids:
                    ids[pid] = url
    return ids
