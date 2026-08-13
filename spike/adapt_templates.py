from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .ooxml import W, clone, make_sdt, q, read_package, set_text, story_parts, text_of, write_package, xml_bytes


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "templates"
TARGET_DIR = ROOT / "spike" / "templates"
BLOCK_START = re.compile(r"^\[\[BLOCK:(BLOCK_[A-Z0-9_]+)(?:\|([^\]]+))?\]\]$")
BLOCK_END = re.compile(r"^\[\[/BLOCK:(BLOCK_[A-Z0-9_]+)(?:\|([^\]]+))?\]\]$")
LOOP_START = re.compile(r"\[\[LOOP:([^\]]+)\]\]")
LOOP_END = re.compile(r"\[\[/LOOP:([^\]]+)\]\]")
TOKEN = re.compile(r"\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}")


def _replace_children(parent: ET.Element, children: list[ET.Element]) -> None:
    for child in list(parent):
        parent.remove(child)
    parent.extend(children)


def _adapt_block_container(parent: ET.Element) -> None:
    output: list[ET.Element] = []
    stack: list[tuple[str, str | None, list[ET.Element]]] = []
    for child in list(parent):
        txt = text_of(child).strip() if child.tag == q(W, "p") else ""
        start = BLOCK_START.match(txt)
        end = BLOCK_END.match(txt)
        if start:
            stack.append((start.group(1), start.group(2), []))
        elif end:
            if not stack or stack[-1][0] != end.group(1):
                raise ValueError(f"Malformed block marker: {txt}")
            name, key, content = stack.pop()
            tag = f"icp:block:{name}" + (f"|{key}" if key else "")
            wrapped = make_sdt(tag, content, name)
            (stack[-1][2] if stack else output).append(wrapped)
        else:
            (stack[-1][2] if stack else output).append(child)
    if stack:
        raise ValueError(f"Unclosed block: {stack[-1][0]}")
    _replace_children(parent, output)


def _strip_marker(element: ET.Element, pattern: re.Pattern[str]) -> None:
    for t in element.iter(q(W, "t")):
        if t.text:
            t.text = pattern.sub("", t.text)


def _adapt_tables(root: ET.Element, intervention: bool) -> None:
    for table in root.iter(q(W, "tbl")):
        children = list(table)
        rows = [(i, ch, text_of(ch)) for i, ch in enumerate(children) if ch.tag == q(W, "tr")]
        start = next(((i, row, LOOP_START.search(txt)) for i, row, txt in rows if LOOP_START.search(txt)), None)
        end = next(((i, row, LOOP_END.search(txt)) for i, row, txt in rows if LOOP_END.search(txt)), None)
        if start or end:
            if not start or not end or start[2].group(1) != end[2].group(1) or start[0] > end[0]:
                raise ValueError("Malformed equipment loop")
            _strip_marker(start[1], LOOP_START)
            _strip_marker(end[1], LOOP_END)
            selected = children[start[0]: end[0] + 1]
            adapted: list[ET.Element] = []
            for row in selected:
                txt = text_of(row)
                bm = re.search(r"\[\[BLOCK:(BLOCK_[A-Z0-9_]+)\]\]", txt)
                em = re.search(r"\[\[/BLOCK:(BLOCK_[A-Z0-9_]+)\]\]", txt)
                if bm or em:
                    if not bm or not em or bm.group(1) != em.group(1):
                        raise ValueError("Malformed row block")
                    _strip_marker(row, re.compile(r"\[\[/?BLOCK:" + re.escape(bm.group(1)) + r"\]\]"))
                    adapted.append(make_sdt(f"icp:block:{bm.group(1)}", [row], bm.group(1)))
                else:
                    adapted.append(row)
            loop = make_sdt(f"icp:loop:{start[2].group(1)}", adapted, start[2].group(1))
            _replace_children(table, children[:start[0]] + [loop] + children[end[0] + 1:])
            header = next((r for r in table.findall(q(W, "tr"))), None)
            if header is not None:
                trpr = header.find(q(W, "trPr"))
                if trpr is None:
                    trpr = ET.Element(q(W, "trPr")); header.insert(0, trpr)
                if trpr.find(q(W, "tblHeader")) is None:
                    ET.SubElement(trpr, q(W, "tblHeader"))

        if intervention:
            # Optional technician row is removed as a complete row, without a new business block.
            for row in list(table):
                if row.tag == q(W, "tr") and "{{ intervention.technician }}" in text_of(row):
                    idx = list(table).index(row)
                    table.remove(row)
                    table.insert(idx, make_sdt("icp:optional:intervention.technician", [row]))


