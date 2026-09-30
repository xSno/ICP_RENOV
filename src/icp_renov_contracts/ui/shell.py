from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from .styles import SPACING
from .surfaces import build_surfaces
from ..services import AlertSettingsService, BackupService, BackupSummaryProvider, CompanySettingsService, ContractLifecycleService, ContractService, DocumentGenerationService, InterventionSheetGenerationService, MasterDataService, NumberingSettingsService, RealBackupSummaryProvider, RestoreService, ReviewService, TemplateCatalogService


NAVIGATION_LABELS = ("Contrats", "Clients & installations", "Paramètres")


_SIDEBAR_ICON_PATHS = {
    "contracts": '<path d="M5 3h8l3 3v11H5z"/><path d="M13 3v4h4M8 11h5M8 14h5"/>',
    "clients": '<circle cx="9" cy="8" r="3"/><path d="M3 17c1-3 3-5 6-5s5 2 6 5M14 9c2 0 3-1 3-3s-1-3-3-3M16 13c2 1 3 2 3 4"/>',
    "settings": '<circle cx="10" cy="10" r="3"/><path d="M10 2v2m0 12v2M2 10h2m12 0h2M4.3 4.3l1.4 1.4m8.6 8.6l1.4 1.4m0-11.4l-1.4 1.4m-8.6 8.6l-1.4 1.4"/>',
    "local": '<rect x="3" y="4" width="14" height="10" rx="1"/><path d="M7 18h6M10 14v4"/>',
    "backup": '<ellipse cx="10" cy="5" rx="6" ry="2.5"/><path d="M4 5v7c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5m-12 3.5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5"/>',
    "air": '<path d="M4 6h12M4 10h9M4 14h6" stroke-width="2"/>',
}


def _sidebar_icon(name: str, color: str = "#DCE8ED") -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{_SIDEBAR_ICON_PATHS[name]}</svg>'
    pixmap = QPixmap(); pixmap.loadFromData(svg.encode("utf-8"), "SVG")
    return QIcon(pixmap)


def _sidebar_status_row(icon_name: str, text: str, object_name: str) -> tuple[QWidget, QLabel]:
    row = QWidget(); row.setObjectName("sidebarStatusRow")
    layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(SPACING["sm"])
    icon = QLabel(); icon.setObjectName("sidebarStatusIcon"); icon.setPixmap(_sidebar_icon(icon_name).pixmap(16, 16))
    label = QLabel(text); label.setObjectName(object_name); label.setWordWrap(True)
    layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop); layout.addWidget(label, 1)
    return row, label


def _sidebar_status_block(icon_name: str, label_text: str, detail_text: str, detail_object_name: str, object_name: str) -> tuple[QWidget, QLabel]:
    block = QWidget(); block.setObjectName(object_name)
    layout = QHBoxLayout(block); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(SPACING["sm"])
    icon = QLabel(); icon.setObjectName("sidebarStatusIcon"); icon.setPixmap(_sidebar_icon(icon_name).pixmap(16, 16))
    words = QWidget(); words_layout = QVBoxLayout(words); words_layout.setContentsMargins(0, 0, 0, 0); words_layout.setSpacing(0)
    label = QLabel(label_text); label.setObjectName("sidebarStatusLabel")
    detail = QLabel(detail_text); detail.setObjectName(detail_object_name); detail.setWordWrap(True)
    words_layout.addWidget(label); words_layout.addWidget(detail)
    layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop); layout.addWidget(words, 1)
    return block, detail


