from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .adapter import adapt
from .formatters import lookup,prepare_context
from .ooxml import W,add_image,clone,get_sdt_tag,image_run,q,read_package,replace_child,set_text,story_parts,write_package,xml_bytes
from .registry import BLOCKS,FIELDS,LOOPS,OBSOLETE_FIELDS,renderable
from .validation import DocumentGenerationError,validate_rendered_docx

def _truthy(value):return value not in (None,"",[],(),False)
def _active(name,linked,ctx,equipment):
    client=ctx.get("client",{});contract=ctx.get("contract",{});service=ctx.get("service",{});pricing=ctx.get("pricing",{});selected=set(ctx.get("template",{}).get("selected_blocks",()))
    direct={"BLOCK_CLIENT_PERSON":client.get("party_type")=="PERSON","BLOCK_CLIENT_ORGANIZATION":client.get("party_type")=="ORGANIZATION",
            "BLOCK_CLIENT_CONSUMER":client.get("regime")=="CONSUMER","BLOCK_CLIENT_NON_PROFESSIONAL":client.get("regime")=="NON_PROFESSIONAL","BLOCK_CLIENT_PROFESSIONAL":client.get("regime")=="PROFESSIONAL",
            "BLOCK_BILLING_ADDRESS_DIFFERENT":_truthy(client.get("billing_address")),"BLOCK_INCLUDED_OPTIONS":_truthy(service.get("included_options")),
            "BLOCK_ADDITIONAL_EXCLUSIONS":_truthy(service.get("additional_exclusions")),"BLOCK_MISSED_APPOINTMENT_FEE":_truthy(pricing.get("missed_appointment_fee")),
            "BLOCK_SPECIAL_TERMS":_truthy(contract.get("special_terms")),"BLOCK_RENEWAL_NONE":contract.get("renewal_mode")=="NONE",
            "BLOCK_RENEWAL_MANUAL":contract.get("renewal_mode")=="MANUAL","BLOCK_RENEWAL_TACIT":contract.get("renewal_mode")=="TACIT",
            "BLOCK_REFRIGERANT_IN_HOUSE":service.get("refrigerant_handling_mode")=="IN_HOUSE_AUTHORIZED","BLOCK_REFRIGERANT_PARTNER":service.get("refrigerant_handling_mode")=="PARTNER",
            "BLOCK_REFRIGERANT_EXCLUDED":service.get("refrigerant_handling_mode")=="EXCLUDED","BLOCK_EQUIPMENT_OBSERVATIONS":bool(equipment and _truthy(equipment.get("observations")))}
    if name=="BLOCK_EARLY_PERFORMANCE":return name in selected and "BLOCK_WITHDRAWAL" in selected and bool(contract.get("early_performance_requested"))
    if name in {"BLOCK_WITHDRAWAL","BLOCK_ELECTRONIC_TERMINATION"}:return name in selected
    if name=="BLOCK_OPTIONAL_COMPANY_FIELD":return _truthy(lookup(ctx,linked,equipment))
    return direct.get(name,False)
def _preflight_adapted(path:Path)->None:
    parts=read_package(path);seen_loop=False
    for name in story_parts(parts):
        root=ET.fromstring(parts[name])
        visible="".join(node.text or "" for node in root.iter(q(W,"t")))
        if any(marker in visible for marker in ("À compléter","A COMPLETER","TODO","[INSÉRER","[INSERT")):
            raise DocumentGenerationError("editorial_marker","Le modèle contient un repère éditorial non autorisé.")
        for sdt in root.iter(q(W,"sdt")):
            tag=get_sdt_tag(sdt)
            try:prefix,kind,payload=tag.split(":",2)
            except ValueError as exc:raise DocumentGenerationError("malformed_marker","Le modèle sélectionné ne peut pas être utilisé.") from exc
            if prefix!="icp":raise DocumentGenerationError("unknown_marker","Le modèle sélectionné ne peut pas être utilisé.")
            value=payload.split("|",1)[0]
            if kind in {"field","image","optional"} and (not renderable(value) or value in OBSOLETE_FIELDS):raise DocumentGenerationError("unknown_placeholder","Le modèle contient un champ non autorisé.")
            if kind=="block" and value not in BLOCKS:raise DocumentGenerationError("unknown_block","Le modèle contient un bloc non autorisé.")
            if kind=="loop":
                if value not in LOOPS:raise DocumentGenerationError("unknown_loop","Le modèle contient une liste non autorisée.")
                seen_loop=True
            if kind not in {"field","image","optional","block","loop"}:raise DocumentGenerationError("unknown_marker","Le modèle sélectionné ne peut pas être utilisé.")
    if not seen_loop:raise DocumentGenerationError("missing_loop","Le modèle ne contient pas la liste des équipements requise.")