def _adapt_intervention_optional_groups(body: ET.Element) -> None:
    children = list(body)
    i = 0
    out: list[ET.Element] = []
    while i < len(children):
        child = children[i]
        txt = text_of(child).strip()
        if "{{ intervention.other }}" in txt:
            out.append(make_sdt("icp:optional:intervention.other", [child])); i += 1; continue
        if txt in {"Observations :", "Anomalies constatées :"} and i + 1 < len(children):
            key = "intervention.notes" if txt.startswith("Observations") else "intervention.issues"
            if f"{{{{ {key} }}}}" in text_of(children[i + 1]):
                out.append(make_sdt(f"icp:optional:{key}", [child, children[i + 1]])); i += 2; continue
        out.append(child); i += 1
    _replace_children(body, out)


def _adapt_fields(root: ET.Element) -> None:
    for parent in list(root.iter()):
        for run in list(parent):
            if run.tag != q(W, "r"):
                continue
            text = text_of(run)
            matches = list(TOKEN.finditer(text))
            if not matches:
                continue
            replacements: list[ET.Element] = []
            pos = 0
            for match in matches:
                if match.start() > pos:
                    literal = clone(run); set_text(literal, text[pos:match.start()]); replacements.append(literal)
                key = match.group(1)
                # The tag is authoritative; visible design-time text is marker-free.
                placeholder_run = clone(run); set_text(placeholder_run, f"<{key}>")
                kind = "image" if key == "company.logo" else "field"
                replacements.append(make_sdt(f"icp:{kind}:{key}", [placeholder_run], key))
                pos = match.end()
            if pos < len(text):
                literal = clone(run); set_text(literal, text[pos:]); replacements.append(literal)
            idx = list(parent).index(run); parent.remove(run)
            for offset, node in enumerate(replacements):
                parent.insert(idx + offset, node)


def _layout_repairs(root: ET.Element, force_habitation_breaks: bool = False) -> None:
    """Minimal copy-only repairs proven necessary by Word/PDF visual QA."""
    for paragraph in root.iter(q(W, "p")):
        txt = text_of(paragraph).strip()
        ppr = paragraph.find(q(W, "pPr"))
        style = None if ppr is None else ppr.find(q(W, "pStyle"))
        is_heading = style is not None and style.get(q(W, "val")) == "Heading1"
        if is_heading:
            if ppr is None:
                ppr = ET.Element(q(W, "pPr")); paragraph.insert(0, ppr)
            spacing = ppr.find(q(W, "spacing"))
            if spacing is None:
                spacing = ET.SubElement(ppr, q(W, "spacing"))
            spacing.set(q(W, "before"), "200")
            spacing.set(q(W, "after"), "140")
            spacing.set(q(W, "line"), "300")
            spacing.set(q(W, "lineRule"), "atLeast")
            indent = ppr.find(q(W, "ind"))
            if indent is None:
                indent = ET.SubElement(ppr, q(W, "ind"))
            indent.set(q(W, "left"), "0")
            indent.set(q(W, "right"), "0")
            indent.set(q(W, "firstLine"), "0")
            if ppr.find(q(W, "keepNext")) is None:
                ET.SubElement(ppr, q(W, "keepNext"))
            old_break = ppr.find(q(W, "pageBreakBefore"))
            if old_break is not None:
                ppr.remove(old_break)
        for run in paragraph.findall(q(W, "r")):
            run_text = text_of(run)
            repaired = run_text
            if "Mention :" in repaired and not repaired.startswith("\n"):
                repaired = repaired.replace("Mention :", "\nMention :", 1)
            if "Signature :" in repaired:
                repaired = repaired.replace("Signature :", "\nSignature :")
            if repaired != run_text:
                set_text(run, repaired)
    if force_habitation_breaks:
        children = list(root)
        for index, paragraph in enumerate(children[:-1]):
            if paragraph.tag != q(W, "p") or not text_of(paragraph).strip().startswith(("Article 6 -", "Article 8 -")):
                continue
            following = children[index + 1]
            if following.tag != q(W, "p"):
                continue
            first_run = next((run for run in following.iter(q(W, "r"))), None)
            if first_run is not None and first_run.find(q(W, "br")) is None:
                pos = 1 if first_run.find(q(W, "rPr")) is not None else 0
                first_run.insert(pos, ET.Element(q(W, "br")))


