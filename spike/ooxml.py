from __future__ import annotations

import copy
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
for prefix, uri in (("w", W), ("r", R), ("v", V), ("wp", WP), ("a", A), ("pic", PIC)):
    ET.register_namespace(prefix, uri)


def q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def read_package(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def write_package(parts: dict[str, bytes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(parts):
            zf.writestr(name, parts[name])


def story_parts(parts: dict[str, bytes]) -> list[str]:
    return sorted(name for name in parts if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", name))


def parse_xml(data: bytes) -> ET.Element:
    return ET.fromstring(data)


def xml_bytes(root: ET.Element) -> bytes:
    # ElementTree does not retain unused namespace declarations referenced only
    # by mc:Ignorable. Removing that hint avoids producing unbound prefixes;
    # all actual extension elements remain namespace-qualified.
    root.attrib.pop(q(MC, "Ignorable"), None)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def opc_xml_bytes(root: ET.Element, namespace: str) -> bytes:
    """Serialize OPC infrastructure parts with their required default namespace.

    LibreOffice 26.2 rejects otherwise valid DOCX packages when ElementTree
    rewrites [Content_Types].xml or *.rels using an ns0-prefixed root.
    """
    ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def text_of(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(q(W, "t")))


def set_text(run: ET.Element, value: str) -> None:
    rpr = run.find(q(W, "rPr"))
    for child in list(run):
        if child is not rpr:
            run.remove(child)
    if value == "":
        return
    lines = value.split("\n")
    for i, line in enumerate(lines):
        if i:
            ET.SubElement(run, q(W, "br"))
        t = ET.SubElement(run, q(W, "t"))
        if line[:1].isspace() or line[-1:].isspace():
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = line


def make_sdt(tag: str, children: list[ET.Element], alias: str | None = None) -> ET.Element:
    sdt = ET.Element(q(W, "sdt"))
    pr = ET.SubElement(sdt, q(W, "sdtPr"))
    tag_el = ET.SubElement(pr, q(W, "tag"))
    tag_el.set(q(W, "val"), tag)
    if alias:
        a = ET.SubElement(pr, q(W, "alias"))
        a.set(q(W, "val"), alias)
    content = ET.SubElement(sdt, q(W, "sdtContent"))
    content.extend(children)
    return sdt


def get_sdt_tag(sdt: ET.Element) -> str:
    tag = sdt.find(f"./{q(W, 'sdtPr')}/{q(W, 'tag')}")
    return "" if tag is None else tag.get(q(W, "val"), "")


def replace_child(parent: ET.Element, old: ET.Element, replacements: list[ET.Element]) -> None:
    idx = list(parent).index(old)
    parent.remove(old)
    for offset, node in enumerate(replacements):
        parent.insert(idx + offset, node)


def clone(element: ET.Element) -> ET.Element:
    return copy.deepcopy(element)


def part_rels_name(part_name: str) -> str:
    p = Path(part_name)
    return str(p.parent / "_rels" / f"{p.name}.rels").replace("\\", "/")


def add_image_relationship(parts: dict[str, bytes], part_name: str, image_bytes: bytes) -> str:
    rel_name = part_rels_name(part_name)
    if rel_name in parts:
        rel_root = parse_xml(parts[rel_name])
    else:
        rel_root = ET.Element(q(REL, "Relationships"))
    ids = {el.get("Id", "") for el in rel_root}
    n = 1
    while f"rIdIcpLogo{n}" in ids:
        n += 1
    rid = f"rIdIcpLogo{n}"
    media_name = f"word/media/icp_logo_{n}.png"
    rel = ET.SubElement(rel_root, q(REL, "Relationship"))
    rel.set("Id", rid)
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")
    rel.set("Target", f"media/icp_logo_{n}.png")
    parts[rel_name] = opc_xml_bytes(rel_root, REL)
    parts[media_name] = image_bytes
    ct_root = parse_xml(parts["[Content_Types].xml"])
    if not any(el.get("Extension") == "png" for el in ct_root.findall(q(CT, "Default"))):
        default = ET.SubElement(ct_root, q(CT, "Default"))
        default.set("Extension", "png")
        default.set("ContentType", "image/png")
        parts["[Content_Types].xml"] = opc_xml_bytes(ct_root, CT)
    return rid


def image_run(rid: str, width_pt: int = 120, height_pt: int = 36) -> ET.Element:
    # VML is intentionally used here: it is compact, local/offline, and supported by Word.
    run = ET.Element(q(W, "r"))
    pict = ET.SubElement(run, q(W, "pict"))
    shape = ET.SubElement(pict, q(V, "shape"))
    shape.set("id", "icp_logo")
    shape.set("type", "#_x0000_t75")
    shape.set("style", f"width:{width_pt}pt;height:{height_pt}pt")
    image = ET.SubElement(shape, q(V, "imagedata"))
    image.set(q(R, "id"), rid)
    image.set("title", "Logo ICP Renov")
    return run
