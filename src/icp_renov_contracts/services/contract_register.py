from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
import unicodedata

from ..domain import ContractStatus, DocumentKind, SignedCopyState
from ..errors import ContractLifecycleError
from ..storage import WorkspaceService
from .contract_events import ContractLifecycleService
from .contracts import ContractService
from .review import ReviewService
from .settings import AlertSettingsService


class ContractOperationalSignalKind(str, Enum):
    ACTION = "ACTION"
    INFORMATION = "INFORMATION"
    NONE = "NONE"


class ContractRegisterFilter(str, Enum):
    ALL = "ALL"
    ACTIONS = "ACTIONS"
    DRAFTS = "DRAFTS"
    ACTIVE = "ACTIVE"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class ContractOperationalSignal:
    kind: ContractOperationalSignalKind
    label: str = ""
    relevant_date: date | None = None
    priority: int = 99


@dataclass(frozen=True)
class ContractRegisterRow:
    contract_id: str
    number_label: str
    client_name: str
    site_label: str
    status: ContractStatus
    status_label: str
    due_date: date | None
    signal: ContractOperationalSignal
    document_lines: tuple[str, ...]
    created_at_utc: str


@dataclass(frozen=True)
class BackupSummary:
    label: str
    reminder: str = ""
    can_create_now: bool = False


class BackupSummaryProvider:
    """Small S10 boundary; the real archive/restore engine intentionally comes later."""

    def summary(self) -> BackupSummary:
        return BackupSummary("Sauvegarde non configurée")


STATUS_LABELS = {
    ContractStatus.DRAFT: "Brouillon", ContractStatus.TO_SIGN: "À signer",
    ContractStatus.SIGNED: "Signé", ContractStatus.ACTIVE: "Actif",
    ContractStatus.TERMINATED: "Résilié", ContractStatus.EXPIRED: "Expiré",
    ContractStatus.ABANDONED: "Abandonné",
}


def normalize_search(value: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFKD", value).casefold()
        if not unicodedata.combining(character)
    ).strip()


