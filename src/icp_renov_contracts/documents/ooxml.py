from __future__ import annotations

import copy
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

W="http://schemas.openxmlformats.org/wordprocessingml/2006/main"; R="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V="urn:schemas-microsoft-com:vml"; REL="http://schemas.openxmlformats.org/package/2006/relationships"; CT="http://schemas.openxmlformats.org/package/2006/content-types"
MC="http://schemas.openxmlformats.org/markup-compatibility/2006"
for prefix, uri in (("w",W),("r",R),("v",V)): ET.register_namespace(prefix,uri)

def q(ns:str,name:str)->str:return f"{{{ns}}}{name}"
def clone(node:ET.Element)->ET.Element:return copy.deepcopy(node)
def read_package(path:Path)->dict[str,bytes]:
    with zipfile.ZipFile(path) as package:return {name:package.read(name) for name in package.namelist()}
def write_package(parts:dict[str,bytes],path:Path)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as package:
        for name in sorted(parts):package.writestr(name,parts[name])
def story_parts(parts:dict[str,bytes])->list[str]:
    return sorted(name for name in parts if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml",name))
def xml_bytes(root:ET.Element)->bytes:
    root.attrib.pop(q(MC,"Ignorable"),None)
    return ET.tostring(root,encoding="utf-8",xml_declaration=True)
def opc_xml_bytes(root:ET.Element,namespace:str)->bytes:
    ET.register_namespace("",namespace);return ET.tostring(root,encoding="utf-8",xml_declaration=True)
def text_of(node:ET.Element)->str:return "".join(item.text or "" for item in node.iter(q(W,"t")))
def set_text(run:ET.Element,value:str)->None:
    rpr=run.find(q(W,"rPr"))
    for child in list(run):
        if child is not rpr:run.remove(child)
    for index,line in enumerate(value.split("\n")):
        if index:ET.SubElement(run,q(W,"br"))
        if line:
            text=ET.SubElement(run,q(W,"t"));text.text=line
            if line[:1].isspace() or line[-1:].isspace():text.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
def make_sdt(tag:str,children:list[ET.Element])->ET.Element:
    sdt=ET.Element(q(W,"sdt"));pr=ET.SubElement(sdt,q(W,"sdtPr"));item=ET.SubElement(pr,q(W,"tag"));item.set(q(W,"val"),tag)
    content=ET.SubElement(sdt,q(W,"sdtContent"));content.extend(children);return sdt
def get_sdt_tag(sdt:ET.Element)->str:
    item=sdt.find(f"./{q(W,'sdtPr')}/{q(W,'tag')}");return "" if item is None else item.get(q(W,"val"),"")
def replace_child(parent:ET.Element,old:ET.Element,new:list[ET.Element])->None:
    index=list(parent).index(old);parent.remove(old)
    for offset,node in enumerate(new):parent.insert(index+offset,node)
def add_image(parts:dict[str,bytes],part_name:str,data:bytes)->str:
    part=Path(part_name);rels=str(part.parent/"_rels"/f"{part.name}.rels").replace("\\","/")
    root=ET.fromstring(parts[rels]) if rels in parts else ET.Element(q(REL,"Relationships")); used={n.get("Id") for n in root}
    number=1
    while f"rIdIcpLogo{number}" in used:number+=1
    rid=f"rIdIcpLogo{number}";relation=ET.SubElement(root,q(REL,"Relationship"));relation.set("Id",rid)
    relation.set("Type","http://schemas.openxmlformats.org/officeDocument/2006/relationships/image");relation.set("Target",f"media/icp_logo_{number}.png")
    parts[rels]=opc_xml_bytes(root,REL);parts[f"word/media/icp_logo_{number}.png"]=data
    ct=ET.fromstring(parts["[Content_Types].xml"])
    if not any(item.get("Extension")=="png" for item in ct.findall(q(CT,"Default"))):
        item=ET.SubElement(ct,q(CT,"Default"));item.set("Extension","png");item.set("ContentType","image/png")
    parts["[Content_Types].xml"]=opc_xml_bytes(ct,CT);return rid
def image_run(rid:str)->ET.Element:
    run=ET.Element(q(W,"r"));pict=ET.SubElement(run,q(W,"pict"));shape=ET.SubElement(pict,q(V,"shape"));shape.set("id","icp_logo")
    shape.set("type","#_x0000_t75");shape.set("style","width:120pt;height:36pt")
    image=ET.SubElement(shape,q(V,"imagedata"));image.set(q(R,"id"),rid);image.set("title","Logo ICP Renov");return run
