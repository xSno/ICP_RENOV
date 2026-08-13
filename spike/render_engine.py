from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .errors import RenderValidationError
from .formatters import lookup
from .ooxml import W, add_image_relationship, clone, get_sdt_tag, image_run, q, read_package, replace_child, set_text, story_parts, write_package, xml_bytes


def _truthy(value: object) -> bool:
    return value not in (None, "", [], False)


def _authorized_legal_blocks(ctx: dict) -> set[str]:
    """Return blocks from the one exact version-matrix row for recorded context."""
    client_regime = ctx.get("client", {}).get("regime")
    conclusion_mode = ctx.get("contract", {}).get("conclusion_mode")
    rows = ctx.get("template", {}).get("context_authorizations", [])
    matches = [
        row for row in rows
        if row.get("client_regime") == client_regime
        and row.get("conclusion_mode") == conclusion_mode
    ]
    if len(matches) != 1:
        return set()
    return set(matches[0].get("authorized_blocks", []))


def block_active(name: str, linked: str, ctx: dict, equipment: dict | None) -> bool:
    client = ctx.get("client", {})
    contract = ctx.get("contract", {})
    service = ctx.get("service", {})
    pricing = ctx.get("pricing", {})
    authorized_legal = _authorized_legal_blocks(ctx)
    direct = {
        "BLOCK_CLIENT_PERSON": client.get("party_type") == "PERSON",
        "BLOCK_CLIENT_ORGANIZATION": client.get("party_type") == "ORGANIZATION",
        "BLOCK_CLIENT_CONSUMER": client.get("regime") == "CONSUMER",
        "BLOCK_CLIENT_NON_PROFESSIONAL": client.get("regime") == "NON_PROFESSIONAL",
        "BLOCK_CLIENT_PROFESSIONAL": client.get("regime") == "PROFESSIONAL",
        "BLOCK_BILLING_ADDRESS_DIFFERENT": _truthy(client.get("billing_address")) and client.get("billing_address") != client.get("postal_address"),
        "BLOCK_INCLUDED_OPTIONS": _truthy(service.get("included_options")),
        "BLOCK_ADDITIONAL_EXCLUSIONS": _truthy(service.get("additional_exclusions")),
        "BLOCK_MISSED_APPOINTMENT_FEE": _truthy(pricing.get("missed_appointment_fee")),
        "BLOCK_SPECIAL_TERMS": _truthy(contract.get("special_terms")),
        "BLOCK_RENEWAL_NONE": contract.get("renewal_mode") == "NONE",
        "BLOCK_RENEWAL_MANUAL": contract.get("renewal_mode") == "MANUAL",
        "BLOCK_RENEWAL_TACIT": contract.get("renewal_mode") == "TACIT",
        "BLOCK_WITHDRAWAL": "BLOCK_WITHDRAWAL" in authorized_legal,
        "BLOCK_ELECTRONIC_TERMINATION": "BLOCK_ELECTRONIC_TERMINATION" in authorized_legal,
        "BLOCK_REFRIGERANT_IN_HOUSE": service.get("refrigerant_handling_mode") == "IN_HOUSE_AUTHORIZED",
        "BLOCK_REFRIGERANT_PARTNER": service.get("refrigerant_handling_mode") == "PARTNER",
        "BLOCK_REFRIGERANT_EXCLUDED": service.get("refrigerant_handling_mode") == "EXCLUDED",
        "BLOCK_EQUIPMENT_OBSERVATIONS": bool(equipment and _truthy(equipment.get("observations"))),
    }
    if name == "BLOCK_EARLY_PERFORMANCE":
        return (
            "BLOCK_EARLY_PERFORMANCE" in authorized_legal
            and "BLOCK_WITHDRAWAL" in authorized_legal
            and bool(contract.get("early_performance_requested"))
        )
    if name == "BLOCK_OPTIONAL_COMPANY_FIELD":
        return _truthy(lookup(ctx, linked))
    return direct.get(name, False)


def _value_runs(sdt: ET.Element, value: str) -> list[ET.Element]:
    content = sdt.find(q(W, "sdtContent"))
    template_run = None if content is None else next((node for node in content.iter(q(W, "r"))), None)
    run = clone(template_run) if template_run is not None else ET.Element(q(W, "r"))
    set_text(run, value)
    return [run]


def _render_container(parent: ET.Element, ctx: dict, equipment: dict | None, parts: dict[str, bytes], part_name: str) -> None:
    for child in list(parent):
        if child.tag != q(W, "sdt"):
            _render_container(child, ctx, equipment, parts, part_name)
            continue
        tag = get_sdt_tag(child)
        try:
            _, kind, value = tag.split(":", 2)
        except ValueError as exc:
            raise RenderValidationError(f"Malformed content-control tag: {tag}") from exc
        content = child.find(q(W, "sdtContent"))
        if content is None:
            raise RenderValidationError(f"Content control has no content: {tag}")
        if kind == "field":
            rendered = lookup(ctx, value, equipment)
            if isinstance(rendered, (list, dict)):
                raise RenderValidationError(f"Structured/raw value cannot be printed: {value}")
            replace_child(parent, child, _value_runs(child, str(rendered or "")))
        elif kind == "image":
            image_path = Path(str(lookup(ctx, value, equipment))) if lookup(ctx, value, equipment) else None
            if image_path is None:
                replace_child(parent, child, [])
            else:
                if not image_path.is_file():
                    raise RenderValidationError(f"Logo resource not found: {image_path}")
                rid = add_image_relationship(parts, part_name, image_path.read_bytes())
                replace_child(parent, child, [image_run(rid)])
        elif kind == "optional":
            active = _truthy(lookup(ctx, value, equipment))
            if active:
                _render_container(content, ctx, equipment, parts, part_name)
                replace_child(parent, child, list(content))
            else:
                replace_child(parent, child, [])
        elif kind == "block":
            name, _, linked = value.partition("|")
            active = block_active(name, linked, ctx, equipment)
            if active:
                _render_container(content, ctx, equipment, parts, part_name)
                replace_child(parent, child, list(content))
            else:
                replace_child(parent, child, [])
        elif kind == "loop":
            if value != "contract.equipment_items":
                raise RenderValidationError(f"Unknown loop: {value}")
            rendered_nodes: list[ET.Element] = []
            for item in ctx.get("contract", {}).get("equipment_items", []):
                holder = ET.Element("holder")
                holder.extend(clone(node) for node in list(content))
                _render_container(holder, ctx, item, parts, part_name)
                rendered_nodes.extend(list(holder))
            replace_child(parent, child, rendered_nodes)
        else:
            raise RenderValidationError(f"Unknown content-control kind: {kind}")


def render_docx(template: Path, output: Path, context: dict) -> None:
    parts = read_package(template)
    for part_name in story_parts(parts):
        root = ET.fromstring(parts[part_name])
        _render_container(root, context, None, parts, part_name)
        parts[part_name] = xml_bytes(root)
    write_package(parts, output)
