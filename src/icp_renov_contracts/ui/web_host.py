from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..services import (
    ContractOperationalSignalKind,
    ContractRegisterFilter,
    ContractRegisterService,
    RealBackupSummaryProvider,
)


class UiBridge(QObject):
    """The deliberately small, presentation-only WebChannel contract."""

    stateChanged = Signal("QVariant")

    def __init__(self, context, navigate: Callable[[str], None]) -> None:
        super().__init__()
        self.context = context
        self.navigate_legacy = navigate
        self.filter = ContractRegisterFilter.ALL
        self.search = ""
        self.register = ContractRegisterService(
            context.contracts, context.review, context.lifecycle, context.workspace_service,
            RealBackupSummaryProvider(context.backup, context.alerts), context.alerts,
        )

    def snapshot(self) -> dict:
        rows = self.register.rows()
        visible = self.register.filter_rows(rows, self.filter, self.search)
        summary = self.register.backup_summary()
        return {
            "action_count": self.register.action_count(rows),
            "search": self.search,
            "backup": summary.label,
            "filters": [
                {"id": selected.value, "label": label, "active": selected is self.filter}
                for selected, label in (
                    (ContractRegisterFilter.ALL, "Tous"),
                    (ContractRegisterFilter.ACTIONS, "Actions à traiter"),
                    (ContractRegisterFilter.DRAFTS, "Brouillons"),
                    (ContractRegisterFilter.ACTIVE, "Actifs"),
                    (ContractRegisterFilter.TERMINAL, "Terminés"),
                )
            ],
            "rows": [
                {
                    "id": row.contract_id,
                    "number": row.number_label,
                    "updated": "",
                    "client": row.client_name,
                    "site": row.site_label,
                    "status": row.status_label,
                    "status_code": row.status.value,
                    "deadline": row.due_date.strftime("%d/%m/%Y") if row.due_date else "—",
                    "signal": row.signal.label,
                    "needs_action": row.signal.kind is ContractOperationalSignalKind.ACTION,
                    "document": row.document_lines[0] if row.document_lines else "Aucun document",
                }
                for row in visible
            ],
        }

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit(self.snapshot())

    @Slot(str)
    def setFilter(self, value: str) -> None:
        self.filter = ContractRegisterFilter(value)
        self.refresh()

    @Slot(str)
    def setSearch(self, value: str) -> None:
        self.search = value
        self.refresh()

    @Slot(str)
    def openContract(self, contract_id: str) -> None:
        self.navigate_legacy(f"OPEN:{contract_id}")

    @Slot()
    def createContract(self) -> None:
        self.navigate_legacy("NEW")

    @Slot()
    def saveBackup(self) -> None:
        self.context.backup.create_now()
        self.refresh()

    @Slot(str)
    def navigate(self, destination: str) -> None:
        if destination in {"CLIENTS", "SETTINGS"}:
            self.navigate_legacy(destination)


class TrustedLocalPage(QWebEnginePage):
    """Allow only this packaged local web surface; popups and remote navigation are denied."""

    def __init__(self, assets_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.assets_root = assets_root.resolve()

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:
        if not url.isLocalFile():
            return False
        try:
            url.toLocalFile() and Path(url.toLocalFile()).resolve().relative_to(self.assets_root)
        except ValueError:
            return False
        return True

    def createWindow(self, window_type):  # noqa: N802 - Qt API spelling
        return None


class WebUiHost(QWebEngineView):
    def __init__(self, context, navigate: Callable[[str], None]) -> None:
        super().__init__()
        self.assets_root = Path(__file__).parent.parent / "ui_web"
        self._showing_load_error = False
        self.bridge = UiBridge(context, navigate)
        page = TrustedLocalPage(self.assets_root, self)
        self.setPage(page)
        self.channel = QWebChannel(page)
        self.channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self.channel)
        page.loadFinished.connect(self._load_finished)
        self.load(QUrl.fromLocalFile(str(self.assets_root / "index.html")))

    def _load_finished(self, success: bool) -> None:
        if success or self._showing_load_error:
            return
        self._showing_load_error = True
        self.setHtml(
            "<main style='font-family:Segoe UI,Arial;padding:32px'>"
            "<h1>Contrats indisponibles</h1>"
            "<p>La surface locale Contrats ne peut pas être chargée. Redémarrez l’application.</p>"
            "</main>"
        )
