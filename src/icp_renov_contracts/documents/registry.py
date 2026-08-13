from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

FIELD_FILE=Path(__file__).resolve().parents[3]/"docs"/"FIELD_TEMPLATE_CONTRACT_V1_1.txt"
KEY=re.compile(r"^([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*(?:\[\])?)+)\s*$")
BLOCK=re.compile(r"^(BLOCK_[A-Z0-9_]+)\s*$")
@dataclass(frozen=True)
class FieldSpec:key:str;field_type:str="";requiredness:str=""
def load_registry(path:Path=FIELD_FILE):
    fields={};blocks=set();obsolete=set();current=None;in_blocks=False;in_mapping=False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line=raw.strip()
        if line.startswith("14. CONDITIONAL BLOCKS"):in_blocks=True
        elif line.startswith("15. LEGAL"):in_blocks=False
        elif line.startswith("19. MAPPING"):in_mapping=True
        elif line.startswith("20. OPTIONS"):in_mapping=False
        if match:=KEY.match(line):
            key=match.group(1)
            if in_mapping:obsolete.add(key)
            else:current=key;fields.setdefault(key,FieldSpec(key))
            continue
        if in_blocks and (match:=BLOCK.match(line)):blocks.add(match.group(1))
        if current and line.startswith("TYPE:"):
            old=fields[current];fields[current]=FieldSpec(current,line.split(":",1)[1].strip(),old.requiredness)
        elif current and line.startswith("REQUIREDNESS:"):
            old=fields[current];fields[current]=FieldSpec(current,old.field_type,line.split(":",1)[1].strip())
    obsolete.update({"company.siren_siret","company.registration","equipment.notes","contract.equipments","contract.duration_months",
                     "contract.termination_channels","contract.early_termination_reasons","contract.default_notice_days","service.missed_appointment_fee",
                     "pricing.payment_terms","template.audience","document.signed_revision","event.date","legal.tacit_renewal_statutory_text"})
    return fields,blocks,obsolete
FIELDS,BLOCKS,OBSOLETE_FIELDS=load_registry();LOOPS={"contract.equipment_items"}
def renderable(key:str)->bool:
    return key in FIELDS and key not in OBSOLETE_FIELDS and FIELDS[key].requiredness!="INTERNAL" and FIELDS[key].field_type!="enum" and not key.startswith("intervention.") and key!="equipment.internal_notes"
