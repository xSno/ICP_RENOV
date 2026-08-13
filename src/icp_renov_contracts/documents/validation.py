from __future__ import annotations

from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class DocumentGenerationError(Exception):
    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code); self.code = code; self.user_message = user_message


TOKEN = re.compile(r"\[\[(?:/?(?:BLOCK|LOOP):|FIELD:)[^\]]+\]\]")


def story_xml(path: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            if "word/document.xml" not in names: raise KeyError("word/document.xml")
            return {name: package.read(name) for name in names
                    if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", name)}
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DocumentGenerationError("invalid_docx", "Le document DOCX n’a pas pu être généré.") from exc


def validate_rendered_docx(path: Path) -> None:
    parts = story_xml(path)
    text = "".join("".join(node.text or "" for node in ET.fromstring(data).iter(f"{{{W}}}t")) for data in parts.values())
    if TOKEN.search(text) or "[[" in text:
        raise DocumentGenerationError("unresolved_token", "Le document DOCX n’a pas pu être généré.")


def validate_pdf(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DocumentGenerationError("invalid_pdf", "Le PDF n’a pas pu être créé.") from exc
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
        raise DocumentGenerationError("invalid_pdf", "Le PDF n’a pas pu être créé.")
    pages = len(re.findall(rb"/Type\s*/Page\b", data))
    if pages < 1: raise DocumentGenerationError("invalid_pdf", "Le PDF n’a pas pu être créé.")
    return pages
