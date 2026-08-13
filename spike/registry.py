from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELD_CONTRACT = ROOT / "docs" / "FIELD_TEMPLATE_CONTRACT_V1_1.txt"

KEY_RE = re.compile(r"^([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*(?:\[\])?)+)\s*$")
BLOCK_RE = re.compile(r"^(BLOCK_[A-Z0-9_]+)\s*$")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    field_type: str = ""
    requiredness: str = ""


def load_registry(path: Path = FIELD_CONTRACT) -> tuple[dict[str, FieldSpec], set[str], set[str]]:
    text = path.read_text(encoding="utf-8")
    fields: dict[str, FieldSpec] = {}
    blocks: set[str] = set()
    obsolete: set[str] = set()
    current: str | None = None
    in_blocks = False
    in_mapping = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("14. CONDITIONAL BLOCKS"):
            in_blocks = True
        elif line.startswith("15. LEGAL"):
            in_blocks = False
        elif line.startswith("19. MAPPING"):
            in_mapping = True
        elif line.startswith("20. OPTIONS"):
            in_mapping = False

        m = KEY_RE.match(line)
        if m:
            key = m.group(1)
            if in_mapping:
                obsolete.add(key)
            else:
                current = key
                fields.setdefault(key, FieldSpec(key))
            continue
        if in_blocks:
            bm = BLOCK_RE.match(line)
            if bm:
                blocks.add(bm.group(1))
        if current and line.startswith("TYPE:"):
            old = fields[current]
            fields[current] = FieldSpec(current, line.split(":", 1)[1].strip(), old.requiredness)
        elif current and line.startswith("REQUIREDNESS:"):
            old = fields[current]
            fields[current] = FieldSpec(current, old.field_type, line.split(":", 1)[1].strip())

    # Explicitly deleted/renamed source keys from section 19.
    obsolete.update({
        "company.siren_siret", "company.registration", "equipment.notes",
        "contract.equipments", "contract.duration_months", "contract.termination_channels",
        "contract.early_termination_reasons", "contract.default_notice_days",
        "service.missed_appointment_fee", "pricing.payment_terms", "template.audience",
        "document.signed_revision", "event.date", "legal.tacit_renewal_statutory_text",
    })
    return fields, blocks, obsolete


FIELDS, BLOCKS, OBSOLETE_FIELDS = load_registry()
LOOPS = {"contract.equipment_items"}
FORBIDDEN_CONTRACT_FIELDS = {"equipment.internal_notes"} | {
    key for key in FIELDS if key.startswith("intervention.")
}
RAW_RENDER_TYPES = {"enum"}


def is_renderable_field(key: str, document_kind: str) -> bool:
    if key not in FIELDS or key in OBSOLETE_FIELDS:
        return False
    spec = FIELDS[key]
    if spec.requiredness == "INTERNAL":
        return False
    if document_kind == "CONTRACT" and key in FORBIDDEN_CONTRACT_FIELDS:
        return False
    return spec.field_type not in RAW_RENDER_TYPES
