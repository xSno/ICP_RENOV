from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree as ET

from spike.adapt_templates import adapt
from spike.converters import FailingConverter, LibreOfficeConverter, PdfConverter, UnavailableConverter, WordComConverter, discover_word_executable
from spike.formatters import french_date, french_money, french_percent, months_label, prepare_context, standard_initial_end_date
from spike.ooxml import W, get_sdt_tag, q, read_package, story_parts, text_of, write_package, xml_bytes
from spike.pipeline import OFFICIAL_GENERATION_SIMULATION, TEMPLATE_VALIDATION_TEST, generate, load_fixture
from spike.render import DEFAULT_CONVERTER, create_converter
from spike.render_engine import block_active, render_docx
from spike.validation import FixtureValidationError, TemplateValidationError, preflight_template, validate_docx, validate_fixture


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "spike" / "templates"
FIXTURES = ROOT / "spike" / "fixtures"
TEST_TMP = ROOT / "spike" / "tmp" / "tests"
TEST_TMP.mkdir(parents=True, exist_ok=True)


@contextmanager
def scratch():
    path = TEST_TMP / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def template(name: str) -> Path:
    return TEMPLATES / name


def fixture(name: str) -> Path:
    return FIXTURES / f"{name}.json"


def write_fixture(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def available_consumer_fixture(path: Path) -> Path:
    data = load_fixture(fixture("01_consumer_none_1"))
    data["template"].update({
        "version_status": "AVAILABLE",
        "contract_type_code": "CLIMATE_MAINTENANCE",
        "allowed_client_regimes": ["CONSUMER"],
    })
    return write_fixture(path, data)


class MinimalValidPdfConverter(PdfConverter):
    """Test double that reaches the destination publication step."""

    name = "minimal-valid-pdf"

    def available(self) -> bool:
        return True

    def convert(self, docx: Path, pdf: Path) -> None:
        pdf.write_bytes(b"%PDF-1.4\n" + (b"0" * 1500) + b"\n%%EOF\n")


def mutate_first_tag(source: Path, target: Path, old_prefix: str, new_tag: str) -> None:
    parts = read_package(source)
    for part_name in story_parts(parts):
        root = ET.fromstring(parts[part_name])
        for sdt in root.iter(q(W, "sdt")):
            if get_sdt_tag(sdt).startswith(old_prefix):
                tag = sdt.find(f"./{q(W, 'sdtPr')}/{q(W, 'tag')}")
                tag.set(q(W, "val"), new_tag)
                parts[part_name] = xml_bytes(root)
                write_package(parts, target)
                return
    raise AssertionError(f"No tag with prefix {old_prefix}")


class FormatterTests(unittest.TestCase):
    def test_french_formats(self):
        self.assertEqual(french_date("2026-08-12"), "12 août 2026")
        self.assertEqual(french_money("1200.00"), "1\u202f200,00")
        self.assertEqual(french_percent("0.20"), "20")
        self.assertEqual(months_label(12), "12 mois")

    def test_standard_end_date_anniversary_minus_one_day(self):
        self.assertEqual(standard_initial_end_date("2026-09-01", 12), "2027-08-31")
        self.assertEqual(standard_initial_end_date("2026-01-31", 1), "2026-02-27")
        self.assertEqual(standard_initial_end_date("2023-03-01", 12), "2024-02-29")

    def test_custom_end_date_is_not_recalculated(self):
        raw = load_fixture(fixture("01_consumer_none_1"))
        raw["contract"].update({"initial_duration_mode": "CUSTOM", "initial_end_date": "2028-02-29"})
        self.assertEqual(prepare_context(raw)["contract"]["initial_end_date"], "29 février 2028")

    def test_pricing_is_calculated_and_contradictory_inputs_are_ignored(self):
        raw = load_fixture(fixture("01_consumer_none_1"))
        raw["pricing"].update({"vat_amount": "999.99", "annual_ttc": "1.00"})
        pricing = prepare_context(raw)["pricing"]
        self.assertEqual(pricing["vat_amount"], "200,00")
        self.assertEqual(pricing["annual_ttc"], "1\u202f200,00")

    def test_controlled_fixture_values_are_enforced(self):
        raw = load_fixture(fixture("01_consumer_none_1"))
        raw["contract"]["conclusion_mode"] = "IN_PERSON"
        with self.assertRaisesRegex(FixtureValidationError, "contract.conclusion_mode"):
            validate_fixture(raw, "CONTRACT")
        raw = load_fixture(fixture("02_consumer_tacit_10_logo"))
        raw["pricing"]["renewal_price_rule"] = "SAME_PRICE"
        with self.assertRaisesRegex(FixtureValidationError, "renewal_price_rule"):
            validate_fixture(raw, "CONTRACT")
        raw = load_fixture(fixture("01_consumer_none_1"))
        raw["service"]["included_options"] = ["Nettoyage renforcé"]
        with self.assertRaisesRegex(FixtureValidationError, "included_options"):
            validate_fixture(raw, "CONTRACT")

    def test_professional_contract_accepts_absent_optional_conclusion_mode(self):
        raw = load_fixture(fixture("03_professional_manual_30"))
        raw["contract"].pop("conclusion_mode")
        validate_fixture(raw, "CONTRACT")

    def test_legal_context_requires_recorded_conclusion_mode(self):
        raw = load_fixture(fixture("02_consumer_tacit_10_logo"))
        raw["contract"].pop("conclusion_mode")
        with self.assertRaisesRegex(FixtureValidationError, "contract.conclusion_mode"):
            validate_fixture(raw, "CONTRACT")

        no_matrix = load_fixture(fixture("03_professional_manual_30"))
        no_matrix["contract"].pop("conclusion_mode")
        no_matrix["contract"]["early_performance_requested"] = True
        with self.assertRaisesRegex(FixtureValidationError, "contract.conclusion_mode"):
            validate_fixture(no_matrix, "CONTRACT")


class FixtureValidationScopeTests(unittest.TestCase):
    def test_intervention_sheet_does_not_require_client_regime(self):
        raw = load_fixture(fixture("06_intervention_present"))
        raw["client"].pop("regime")
        validate_fixture(raw, "INTERVENTION_SHEET")

    def test_intervention_sheet_ignores_unused_contract_conditions(self):
        raw = load_fixture(fixture("06_intervention_present"))
        for key in (
            "type_code", "issue_date", "start_date", "initial_duration_mode",
            "initial_duration_months", "visits_per_year", "renewal_mode",
        ):
            raw["contract"].pop(key, None)
        raw.pop("service")
        raw.pop("pricing")
        raw["template"].pop("context_authorizations")
        validate_fixture(raw, "INTERVENTION_SHEET")

    def test_intervention_sheet_requires_intervention_date(self):
        raw = load_fixture(fixture("06_intervention_present"))
        raw["intervention"].pop("date")
        with self.assertRaisesRegex(FixtureValidationError, "intervention.date"):
            validate_fixture(raw, "INTERVENTION_SHEET")

    def test_intervention_sheet_requires_equipment(self):
        raw = load_fixture(fixture("06_intervention_present"))
        raw["contract"]["equipment_items"] = []
        with self.assertRaisesRegex(FixtureValidationError, "contract.equipment_items"):
            validate_fixture(raw, "INTERVENTION_SHEET")

    def test_contract_requiredness_is_unchanged(self):
        raw = load_fixture(fixture("01_consumer_none_1"))
        raw["client"].pop("regime")
        with self.assertRaisesRegex(FixtureValidationError, "client.regime"):
            validate_fixture(raw, "CONTRACT")


class TemplateTests(unittest.TestCase):
    def test_all_adapted_templates_preflight(self):
        for path in TEMPLATES.glob("*.docx"):
            kind = "INTERVENTION_SHEET" if "FICHE_INTERVENTION" in path.name else "CONTRACT"
            info = preflight_template(path, kind)
            self.assertIn("contract.equipment_items", info["loops"])

    def test_unknown_placeholder_rejected(self):
        with scratch() as tmp:
            bad = Path(tmp) / "bad.docx"
            mutate_first_tag(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), bad, "icp:field:", "icp:field:client.unknown")
            with self.assertRaisesRegex(TemplateValidationError, "Unknown placeholder"):
                preflight_template(bad, "CONTRACT")

    def test_obsolete_placeholder_rejected(self):
        with scratch() as tmp:
            bad = Path(tmp) / "bad.docx"
            mutate_first_tag(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), bad, "icp:field:", "icp:field:company.siren_siret")
            with self.assertRaisesRegex(TemplateValidationError, "Obsolete placeholder"):
                preflight_template(bad, "CONTRACT")

    def test_unknown_block_rejected(self):
        with scratch() as tmp:
            bad = Path(tmp) / "bad.docx"
            mutate_first_tag(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), bad, "icp:block:", "icp:block:BLOCK_ARBITRARY")
            with self.assertRaisesRegex(TemplateValidationError, "Unknown conditional block"):
                preflight_template(bad, "CONTRACT")

    def test_malformed_loop_rejected(self):
        with scratch() as tmp:
            bad = Path(tmp) / "bad.docx"
            mutate_first_tag(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), bad, "icp:loop:", "icp:loop:contract.other_items")
            with self.assertRaisesRegex(TemplateValidationError, "Unknown loop"):
                preflight_template(bad, "CONTRACT")

    def test_incompatible_document_kind_rejected(self):
        with self.assertRaisesRegex(TemplateValidationError, "revision in INTERVENTION_SHEET"):
            preflight_template(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), "INTERVENTION_SHEET")

    def test_adaptation_uses_libreoffice_safe_page_layout(self):
        source = ROOT / "templates" / "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_PROFESSIONNEL_V1_0.docx"
        with scratch() as tmp:
            target = Path(tmp) / source.name
            adapt(source, target)
            parts = read_package(target)

            settings = ET.fromstring(parts["word/settings.xml"])
            self.assertIsNone(settings.find(f".//{q(W, 'useFELayout')}"))
            self.assertFalse(any(
                setting.get(q(W, "name")) == "doNotFlipMirrorIndents"
                for setting in settings.iter(q(W, "compatSetting"))
            ))

            document = ET.fromstring(parts["word/document.xml"])
            body = document.find(q(W, "body"))
            children = list(body)
            signature_index = next(
                index for index, paragraph in enumerate(children)
                if paragraph.tag == q(W, "p") and text_of(paragraph).strip().startswith("Article 15 -")
            )
            self.assertTrue(any(
                br.get(q(W, "type")) == "page"
                for br in children[signature_index].iter(q(W, "br"))
            ))
            self.assertFalse(any(
                br.get(q(W, "type")) == "page"
                for br in children[signature_index - 1].iter(q(W, "br"))
            ))
            annex_index = next(
                index for index, paragraph in enumerate(children)
                if paragraph.tag == q(W, "p") and text_of(paragraph).strip().startswith("Annexe 1 -")
            )
            self.assertTrue(any(
                br.get(q(W, "type")) == "page"
                for br in children[annex_index].iter(q(W, "br"))
            ))
            self.assertFalse(any(
                br.get(q(W, "type")) == "page"
                for br in children[annex_index - 1].iter(q(W, "br"))
            ))


