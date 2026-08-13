from __future__ import annotations

from PySide6.QtCore import Qt
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
from ..services import ContractService, MasterDataService, ReviewService


NAVIGATION_LABELS = ("Contrats", "Clients & installations", "Paramètres")


class ApplicationShell(QWidget):
    def __init__(self, master_data: MasterDataService, contracts: ContractService, review: ReviewService) -> None:
        super().__init__()
        self.setObjectName("applicationRoot")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(SPACING["lg"], SPACING["xl"], SPACING["lg"], SPACING["lg"])
        sidebar_layout.setSpacing(SPACING["sm"])

        brand = QLabel("ICP Renov")
        brand.setObjectName("brand")
        subtitle = QLabel("Contrats d’entretien")
        subtitle.setObjectName("brandSubtitle")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(SPACING["xl"])

        self._buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for label in NAVIGATION_LABELS:
            button = QPushButton(label.replace("&", "&&"))
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, destination=label: self.navigate(destination))
            group.addButton(button)
            self._buttons[label] = button
            sidebar_layout.addWidget(button)
        sidebar_layout.addStretch(1)

        self.stack = QStackedWidget()
        self._surfaces = build_surfaces(master_data, contracts, review)
        self._indices: dict[str, int] = {}
        for label in NAVIGATION_LABELS:
            self._indices[label] = self.stack.addWidget(self._surfaces[label])

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(SPACING["xl"], SPACING["xl"], SPACING["xl"], SPACING["xl"])
        content_layout.addWidget(self.stack)

        root.addWidget(sidebar)
        root.addWidget(content, 1)
        self.navigate("Contrats")

    @property
    def navigation_labels(self) -> tuple[str, ...]:
        return tuple(self._buttons)

    @property
    def current_surface(self) -> str:
        return self.stack.currentWidget().title

    def surface(self, destination: str) -> QWidget:
        return self._surfaces[destination]

    def navigate(self, destination: str) -> None:
        if destination not in self._indices:
            raise ValueError(f"Unknown navigation destination: {destination}")
        self.stack.setCurrentIndex(self._indices[destination])
        for label, button in self._buttons.items():
            active = label == destination
            button.setChecked(active)
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
