from __future__ import annotations

from pathlib import Path
import re
from xml.etree import ElementTree as ET

from .ooxml import W,clone,make_sdt,q,read_package,set_text,story_parts,text_of,write_package,xml_bytes

BLOCK_START=re.compile(r"^\[\[BLOCK:(BLOCK_[A-Z0-9_]+)(?:\|([^\]]+))?\]\]$");BLOCK_END=re.compile(r"^\[\[/BLOCK:(BLOCK_[A-Z0-9_]+)(?:\|([^\]]+))?\]\]$")
LOOP_START=re.compile(r"\[\[LOOP:([^\]]+)\]\]");LOOP_END=re.compile(r"\[\[/LOOP:([^\]]+)\]\]");TOKEN=re.compile(r"\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}")
def _replace(parent,children):
    for child in list(parent):parent.remove(child)
    parent.extend(children)
def _blocks(parent):
    output=[];stack=[]
    for child in list(parent):
        text=text_of(child).strip() if child.tag==q(W,"p") else "";start=BLOCK_START.match(text);end=BLOCK_END.match(text)
        if start:stack.append((start.group(1),start.group(2),[]))
        elif end:
            if not stack or stack[-1][0]!=end.group(1):raise ValueError("malformed block")
            name,key,content=stack.pop();wrapped=make_sdt(f"icp:block:{name}"+(f"|{key}" if key else ""),content);(stack[-1][2] if stack else output).append(wrapped)
        else:(stack[-1][2] if stack else output).append(child)
    if stack:raise ValueError("unclosed block")
    _replace(parent,output)
def _strip(node,pattern):
    for text in node.iter(q(W,"t")):
        if text.text:text.text=pattern.sub("",text.text)
def _tables(root):
    for table in root.iter(q(W,"tbl")):
        children=list(table);rows=[(index,row,text_of(row)) for index,row in enumerate(children) if row.tag==q(W,"tr")]
        start=next(((i,row,LOOP_START.search(text)) for i,row,text in rows if LOOP_START.search(text)),None);end=next(((i,row,LOOP_END.search(text)) for i,row,text in rows if LOOP_END.search(text)),None)
        if not (start or end):continue
        if not start or not end or start[2].group(1)!=end[2].group(1) or start[0]>end[0]:raise ValueError("malformed equipment loop")
        _strip(start[1],LOOP_START);_strip(end[1],LOOP_END);adapted=[]
        for row in children[start[0]:end[0]+1]:
            text=text_of(row);begin=re.search(r"\[\[BLOCK:(BLOCK_[A-Z0-9_]+)\]\]",text);finish=re.search(r"\[\[/BLOCK:(BLOCK_[A-Z0-9_]+)\]\]",text)
            if begin or finish:
                if not begin or not finish or begin.group(1)!=finish.group(1):raise ValueError("malformed row block")
                _strip(row,re.compile(r"\[\[/?BLOCK:"+re.escape(begin.group(1))+r"\]\]"));adapted.append(make_sdt(f"icp:block:{begin.group(1)}",[row]))
            else:adapted.append(row)
        loop=make_sdt(f"icp:loop:{start[2].group(1)}",adapted);_replace(table,children[:start[0]]+[loop]+children[end[0]+1:])
        header=next((row for row in table.findall(q(W,"tr"))),None)
        if header is not None:
            props=header.find(q(W,"trPr"))
            if props is None:props=ET.Element(q(W,"trPr"));header.insert(0,props)
            if props.find(q(W,"tblHeader")) is None:ET.SubElement(props,q(W,"tblHeader"))
def _fields(root):
    for parent in list(root.iter()):
        for run in list(parent):
            if run.tag!=q(W,"r"):continue
            text=text_of(run);matches=list(TOKEN.finditer(text))
            if not matches:continue
            replacements=[];position=0
            for match in matches:
                if match.start()>position:
                    literal=clone(run);set_text(literal,text[position:match.start()]);replacements.append(literal)
                key=match.group(1);placeholder=clone(run);set_text(placeholder,f"<{key}>");replacements.append(make_sdt(f"icp:{'image' if key=='company.logo' else 'field'}:{key}",[placeholder]));position=match.end()
            if position<len(text):literal=clone(run);set_text(literal,text[position:]);replacements.append(literal)
            index=list(parent).index(run);parent.remove(run)
            for offset,node in enumerate(replacements):parent.insert(index+offset,node)
