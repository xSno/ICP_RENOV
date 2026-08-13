from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .styles import SPACING


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


def build_surfaces() -> dict[str, PlaceholderSurface]:
    return {
        "Contrats": PlaceholderSurface(
            "Contrats",
            "Créez, retrouvez et suivez les contrats d’entretien depuis cet accueil opérationnel.",
        ),
        "Clients & installations": PlaceholderSurface(
            "Clients & installations",
            "Gérez ici les fiches clients, leurs sites et leurs équipements associés.",
        ),
        "Paramètres": PlaceholderSurface(
            "Paramètres",
            "Configurez ultérieurement la société, les modèles, la numérotation et le stockage local.",
        ),
    }

