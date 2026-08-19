from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .styles import SPACING
from .clients_view import ClientsInstallationsView
from .contracts_view import ContractsView
from .settings_view import CompanySettingsView
from ..services import AlertSettingsService, BackupService, CompanySettingsService, ContractLifecycleService, ContractService, DocumentGenerationService, InterventionSheetGenerationService, MasterDataService, NumberingSettingsService, RealBackupSummaryProvider, RestoreService, ReviewService, TemplateCatalogService


class PlaceholderSurface(QWidget):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.title = title
        self.setObjectName("contentSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["xl"], SPACING["xl"], SPACING["xl"], SPACING["xl"])
        layout.setSpacing(SPACING["md"])

        heading = QLabel(title)
        heading.setObjectName("screenTitle")
        description_label = QLabel(description)
        description_label.setObjectName("screenDescription")
        description_label.setWordWrap(True)
        description_label.setMaximumWidth(720)

        layout.addWidget(heading)
        layout.addWidget(description_label)
        layout.addStretch(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)


def build_surfaces(master_data: MasterDataService, contracts: ContractService, review: ReviewService,
                   generation: DocumentGenerationService | None = None,
                   lifecycle: ContractLifecycleService | None = None,
                   interventions: InterventionSheetGenerationService | None = None,
                   company: CompanySettingsService | None = None,
                   template_catalog: TemplateCatalogService | None = None,
                   numbering: NumberingSettingsService | None = None,
                   alerts: AlertSettingsService | None = None, backup: BackupService | None = None,
                   restore: RestoreService | None = None, diagnostic=None) -> dict[str, QWidget]:
    settings = CompanySettingsView(company, review.workspace_service, template_catalog, numbering, alerts, backup, restore, diagnostic) if company else PlaceholderSurface(
        "Paramètres", "Les paramètres société ne sont pas disponibles dans ce contexte.")
    return {
        "Contrats": ContractsView(contracts, review, generation, lifecycle,interventions, backup_provider=RealBackupSummaryProvider(backup, alerts) if backup else None, alert_settings=alerts, open_diagnostic=getattr(settings, "open_diagnostic", None)),
        "Clients & installations": ClientsInstallationsView(master_data),
        "Paramètres": settings,
    }