class ContractRegisterService:
    """Read-only operational projection. It derives rows from existing contract truth only."""

    def __init__(self, contracts: ContractService, review: ReviewService,
                 lifecycle: ContractLifecycleService, workspace_service: WorkspaceService,
                 backup_provider: BackupSummaryProvider | None = None,
                 alert_settings: AlertSettingsService | None = None) -> None:
        self.contracts = contracts
        self.review = review
        self.lifecycle = lifecycle
        self.workspace_service = workspace_service
        self.backup_provider = backup_provider or BackupSummaryProvider()
        self.alert_settings = alert_settings

    def workspace_writable(self) -> bool:
        inspection = self.workspace_service.inspect(self.review.workspace.root)
        return inspection.available and inspection.writable

    def backup_summary(self) -> BackupSummary:
        return self.backup_provider.summary()

    def rows(self) -> tuple[ContractRegisterRow, ...]:
        # This is the accepted lifecycle safe-entry point; no alert signal writes events.
        self.lifecycle.reconcile_lifecycle()
        rows = tuple(self._row(item) for item in self.contracts.list_drafts())
        return tuple(sorted(rows, key=self._sort_key))

    def filter_rows(self, rows: tuple[ContractRegisterRow, ...], selected: ContractRegisterFilter,
                    search: str = "") -> tuple[ContractRegisterRow, ...]:
        needle = normalize_search(search)
        return tuple(row for row in rows if self._matches_filter(row, selected) and self._matches_search(row, needle))

    @staticmethod
    def action_count(rows: tuple[ContractRegisterRow, ...]) -> int:
        return sum(row.signal.kind is ContractOperationalSignalKind.ACTION for row in rows)

    @staticmethod
    def _matches_filter(row: ContractRegisterRow, selected: ContractRegisterFilter) -> bool:
        if selected is ContractRegisterFilter.ALL:
            return True
        if selected is ContractRegisterFilter.ACTIONS:
            return row.signal.kind is ContractOperationalSignalKind.ACTION
        if selected is ContractRegisterFilter.DRAFTS:
            return row.status is ContractStatus.DRAFT
        if selected is ContractRegisterFilter.ACTIVE:
            return row.status in {ContractStatus.SIGNED, ContractStatus.ACTIVE}
        return row.status in {ContractStatus.TERMINATED, ContractStatus.EXPIRED, ContractStatus.ABANDONED}

    @staticmethod
    def _matches_search(row: ContractRegisterRow, needle: str) -> bool:
        if not needle:
            return True
        return any(needle in normalize_search(value) for value in (row.number_label, row.client_name, row.site_label))

    @staticmethod
    def _sort_key(row: ContractRegisterRow):
        action = row.signal.kind is ContractOperationalSignalKind.ACTION
        return (0 if action else 1, row.signal.relevant_date or date.max, row.signal.priority, row.created_at_utc, row.contract_id)

    def _row(self, item) -> ContractRegisterRow:
        contract = self.contracts.get(item.id)
        documents = self.lifecycle.revisions(contract.id)
        terminal = contract.status in {ContractStatus.TERMINATED, ContractStatus.EXPIRED, ContractStatus.ABANDONED}
        due_date, projection = self._due_date_and_projection(contract.id, contract.status)
        document_lines, document_problem, authority = self._document_summary(contract.id, documents)
        signal = self._signal(contract, projection, document_problem, authority, terminal)
        return ContractRegisterRow(
            contract.id, contract.number or "Brouillon sans numéro",
            contract.client_snapshot.display_name if contract.client_snapshot else "Client à sélectionner",
            contract.site_snapshot.label if contract.site_snapshot else "Site à sélectionner",
            contract.status, STATUS_LABELS[contract.status], due_date, signal, document_lines,
            item.created_at_utc,
        )

    def _due_date_and_projection(self, contract_id: str, status: ContractStatus):
        if status in {ContractStatus.DRAFT, ContractStatus.TO_SIGN}:
            return self._proposed_end(contract_id), None
        if status is ContractStatus.ABANDONED:
            return None, None
        try:
            projection = self.lifecycle.lifecycle_projection(contract_id)
        except ContractLifecycleError:
            return None, None
        if status is ContractStatus.TERMINATED:
            return self._event_date(contract_id, "TERMINATED") or projection.period.end, projection
        if status is ContractStatus.EXPIRED:
            return self._event_date(contract_id, "EXPIRED") or projection.period.end, projection
        return projection.period.end, projection

    def _proposed_end(self, contract_id: str) -> date | None:
        try:
            value = self.contracts.get_conditions(contract_id).resolved_end_date
            return date.fromisoformat(value) if value else None
        except (ValueError, AttributeError):
            return None

    def _event_date(self, contract_id: str, type_name: str) -> date | None:
        for event in self.lifecycle.history(contract_id):
            if event.type.value == type_name and event.effective_date:
                try:
                    return date.fromisoformat(event.effective_date)
                except ValueError:
                    return None
        return None

    def _document_summary(self, contract_id: str, documents):
        if not documents:
            return ("Aucun document",), False, None
        latest = max(documents, key=lambda document: document.revision_index or 0)
        lines = [f"{latest.revision} · non signé"]
        problem = any(
            self.lifecycle.resolve_document_path(path) is None
            for document in documents for path in (document.docx_relpath, document.pdf_relpath)
        )
        authority = None
        try:
            authority = self.lifecycle.signature_authority(contract_id)
        except ContractLifecycleError:
            problem = True
        if authority:
            signed_line = f"{authority.document.revision} · signé"
            if authority.document.id == latest.id:
                lines = [signed_line]
            else:
                lines.append(signed_line)
            copy_state = self.lifecycle.signed_copy_state(authority.document)
            if copy_state is SignedCopyState.VALID:
                lines.append("Copie signée")
        return tuple(lines), problem, authority

    def _signal(self, contract, projection, document_problem: bool, authority, terminal: bool) -> ContractOperationalSignal:
        if terminal:
            if contract.status is ContractStatus.TERMINATED:
                return self._information("Résilié le", self._event_date(contract.id, "TERMINATED"))
            if contract.status is ContractStatus.EXPIRED:
                return self._information("Expiré le", self._event_date(contract.id, "EXPIRED"))
            return ContractOperationalSignal(ContractOperationalSignalKind.INFORMATION, "Abandonné", priority=4)
        if document_problem:
            return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Documents du contrat à vérifier", priority=1)
        if contract.status is ContractStatus.DRAFT:
            complete = self.review.business_data_complete(contract.id)
            return ContractOperationalSignal(
                ContractOperationalSignalKind.ACTION,
                "Finaliser le brouillon" if complete else "Compléter le brouillon", priority=2,
            )
        if contract.status is ContractStatus.TO_SIGN:
            threshold = self.alert_settings.get().signature_followup_days if self.alert_settings else None
            if threshold is not None:
                revisions = self.lifecycle.revisions(contract.id)
                if revisions:
                    try:
                        generated = datetime.fromisoformat(revisions[0].generated_at_utc).date()
                    except ValueError:
                        generated = None
                    if generated is not None and self.lifecycle.date_provider.today() >= generated + timedelta(days=threshold):
                        due = generated + timedelta(days=threshold)
                        return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Relance signature à effectuer", due, 3)
            return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Signature à enregistrer", priority=3)
        if authority:
            copy_state = self.lifecycle.signed_copy_state(authority.document)
            if copy_state is SignedCopyState.NONE:
                return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Copie signée à archiver", priority=1)
            if copy_state in {SignedCopyState.MISSING, SignedCopyState.HASH_MISMATCH}:
                return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Copie signée à vérifier", priority=1)
        if projection is None:
            return ContractOperationalSignal(ContractOperationalSignalKind.NONE)
        today = self.lifecycle.date_provider.today()
        termination = projection.pending_termination
        if termination and termination.effective_date:
            try:
                effective = date.fromisoformat(termination.effective_date)
            except ValueError:
                effective = None
            if effective and effective > today:
                return self._information("Résiliation programmée le", effective, 1)
        if contract.status is ContractStatus.SIGNED and projection.authority.start_date > today:
            return self._information("Prise d’effet prévue le", projection.authority.start_date, 2)
        if contract.status is ContractStatus.ACTIVE and projection.renewal_mode == "TACIT" and projection.renewal_unresolved:
            return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Reconduction à confirmer", projection.next_attention_date, 4)
        if (contract.status is ContractStatus.ACTIVE and projection.renewal_mode == "MANUAL"
                and projection.next_attention_date and today >= projection.next_attention_date):
            return ContractOperationalSignal(ContractOperationalSignalKind.ACTION, "Renouvellement à préparer", projection.next_attention_date, 5)
        if projection.non_renewal_event:
            return self._information("Fin prévue le", projection.period.end, 3)
        return self._information("Échéance le", projection.period.end, 4)

    @staticmethod
    def _information(prefix: str, value: date | None, priority: int = 4) -> ContractOperationalSignal:
        label = prefix if value is None else f"{prefix} {value.strftime('%d/%m/%Y')}"
        return ContractOperationalSignal(ContractOperationalSignalKind.INFORMATION, label, value, priority)
