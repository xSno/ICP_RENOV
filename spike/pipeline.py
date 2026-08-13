from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .converters import PdfConverter
from .errors import ConversionError, OutputError, SpikeError
from .formatters import prepare_context
from .render_engine import render_docx
from .validation import preflight_template, validate_docx, validate_fixture, validate_official_compatibility, validate_pdf


TEMPLATE_VALIDATION_TEST = "TEMPLATE_VALIDATION_TEST"
OFFICIAL_GENERATION_SIMULATION = "OFFICIAL_GENERATION_SIMULATION"


@dataclass
class GenerationResult:
    status: str
    document_kind: str
    converter: str
    generation_mode: str
    artifact_classification: str
    docx_path: str | None = None
    pdf_path: str | None = None
    error: str | None = None
    official_artifact_reported: bool = False
    consumes_contract_number: bool = False
    creates_contract_revision: bool = False
    changes_contract_status: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def load_fixture(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    logo = data.get("company", {}).get("logo")
    if logo:
        logo_path = Path(logo)
        if not logo_path.is_absolute():
            data["company"]["logo"] = str((path.parent / logo_path).resolve())
    return data


def generate(
    template: Path,
    fixture: Path,
    output_dir: Path,
    converter: PdfConverter,
    mode: str = TEMPLATE_VALIDATION_TEST,
) -> GenerationResult:
    raw = load_fixture(fixture)
    kind = raw.get("template", {}).get("document_kind", "")
    classification = "TEST_ARTIFACT" if mode == TEMPLATE_VALIDATION_TEST else "SIMULATED_OFFICIAL_ARTIFACT"
    result = GenerationResult(
        status="FAILURE", document_kind=kind, converter=converter.name,
        generation_mode=mode, artifact_classification=classification,
    )
    stem = f"TEST_{fixture.stem}" if mode == TEMPLATE_VALIDATION_TEST else fixture.stem
    final_docx = output_dir / f"{stem}.docx"
    final_pdf = output_dir / f"{stem}.pdf"
    def cleanup(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        if mode not in {TEMPLATE_VALIDATION_TEST, OFFICIAL_GENERATION_SIMULATION}:
            raise SpikeError(f"Unknown generation mode: {mode}")
        if kind not in {"CONTRACT", "INTERVENTION_SHEET"}:
            raise SpikeError(f"Incompatible document kind: {kind}")
        preflight_template(template, kind)
        validate_fixture(raw, kind)
        if mode == OFFICIAL_GENERATION_SIMULATION:
            validate_official_compatibility(raw, kind)
        if not converter.available():
            raise ConversionError(f"PDF converter unavailable: {converter.name}")
        prepared = prepare_context(raw)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = output_dir.parent / f".icp_spike_{uuid.uuid4().hex}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        try:
            tmp_docx = tmp_dir / "rendered.docx"
            tmp_pdf = tmp_dir / "rendered.pdf"
            render_docx(template, tmp_docx, prepared)
            validate_docx(tmp_docx, prepared["contract"]["equipment_items"], bool(prepared["company"].get("logo")))
            converter.convert(tmp_docx, tmp_pdf)
            validate_pdf(tmp_pdf)
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(tmp_docx, final_docx)
                shutil.copy2(tmp_pdf, final_pdf)
            except OSError as exc:
                cleanup(final_docx)
                cleanup(final_pdf)
                raise OutputError(f"Destination unwritable: {output_dir}") from exc
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        result.status = "SUCCESS"
        result.docx_path = str(final_docx.resolve())
        result.pdf_path = str(final_pdf.resolve())
        if mode == OFFICIAL_GENERATION_SIMULATION:
            result.official_artifact_reported = True
        if mode == OFFICIAL_GENERATION_SIMULATION and kind == "CONTRACT":
            result.consumes_contract_number = True
            result.creates_contract_revision = True
            result.changes_contract_status = True
        return result
    except Exception as exc:
        # Atomic guarantee: no official pair survives any failure.
        cleanup(final_docx)
        cleanup(final_pdf)
        result.error = f"{type(exc).__name__}: {exc}"
        return result