def _intervention_optionals(body):
    technician="{{ intervention.technician }}"
    for table in body.iter(q(W,"tbl")):
        for row in list(table):
            if row.tag==q(W,"tr") and technician in text_of(row):
                index=list(table).index(row);table.remove(row);table.insert(index,make_sdt("icp:optional:intervention.technician",[row]))
    pairs={"{{ intervention.notes }}":"intervention.notes","{{ intervention.issues }}":"intervention.issues"}
    children=list(body);index=0
    while index<len(children):
        child=children[index];text=text_of(child)
        if child.tag==q(W,"p") and text in pairs and index>0 and children[index-1].tag==q(W,"p"):
            label=children[index-1];field=child;position=list(body).index(label);body.remove(label);body.remove(field)
            body.insert(position,make_sdt(f"icp:optional:{pairs[text]}",[label,field]));children=list(body);index=max(position-1,0);continue
        index+=1
    for paragraph in list(body):
        if paragraph.tag!=q(W,"p"):continue
        text=text_of(paragraph);key=next((value for token,value in {
            "{{ intervention.other }}":"intervention.other",
            "{{ intervention.quote_recommended }}":"intervention.quote_recommended",
        }.items() if token in text),None)
        if key:
            position=list(body).index(paragraph);body.remove(paragraph);body.insert(position,make_sdt(f"icp:optional:{key}",[paragraph]))
    for paragraph in list(body):
        if paragraph.tag==q(W,"p") and text_of(paragraph).strip()=="Observations et anomalies":
            position=list(body).index(paragraph);body.remove(paragraph);body.insert(position,make_sdt("icp:block:BLOCK_INTERVENTION_DETAILS",[paragraph]))
def _normalize_layout(root):
    compat=root.find(q(W,"compat"))
    if compat is not None:
        for item in list(compat):
            if item.tag==q(W,"useFELayout") or (item.tag==q(W,"compatSetting") and item.get(q(W,"name"))=="doNotFlipMirrorIndents"):compat.remove(item)
def _layout_repairs(root):
    for paragraph in root.iter(q(W,"p")):
        props=paragraph.find(q(W,"pPr"));style=None if props is None else props.find(q(W,"pStyle"))
        if style is not None and style.get(q(W,"val"))=="Heading1":
            if props is None:props=ET.Element(q(W,"pPr"));paragraph.insert(0,props)
            spacing=props.find(q(W,"spacing"))
            if spacing is None:spacing=ET.SubElement(props,q(W,"spacing"))
            spacing.set(q(W,"before"),"200");spacing.set(q(W,"after"),"140");spacing.set(q(W,"line"),"300");spacing.set(q(W,"lineRule"),"atLeast")
            indent=props.find(q(W,"ind"))
            if indent is None:indent=ET.SubElement(props,q(W,"ind"))
            for key in ("left","right","firstLine"):indent.set(q(W,key),"0")
            if props.find(q(W,"keepNext")) is None:ET.SubElement(props,q(W,"keepNext"))
            old=props.find(q(W,"pageBreakBefore"))
            if old is not None:props.remove(old)
        for run in paragraph.findall(q(W,"r")):
            value=text_of(run);repaired=value
            if "Mention :" in repaired and not repaired.startswith("\n"):repaired=repaired.replace("Mention :","\nMention :",1)
            if "Signature :" in repaired:repaired=repaired.replace("Signature :","\nSignature :")
            if repaired!=value:set_text(run,repaired)
def _page_breaks(parent):
    children=list(parent)
    for index,child in enumerate(children):
        if child.tag!=q(W,"p"):_page_breaks(child);continue
        heading=text_of(child).strip()
        if not heading.startswith(("Article 15 -","Annexe 1 -","Annexe 2 -")):continue
        if index:
            previous=children[index-1]
            if previous.tag==q(W,"p") and not text_of(previous).strip() and any(br.get(q(W,"type"))=="page" for br in previous.iter(q(W,"br"))):parent.remove(previous)
        if heading.startswith("Annexe 2 -"):
            props=child.find(q(W,"pPr"))
            if props is None:props=ET.Element(q(W,"pPr"));child.insert(0,props)
            if props.find(q(W,"pageBreakBefore")) is None:ET.SubElement(props,q(W,"pageBreakBefore"))
        else:
            run=next(child.iter(q(W,"r")),None)
            if run is not None and not any(br.get(q(W,"type"))=="page" for br in child.iter(q(W,"br"))):
                page=ET.Element(q(W,"br"));page.set(q(W,"type"),"page");run.insert(1 if run.find(q(W,"rPr")) is not None else 0,page)
def adapt_package(source:Path)->dict[str,bytes]:
    parts=read_package(source)
    for name in story_parts(parts):
        root=ET.fromstring(parts[name])
        if name=="word/document.xml":
            body=root.find(q(W,"body"))
            if body is None:raise ValueError("missing document body")
            _blocks(body);_tables(body);_intervention_optionals(body)
        _fields(root)
        if name=="word/document.xml":_layout_repairs(body);_page_breaks(body)
        parts[name]=xml_bytes(root)
    if "word/settings.xml" in parts:
        settings=ET.fromstring(parts["word/settings.xml"]);_normalize_layout(settings);parts["word/settings.xml"]=xml_bytes(settings)
    return parts

def adapt(source:Path,target:Path)->None:
    write_package(adapt_package(source),target)
