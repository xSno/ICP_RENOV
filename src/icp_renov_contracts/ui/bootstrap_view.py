from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .styles import SPACING


class BootstrapView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("applicationRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(96, 72, 96, 72)

        surface = QWidget()
        surface.setObjectName("contentSurface")
        content = QVBoxLayout(surface)
        content.setContentsMargins(48, 48, 48, 48)
        content.setSpacing(SPACING["lg"])

        title = QLabel("Bienvenue dans ICP Renov")
        title.setObjectName("screenTitle")
        description = QLabel(
            "Aucun dossier de travail actif n’est configuré sur ce poste. "
            "Choisissez la direction de première utilisation."
        )
        description.setObjectName("screenDescription")
        description.setWordWrap(True)

        actions = QHBoxLayout()
        actions.setSpacing(SPACING["md"])
        self.configure_button = QPushButton("Configurer ce poste")
        self.configure_button.setObjectName("primaryButton")
        self.restore_button = QPushButton("Restaurer une sauvegarde existante")
        self.restore_button.setObjectName("secondaryButton")
        for button in (self.configure_button, self.restore_button):
            button.setToolTip("Cette action sera mise en œuvre dans une étape ultérieure.")
        actions.addWidget(self.configure_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)

        content.addWidget(title)
        content.addWidget(description)
        content.addLayout(actions)
        outer.addWidget(surface)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

