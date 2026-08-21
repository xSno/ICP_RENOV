from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from ..runtime_resources import field_template_path


FIELD_FILE=field_template_path()
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
INTERVENTION_FIELDS=frozenset({"intervention.date","intervention.technician","intervention.other","intervention.notes","intervention.issues","intervention.quote_recommended"})
SHEET_BLOCKS=frozenset({"BLOCK_OPTIONAL_COMPANY_FIELD","BLOCK_EQUIPMENT_OBSERVATIONS","BLOCK_INTERVENTION_DETAILS"})
def renderable(key:str,document_kind:str="CONTRACT")->bool:
    if key not in FIELDS or key in OBSOLETE_FIELDS or FIELDS[key].requiredness=="INTERNAL" or key=="equipment.internal_notes":return False
    if document_kind=="INTERVENTION_SHEET":return FIELDS[key].field_type!="enum" and (key in INTERVENTION_FIELDS or not key.startswith("intervention."))
    return FIELDS[key].field_type!="enum" and not key.startswith("intervention.")