def _empty_required(key,value):
    if value is None or value=="" or value==[] or value==():return True
    if key=="contract.visits_per_year":
        try:return int(value)<1
        except (TypeError,ValueError):return True
    if key=="contract.breach_cure_period_days":
        try:return int(value)<0
        except (TypeError,ValueError):return True
    return False
def _required_values(parent,ctx,equipment):
    for child in list(parent):
        if child.tag!=q(W,"sdt"):
            yield from _required_values(child,ctx,equipment);continue
        _,kind,value=get_sdt_tag(child).split(":",2);content=child.find(q(W,"sdtContent"))
        if content is None:continue
        if kind=="field":
            key=value.partition("|")[0];spec=FIELDS[key]
            if spec.requiredness=="REQUIRED" or spec.requiredness.startswith("REQUIRED_WHEN"):
                yield key,lookup(ctx,key,equipment)
        elif kind=="optional" or kind=="block":
            active=_truthy(lookup(ctx,value,equipment)) if kind=="optional" else _active(value.partition("|")[0],value.partition("|")[2],ctx,equipment)
            if active:yield from _required_values(content,ctx,equipment)
        elif kind=="loop":
            for item in ctx["contract"]["equipment_items"]:yield from _required_values(content,ctx,item)
def _preflight_required_values(parts,ctx):
    missing=[]
    for name in story_parts(parts):
        root=ET.fromstring(parts[name])
        for key,value in _required_values(root,ctx,None):
            if _empty_required(key,value) and key not in missing:missing.append(key)
    if missing:
        raise DocumentGenerationError("required_value","Le contrat contient une information requise manquante pour le modèle sélectionné.")
def _render(parent,ctx,equipment,parts,part_name):
    for child in list(parent):
        if child.tag!=q(W,"sdt"):_render(child,ctx,equipment,parts,part_name);continue
        _,kind,value=get_sdt_tag(child).split(":",2);content=child.find(q(W,"sdtContent"))
        if content is None:raise DocumentGenerationError("malformed_marker","Le document DOCX n’a pas pu être généré.")
        if kind=="field":
            rendered=lookup(ctx,value,equipment);run=next(content.iter(q(W,"r")),ET.Element(q(W,"r")));run=clone(run);set_text(run,str(rendered or ""));replace_child(parent,child,[run])
        elif kind=="image":
            path=Path(str(lookup(ctx,value,equipment))) if lookup(ctx,value,equipment) else None
            if path is None:replace_child(parent,child,[])
            elif not path.is_file():raise DocumentGenerationError("missing_resource","Le document DOCX n’a pas pu être généré.")
            else:replace_child(parent,child,[image_run(add_image(parts,part_name,path.read_bytes()))])
        elif kind=="optional" or kind=="block":
            active=_truthy(lookup(ctx,value,equipment)) if kind=="optional" else _active(*((value.partition("|")[0],value.partition("|")[2],ctx,equipment)))
            if active:_render(content,ctx,equipment,parts,part_name);replace_child(parent,child,list(content))
            else:replace_child(parent,child,[])
        elif kind=="loop":
            nodes=[]
            for item in ctx["contract"]["equipment_items"]:
                holder=ET.Element("holder");holder.extend(clone(node) for node in list(content));_render(holder,ctx,item,parts,part_name);nodes.extend(list(holder))
            replace_child(parent,child,nodes)
class ProductionDocxRenderer:
    def render(self,source:Path,output:Path,context:dict,workdir:Path)->None:
        adapted=workdir/"adapted.docx"
        try:adapt(source,adapted);_preflight_adapted(adapted);parts=read_package(adapted);prepared=prepare_context(context);_preflight_required_values(parts,prepared)
        except DocumentGenerationError:raise
        except Exception as exc:raise DocumentGenerationError("template_preflight","Le modèle sélectionné ne peut pas être utilisé.") from exc
        for name in story_parts(parts):
            root=ET.fromstring(parts[name]);_render(root,prepared,None,parts,name);parts[name]=xml_bytes(root)
        write_package(parts,output);validate_rendered_docx(output)