class RenderingTests(unittest.TestCase):
    def _render_details(self, fixture_name: str, template_name: str) -> tuple[str, bool]:
        raw = load_fixture(fixture(fixture_name)); ctx = prepare_context(raw)
        with scratch() as tmp:
            out = Path(tmp) / "out.docx"
            render_docx(template(template_name), out, ctx)
            validate_docx(out, ctx["contract"]["equipment_items"], bool(ctx["company"].get("logo")))
            parts = read_package(out)
            text = "\n".join(text_of(ET.fromstring(parts[name])) for name in story_parts(parts))
            has_logo = any(name.startswith("word/media/icp_logo_") for name in parts)
            return text, has_logo

    def _render(self, fixture_name: str, template_name: str) -> str:
        return self._render_details(fixture_name, template_name)[0]

    def test_1_10_30_equipment_and_order(self):
        cases = [
            ("01_consumer_none_1", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx", 1),
            ("02_consumer_tacit_10_logo", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx", 10),
            ("03_professional_manual_30", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_PROFESSIONNEL_V1_0.docx", 30),
        ]
        for fixture_name, template_name, count in cases:
            text = self._render(fixture_name, template_name)
            positions = [text.index(f"MODELE-{i:02d}") for i in range(1, count + 1)]
            self.assertEqual(positions, sorted(positions))

    def test_renewal_and_refrigerant_blocks_are_mutually_exclusive(self):
        manual = self._render("03_professional_manual_30", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_PROFESSIONNEL_V1_0.docx")
        self.assertIn("n'est pas reconduit automatiquement", manual)
        self.assertNotIn("reconduit tacitement", manual)
        self.assertIn("Référence de capacité", manual)
        self.assertNotIn("partenaire identifié", manual)
        partner = self._render("04_professional_tacit_partner", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_PROFESSIONNEL_V1_0.docx")
        self.assertIn("reconduit tacitement", partner)
        self.assertIn("partenaire identifié", partner)
        self.assertNotIn("Référence de capacité", partner)

    def test_early_performance_dependency(self):
        raw = prepare_context(load_fixture(fixture("02_consumer_tacit_10_logo")))
        self.assertTrue(block_active("BLOCK_EARLY_PERFORMANCE", "", raw, None))
        raw["template"]["context_authorizations"][0]["authorized_blocks"].remove("BLOCK_WITHDRAWAL")
        self.assertFalse(block_active("BLOCK_EARLY_PERFORMANCE", "", raw, None))

    def test_distance_email_context_and_missing_mode_fail_closed(self):
        raw = prepare_context(load_fixture(fixture("02_consumer_tacit_10_logo")))
        self.assertEqual(raw["contract"]["conclusion_mode"], "DISTANCE_EMAIL")
        self.assertTrue(block_active("BLOCK_WITHDRAWAL", "", raw, None))
        self.assertTrue(block_active("BLOCK_EARLY_PERFORMANCE", "", raw, None))

        raw["contract"].pop("conclusion_mode")
        self.assertFalse(block_active("BLOCK_WITHDRAWAL", "", raw, None))
        self.assertFalse(block_active("BLOCK_EARLY_PERFORMANCE", "", raw, None))

    def test_same_matrix_authorizes_only_exact_recorded_context(self):
        raw = prepare_context(load_fixture(fixture("02_consumer_tacit_10_logo")))
        self.assertTrue(block_active("BLOCK_WITHDRAWAL", "", raw, None))
        raw["contract"]["conclusion_mode"] = "IN_PREMISES"
        self.assertFalse(block_active("BLOCK_WITHDRAWAL", "", raw, None))
        self.assertFalse(block_active("BLOCK_EARLY_PERFORMANCE", "", raw, None))

    def test_optional_intervention_content_removal(self):
        text = self._render("07_intervention_optional_absent", "ICP_RENOV_TEMPLATE_FICHE_INTERVENTION_V1_0.docx")
        self.assertNotIn("Technicien\n", text)
        self.assertNotIn("☐ Autre", text)
        self.assertNotIn("Observations :", text)
        self.assertNotIn("Anomalies constatées :", text)

    def test_optional_content_presence_and_absence(self):
        hab, hab_logo = self._render_details("02_consumer_tacit_10_logo", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx")
        self.assertIn("Prestations ou options supplémentaires incluses : nettoyage approfondi, désinfection.", hab)
        self.assertIn("Exclusions complémentaires : Accès en toiture hors périmètre.", hab)
        self.assertIn("Article 14 - Conditions particulières", hab)
        self.assertIn("Observations : Accès par trappe technique.", hab)
        self.assertTrue(hab_logo)

        pro = self._render("03_professional_manual_30", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_PROFESSIONNEL_V1_0.docx")
        self.assertIn("Adresse de facturation :", pro)
        self.assertIn("frais de rendez-vous non réalisable", pro)

        absent, absent_logo = self._render_details("04_professional_tacit_partner", "ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_PROFESSIONNEL_V1_0.docx")
        for orphan in (
            "Adresse de facturation :", "Prestations ou options supplémentaires incluses :",
            "Exclusions complémentaires :", "frais de rendez-vous non réalisable",
            "Article 14 - Conditions particulières", "Observations :",
        ):
            self.assertNotIn(orphan, absent)
        self.assertFalse(absent_logo)

        present, present_logo = self._render_details("06_intervention_present", "ICP_RENOV_TEMPLATE_FICHE_INTERVENTION_V1_0.docx")
        for expected in ("Technicien Démo", "Mesure complémentaire fictive", "Observations :", "Anomalies constatées :"):
            self.assertIn(expected, present)
        self.assertTrue(present_logo)

        intervention_absent, intervention_absent_logo = self._render_details("07_intervention_optional_absent", "ICP_RENOV_TEMPLATE_FICHE_INTERVENTION_V1_0.docx")
        for orphan in ("Technicien\n", "☐ Autre", "Observations :", "Anomalies constatées :"):
            self.assertNotIn(orphan, intervention_absent)
        self.assertFalse(intervention_absent_logo)

    def test_logo_package_uses_default_opc_namespaces(self):
        raw = load_fixture(fixture("02_consumer_tacit_10_logo")); ctx = prepare_context(raw)
        with scratch() as tmp:
            out = Path(tmp) / "logo.docx"
            render_docx(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), out, ctx)
            parts = read_package(out)
            self.assertIn(b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"', parts["[Content_Types].xml"])
            self.assertIn(b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"', parts["word/_rels/document.xml.rels"])
            self.assertNotIn(b"ns0:", parts["[Content_Types].xml"])
            self.assertNotIn(b"ns0:", parts["word/_rels/document.xml.rels"])


class AtomicPipelineTests(unittest.TestCase):
    def test_to_validate_accepted_as_validation_test_without_official_effects(self):
        with scratch() as tmp:
            result = generate(
                template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"),
                fixture("01_consumer_none_1"), Path(tmp) / "out", MinimalValidPdfConverter(),
                TEMPLATE_VALIDATION_TEST,
            )
            self.assertEqual(result.status, "SUCCESS", result.error)
            self.assertEqual(result.artifact_classification, "TEST_ARTIFACT")
            self.assertTrue(Path(result.docx_path).name.startswith("TEST_"))
            self.assertFalse(result.official_artifact_reported)
            self.assertFalse(result.consumes_contract_number)
            self.assertFalse(result.creates_contract_revision)
            self.assertFalse(result.changes_contract_status)

    def test_to_validate_rejected_in_official_mode(self):
        with scratch() as tmp:
            result = generate(
                template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"),
                fixture("01_consumer_none_1"), Path(tmp) / "out", MinimalValidPdfConverter(),
                OFFICIAL_GENERATION_SIMULATION,
            )
            self.assertEqual(result.status, "FAILURE")
            self.assertIn("version_status = AVAILABLE", result.error)
            self.assertFalse(result.official_artifact_reported)

    def test_official_mode_rejects_incompatible_regime_and_contract_type(self):
        with scratch() as tmp:
            official = available_consumer_fixture(Path(tmp) / "official.json")
            data = load_fixture(official)
            data["client"]["regime"] = "PROFESSIONAL"
            mismatch_regime = write_fixture(Path(tmp) / "regime.json", data)
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), mismatch_regime, Path(tmp) / "out1", MinimalValidPdfConverter(), OFFICIAL_GENERATION_SIMULATION)
            self.assertIn("client regime mismatch", result.error)

            data = load_fixture(official)
            data["template"]["contract_type_code"] = "TEST_OTHER_TYPE"
            mismatch_type = write_fixture(Path(tmp) / "type.json", data)
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), mismatch_type, Path(tmp) / "out2", MinimalValidPdfConverter(), OFFICIAL_GENERATION_SIMULATION)
            self.assertIn("contract type mismatch", result.error)

    def test_complete_available_official_simulation_reports_effects(self):
        with scratch() as tmp:
            official = available_consumer_fixture(Path(tmp) / "official.json")
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), official, Path(tmp) / "out", MinimalValidPdfConverter(), OFFICIAL_GENERATION_SIMULATION)
            self.assertEqual(result.status, "SUCCESS", result.error)
            self.assertTrue(result.official_artifact_reported)
            self.assertTrue(result.consumes_contract_number)
            self.assertTrue(result.creates_contract_revision)
            self.assertTrue(result.changes_contract_status)

    def test_pdf_failure_never_reports_official_success(self):
        with scratch() as tmp:
            official = available_consumer_fixture(Path(tmp) / "official.json")
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), official, Path(tmp) / "out", FailingConverter(), OFFICIAL_GENERATION_SIMULATION)
            self.assertEqual(result.status, "FAILURE")
            self.assertFalse(result.official_artifact_reported)
            self.assertFalse(result.consumes_contract_number)
            self.assertFalse((Path(tmp) / "out").exists())

    def test_converter_unavailable_has_no_artifact(self):
        with scratch() as tmp:
            out = Path(tmp) / "out"
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), fixture("01_consumer_none_1"), out, UnavailableConverter())
            self.assertEqual(result.status, "FAILURE")
            self.assertFalse(result.official_artifact_reported)
            self.assertFalse(out.exists())

    def test_docx_success_pdf_failure_is_atomic(self):
        with scratch() as tmp:
            out = Path(tmp) / "out"
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), fixture("01_consumer_none_1"), out, FailingConverter())
            self.assertEqual(result.status, "FAILURE")
            self.assertFalse(result.official_artifact_reported)
            self.assertEqual(list(out.glob("*")) if out.exists() else [], [])
            self.assertEqual(list(Path(tmp).glob(".icp_spike_*")), [])

    def test_missing_required_value(self):
        with scratch() as tmp:
            bad_fixture = Path(tmp) / "bad.json"
            data = load_fixture(fixture("01_consumer_none_1")); data["client"]["postal_address"] = ""
            bad_fixture.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), bad_fixture, Path(tmp) / "out", FailingConverter())
            self.assertIn("Missing REQUIRED value", result.error)

    def test_unwritable_equivalent_destination(self):
        with scratch() as tmp:
            blocked = Path(tmp) / "not_a_directory"; blocked.write_text("occupied", encoding="utf-8")
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), fixture("01_consumer_none_1"), blocked, MinimalValidPdfConverter())
            self.assertEqual(result.status, "FAILURE")
            self.assertIn("Destination unwritable", result.error)
            self.assertFalse(result.official_artifact_reported)
            self.assertEqual(blocked.read_text(encoding="utf-8"), "occupied")


class ConverterDiscoveryTests(unittest.TestCase):
    def test_cli_default_converter_is_libreoffice(self):
        self.assertEqual(DEFAULT_CONVERTER, "libreoffice")
        self.assertIsInstance(create_converter(), LibreOfficeConverter)

    def test_word_is_discovered_from_normal_windows_installation(self):
        executable = discover_word_executable()
        if executable is None:
            self.skipTest("Microsoft Word is not installed")
        self.assertTrue(executable.is_file())
        self.assertEqual(executable.name.upper(), "WINWORD.EXE")


@unittest.skipUnless(
    WordComConverter().available() and os.environ.get("ICP_SKIP_WORD_INTEGRATION") != "1",
    "Microsoft Word integration disabled or unavailable",
)
class WordConversionIntegrationTests(unittest.TestCase):
    def test_real_word_conversion(self):
        with scratch() as tmp:
            result = generate(template("ICP_RENOV_TEMPLATE_CONTRAT_ENTRETIEN_HABITATION_V1_0.docx"), fixture("01_consumer_none_1"), Path(tmp) / "out", WordComConverter())
            self.assertEqual(result.status, "SUCCESS", result.error)
            self.assertTrue(Path(result.pdf_path).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
