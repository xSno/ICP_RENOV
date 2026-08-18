from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import os
import sqlite3
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from icp_renov_contracts.domain import AlertSettings, AnnualNumberingPolicy, NumberFormatMode, NumberingSettings
from icp_renov_contracts.errors import NumberingCollisionError, NumberingValidationError
from icp_renov_contracts.repositories import AlertSettingsRepository, NumberingSettingsRepository
from icp_renov_contracts.services import (
    AlertSettingsService, ContractLifecycleService, ContractOperationalSignalKind, ContractRegisterService,
    NumberingSettingsService, PersistedContractNumberAllocator,
)
from icp_renov_contracts.ui.numbering_alerts_view import NumberingAlertsSettingsPage

from test_contract_events import RecordingOpener, UnavailableAllocator
from test_document_generation import FakeConverter, GenerationCase


class Clock:
    def __init__(self, value: date): self.value = value
    def today(self) -> date: return self.value


class NumberingS13Tests(GenerationCase):
    @classmethod
    def setUpClass(cls): cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.clock = Clock(date(2026, 8, 18))
        self.numbering = NumberingSettingsService(NumberingSettingsRepository(self.context.database), self.clock)
        self.alerts = AlertSettingsService(AlertSettingsRepository(self.context.database))

    @staticmethod
    def configured(counter=42, mode=NumberFormatMode.PREFIX_YEAR_COUNTER,
                   policy=AnnualNumberingPolicy.CONTINUOUS, prefix="SYNTH"):
        return NumberingSettings(mode, prefix, 4, policy, 42, counter)

    def test_migration_12_has_typed_singletons_and_never_infers_from_existing_number(self):
        with self.context.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 12)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM alert_settings").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM numbering_settings").fetchone()[0], 0)
            connection.execute("UPDATE contracts SET number='HIST-0099' WHERE id=?", (self.contract.id,))
        self.context.database.initialize()
        self.assertIsNone(self.numbering.get())
        self.assertTrue(self.numbering.has_official_numbers())
        self.assertEqual(self.contracts.get(self.contract.id).number, "HIST-0099")

    def test_closed_formatter_preview_width_empty_prefix_and_validation(self):
        saved = self.numbering.save(self.configured())
        self.assertEqual(self.numbering.preview(), "SYNTH-2026-0042")
        self.assertEqual(self.numbering.preview(), "SYNTH-2026-0042")
        self.assertEqual(self.numbering.get().next_counter, 42)
        self.assertEqual(self.numbering.candidate(replace(saved, prefix="", format_mode=NumberFormatMode.PREFIX_COUNTER)), "0042")
        self.assertEqual(self.numbering.candidate(replace(saved, next_counter=10000)), "SYNTH-2026-10000")
        with self.assertRaises(NumberingValidationError):
            self.numbering.save(replace(saved, format_mode=NumberFormatMode.PREFIX_COUNTER,
                                        annual_policy=AnnualNumberingPolicy.RESET_ANNUALLY))

    def test_allocation_rolls_back_advances_once_and_blocks_collision(self):
        self.numbering.save(self.configured())
        allocator = PersistedContractNumberAllocator(self.numbering)
        with self.assertRaises(RuntimeError):
            with self.context.database.transaction() as connection:
                self.assertEqual(allocator.allocate(connection), "SYNTH-2026-0042")
                raise RuntimeError("publication failed")
        self.assertEqual(self.numbering.get().next_counter, 42)
        with self.context.database.transaction() as connection:
            self.assertEqual(allocator.allocate(connection), "SYNTH-2026-0042")
        self.assertEqual(self.numbering.get().next_counter, 43)
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contracts SET number='SYNTH-2026-0043' WHERE id=?", (self.contract.id,))
        self.assertFalse(allocator.available()); self.assertEqual(allocator.unavailability_reason, "collision")
        with self.context.database.transaction() as connection:
            with self.assertRaises(NumberingCollisionError): allocator.allocate(connection)

    def test_continuous_and_annual_reset_are_deterministic_without_preview_rollover(self):
        self.numbering.save(self.configured())
        allocator = PersistedContractNumberAllocator(self.numbering)
        with self.context.database.transaction() as connection: allocator.allocate(connection)
        self.clock.value = date(2027, 1, 1)
        self.assertEqual(self.numbering.preview(), "SYNTH-2027-0043")
        self.assertIsNone(self.numbering.get().counter_year)
        with self.context.database.transaction() as connection:
            connection.execute("DELETE FROM numbering_settings")
        self.clock.value = date(2026, 12, 31)
        self.numbering.save(self.configured(policy=AnnualNumberingPolicy.RESET_ANNUALLY))
        with self.context.database.transaction() as connection: allocator.allocate(connection)
        self.clock.value = date(2027, 1, 1)
        self.assertEqual(self.numbering.preview(), "SYNTH-2027-0042")
        self.assertEqual(self.numbering.get().counter_year, 2026)
        self.assertEqual(self.numbering.get().next_counter, 43)

    def test_correction_is_future_only_allows_unused_lower_and_creates_no_event(self):
        self.numbering.save(self.configured())
        allocator = PersistedContractNumberAllocator(self.numbering)
        with self.context.database.transaction() as connection:
            allocated = allocator.allocate(connection)
            connection.execute("UPDATE contracts SET number=? WHERE id=?", (allocated, self.contract.id))
        with self.context.database.connection() as connection:
            before = connection.execute("SELECT COUNT(*) FROM contract_events").fetchone()[0]
        corrected = self.numbering.correct_next_counter(40)
        self.assertEqual(corrected.next_counter, 40); self.assertEqual(self.contracts.get(self.contract.id).number, allocated)
        with self.context.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM contract_events").fetchone()[0], before)
        self.assertEqual(self.numbering.preview(), "SYNTH-2026-0040")

    def test_persisted_allocator_drives_r01_failure_and_r02_ignores_numbering(self):
        self.numbering.save(self.configured())
        allocator = PersistedContractNumberAllocator(self.numbering)
        service = self.service(allocator=allocator)
        self.assertEqual(service.preview_number(self.contract.id), "SYNTH-2026-0042")
        failing = self.service(converter=FakeConverter("failure"), allocator=allocator)
        self.assert_atomic_failure(failing); self.assertEqual(self.numbering.get().next_counter, 42)
        result = service.generate(self.contract.id)
        self.assertEqual((result.contract_number, result.document.revision), ("SYNTH-2026-0042", "R01"))
        self.assertEqual(self.numbering.get().next_counter, 43)
        self.lifecycle = ContractLifecycleService(self.context.database, self.contracts, self.documents,
            self.context.lifecycle.events, self.context.workspace.root, RecordingOpener(), self.clock)
        self.lifecycle.reopen_for_correction(self.contract.id)
        service.number_allocator = UnavailableAllocator()
        r02 = service.generate(self.contract.id)
        self.assertEqual((r02.contract_number, r02.document.revision), ("SYNTH-2026-0042", "R02"))
        self.assertEqual(self.numbering.get().next_counter, 43)

    def test_alert_settings_partial_zero_and_signature_followup_projection(self):
        stored = self.alerts.save(AlertSettings(signature_followup_days=0, backup_reminder_days=30))
        self.assertIsNone(stored.default_internal_alert_days); self.assertEqual(stored.signature_followup_days, 0)
        self.numbering.save(self.configured())
        result = self.service(allocator=PersistedContractNumberAllocator(self.numbering)).generate(self.contract.id)
        generated = date.fromisoformat(result.document.generated_at_utc[:10]); self.clock.value = generated
        lifecycle = ContractLifecycleService(self.context.database, self.contracts, self.documents,
            self.context.lifecycle.events, self.context.workspace.root, RecordingOpener(), self.clock)
        register = ContractRegisterService(self.contracts, self.review, lifecycle,
                                           self.context.workspace_service, alert_settings=self.alerts)
        row = next(row for row in register.rows() if row.contract_id == self.contract.id)
        self.assertEqual(row.signal.label, "Relance signature à effectuer")
        self.assertIs(row.signal.kind, ContractOperationalSignalKind.ACTION)
        self.alerts.save(AlertSettings(signature_followup_days=None, backup_reminder_days=30))
        row = next(row for row in register.rows() if row.contract_id == self.contract.id)
        self.assertEqual(row.signal.label, "Signature à enregistrer")

    def test_internal_alert_default_fills_only_an_empty_renewing_contract_value(self):
        self.context.alerts.save(AlertSettings(default_internal_alert_days=45))
        current = self.contracts.get_conditions(self.contract.id)
        value = self.contracts.save_conditions(self.contract.id, replace(current, renewal_mode="MANUAL",
            renewal_period_months=12, renewal_price_rule="FIXED", internal_alert_days=None))
        self.assertEqual(value.internal_alert_days, 45)
        preserved = self.contracts.save_conditions(self.contract.id, replace(value, internal_alert_days=30))
        self.context.alerts.save(AlertSettings(default_internal_alert_days=60))
        preserved = self.contracts.save_conditions(self.contract.id, preserved)
        self.assertEqual(preserved.internal_alert_days, 30)

    def test_ui_closed_choices_unconfigured_then_read_only_and_alert_disable(self):
        page = NumberingAlertsSettingsPage(self.numbering, self.alerts)
        self.assertEqual(page.preview.text(), "Numérotation à configurer")
        self.assertEqual(page.format_combo.count(), 3); self.assertEqual(page.policy_combo.count(), 3)
        page.format_combo.setCurrentIndex(2); page.prefix.setText("SYNTH"); page.policy_combo.setCurrentIndex(1)
        page.start_counter.setValue(42); page.next_counter.setValue(42); page._save_numbering()
        self.assertEqual(page.preview.text(), "SYNTH-2026-0042")
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contracts SET number='EXISTING-1' WHERE id=?", (self.contract.id,))
        page._load(); self.assertTrue(page.next_counter.isHidden()); self.assertFalse(page.correct_number.isHidden())
        enabled, value = page.alert_controls["signature_followup_days"]
        enabled.setChecked(True); value.setValue(0); page._save_alert_settings()
        self.assertEqual(self.alerts.get().signature_followup_days, 0)
        enabled.setChecked(False); page._save_alert_settings(); self.assertIsNone(self.alerts.get().signature_followup_days)
        page.deleteLater()


if __name__ == "__main__": unittest.main(verbosity=2)
