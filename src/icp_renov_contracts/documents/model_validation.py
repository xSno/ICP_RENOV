from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid
from xml.etree import ElementTree as ET

from .adapter import adapt
from .ooxml import W, q
from .renderer import ProductionDocxRenderer, _preflight_adapted
from .source_store import TemplateSourceStore, sha256_file
from .validation import DocumentGenerationError, story_xml, validate_pdf, validate_rendered_docx


@dataclass(frozen=True)
class StructureControlResult:
    passed: bool
    issues: tuple[str, ...]
    equipment_loop: bool


@dataclass(frozen=True)
class ValidationRunResult:
    cases: tuple[str, ...]
    equipment_coverage: tuple[int, ...]
    evidence_paths: tuple[str, ...]
    evidence_hashes: tuple[str, ...]


ISSUE_LABELS = {
    "unknown_placeholder": "Placeholder inconnu",
    "unknown_block": "Bloc non reconnu",
    "unknown_loop": "Boucle équipements invalide",
    "missing_loop": "Boucle équipements requise absente",
    "editorial_marker": "Marqueur de conception présent",
    "malformed_marker": "Marqueur de modèle invalide",
    "unknown_marker": "Marqueur non reconnu",
    "invalid_docx": "DOCX source illisible",
}


def source_has_equipment_loop(source: Path) -> bool:
    try:
        for data in story_xml(source).values():
            root = ET.fromstring(data)
            text = "".join(node.text or "" for node in root.iter(q(W, "t")))
            if "[[LOOP:contract.equipment_items]]" in text:
                return True
    except (DocumentGenerationError, ET.ParseError):
        return False
    return False


class TemplateStructureValidator:
    def control(self, source: Path, document_kind: str, contract_type_code: str) -> StructureControlResult:
        issues: list[str] = []
        if document_kind not in {"CONTRACT", "INTERVENTION_SHEET"}:
            issues.append("Type de document incohérent")
        if document_kind == "CONTRACT" and contract_type_code != "CLIMATE_MAINTENANCE":
            issues.append("Type de contrat incohérent")
        if document_kind == "INTERVENTION_SHEET" and contract_type_code:
            issues.append("Type de contrat non applicable à une fiche d’intervention")
        equipment_loop = source_has_equipment_loop(source)
        work = source.parent / f".structure-control-{uuid.uuid4().hex}"
        try:
            TemplateSourceStore.validate_docx(source)
            work.mkdir()
            adapted = work / "adapted.docx"
            adapt(source, adapted)
            _preflight_adapted(adapted, document_kind)
        except DocumentGenerationError as exc:
            issues.append(ISSUE_LABELS.get(exc.code, exc.user_message))
        except (OSError, ValueError, ET.ParseError):
            issues.append("Structure DOCX invalide")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return StructureControlResult(not issues, tuple(dict.fromkeys(issues)), equipment_loop)


class NonOfficialModelValidationRunner:
    def __init__(self, workspace_root: Path, renderer: ProductionDocxRenderer, converter,
                 company_provider) -> None:
        self.workspace_root = workspace_root.resolve()
        self.renderer = renderer
        self.converter = converter
        self.company_provider = company_provider

    def run(self, version, source: Path, output_root: Path | None = None) -> ValidationRunResult:
        if not self.converter.available():
            raise DocumentGenerationError(
                "converter_unavailable",
                "LibreOffice 26.2.5.2 n’est pas disponible sur ce poste.",
            )
        attempt = uuid.uuid4().hex
        base = output_root.resolve() if output_root else self.workspace_root / "validation" / "models" / version.id
        root = base / attempt
        root.mkdir(parents=True, exist_ok=False)
        has_loop = source_has_equipment_loop(source)
        counts = (1, 10, 30) if has_loop else (1,)
        cases: list[str] = []
        paths: list[str] = []
        hashes: list[str] = []
        for index, count in enumerate(counts):
            case = f"equipment-{count}" if has_loop else "document-standard"
            cases.append(case)
            case_root = root / case
            case_root.mkdir()
            docx = case_root / "validation.docx"
            pdf = case_root / "validation.pdf"
            work = case_root / "work"
            work.mkdir()
            context = self._context(version, count, observations=index != 1)
            self.renderer.render(source, docx, context, work)
            validate_rendered_docx(docx)
            self.converter.convert(docx, pdf)
            validate_pdf(pdf)
            for path in (docx, pdf):
                paths.append(str(path) if output_root else path.relative_to(self.workspace_root).as_posix())
                hashes.append(sha256_file(path))
        return ValidationRunResult(tuple(cases), counts if has_loop else (), tuple(paths), tuple(hashes))

    def _context(self, version, count: int, observations: bool) -> dict[str, object]:
        company = self.company_provider.get()
        regime = next(iter(version.allowed_client_regimes or version.target_client_regimes), "CONSUMER")
        equipment = tuple({
            "position": index,
            "type": "Climatiseur synthétique",
            "brand": "Marque Test",
            "model": f"M-{index + 1:02d}",
            "serial_number": f"SYNTH-{index + 1:03d}",
            "power_kw": "3.5",
            "location": f"Zone {index + 1:02d}",
            "install_date": "2024-01-15",
            "observations": "Observation synthétique" if observations and index == 0 else "",
        } for index in range(count))
        return {
            "company": company,
            "client": {
                "party_type": "ORGANIZATION", "organization_name": "CLIENT SYNTHÉTIQUE",
                "representative_name": "Camille Test", "representative_role": "Responsable",
                "address_line1": "1 rue du Test", "postal_code": "75001", "city": "Paris",
                "country": "France", "phone": "0102030405", "email": "client@example.invalid",
                "postal_address": "1 rue du Test, 75001 Paris", "signatory_name": "Camille Test",
                "signatory_role": "Responsable", "regime": regime,
            },
            "site": {"label": "Site synthétique", "address_line1": "2 rue du Test", "postal_code": "75001", "city": "Paris", "country": "France"},
            "contract": {
                "number": "SYNTH-VALIDATION-NON-OFFICIELLE", "issue_date": "2026-08-17",
                "start_date": "2026-09-01", "initial_end_date": "2027-08-31",
                "signature_city": "Paris", "visits_per_year": 1, "initial_duration_months": 12,
                "renewal_mode": "NONE", "renewal_period_months": 12,
                "non_renewal_notice_days": 30, "non_renewal_notice_channels": ("EMAIL",),
                "breach_cure_period_days": 15, "early_performance_requested": False,
                "special_terms": "Conditions synthétiques de validation.",
                "equipment_items": equipment,
            },
            "service": {
                "included_area": "Entretien synthétique", "business_hours": "8h–18h",
                "travel_included": True, "priority_breakdown": False,
                "priority_breakdown_delay": "", "included_options": ("DEEP_CLEANING",),
                "additional_exclusions": "Aucune", "refrigerant_handling_mode": "EXCLUDED",
            },
            "pricing": {
                "annual_ht": "100.00", "vat_rate": "20", "payment_due_days": 30,
                "payment_terms_custom_text": "", "payment_methods": ("BANK_TRANSFER",),
                "missed_appointment_fee": "25.00", "renewal_price_rule": "FIXED",
            },
            "intervention": {
                "date": "2026-08-17", "technician": "Technicien synthétique",
                "other": "Validation technique", "notes": "Note synthétique\nDeuxième ligne",
                "issues": "Aucune anomalie", "quote_recommended": False,
            },
            "document": {"document_kind": version.document_kind},
            "template": {"selected_blocks": ()},
        }
