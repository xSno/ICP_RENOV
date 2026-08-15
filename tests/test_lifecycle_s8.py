from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication,QLabel,QLineEdit,QPushButton

from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.database.service import MIGRATIONS
from icp_renov_contracts.domain import ContractEventType,ContractStatus,TemplateVersionStatus
from icp_renov_contracts.errors import ContractLifecycleError
from icp_renov_contracts.repositories import ContractEventRepository
from icp_renov_contracts.services import ContractLifecycleService
from icp_renov_contracts.ui.contracts_view import ContractsView
from icp_renov_contracts.ui.documents_view import (
    AbandonConfirmationDialog,DocumentsView,LinkedDraftConfirmationDialog,NonRenewalDialog,
    RenewalConfirmationDialog,TerminationDialog,
)

from test_contract_events import RecordingOpener,UnavailableAllocator
from test_document_generation import GenerationCase,REQUIRED_COMPANY_FIELDS
from test_foundation import scratch


class Clock:
    def __init__(self,value):self.value=value
    def today(self):return self.value


class MigrationEightTests(unittest.TestCase):
    def test_migration_eight_over_seven_is_idempotent_and_bounded(self):
        with scratch() as temporary:
            database=DatabaseService(Path(temporary)/"s7.sqlite3")
            with database.transaction() as connection:
                for migration in MIGRATIONS[:7]:
                    for statement in migration.statements:connection.execute(statement)
                    connection.execute("INSERT OR REPLACE INTO schema_migrations VALUES (?,?)",(migration.version,"frozen"))
            database.initialize();database.initialize()
            with database.connection() as connection:
                columns=[row[1] for row in connection.execute("PRAGMA table_info(contracts)")]
                self.assertEqual(columns.count("predecessor_contract_id"),1);self.assertEqual(columns.count("terminal_status"),1)
                connection.execute("INSERT INTO contracts(id,status,type_code,created_at_utc,updated_at_utc) VALUES ('p','DRAFT','CLIMATE_MAINTENANCE','x','x')")
                connection.execute("INSERT INTO contract_conditions(contract_id,updated_at_utc) VALUES ('p','x')")
                with self.assertRaises(sqlite3.IntegrityError):connection.execute("UPDATE contracts SET predecessor_contract_id='p' WHERE id='p'")
            self.assertEqual(database.schema_version(),9)


