"""Disposable PQ-06 test: isolate Word mirror-indent compatibility behavior."""

from pathlib import Path
from xml.etree import ElementTree as ET

from spike.ooxml import W, q, read_package, text_of, write_package, xml_bytes


ROOT = Path(__file__).resolve().parents[2]


def repair_signature_break(root: ET.Element, *, run_break: bool = False) -> None:
    body = root.find(q(W, "body"))
    children = list(body)
    for index, paragraph in enumerate(children):
        if paragraph.tag != q(W, "p") or not text_of(paragraph).strip().startswith("Article 15 -"):
            continue
        if index and children[index - 1].tag == q(W, "p") and any(
            br.get(q(W, "type")) == "page" for br in children[index - 1].iter(q(W, "br"))
        ):
            body.remove(children[index - 1])
        ppr = paragraph.find(q(W, "pPr"))
        if ppr is None:
            ppr = ET.Element(q(W, "pPr"))
            paragraph.insert(0, ppr)
        if run_break:
            first_run = next(paragraph.iter(q(W, "r")), None)
            if first_run is not None:
                position = 1 if first_run.find(q(W, "rPr")) is not None else 0
                br = ET.Element(q(W, "br"))
                br.set(q(W, "type"), "page")
                first_run.insert(position, br)
        elif ppr.find(q(W, "pageBreakBefore")) is None:
            ET.SubElement(ppr, q(W, "pageBreakBefore"))


def make_case(source: Path, target: Path, *, run_break: bool = False) -> None:
    parts = read_package(source)
    document = ET.fromstring(parts["word/document.xml"])
    repair_signature_break(document, run_break=run_break)
    parts["word/document.xml"] = xml_bytes(document)

    settings = ET.fromstring(parts["word/settings.xml"])
    compat = settings.find(q(W, "compat"))
    if compat is not None:
        for setting in list(compat):
            if setting.tag == q(W, "compatSetting") and setting.get(q(W, "name")) == "doNotFlipMirrorIndents":
                compat.remove(setting)
    parts["word/settings.xml"] = xml_bytes(settings)
    write_package(parts, target)


for filename in ("03_professional_manual_30.docx", "04_professional_tacit_partner.docx"):
    make_case(
        ROOT / "spike" / "output" / filename,
        ROOT / "spike" / "diagnostic_evidence" / filename.replace(".docx", "_P_no_mirror_indent.docx"),
    )
    make_case(
        ROOT / "spike" / "output" / filename,
        ROOT / "spike" / "diagnostic_evidence" / filename.replace(".docx", "_Q_inline_break_no_mirror.docx"),
        run_break=True,
    )


def normalize_reference_order(source: Path, target: Path) -> None:
    parts = read_package(source)
    document = ET.fromstring(parts["word/document.xml"])
    for section in document.iter(q(W, "sectPr")):
        headers = [child for child in list(section) if child.tag == q(W, "headerReference")]
        footers = [child for child in list(section) if child.tag == q(W, "footerReference")]
        for child in headers + footers:
            section.remove(child)
        order = {"default": 0, "first": 1, "even": 2}
        headers.sort(key=lambda child: order.get(child.get(q(W, "type")), 9))
        footers.sort(key=lambda child: order.get(child.get(q(W, "type")), 9))
        for child in reversed(headers + footers):
            section.insert(0, child)
    parts["word/document.xml"] = xml_bytes(document)
    settings = ET.fromstring(parts["word/settings.xml"])
    compat = settings.find(q(W, "compat"))
    if compat is not None:
        for setting in list(compat):
            if setting.tag == q(W, "compatSetting") and setting.get(q(W, "name")) == "doNotFlipMirrorIndents":
                compat.remove(setting)
    parts["word/settings.xml"] = xml_bytes(settings)
    write_package(parts, target)


