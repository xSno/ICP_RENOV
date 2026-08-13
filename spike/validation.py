from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .errors import FixtureValidationError, RenderValidationError, TemplateValidationError
from .ooxml import W, get_sdt_tag, q, read_package, story_parts, text_of
from .registry import BLOCKS, FIELDS, LOOPS, OBSOLETE_FIELDS, is_renderable_field


EDITORIAL_MARKERS = ("À compléter", "A COMPLETER", "TODO", "[INSÉRER", "[INSERT")
RAW_ENUMS = {
    "PERSON", "ORGANIZATION", "CONSUMER", "NON_PROFESSIONAL", "PROFESSIONAL",
    "NONE", "MANUAL", "TACIT", "IN_HOUSE_AUTHORIZED", "PARTNER", "EXCLUDED",
    "STANDARD", "CUSTOM", "BANK_TRANSFER", "CHEQUE", "DIRECT_DEBIT",
    "IN_PREMISES", "OFF_PREMISES", "DISTANCE_EMAIL", "ONLINE_INTERFACE", "OTHER_DISTANCE",
    "FIXED", "NEW_PRICE_ON_RENEWAL", "DEEP_CLEANING", "DISINFECTION",
}
PARTY_TYPES = {"PERSON", "ORGANIZATION"}
CLIENT_REGIMES = {"CONSUMER", "NON_PROFESSIONAL", "PROFESSIONAL"}
CONCLUSION_MODES = {"IN_PREMISES", "OFF_PREMISES", "DISTANCE_EMAIL", "ONLINE_INTERFACE", "OTHER_DISTANCE"}
INITIAL_DURATION_MODES = {"STANDARD", "CUSTOM"}
RENEWAL_MODES = {"NONE", "MANUAL", "TACIT"}
RENEWAL_PRICE_RULES = {"FIXED", "NEW_PRICE_ON_RENEWAL"}
INCLUDED_OPTIONS = {"DEEP_CLEANING", "DISINFECTION"}
REFRIGERANT_MODES = {"IN_HOUSE_AUTHORIZED", "PARTNER", "EXCLUDED"}
TEMPLATE_STATUSES = {"TO_VALIDATE", "AVAILABLE", "ARCHIVED"}
LEGAL_CONTEXT_BLOCKS = {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE", "BLOCK_ELECTRONIC_TERMINATION"}


def _tag_value(tag: str) -> tuple[str, str]:
    bits = tag.split(":", 2)
    if len(bits) != 3 or bits[0] != "icp":
        raise TemplateValidationError(f"Unknown content-control tag: {tag or '<empty>'}")
    return bits[1], bits[2]


def preflight_template(path: Path, document_kind: str) -> dict:
    try:
        parts = read_package(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise TemplateValidationError(f"Unreadable DOCX template: {path}") from exc
    found = {"fields": set(), "blocks": set(), "loops": set(), "images": set(), "optionals": set()}
    for part_name in story_parts(parts):
        try:
            root = ET.fromstring(parts[part_name])
        except ET.ParseError as exc:
            raise TemplateValidationError(f"Malformed XML in {part_name}") from exc
        raw_text = text_of(root)
        if "{{" in raw_text or "[[" in raw_text:
            raise TemplateValidationError(f"Unadapted textual marker remains in {part_name}")
        for marker in EDITORIAL_MARKERS:
            if marker in raw_text:
                raise TemplateValidationError(f"Editorial marker remains in {part_name}: {marker}")
        for sdt in root.iter(q(W, "sdt")):
            tag = get_sdt_tag(sdt)
            kind, value = _tag_value(tag)
            if kind in {"field", "image"}:
                key = value.split(";", 1)[0]
                if key in OBSOLETE_FIELDS:
                    raise TemplateValidationError(f"Obsolete placeholder: {key}")
                if key not in FIELDS:
                    raise TemplateValidationError(f"Unknown placeholder: {key}")
                if not is_renderable_field(key, document_kind) and kind != "image":
                    raise TemplateValidationError(f"Forbidden/raw/internal placeholder: {key}")
                found["images" if kind == "image" else "fields"].add(key)
            elif kind == "block":
                name, _, linked = value.partition("|")
                if name not in BLOCKS:
                    raise TemplateValidationError(f"Unknown conditional block: {name}")
                if name == "BLOCK_OPTIONAL_COMPANY_FIELD":
                    if not linked.startswith("company.") or linked not in FIELDS:
                        raise TemplateValidationError(f"Invalid optional company-field binding: {linked}")
                found["blocks"].add(name)
            elif kind == "loop":
                if value not in LOOPS:
                    raise TemplateValidationError(f"Unknown loop: {value}")
                found["loops"].add(value)
                content = sdt.find(q(W, "sdtContent"))
                if content is None or not any(el.tag == q(W, "tr") or el.tag == q(W, "sdt") for el in list(content)):
                    raise TemplateValidationError(f"Malformed equipment loop: {value}")
            elif kind == "optional":
                if value not in FIELDS or not value.startswith("intervention."):
                    raise TemplateValidationError(f"Unknown optional binding: {value}")
                found["optionals"].add(value)
            else:
                raise TemplateValidationError(f"Unknown content-control kind: {kind}")
    if "contract.equipment_items" not in found["loops"]:
        raise TemplateValidationError("Missing required equipment loop")
    if document_kind == "CONTRACT" and any(key.startswith("intervention.") for key in found["fields"]):
        raise TemplateValidationError("Incompatible document kind: intervention field in CONTRACT")
    if document_kind == "INTERVENTION_SHEET" and "document.revision" in found["fields"]:
        raise TemplateValidationError("Incompatible document kind: revision in INTERVENTION_SHEET")
    return {key: sorted(value) for key, value in found.items()}


def validate_fixture(ctx: dict, document_kind: str) -> None:
    if document_kind == "INTERVENTION_SHEET":
        required = [
            "company.legal_name", "client.party_type", "client.postal_address",
            "site.address_line1", "site.postal_code", "site.city", "site.country",
            "contract.number", "intervention.date",
        ]
    else:
        required = [
            "company.legal_name", "company.registered_address", "company.phone", "company.email",
            "client.party_type", "client.regime", "client.postal_address",
            "site.address_line1", "site.postal_code", "site.city", "site.country",
            "contract.type_code", "contract.number", "contract.issue_date", "contract.start_date",
            "contract.initial_duration_mode", "contract.visits_per_year", "contract.renewal_mode",
            "service.included_area", "service.business_hours", "service.travel_included",
            "service.priority_breakdown", "service.refrigerant_handling_mode",
            "pricing.annual_ht", "pricing.vat_rate", "pricing.payment_terms_code", "pricing.payment_methods",
        ]
    for key in required:
        value: object = ctx
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value in (None, "", []):
            raise FixtureValidationError(f"Missing REQUIRED value: {key}")
    client = ctx.get("client", {})
    contract = ctx.get("contract", {})
    service = ctx.get("service", {})
    pricing = ctx.get("pricing", {})
    template = ctx.get("template", {})
    controlled = [
        ("client.party_type", client.get("party_type"), PARTY_TYPES),
        ("template.version_status", template.get("version_status"), TEMPLATE_STATUSES),
    ]
    if document_kind == "CONTRACT":
        controlled.extend([
            ("client.regime", client.get("regime"), CLIENT_REGIMES),
            ("contract.initial_duration_mode", contract.get("initial_duration_mode"), INITIAL_DURATION_MODES),
            ("contract.renewal_mode", contract.get("renewal_mode"), RENEWAL_MODES),
            ("service.refrigerant_handling_mode", service.get("refrigerant_handling_mode"), REFRIGERANT_MODES),
        ])
        if contract.get("conclusion_mode") not in (None, ""):
            controlled.append(("contract.conclusion_mode", contract.get("conclusion_mode"), CONCLUSION_MODES))
    for key, value, allowed in controlled:
        if value not in allowed:
            raise FixtureValidationError(f"Invalid controlled value for {key}: {value}")
    if document_kind == "CONTRACT":
        unknown_options = set(service.get("included_options", [])) - INCLUDED_OPTIONS
        if unknown_options:
            raise FixtureValidationError(f"Invalid controlled value for service.included_options: {sorted(unknown_options)}")
        if contract.get("type_code") not in (None, "CLIMATE_MAINTENANCE"):
            raise FixtureValidationError(f"Invalid controlled value for contract.type_code: {contract.get('type_code')}")
        duration_mode = contract.get("initial_duration_mode")
        if duration_mode == "STANDARD":
            if not isinstance(contract.get("initial_duration_months"), int) or contract["initial_duration_months"] < 1:
                raise FixtureValidationError("Missing REQUIRED value: contract.initial_duration_months")
        elif not contract.get("initial_end_date"):
            raise FixtureValidationError("Missing REQUIRED value: contract.initial_end_date")
        renewal_rule = pricing.get("renewal_price_rule")
        if renewal_rule is not None and renewal_rule not in RENEWAL_PRICE_RULES:
            raise FixtureValidationError(f"Invalid controlled value for pricing.renewal_price_rule: {renewal_rule}")
        if contract.get("renewal_mode") != "NONE" and renewal_rule not in RENEWAL_PRICE_RULES:
            raise FixtureValidationError(f"Invalid controlled value for pricing.renewal_price_rule: {pricing.get('renewal_price_rule')}")
        if not isinstance(pricing.get("payment_methods"), list) or not pricing["payment_methods"]:
            raise FixtureValidationError("Missing REQUIRED value: pricing.payment_methods")
        if pricing.get("payment_due_days") is not None and int(pricing["payment_due_days"]) < 0:
            raise FixtureValidationError("pricing.payment_due_days must be non-negative")
        if "authorized_blocks" in template:
            raise FixtureValidationError("Flat template.authorized_blocks metadata is forbidden")
        seen_contexts: set[tuple[str, str]] = set()
        for row in template.get("context_authorizations", []):
            if row.get("client_regime") not in CLIENT_REGIMES or row.get("conclusion_mode") not in CONCLUSION_MODES:
                raise FixtureValidationError("Invalid context authorization row")
            context_key = (row["client_regime"], row["conclusion_mode"])
            if context_key in seen_contexts:
                raise FixtureValidationError("Duplicate context authorization row")
            seen_contexts.add(context_key)
            if set(row.get("authorized_blocks", [])) - LEGAL_CONTEXT_BLOCKS:
                raise FixtureValidationError("Context authorization contains a non-legal block")
        if contract.get("conclusion_mode") in (None, ""):
            regime = client.get("regime")
            exercises_legal_context = any(
                row.get("client_regime") == regime
                and bool(set(row.get("authorized_blocks", [])) & LEGAL_CONTEXT_BLOCKS)
                for row in template.get("context_authorizations", [])
            )
            if contract.get("early_performance_requested") or exercises_legal_context:
                raise FixtureValidationError("Missing REQUIRED value: contract.conclusion_mode")
    items = ctx.get("contract", {}).get("equipment_items", [])
    if not items:
        raise FixtureValidationError("Missing REQUIRED value: contract.equipment_items")
    positions = [int(item.get("position", -1)) for item in items]
    if len(set(positions)) != len(positions) or any(p < 1 for p in positions):
        raise FixtureValidationError("Equipment positions must be unique positive integers")
    for item in items:
        for key in ("type", "location"):
            if not item.get(key):
                raise FixtureValidationError(f"Missing REQUIRED equipment value: {key}")
        if "internal_notes" in item:
            # Allowed in source fixture only to prove it cannot leak.
            pass


def validate_official_compatibility(ctx: dict, document_kind: str) -> None:
    template = ctx.get("template", {})
    if template.get("version_status") != "AVAILABLE":
        raise FixtureValidationError("Official generation requires template.version_status = AVAILABLE")
    if template.get("document_kind") != document_kind:
        raise FixtureValidationError("Official generation document kind mismatch")
    if document_kind == "CONTRACT":
        contract_type = ctx.get("contract", {}).get("type_code")
        if template.get("contract_type_code") != contract_type:
            raise FixtureValidationError("Official generation contract type mismatch")
        regime = ctx.get("client", {}).get("regime")
        if regime not in template.get("allowed_client_regimes", []):
            raise FixtureValidationError("Official generation client regime mismatch")


def validate_docx(path: Path, expected_equipment: list[dict], expect_logo: bool) -> None:
    if not path.exists() or path.stat().st_size < 1000:
        raise RenderValidationError(f"Missing output DOCX: {path}")
    try:
        parts = read_package(path)
    except Exception as exc:
        raise RenderValidationError(f"Unreadable output DOCX: {path}") from exc
    text_parts: list[str] = []
    for name in story_parts(parts):
        try:
            root = ET.fromstring(parts[name])
        except ET.ParseError as exc:
            raise RenderValidationError(f"Unreadable rendered XML: {name}") from exc
        text_parts.append(text_of(root))
        if any(get_sdt_tag(sdt).startswith("icp:") for sdt in root.iter(q(W, "sdt"))):
            raise RenderValidationError(f"Unresolved content control in {name}")
    all_text = "\n".join(text_parts)
    if re.search(r"\{\{.*?\}\}|\[\[.*?\]\]|icp:(?:field|block|loop|optional|image)", all_text):
        raise RenderValidationError("Unresolved token remains in rendered DOCX")
    for enum in RAW_ENUMS:
        # Do not flag a coincidental enum substring embedded in a test identifier.
        if re.search(rf"(?<![A-Z0-9_-]){re.escape(enum)}(?![A-Z0-9_-])", all_text):
            raise RenderValidationError(f"Raw technical enum printed: {enum}")
    for item in expected_equipment:
        display = " - ".join(x for x in (item.get("type"), item.get("brand"), item.get("model")) if x)
        if all_text.count(display) != 1:
            raise RenderValidationError(f"Equipment row mismatch for: {display}")
        internal = item.get("internal_notes")
        if internal and internal in all_text:
            raise RenderValidationError("equipment.internal_notes leaked into document")
    has_logo = any(name.startswith("word/media/icp_logo_") for name in parts)
    if has_logo != expect_logo:
        raise RenderValidationError(f"Logo presence mismatch: expected {expect_logo}, got {has_logo}")


def validate_pdf(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 1000:
        raise RenderValidationError(f"Missing PDF: {path}")
    data = path.read_bytes()
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
        raise RenderValidationError(f"Unreadable PDF: {path}")