class LifecycleS8Tests(GenerationCase):
    @classmethod
    def setUpClass(cls):cls.application=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp();self.clock=Clock(date(2026,8,13));self.events=ContractEventRepository(self.context.database)
        self.lifecycle=ContractLifecycleService(self.context.database,self.contracts,self.documents,self.events,self.context.workspace.root,RecordingOpener(),self.clock,lambda:"2026-08-13T10:00:00+00:00")

    def _conditions(self,mode="TACIT",start="2026-01-01",months=12,price_rule="FIXED",price="100"):
        current=self.contracts.get_conditions(self.contract.id)
        return replace(current,start_date=start,initial_duration_mode="STANDARD",initial_duration_months=months,initial_end_date=None,
            annual_ht=price,vat_rate="20",renewal_mode=mode,renewal_period_months=12 if mode!="NONE" else None,
            non_renewal_notice_days=60 if mode=="TACIT" else None,non_renewal_notice_channels=("EMAIL",) if mode=="TACIT" else (),
            internal_alert_days=90 if mode!="NONE" else None,renewal_price_rule=price_rule if mode!="NONE" else None,breach_cure_period_days=15)

    def _signed(self,mode="TACIT",start="2026-01-01",price_rule="FIXED",price="100"):
        self.contracts.save_conditions(self.contract.id,self._conditions(mode,start,12,price_rule,price));result=self.service().generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id,result.document.id,"2026-01-02");return result

    def _enable_generation(self):
        version=self.contracts.selected_template_version(self.contract.id);source=next((Path(__file__).parents[1]/"templates").glob("*HABITATION*.docx"));rel,digest=self.store.import_source(source,version.id)
        self.catalog.set_generation_metadata(version.id,rel,digest,REQUIRED_COMPANY_FIELDS)

    def _types(self,contract_id=None):return [event.type for event in reversed(self.lifecycle.history(contract_id or self.contract.id))]

    def test_exact_signed_r01_controls_period_mode_price_and_attention_not_latest_r02(self):
        self.contracts.save_conditions(self.contract.id,self._conditions("TACIT","2026-01-01",12,"FIXED","100"));service=self.service();r01=service.generate(self.contract.id)
        self.lifecycle.reopen_for_correction(self.contract.id);self.contracts.save_conditions(self.contract.id,self._conditions("MANUAL","2026-02-01",24,"NEW_PRICE_ON_RENEWAL","999"));service.number_allocator=UnavailableAllocator();r02=service.generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-01-02");projection=self.lifecycle.lifecycle_projection(self.contract.id)
        self.assertEqual(projection.authority.document.id,r01.document.id);self.assertEqual((projection.period.start,projection.period.end),(date(2026,1,1),date(2026,12,31)))
        self.assertEqual(projection.renewal_mode,"TACIT");self.assertEqual(projection.price.annual_ht,Decimal("100.00"));self.assertEqual(projection.renewal_price_rule,"FIXED")
        self.assertEqual(projection.next_attention_date,date(2026,10,2));self.assertEqual(projection.non_renewal_deadline,date(2026,11,1));self.assertEqual(len(self.documents.list_for_contract(self.contract.id)),2)

    def test_fixed_and_new_price_renewals_are_contiguous_immutable_and_create_no_rxx(self):
        signed=self._signed();before=signed.document.snapshot_json;count=len(self.documents.list_for_contract(self.contract.id));event=self.lifecycle.confirm_renewal(self.contract.id)
        projection=self.lifecycle.lifecycle_projection(self.contract.id);self.assertEqual((event.period_start,event.period_end),("2027-01-01","2027-12-31"));self.assertEqual(event.renewal_annual_ht,"100.00");self.assertEqual(event.renewal_vat_amount,"20.00");self.assertEqual(event.renewal_annual_ttc,"120.00")
        second=self.lifecycle.confirm_renewal(self.contract.id);self.assertEqual((second.period_start,second.period_end),("2028-01-01","2028-12-31"));self.assertEqual(self.lifecycle.lifecycle_projection(self.contract.id).price.annual_ht,Decimal("100.00"))
        self.assertEqual(len(self.documents.list_for_contract(self.contract.id)),count);self.assertEqual(self.documents.get(signed.document.id).snapshot_json,before)
        with self.context.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):connection.execute("UPDATE contract_events SET renewal_annual_ht='1' WHERE id=?",(event.id,))

        other=self.contracts.create_draft();self.contract=other;self.complete(renewal="TACIT");self._enable_generation();self.contracts.save_conditions(other.id,self._conditions("TACIT","2026-01-01",12,"NEW_PRICE_ON_RENEWAL","100"));result=self.service().generate(other.id);self.lifecycle.record_signature(other.id,result.document.id,"2026-01-02")
        with self.assertRaises(ContractLifecycleError):self.lifecycle.confirm_renewal(other.id)
        priced=self.lifecycle.confirm_renewal(other.id,"150","20");self.assertEqual((priced.renewal_vat_amount,priced.renewal_annual_ttc),("30.00","180.00"))

    def test_tacit_attention_never_auto_renews_or_expires_and_notice_expires_once(self):
        self._signed();before=self.lifecycle.history(self.contract.id);self.assertEqual(self.lifecycle.reconcile_lifecycle(date(2027,1,2)),())
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.ACTIVE);self.assertEqual(self.lifecycle.history(self.contract.id),before)
        notice=self.lifecycle.record_non_renewal(self.contract.id,"2026-10-15","Notification réelle");self.assertEqual(notice.effective_date,"2026-10-15")
        with self.assertRaises(ContractLifecycleError):self.lifecycle.confirm_renewal(self.contract.id)
        self.lifecycle.reconcile_lifecycle(date(2026,12,31));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.EXPIRED);self.assertEqual(self._types().count(ContractEventType.EXPIRED),1)
        self.lifecycle.reconcile_lifecycle(date(2027,1,1));self.assertEqual(self._types().count(ContractEventType.EXPIRED),1)

    def test_none_expires_exactly_once_at_term(self):
        self._signed("NONE");self.lifecycle.reconcile_lifecycle(date(2026,12,30));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.ACTIVE)
        self.lifecycle.reconcile_lifecycle(date(2026,12,31));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.EXPIRED);event=next(event for event in self.lifecycle.history(self.contract.id) if event.type is ContractEventType.EXPIRED);self.assertEqual(event.effective_date,"2026-12-31")
        self.lifecycle.reconcile_lifecycle(date(2027,1,1));self.assertEqual(self._types().count(ContractEventType.EXPIRED),1)

    def test_none_and_manual_expire_and_linked_draft_is_independent_snapshot_prefill(self):
        self._signed("MANUAL");predecessor=self.contracts.get(self.contract.id);pre_events=self.lifecycle.history(self.contract.id);linked=self.lifecycle.create_linked_draft(self.contract.id)
        self.assertEqual(linked.predecessor_contract_id,self.contract.id);self.assertIs(linked.status,ContractStatus.DRAFT);self.assertIsNone(linked.number);self.assertEqual(self._types(linked.id),[ContractEventType.CREATED]);self.assertEqual(self.lifecycle.history(self.contract.id),pre_events)
        linked_conditions=self.contracts.get_conditions(linked.id);self.assertEqual((linked_conditions.issue_date,linked_conditions.start_date,linked_conditions.resolved_end_date),("2026-08-13","2027-01-01","2027-12-31"))
        self.assertEqual(linked.client_snapshot,predecessor.client_snapshot);self.assertEqual([item.snapshot for item in linked.equipment_items],[item.snapshot for item in predecessor.equipment_items]);self.assertIsNone(linked.number)
        self.lifecycle.reconcile_lifecycle(date(2026,12,31));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.EXPIRED);self.assertIs(self.contracts.get(linked.id).status,ContractStatus.DRAFT)

    def test_linked_draft_does_not_reuse_an_archived_historical_template(self):
        self._signed("MANUAL");version=self.contracts.selected_template_version(self.contract.id)
        self.catalog.update_status(version.id,TemplateVersionStatus.ARCHIVED)
        linked=self.lifecycle.create_linked_draft(self.contract.id)
        self.assertIsNone(linked.template_version_id)

    def test_linked_draft_notice_is_persistent_informational_and_draft_only(self):
        self._signed("MANUAL");predecessor_id=self.contract.id
        linked=self.lifecycle.create_linked_draft(predecessor_id);before=self.lifecycle.history(linked.id)
        view=ContractsView(self.contracts,self.review,self.service(),self.lifecycle);view.show();self.application.processEvents()
        try:
            view.open_contract(linked.id);self.application.processEvents()
            self.assertTrue(view.linked_draft_notice.isVisible())
            self.assertEqual(view.linked_draft_notice.text(),"Brouillon prérempli depuis le contrat précédent.\nVérifiez les dates, les équipements, le régime et les conditions.")
            for index in (1,2,3,0):
                view.navigate_step(index);self.application.processEvents();self.assertTrue(view.linked_draft_notice.isVisible())
            self.assertEqual(self.lifecycle.history(linked.id),before)
            ordinary=self.contracts.create_draft();view.open_contract(ordinary.id);self.application.processEvents()
            self.assertFalse(view.linked_draft_notice.isVisible())
            view.open_contract(predecessor_id);self.application.processEvents()
            self.assertFalse(view.linked_draft_notice.isVisible())
        finally:view.close();self.application.processEvents()

    def test_future_and_immediate_termination_order_priority_and_atomicity(self):
        self._signed();scheduled=self.lifecycle.schedule_termination(self.contract.id,"2026-09-01","Cessation","2026-08-01","Note")
        self.assertEqual(scheduled.reason_text,"Cessation");self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.ACTIVE)
        with self.assertRaises(ContractLifecycleError):self.lifecycle.schedule_termination(self.contract.id,"2026-10-01","Autre")
        self.lifecycle.reconcile_lifecycle(date(2026,9,1));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TERMINATED);self.assertEqual(self._types().count(ContractEventType.TERMINATED),1)
        self.lifecycle.reconcile_lifecycle(date(2027,1,1));self.assertNotIn(ContractEventType.EXPIRED,self._types())

        other=self.contracts.create_draft();self.contract=other;self.complete(renewal="TACIT");self._enable_generation();self.contracts.save_conditions(other.id,self._conditions("TACIT","2026-09-01"));result=self.service().generate(other.id);self.lifecycle.record_signature(other.id,result.document.id,"2026-08-01")
        self.lifecycle.schedule_termination(other.id,"2026-08-15","Avant prise d’effet");self.lifecycle.reconcile_lifecycle(date(2026,8,15))
        self.assertIs(self.contracts.get(other.id).status,ContractStatus.TERMINATED);self.assertNotIn(ContractEventType.ACTIVATED,self._types(other.id))

    def test_activation_then_later_termination_reconciles_in_factual_order(self):
        self.contracts.save_conditions(self.contract.id,self._conditions("TACIT","2026-09-01"));result=self.service().generate(self.contract.id);self.lifecycle.record_signature(self.contract.id,result.document.id,"2026-08-01")
        self.lifecycle.schedule_termination(self.contract.id,"2026-10-15","Après prise d’effet");self.lifecycle.reconcile_lifecycle(date(2026,10,20));events=list(reversed(self.lifecycle.history(self.contract.id)));types=[e.type for e in events]
        self.assertLess(types.index(ContractEventType.ACTIVATED),types.index(ContractEventType.TERMINATED));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TERMINATED)
        self.assertEqual(next(e for e in events if e.type is ContractEventType.ACTIVATED).effective_date,"2026-09-01");self.assertEqual(next(e for e in events if e.type is ContractEventType.TERMINATED).effective_date,"2026-10-15")

    def test_immediate_termination_failure_and_abandon_preserve_atomic_truth(self):
        self._signed()
        with patch.object(ContractEventRepository,"insert",side_effect=[None,sqlite3.OperationalError("terminal fail")]):
            with self.assertRaises(ContractLifecycleError):self.lifecycle.schedule_termination(self.contract.id,"2026-08-01","Immédiate")
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.ACTIVE);self.assertNotIn(ContractEventType.TERMINATION_SCHEDULED,self._types())
        draft=self.contracts.create_draft();event=self.lifecycle.abandon(draft.id);self.assertIs(event.type,ContractEventType.ABANDONED);self.assertIs(self.contracts.get(draft.id).status,ContractStatus.ABANDONED)
        with self.assertRaises(ContractLifecycleError):self.lifecycle.abandon(self.contract.id)
        numbered=self.contracts.create_draft();self.contract=numbered;self.complete(renewal="NONE");self.contracts.save_conditions(numbered.id,self._conditions("NONE"));self._enable_generation();result=self.service().generate(numbered.id);self.lifecycle.abandon(numbered.id)
        self.assertIs(self.contracts.get(numbered.id).status,ContractStatus.ABANDONED);self.assertTrue(result.docx_path.is_file());self.assertEqual(self.contracts.get(numbered.id).number,result.contract_number)

    def test_ui_manual_and_tacit_groups_modals_and_no_automatic_wording(self):
        self._signed("MANUAL");view=DocumentsView(self.lifecycle,lambda message,error:None);view.load(self.contract.id)
        text=" ".join(label.text() for label in view.findChildren(QLabel));buttons=[button.text() for button in view.findChildren(QPushButton)]
        self.assertIn("Renouvellement manuel",text);self.assertIn("Période actuelle",text);self.assertIn("Préparer le renouvellement",buttons);self.assertIn("Programmer une résiliation",buttons);self.assertNotIn("Résilier maintenant",buttons)
        linked=LinkedDraftConfirmationDialog();self.assertIn("nouveau Brouillon", " ".join(label.text() for label in linked.findChildren(QLabel)))
        termination=TerminationDialog(self.clock.today());self.assertIsNotNone(termination.findChild(QLineEdit,"terminationEffectiveDate"));self.assertIsNotNone(termination.findChild(QLineEdit,"terminationReason"))

    def test_ui_tacit_confirmation_notice_terminal_and_abandon_language(self):
        self._signed("TACIT",price_rule="NEW_PRICE_ON_RENEWAL");projection=self.lifecycle.lifecycle_projection(self.contract.id);view=DocumentsView(self.lifecycle,lambda message,error:None);view.load(self.contract.id)
        text=" ".join(label.text() for label in view.findChildren(QLabel));buttons=[button.text() for button in view.findChildren(QPushButton)]
        self.assertIn("Reconduction tacite",text);self.assertIn("Confirmer la reconduction",buttons);self.assertIn("Enregistrer une fin de contrat",buttons);self.assertNotIn("renouvellement automatique",text.lower())
        renewal=RenewalConfirmationDialog(projection);self.assertIsNotNone(renewal.findChild(QLineEdit,"renewalAnnualHt"));self.assertIsNotNone(renewal.findChild(QLineEdit,"renewalVatRate"))
        nonrenewal=NonRenewalDialog(projection,self.clock.today());self.assertIn("n’envoie aucun message automatiquement", " ".join(label.text() for label in nonrenewal.findChildren(QLabel)))
        self.lifecycle.schedule_termination(self.contract.id,"2026-08-01","Motif");view.load(self.contract.id);self.assertIn("Contrat résilié depuis", " ".join(label.text() for label in view.findChildren(QLabel)))
        draft=self.contracts.create_draft();draft_view=DocumentsView(self.lifecycle,lambda message,error:None);draft_view.load(draft.id);draft_buttons=[button.text() for button in draft_view.findChildren(QPushButton)]
        self.assertIn("Abandonner le contrat",draft_buttons);self.assertNotIn("Programmer une résiliation",draft_buttons);abandon=AbandonConfirmationDialog();self.assertIn("Abandonné", " ".join(label.text() for label in abandon.findChildren(QLabel)))


if __name__=="__main__":unittest.main()
