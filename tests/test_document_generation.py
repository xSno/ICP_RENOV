from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import zipfile
import re
from xml.etree import ElementTree as ET

from icp_renov_contracts.documents import ProductionDocxRenderer, StaticCompanyDocumentDataProvider, TemplateSourceStore
from icp_renov_contracts.documents.formatters import prepare_context
from icp_renov_contracts.documents.providers import ContractNumberAllocator
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.documents.source_store import sha256_file
from icp_renov_contracts.domain import ContractStatus, ContextAuthorization, TemplateValidationMetadata
from icp_renov_contracts.repositories import ContractDocumentRepository
from icp_renov_contracts.services import DocumentGenerationService, ReviewService

from test_review import FakeCapabilities, ReviewCase
from test_master_data import equipment, organization, site
from PySide6.QtWidgets import QApplication,QDialog
from icp_renov_contracts.ui.contracts_view import ContractsView
from icp_renov_contracts.ui.review_view import GenerationConfirmationDialog


class SyntheticAllocator(ContractNumberAllocator):
    def __init__(self, database, fail: bool = False): self.database=database;self.fail=fail
    def available(self): return True
    def preview_next(self):
        with self.database.connection() as connection:return f"SYNTH-S5-{connection.execute('SELECT value FROM synthetic_number').fetchone()[0]:04d}"
    def allocate(self, connection):
        if self.fail: raise sqlite3.OperationalError("synthetic allocation failure")
        value=connection.execute("SELECT value FROM synthetic_number").fetchone()[0]
        connection.execute("UPDATE synthetic_number SET value=value+1")
        return f"SYNTH-S5-{value:04d}"
    def consumed(self):
        with self.database.connection() as connection:return connection.execute("SELECT value FROM synthetic_number").fetchone()[0]-1


class FakeConverter:
    def __init__(self, mode="ok"):self.mode=mode
    def available(self):return self.mode!="unavailable"
    def convert(self,docx,pdf):
        if self.mode in {"timeout","failure","wrong_version"}:raise DocumentGenerationError(f"converter_{self.mode}","Le PDF n’a pas pu être créé.")
        if self.mode=="invalid":pdf.write_bytes(b"not-pdf");return
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")


class FailingRenderer:
    def __init__(self,mode="failure"):self.mode=mode
    def render(self,source,output,context,workdir):
        if self.mode=="failure":raise DocumentGenerationError("render_failure","Le document DOCX n’a pas pu être généré.")
        output.write_bytes(b"invalid docx")


COMPANY={
    "legal_name":"SYNTHÉTIQUE — ICP RENOV","trade_name":"ICP Renov","address_line1":"1 rue du Test","postal_code":"75001","city":"Paris","country":"France",
    "registered_address":"1 rue du Test, 75001 Paris, France",
    "siren":"123456789","siret":"12345678900010","phone":"0102030405","email":"test@example.invalid","logo":"",
    "insurer_name":"Assureur synthétique","insurance_policy_number":"SYNTH-001","insurance_scope":"France",
    "refrigerant_capacity_number":"SYNTH-CAP","refrigerant_capacity_body":"Organisme synthétique","mediator_name":"Médiateur synthétique",
    "mediator_address":"1 rue Médiation, Paris","mediator_website":"https://example.invalid",
    "complaints_contact":"reclamations@example.invalid","privacy_contact":"privacy@example.invalid",
    "refrigerant_partner_name":"Partenaire synthétique","withdrawal_contact":"retractation@example.invalid",
    "signatory_name":"Camille Test","signatory_role":"Gérante",
}
REQUIRED_COMPANY_FIELDS=("registered_address","phone","email","signatory_name","signatory_role","mediator_name",
                         "mediator_address","mediator_website","complaints_contact","privacy_contact","refrigerant_partner_name")


