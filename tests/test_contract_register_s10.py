from __future__ import annotations

from dataclasses import replace
from datetime import date
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icp_renov_contracts.domain import ClientDraft, ContractStatus, SiteDraft
from icp_renov_contracts.repositories import ContractEventRepository
from icp_renov_contracts.services import (
    BackupSummary, BackupSummaryProvider, ContractLifecycleService,
    ContractOperationalSignalKind, ContractRegisterFilter, ContractRegisterService,
)
from icp_renov_contracts.ui import MainWindow
from icp_renov_contracts.ui.contracts_view import ContractsView

from test_contract_events import RecordingOpener, UnavailableAllocator
from test_document_generation import GenerationCase
from test_master_data import organization, site


class Clock:
    def __init__(self, value: date): self.value = value
    def today(self) -> date: return self.value


class FakeBackupProvider(BackupSummaryProvider):
    def __init__(self): self.created = 0
    def summary(self):
        return BackupSummary("Dernière sauvegarde le 14/08/2026", "Aucune sauvegarde n’a encore été créée", True)
    def create_now(self): self.created += 1


class ContractRegisterS10Tests(GenerationCase):
    @classmethod
    def setUpClass(cls): cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.clock = Clock(date(2026, 8, 13))
        self.events = ContractEventRepository(self.context.database)
        self.lifecycle = ContractLifecycleService(
            self.context.database, self.contracts, self.documents, self.events,
            self.context.workspace.root, RecordingOpener(), self.clock, lambda: "2026-08-13T10:00:00+00:00",
        )
        self.backup = FakeBackupProvider()
        self.register = ContractRegisterService(
            self.contracts, self.review, self.lifecycle, self.context.workspace_service, self.backup,
        )

    def _rows(self): return self.register.rows()

    def _row(self, contract_id: str): return next(row for row in self._rows() if row.contract_id == contract_id)

    @staticmethod
    def _table_row(view, contract_id: str) -> int:
        return next(index for index in range(view.draft_list.rowCount())
                    if view.draft_list.item(index, 0).data(Qt.ItemDataRole.UserRole) == contract_id)

    def _signed(self, start: str = "2026-09-01", renewal: str = "TACIT"):
        conditions = replace(self.contracts.get_conditions(self.contract.id), start_date=start,
            initial_duration_mode="STANDARD", initial_duration_months=12, initial_end_date=None,
            renewal_mode=renewal, renewal_period_months=12 if renewal != "NONE" else None,
            non_renewal_notice_days=60 if renewal == "TACIT" else None,
            non_renewal_notice_channels=("EMAIL",) if renewal == "TACIT" else (),
            internal_alert_days=90 if renewal != "NONE" else None, renewal_price_rule="FIXED" if renewal != "NONE" else None)
        self.contracts.save_conditions(self.contract.id, conditions)
        result = self.service().generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id, result.document.id, "2026-08-13")
        return result

    def test_draft_snapshot_search_filters_and_unique_action_counter(self):
        contract = self.contracts.get(self.contract.id)
        original_client = contract.client_snapshot.display_name
        original_site = contract.site_snapshot.label
        self.master.update_client(contract.client_source_id, organization("MAÎTRE MODIFIÉ"))
        self.master.update_site(contract.site_source_id, site("SITE MAÎTRE MODIFIÉ"))
        row = self._row(self.contract.id)
        self.assertEqual((row.client_name, row.site_label), (original_client, original_site))
        self.assertEqual(row.signal.kind, ContractOperationalSignalKind.ACTION)
        self.assertEqual(row.signal.label, "Finaliser le brouillon")
        by_client = self.register.filter_rows(self._rows(), ContractRegisterFilter.ALL, original_client.casefold())
        by_site = self.register.filter_rows(self._rows(), ContractRegisterFilter.ALL, original_site.casefold())
        self.assertEqual([item.contract_id for item in by_client], [self.contract.id])
        self.assertEqual([item.contract_id for item in by_site], [self.contract.id])
        actions = self.register.filter_rows(self._rows(), ContractRegisterFilter.ACTIONS)
        self.assertEqual(self.register.action_count(self._rows()), len({item.contract_id for item in actions}))

    def test_document_summary_uses_exact_signed_authority_not_latest_revision(self):
        r01 = self.service().generate(self.contract.id)
        self.lifecycle.reopen_for_correction(self.contract.id)
        generation = self.service(); generation.number_allocator = UnavailableAllocator()
        r02 = generation.generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id, r01.document.id, "2026-08-13")
        row = self._row(self.contract.id)
        self.assertEqual(row.document_lines[:2], ("R02 · non signé", "R01 · signé"))
        self.assertEqual(row.signal.label, "Copie signée à archiver")
        self.assertNotIn("R02 · signé", row.document_lines)
        self.assertEqual(r02.document.revision, "R02")

    def test_table_keeps_difficult_documents_and_operational_text_readable_at_desktop_widths(self):
        r01 = self.service().generate(self.contract.id)
        self.lifecycle.reopen_for_correction(self.contract.id)
        generation = self.service(); generation.number_allocator = UnavailableAllocator()
        generation.generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id, r01.document.id, "2026-08-13")
        window = MainWindow(self.context)
        view = window.shell.surface("Contrats")
        window.show(); self.application.processEvents()
        try:
            for width in (1600, 1366):
                window.resize(width, 900); self.application.processEvents(); view.refresh_drafts()
                index = self._table_row(view, self.contract.id)
                self.assertFalse(view.draft_list.isColumnHidden(5))
                self.assertEqual(view.draft_list.item(index, 5).text(), "R02 · non signé\nR01 · signé")
                self.assertGreaterEqual(view.draft_list.rowHeight(index), 58)
                self.assertGreaterEqual(view.draft_list.columnWidth(4), 280)
                self.assertIn("Action —\nCopie signée à archiver", view.draft_list.item(index, 4).text())
            window.resize(1280, 900); self.application.processEvents(); view.refresh_drafts()
            index = self._table_row(view, self.contract.id)
            self.assertTrue(view.draft_list.isColumnHidden(5))
            self.assertGreaterEqual(view.draft_list.columnWidth(4), 280)
            self.assertIn("Copie signée à archiver", view.draft_list.item(index, 4).text())
        finally:
            window.close(); self.application.processEvents()

    def test_unicode_snapshots_round_trip_through_register_and_search(self):
        contract = self.contracts.create_draft()
        client = self.master.create_client(ClientDraft(
            "ORGANIZATION", organization_name="Élodie — Client d’été", address_line1="1 rue des Érables",
            postal_code="75001", city="Paris", email="unicode@example.invalid",
        ))
        site = self.master.create_site(client.id, SiteDraft("Île — Site d’intervention", "2 allée des Fêtes", "69001", "Lyon"))
        self.contracts.select_client(contract.id, client.id); self.contracts.select_site(contract.id, site.id)
        row = self._row(contract.id)
        self.assertEqual(row.client_name, "Élodie — Client d’été")
        self.assertEqual(row.site_label, "Île — Site d’intervention")
        matches = self.register.filter_rows(self._rows(), ContractRegisterFilter.ALL, "elodie")
        self.assertIn(contract.id, [item.contract_id for item in matches])

    def test_action_information_and_terminal_rules_use_existing_lifecycle_truth(self):
        self._signed(start="2026-09-01")
        future = self._row(self.contract.id)
        self.assertEqual(future.status, ContractStatus.SIGNED)
        self.assertEqual(future.signal.kind, ContractOperationalSignalKind.ACTION)  # missing signed archive dominates information
        source = self.context.workspace.root / "signed-source.pdf"
        source.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")
        self.lifecycle.add_signed_copy(self.contract.id, source)
        future = self._row(self.contract.id)
        self.assertEqual(future.signal.kind, ContractOperationalSignalKind.INFORMATION)
        self.assertIn("Prise d’effet prévue le 01/09/2026", future.signal.label)
        # Record a terminal lifecycle state through the accepted service, not a register mutation.
        self.lifecycle.schedule_termination(self.contract.id, "2026-08-20", "Test")
        self.lifecycle.reconcile_lifecycle(date(2026, 8, 20))
        terminal = self._row(self.contract.id)
        self.assertEqual(terminal.status, ContractStatus.TERMINATED)
        self.assertNotEqual(terminal.signal.kind, ContractOperationalSignalKind.ACTION)

    def test_tacit_past_end_remains_active_and_actionable(self):
        self._signed(start="2025-01-01", renewal="TACIT")
        self.clock.value = date(2026, 8, 13)
        row = self._row(self.contract.id)
        self.assertEqual(row.status, ContractStatus.ACTIVE)
        self.assertEqual(row.signal.label, "Copie signée à archiver")
        # Once the documentary action is resolved, the S8 tacit state is the primary action.
        authority = self.lifecycle.signature_authority(self.contract.id)
        signed = self.context.workspace.root / "signed.pdf"; signed.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")
        self.lifecycle.add_signed_copy(self.contract.id, signed)
        row = self._row(self.contract.id)
        self.assertEqual(row.signal.label, "Reconduction à confirmer")

    def test_table_mouse_enter_and_session_state_open_without_register_events(self):
        view = ContractsView(self.contracts, self.review, self.service(), self.lifecycle, register_service=self.register)
        view.show(); self.application.processEvents()
        try:
            before = self.lifecycle.history(self.contract.id)
            view.search_input.setText(self.contracts.get(self.contract.id).client_snapshot.display_name)
            view._select_filter(ContractRegisterFilter.DRAFTS)
            self.application.processEvents()
            self.assertEqual(view.draft_list.rowCount(), 1)
            QTest.mouseClick(view.draft_list.viewport(), Qt.MouseButton.LeftButton, pos=view.draft_list.visualItemRect(view.draft_list.item(0, 0)).center())
            self.application.processEvents(); self.assertEqual(view.contract_id, self.contract.id)
            view.show_landing(); self.application.processEvents()
            self.assertEqual(view.search_input.text(), self.contracts.get(self.contract.id).client_snapshot.display_name)
            self.assertEqual(view.selected_filter, ContractRegisterFilter.DRAFTS)
            view.draft_list.setCurrentCell(0, 0); QTest.keyClick(view.draft_list, Qt.Key.Key_Return); self.application.processEvents()
            self.assertEqual(view.contract_id, self.contract.id)
            self.assertEqual(self.lifecycle.history(self.contract.id), before)
            self.assertTrue(view.backup_button.isEnabled()); view.backup_button.click(); self.assertEqual(self.backup.created, 1)
        finally:
            view.close(); self.application.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