for filename in ("03_professional_manual_30", "04_professional_tacit_partner"):
    normalize_reference_order(
        ROOT / "spike" / "diagnostic_evidence" / f"{filename}_O_distinct_page_parts.docx",
        ROOT / "spike" / "diagnostic_evidence" / f"{filename}_R_ordered_distinct_parts.docx",
    )


def remove_manual_page_breaks(source: Path, target: Path) -> None:
    parts = read_package(source)
    document = ET.fromstring(parts["word/document.xml"])
    for paragraph in document.iter(q(W, "p")):
        for run in paragraph.iter(q(W, "r")):
            for br in list(run):
                if br.tag == q(W, "br") and br.get(q(W, "type")) == "page":
                    run.remove(br)
    parts["word/document.xml"] = xml_bytes(document)
    write_package(parts, target)


for filename in (
    "02_consumer_tacit_10_logo", "03_professional_manual_30", "04_professional_tacit_partner"
):
    remove_manual_page_breaks(
        ROOT / "spike" / "output" / f"{filename}.docx",
        ROOT / "spike" / "diagnostic_evidence" / f"{filename}_S_no_manual_breaks.docx",
    )


def remove_compatibility(source: Path, target: Path, *, whole_block: bool) -> None:
    parts = read_package(source)
    settings = ET.fromstring(parts["word/settings.xml"])
    compat = settings.find(q(W, "compat"))
    if compat is not None:
        if whole_block:
            settings.remove(compat)
        else:
            for child in list(compat):
                if child.tag == q(W, "useFELayout"):
                    compat.remove(child)
    parts["word/settings.xml"] = xml_bytes(settings)
    write_package(parts, target)


for filename in ("02_consumer_tacit_10_logo", "03_professional_manual_30"):
    source = ROOT / "spike" / "output" / f"{filename}.docx"
    remove_compatibility(
        source, ROOT / "spike" / "diagnostic_evidence" / f"{filename}_T_no_useFELayout.docx",
        whole_block=False,
    )
    remove_compatibility(
        source, ROOT / "spike" / "diagnostic_evidence" / f"{filename}_U_no_compat_block.docx",
        whole_block=True,
    )


def restore_mirror_flag(source: Path, target: Path) -> None:
    parts = read_package(source)
    settings = ET.fromstring(parts["word/settings.xml"])
    compat = settings.find(q(W, "compat"))
    flag = ET.SubElement(compat, q(W, "compatSetting"))
    flag.set(q(W, "name"), "doNotFlipMirrorIndents")
    flag.set(q(W, "uri"), "http://schemas.microsoft.com/office/word")
    flag.set(q(W, "val"), "1")
    parts["word/settings.xml"] = xml_bytes(settings)
    write_package(parts, target)


restore_mirror_flag(
    ROOT / "spike" / "diagnostic_evidence" / "03_professional_manual_30_T_no_useFELayout.docx",
    ROOT / "spike" / "diagnostic_evidence" / "03_professional_manual_30_V_no_FE_mirror_restored.docx",
)


def annex2_page_break_before(source: Path, target: Path) -> None:
    parts = read_package(source)
    document = ET.fromstring(parts["word/document.xml"])
    for paragraph in document.iter(q(W, "p")):
        if not text_of(paragraph).strip().startswith("Annexe 2 -"):
            continue
        for run in paragraph.iter(q(W, "r")):
            for br in list(run):
                if br.tag == q(W, "br") and br.get(q(W, "type")) == "page":
                    run.remove(br)
        ppr = paragraph.find(q(W, "pPr"))
        if ppr is None:
            ppr = ET.Element(q(W, "pPr")); paragraph.insert(0, ppr)
        ET.SubElement(ppr, q(W, "pageBreakBefore"))
    parts["word/document.xml"] = xml_bytes(document)
    write_package(parts, target)


annex2_page_break_before(
    ROOT / "spike" / "output" / "02_consumer_tacit_10_logo.docx",
    ROOT / "spike" / "diagnostic_evidence" / "02_consumer_tacit_10_logo_X_annex2_page_before.docx",
)