class GenerationCase(ReviewCase):
    def setUp(self):
        super().setUp();self.complete(renewal="TACIT")
        self.contracts.save_conditions(self.contract.id,replace(self.contracts.get_conditions(self.contract.id),breach_cure_period_days=15))
        self.version=self.contracts.selected_template_version(self.contract.id);self.store=TemplateSourceStore(self.context.workspace.root)
        source=next((Path(__file__).parents[1]/"templates").glob("*HABITATION*.docx"));rel,digest=self.store.import_source(source,self.version.id)
        self.catalog.set_generation_metadata(self.version.id,rel,digest,REQUIRED_COMPANY_FIELDS)
        self.version=self.catalog.get_version(self.version.id)
        with self.context.database.transaction() as connection:
            connection.execute("CREATE TABLE synthetic_number(value INTEGER NOT NULL)");connection.execute("INSERT INTO synthetic_number VALUES (1)")
        self.allocator=SyntheticAllocator(self.context.database);self.documents=ContractDocumentRepository(self.context.database)
        self.review=ReviewService(self.contracts,self.context.workspace_service,self.context.workspace,FakeCapabilities(True,True))

    def service(self,renderer=None,converter=None,allocator=None):
        service=DocumentGenerationService(self.context.database,self.contracts,self.documents,self.review,self.store,
            renderer or ProductionDocxRenderer(),converter or FakeConverter(),StaticCompanyDocumentDataProvider(COMPANY),
            allocator or self.allocator,self.context.workspace.root)
        self.review.generation_ready=service.available;return service

    def assert_atomic_failure(self,service):
        with self.assertRaises(DocumentGenerationError):service.generate(self.contract.id)
        contract=self.contracts.get(self.contract.id);self.assertIs(contract.status,ContractStatus.DRAFT);self.assertIsNone(contract.number)
        self.assertEqual(self.documents.list_for_contract(self.contract.id),());self.assertEqual(self.allocator.consumed(),0)
        folder=self.context.workspace.root/"documents"/"contracts"/self.contract.id/"R01"
        self.assertFalse(any(path.suffix==".pdf" for path in folder.glob("*") if folder.exists()))

    def authorize(self,blocks,early=False):
        metadata=TemplateValidationMetadata(("CONSUMER",),(ContextAuthorization("CONSUMER","OFF_PREMISES",tuple(blocks)),))
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contract_template_versions SET validation_metadata_json=? WHERE id=?",(metadata.to_json(),self.version.id))
        self.version=self.catalog.get_version(self.version.id)
        conditions=replace(self.contracts.get_conditions(self.contract.id),conclusion_mode="OFF_PREMISES",early_performance_requested=early)
        self.contracts.save_conditions(self.contract.id,conditions)

    @staticmethod
    def docx_text(path):
        with zipfile.ZipFile(path) as package:
            root=ET.fromstring(package.read("word/document.xml"))
        return "".join(node.text or "" for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))


