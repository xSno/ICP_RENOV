from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .styles import SPACING
from .clients_view import ClientsInstallationsView
from .contracts_view import ContractsView
from ..services import ContractService, MasterDataService


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


def build_surfaces(master_data: MasterDataService, contracts: ContractService) -> dict[str, QWidget]:
    return {
        "Contrats": ContractsView(contracts),
        "Clients & installations": ClientsInstallationsView(master_data),
        "Paramètres": PlaceholderSurface(
            "Paramètres",
            "Configurez ultérieurement la société, les modèles, la numérotation et le stockage local.",
        ),
    }