def _insert_explicit_page_breaks(parent: ET.Element) -> None:
    """Put required breaks inside the target heading, avoiding LO's empty-break defect."""
    children = list(parent)
    for index, child in enumerate(children):
        if child.tag != q(W, "p"):
            _insert_explicit_page_breaks(child)
            continue
        if not text_of(child).strip().startswith(("Article 15 -", "Annexe 1 -", "Annexe 2 -")):
            continue
        if index:
            previous = children[index - 1]
            if previous.tag == q(W, "p") and not text_of(previous).strip() and any(
                br.get(q(W, "type")) == "page" for br in previous.iter(q(W, "br"))
            ):
                parent.remove(previous)
        if text_of(child).strip().startswith("Annexe 2 -"):
            ppr = child.find(q(W, "pPr"))
            if ppr is None:
                ppr = ET.Element(q(W, "pPr"))
                child.insert(0, ppr)
            if ppr.find(q(W, "pageBreakBefore")) is None:
                ET.SubElement(ppr, q(W, "pageBreakBefore"))
            continue
        first_run = next(child.iter(q(W, "r")), None)
        if first_run is not None and not any(
            br.get(q(W, "type")) == "page" for br in child.iter(q(W, "br"))
        ):
            position = 1 if first_run.find(q(W, "rPr")) is not None else 0
            page_break = ET.Element(q(W, "br"))
            page_break.set(q(W, "type"), "page")
            first_run.insert(position, page_break)


def _remove_libreoffice_layout_conflicts(root: ET.Element) -> None:
    """Remove the two Word flags proven to shift LO page headers and footers."""
    compat = root.find(q(W, "compat"))
    if compat is None:
        return
    for setting in list(compat):
        if setting.tag == q(W, "useFELayout") or (
            setting.tag == q(W, "compatSetting")
            and setting.get(q(W, "name")) == "doNotFlipMirrorIndents"
        ):
            compat.remove(setting)


def adapt(source: Path, target: Path) -> None:
    parts = read_package(source)
    intervention = "FICHE_INTERVENTION" in source.name
    for name in story_parts(parts):
        root = ET.fromstring(parts[name])
        if name == "word/document.xml":
            body = root.find(q(W, "body"))
            if body is None:
                raise ValueError("Missing document body")
            _adapt_block_container(body)
            _adapt_tables(body, intervention)
            if intervention:
                _adapt_intervention_optional_groups(body)
        _adapt_fields(root)
        if name == "word/document.xml":
            body = root.find(q(W, "body"))
            _layout_repairs(body, "HABITATION" in source.name)
            _insert_explicit_page_breaks(body)
        parts[name] = xml_bytes(root)
    if "word/settings.xml" in parts:
        settings = ET.fromstring(parts["word/settings.xml"])
        _remove_libreoffice_layout_conflicts(settings)
        parts["word/settings.xml"] = xml_bytes(settings)
    write_package(parts, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt source DOCX markers to tagged Word content controls")
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--target-dir", type=Path, default=TARGET_DIR)
    args = parser.parse_args()
    args.target_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(args.source_dir.glob("*.docx")):
        target = args.target_dir / source.name
        adapt(source, target)
        print(f"{source.name}: {hashlib.sha256(source.read_bytes()).hexdigest()} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