class ProductionGenerationTests(GenerationCase):
    def test_success_is_atomic_r01_hashed_relative_and_locked(self):
        preview=self.allocator.preview_next();self.assertEqual(self.allocator.consumed(),0)
        result=self.service().generate(self.contract.id);contract=self.contracts.get(self.contract.id)
        self.assertEqual(preview,result.contract_number);self.assertEqual(self.allocator.consumed(),1);self.assertEqual(contract.number,preview)
        self.assertIs(contract.status,ContractStatus.TO_SIGN);self.assertEqual(result.document.revision,"R01");self.assertEqual(len(self.documents.list_for_contract(contract.id)),1)
        self.assertFalse(Path(result.document.docx_relpath).is_absolute());self.assertTrue(result.docx_path.is_file());self.assertTrue(result.pdf_path.is_file())
        self.assertEqual(hashlib.sha256(result.docx_path.read_bytes()).hexdigest(),result.document.docx_sha256)
        self.assertEqual(hashlib.sha256(result.pdf_path.read_bytes()).hexdigest(),result.document.pdf_sha256)
        with self.assertRaises(Exception):self.contracts.update_signatory(contract.id,"Changed","Changed")
        with self.assertRaises(DocumentGenerationError):self.service().generate(contract.id)
        self.assertEqual(self.allocator.consumed(),1)

    def test_snapshot_uses_contract_values_and_never_internal_notes(self):
        item=self.contracts.get(self.contract.id).equipment_items[0];self.contracts.update_observation(self.contract.id,item.id,"Observation contractuelle")
        client_id=self.contracts.get(self.contract.id).client_source_id;site_id=self.contracts.get(self.contract.id).site_source_id;equipment_id=item.source_equipment_id
        self.master.update_client(client_id,organization("MAÎTRE MODIFIÉ"));self.master.update_site(site_id,site("SITE MODIFIÉ"));self.master.update_equipment(equipment_id,equipment("ÉQUIPEMENT MODIFIÉ","Toit",internal_notes="SECRET INTERNE"))
        snapshot=self.service().generate(self.contract.id).document.snapshot;serialized=str(snapshot)
        self.assertIn("Client Snapshot",serialized);self.assertIn("Site Snapshot",serialized);self.assertIn("Unité Snapshot",serialized)
        self.assertIn("Observation contractuelle",serialized);self.assertNotIn("SECRET INTERNE",serialized);self.assertNotIn("MAÎTRE MODIFIÉ",serialized)
        self.assertEqual(snapshot["company"]["legal_name"],COMPANY["legal_name"]);self.assertEqual(snapshot["template"]["source_hash"],self.version.source_hash)
        self.assertEqual(snapshot["contract"]["visits_per_year"],2);self.assertEqual(snapshot["contract"]["breach_cure_period_days"],15)

    def test_renderer_has_no_tokens_raw_codes_or_internal_notes(self):
        result=self.service().generate(self.contract.id)
        with zipfile.ZipFile(result.docx_path) as package:
            data=b"".join(package.read(name) for name in package.namelist() if name.startswith("word/") and name.endswith(".xml"))
            document=ET.fromstring(package.read("word/document.xml"));text="".join(node.text or "" for node in document.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        self.assertNotIn(b"[[",data);self.assertNotIn(b"internal_notes",data)
        self.assertIn("réalise 2 visite(s)",text)

    def test_required_semantic_values_render_without_blank_holes(self):
        text=self.docx_text(self.service().generate(self.contract.id).docx_path)
        self.assertIn("Siège1 rue du Test, 75001 Paris, France",text)
        self.assertIn("restée sans effet pendant 15 jours",text)
        self.assertIn("partenaire identifié pour ce contrat : Partenaire synthétique",text)

    def test_signature_city_present_renders_city_and_handwritten_date(self):
        self.contracts.save_conditions(self.contract.id,replace(self.contracts.get_conditions(self.contract.id),signature_city="Paris"))
        result=self.service().generate(self.contract.id);text=self.docx_text(result.docx_path)
        self.assertIn("Fait à Paris, le ____________________.",text)
        self.assertEqual(result.document.snapshot["contract"]["signature_city"],"Paris")
        self.assertNotIn("signature_date",result.document.snapshot["contract"])

    def test_empty_signature_city_renders_handwritten_blanks_without_orphan_comma(self):
        result=self.service().generate(self.contract.id);text=self.docx_text(result.docx_path)
        self.assertIn("Fait à ____________________, le ____________________.",text)
        self.assertNotIn("Fait à ,",text)
        self.assertEqual(result.document.snapshot["contract"]["signature_city"],"")
        self.assertNotIn("signature_date",result.document.snapshot["contract"])

    def test_missing_visits_or_active_cure_period_cannot_publish(self):
        self.contracts.save_conditions(self.contract.id,replace(self.contracts.get_conditions(self.contract.id),visits_per_year=None))
        self.assert_atomic_failure(self.service())
        self.contracts.save_conditions(self.contract.id,replace(self.contracts.get_conditions(self.contract.id),visits_per_year=2,breach_cure_period_days=None))
        self.assert_atomic_failure(self.service())

    def test_required_company_semantics_cannot_render_blank(self):
        for key in ("registered_address","refrigerant_partner_name"):
            with self.subTest(key=key):
                company=dict(COMPANY);company.pop(key)
                if key=="registered_address":company.update(address_line1="",postal_code="",city="",country="")
                service=self.service();service.company_provider=StaticCompanyDocumentDataProvider(company)
                self.assert_atomic_failure(service)

    def test_registered_address_is_deterministically_rendered_from_structured_company_data(self):
        company=dict(COMPANY);company.pop("registered_address")
        service=self.service();service.company_provider=StaticCompanyDocumentDataProvider(company)
        self.assertIn("Siège1 rue du Test, 75001, Paris, France",self.docx_text(service.generate(self.contract.id).docx_path))

    def test_active_withdrawal_requires_contact(self):
        self.authorize(("BLOCK_WITHDRAWAL",))
        self.catalog.set_generation_metadata(self.version.id,self.version.source_relpath,self.version.source_hash,REQUIRED_COMPANY_FIELDS+("withdrawal_contact",))
        company=dict(COMPANY);company.pop("withdrawal_contact")
        service=self.service();service.company_provider=StaticCompanyDocumentDataProvider(company)
        self.assert_atomic_failure(service)

    def test_legal_blocks_require_exact_authorization_and_early_request(self):
        text=self.docx_text(self.service().generate(self.contract.id).docx_path)
        self.assertNotIn("Formulaire de rétractation",text);self.assertNotIn("commencer avant la fin du délai",text)
        self.assertNotIn("résiliation par voie électronique",text)

    def test_early_performance_false_is_absent_even_when_authorized(self):
        self.authorize(("BLOCK_WITHDRAWAL","BLOCK_EARLY_PERFORMANCE"),False)
        text=self.docx_text(self.service().generate(self.contract.id).docx_path)
        self.assertIn("Formulaire de rétractation",text);self.assertNotIn("commencer avant la fin du délai",text)

    def test_early_true_without_explicit_authorization_is_absent(self):
        conditions=replace(self.contracts.get_conditions(self.contract.id),conclusion_mode="OFF_PREMISES",early_performance_requested=True)
        context=DocumentGenerationService._snapshot(self.contracts.get(self.contract.id),conditions,self.version,COMPANY,"SYNTH-DIRECT")
        output=self.context.workspace.root/"direct.docx";work=self.context.workspace.root/"direct-work";work.mkdir()
        self.service().renderer.render(self.store.resolve(self.version.source_relpath),output,context,work)
        self.assertNotIn("commencer avant la fin du délai",self.docx_text(output))

    def test_early_true_with_withdrawal_authorization_is_present(self):
        self.authorize(("BLOCK_WITHDRAWAL","BLOCK_EARLY_PERFORMANCE"),True)
        text=self.docx_text(self.service().generate(self.contract.id).docx_path)
        self.assertIn("Formulaire de rétractation",text);self.assertIn("commencer avant la fin du délai",text)

    def test_electronic_termination_requires_explicit_authorization(self):
        self.authorize(("BLOCK_WITHDRAWAL",))
        self.assertNotIn("résiliation par voie électronique",self.docx_text(self.service().generate(self.contract.id).docx_path))

    def test_no_company_business_fallbacks_and_optional_line_is_removed(self):
        company=prepare_context({"company":{},"client":{},"site":{},"contract":{"equipment_items":[]},"service":{},"pricing":{}})["company"]
        for key in ("mediator_address","mediator_website","complaints_contact","privacy_contact","withdrawal_contact","registered_address","refrigerant_partner_name"):
            self.assertFalse(company.get(key))
        data=dict(COMPANY);data.update(insurer_name="",insurance_policy_number="",insurance_scope="")
        service=self.service();service.company_provider=StaticCompanyDocumentDataProvider(data)
        text=self.docx_text(service.generate(self.contract.id).docx_path)
        self.assertNotIn("Assurance responsabilité civile professionnelle :",text)


class AtomicFailureTests(GenerationCase):
    def test_renderer_failure(self):self.assert_atomic_failure(self.service(renderer=FailingRenderer()))
    def test_docx_postflight_failure(self):self.assert_atomic_failure(self.service(renderer=FailingRenderer("invalid")))
    def test_converter_unavailable(self):self.assert_atomic_failure(self.service(converter=FakeConverter("unavailable")))
    def test_wrong_libreoffice_version(self):self.assert_atomic_failure(self.service(converter=FakeConverter("wrong_version")))
    def test_converter_timeout(self):self.assert_atomic_failure(self.service(converter=FakeConverter("timeout")))
    def test_converter_failure(self):self.assert_atomic_failure(self.service(converter=FakeConverter("failure")))
    def test_invalid_pdf(self):self.assert_atomic_failure(self.service(converter=FakeConverter("invalid")))
    def test_allocator_or_database_publication_failure(self):self.assert_atomic_failure(self.service(allocator=SyntheticAllocator(self.context.database,True)))
    def test_final_collision(self):
        folder=self.context.workspace.root/"documents"/"contracts"/self.contract.id/"R01";folder.mkdir(parents=True);(folder/f"{self.allocator.preview_next()}_R01.docx").write_bytes(b"existing")
        self.assert_atomic_failure(self.service())
    def test_destination_write_failure(self):
        original=Path.write_bytes
        def fail_probe(path,data):
            if path.name==".write-test":raise OSError("write denied")
            return original(path,data)
        with patch.object(Path,"write_bytes",fail_probe):self.assert_atomic_failure(self.service())
    def test_final_move_failure_removes_first_moved_file(self):
        original=Path.replace
        def fail_pdf(path,target):
            if path.name=="contract.pdf":raise OSError("move denied")
            return original(path,target)
        with patch.object(Path,"replace",fail_pdf):self.assert_atomic_failure(self.service())
    def test_database_insert_failure_rolls_back_files_number_and_status(self):
        with patch.object(ContractDocumentRepository,"insert",side_effect=sqlite3.OperationalError("db failure")):
            self.assert_atomic_failure(self.service())


class TemplateSourceTests(GenerationCase):
    def test_source_copy_hash_missing_mutated_and_original_untouched(self):
        source=self.store.verify(self.version.source_relpath,self.version.source_hash);before=source.read_bytes()
        source.write_bytes(before+b"mutation");self.assert_atomic_failure(self.service());source.write_bytes(before)
        source.unlink();self.assert_atomic_failure(self.service())

    def test_version_generation_metadata_is_immutable_after_r01(self):
        self.service().generate(self.contract.id)
        with self.assertRaises(Exception):self.catalog.set_generation_metadata(self.version.id,"other.docx","0"*64,())


class ProductionRendererCoverageTests(GenerationCase):
    def _rewrite_source(self,pattern:bytes,replacement:bytes):
        source=self.store.resolve(self.version.source_relpath);temporary=source.with_suffix(".rewrite.docx");changed=False
        with zipfile.ZipFile(source) as original,zipfile.ZipFile(temporary,"w",zipfile.ZIP_DEFLATED) as output:
            for name in original.namelist():
                data=original.read(name)
                if name.startswith("word/") and name.endswith(".xml") and not changed and pattern in data:
                    data=data.replace(pattern,replacement,1);changed=True
                output.writestr(name,data)
        self.assertTrue(changed);temporary.replace(source)
        self.catalog.set_generation_metadata(self.version.id,self.version.source_relpath,sha256_file(source),())
        self.version=self.catalog.get_version(self.version.id)

    def _assert_marker_rejected(self,pattern,replacement):self._rewrite_source(pattern,replacement);self.assert_atomic_failure(self.service())
    def test_unknown_placeholder_rejected(self):self._assert_marker_rejected(b"{{ company.display_name }}",b"{{ unknown.field }}")
    def test_unknown_block_rejected(self):self._assert_marker_rejected(b"BLOCK_CLIENT_ORGANIZATION",b"BLOCK_UNKNOWN")
    def test_unknown_loop_rejected(self):self._assert_marker_rejected(b"contract.equipment_items",b"contract.unknown_items")
    def test_malformed_loop_rejected(self):self._assert_marker_rejected(b"[[/LOOP:contract.equipment_items]]",b"")

    def _assert_equipment_count(self,total):
        site_id=self.contracts.get(self.contract.id).site_source_id
        for index in range(1,total):
            created=self.master.create_equipment(site_id,equipment(f"Unité {index:02d}",f"Zone {index:02d}"));self.contracts.select_equipment(self.contract.id,created.id)
        selected=self.contracts.get(self.contract.id).equipment_items;self.contracts.update_observation(self.contract.id,selected[-1].id,f"Observation {total}")
        result=self.service().generate(self.contract.id);items=result.document.snapshot["contract"]["equipment_items"]
        self.assertEqual([item["position"] for item in items],list(range(total)));self.assertEqual(items[-1]["observations"],f"Observation {total}")
    def test_ten_equipment_are_ordered(self):self._assert_equipment_count(10)
    def test_thirty_equipment_are_ordered(self):self._assert_equipment_count(30)

    def test_logo_is_optional_and_controlled_labels_have_no_raw_enum(self):
        result=self.service().generate(self.contract.id)
        with zipfile.ZipFile(result.docx_path) as package:
            stories=b"".join(package.read(name) for name in package.namelist() if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml",name))
            self.assertFalse(any(name.startswith("word/media/icp_logo_") for name in package.namelist()))
            self.assertNotIn(b"<w:drawing",stories)
        for raw in (b"CONSUMER",b"PARTNER",b"FIXED",b"TRANSFER"):self.assertNotIn(raw,stories)

    def test_logo_present_uses_package_relationship(self):
        logo=self.context.workspace.root/"logo.png";logo.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        company=dict(COMPANY,logo=str(logo));service=self.service();service.company_provider=StaticCompanyDocumentDataProvider(company)
        result=service.generate(self.contract.id)
        with zipfile.ZipFile(result.docx_path) as package:self.assertTrue(any(name.startswith("word/media/icp_logo_") for name in package.namelist()))


class GenerationUiTests(GenerationCase):
    @classmethod
    def setUpClass(cls):cls.application=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp();self.generation=self.service()
        self.view=ContractsView(self.contracts,self.review,self.generation)
        self.view.open_contract(self.contract.id);self.view.navigate_step(2);self.application.processEvents()
    def tearDown(self):self.view.close();self.view.deleteLater();self.application.processEvents();super().tearDown()
    def test_ready_enables_confirmation_and_cancel_has_no_effect(self):
        self.assertTrue(self.view.review_view.generate_button.isEnabled())
        dialog=GenerationConfirmationDialog(self.generation.preview_number());text=" ".join(label.text() for label in dialog.findChildren(__import__('PySide6.QtWidgets',fromlist=['QLabel']).QLabel))
        self.assertIn("R01",text);self.assertIn("À signer",text);self.assertIn("aucun numéro",text.lower())
        with patch.object(GenerationConfirmationDialog,"exec",return_value=QDialog.DialogCode.Rejected):self.view.review_view.confirm_generation()
        self.assertIsNone(self.contracts.get(self.contract.id).number);self.assertEqual(self.documents.list_for_contract(self.contract.id),())
    def test_success_updates_number_status_landing_and_locks_steps(self):
        with patch.object(GenerationConfirmationDialog,"exec",return_value=QDialog.DialogCode.Accepted):self.view.review_view.confirm_generation()
        self.assertIn("R01",self.view.feedback.text());self.assertEqual(self.view.status_label.text(),"À signer")
        self.assertFalse(self.view.review_view.generate_button.isEnabled());self.view.refresh_drafts();self.assertIn("SYNTH-S5-0001",self.view.draft_list.item(0).text());self.assertIn("À signer",self.view.draft_list.item(0).text())
        self.view.navigate_step(1);self.assertFalse(self.view.conditions_view.isEnabled());self.assertFalse(self.view.step_buttons[3].isEnabled())
    def test_default_production_boundaries_keep_generation_unavailable(self):
        self.assertTrue(self.generation.available(self.contract.id))
        self.context.review.generation_ready=self.context.generation.available
        result=self.context.review.review(self.contract.id);self.assertFalse(result.generation.checks[2].available)


if __name__=="__main__":unittest.main()