class ApplicationShell(QWidget):
    def __init__(self, master_data: MasterDataService, contracts: ContractService, review: ReviewService,
                 generation: DocumentGenerationService | None = None,
                 lifecycle: ContractLifecycleService | None = None,
                 interventions: InterventionSheetGenerationService | None = None,
                 company: CompanySettingsService | None = None,
                 template_catalog: TemplateCatalogService | None = None,
                 numbering: NumberingSettingsService | None = None,
                 alerts: AlertSettingsService | None = None, backup: BackupService | None = None,
                 restore: RestoreService | None = None, diagnostic=None) -> None:
        super().__init__()
        self.setObjectName("applicationRoot")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(232)
        self.sidebar = sidebar
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
        sidebar_layout.setSpacing(SPACING["sm"])

        brand_row = QWidget()
        brand_row.setObjectName("sidebarBrandRow")
        brand_layout = QHBoxLayout(brand_row)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(SPACING["sm"])
        brand_mark = QLabel()
        brand_mark.setObjectName("sidebarBrandMark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setFixedSize(36, 36)
        brand_mark.setPixmap(_sidebar_icon("air", "#FFFFFF").pixmap(20, 20))
        brand_words = QWidget()
        brand_words_layout = QVBoxLayout(brand_words)
        brand_words_layout.setContentsMargins(0, 0, 0, 0)
        brand_words_layout.setSpacing(0)
        brand = QLabel("ICP Renov")
        brand.setObjectName("brand")
        subtitle = QLabel("Contrats d’entretien")
        subtitle.setObjectName("brandSubtitle")
        brand_words_layout.addWidget(brand)
        brand_words_layout.addWidget(subtitle)
        brand_layout.addWidget(brand_mark)
        brand_layout.addWidget(brand_words, 1)
        sidebar_layout.addWidget(brand_row)
        sidebar_layout.addSpacing(SPACING["lg"])

        self._buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for label in NAVIGATION_LABELS:
            button = QPushButton(label.replace("&", "&&"))
            button.setObjectName("navButton")
            button.setIcon(_sidebar_icon({"Contrats": "contracts", "Clients & installations": "clients", "Paramètres": "settings"}[label]))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, destination=label: self.navigate(destination))
            group.addButton(button)
            self._buttons[label] = button
            sidebar_layout.addWidget(button)
        sidebar_layout.addStretch(1)
        local_block, local_indicator = _sidebar_status_block(
            "local", "Mode local", "Données conservées uniquement sur ce poste.", "localApplicationIndicator", "sidebarLocalBlock"
        )
        self.local_indicator = local_indicator
        sidebar_layout.addWidget(local_block)
        status_area = QWidget(); status_area.setObjectName("sidebarStatusArea")
        status_layout = QVBoxLayout(status_area); status_layout.setContentsMargins(0, 0, 0, 0); status_layout.setSpacing(SPACING["xs"])
        backup_row, backup_indicator = _sidebar_status_block("backup", "Sauvegarde", self._backup_summary_label(backup, alerts), "backupStatusIndicator", "sidebarBackupBlock")
        self.backup_indicator = backup_indicator
        status_layout.addWidget(backup_row)
        sidebar_layout.addWidget(status_area)

        self._external_landings: dict[str, Callable[[], None]] = {}
        self.stack = QStackedWidget()
        self._surfaces = build_surfaces(master_data, contracts, review, generation, lifecycle, interventions, company, template_catalog, numbering, alerts, backup, restore, diagnostic)
        self._indices: dict[str, int] = {}
        for label in NAVIGATION_LABELS:
            self._indices[label] = self.stack.addWidget(self._surfaces[label])

        content = QWidget()
        content.setObjectName("contentRoot")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(SPACING["xl"], SPACING["xl"], SPACING["xl"], SPACING["xl"])
        content_layout.addWidget(self.stack)

        root.addWidget(sidebar)
        root.addWidget(content, 1)
        self.navigate("Contrats")

    @staticmethod
    def _backup_summary_label(backup: BackupService | None, alerts: AlertSettingsService | None) -> str:
        if backup is None:
            return BackupSummaryProvider().summary().label
        return RealBackupSummaryProvider(backup, alerts).summary().label

    def set_contracts_landing(self, callback: Callable[[], None]) -> None:
        """Keep the legacy contract workflow available without making it a landing screen."""
        self.set_external_landing("Contrats", callback)

    def set_external_landing(self, destination: str, callback: Callable[[], None]) -> None:
        if destination not in NAVIGATION_LABELS:
            raise ValueError(f"Unknown navigation destination: {destination}")
        self._external_landings[destination] = callback

    @property
    def navigation_labels(self) -> tuple[str, ...]:
        return tuple(self._buttons)

    @property
    def current_surface(self) -> str:
        return self.stack.currentWidget().title

    def surface(self, destination: str) -> QWidget:
        return self._surfaces[destination]

    def navigate(self, destination: str, *, workflow: bool = False) -> None:
        if destination not in self._indices:
            raise ValueError(f"Unknown navigation destination: {destination}")
        if not workflow and destination in self._external_landings:
            self._external_landings[destination]()
            return
        self.stack.setCurrentIndex(self._indices[destination])
        for label, button in self._buttons.items():
            active = label == destination
            button.setChecked(active)
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
