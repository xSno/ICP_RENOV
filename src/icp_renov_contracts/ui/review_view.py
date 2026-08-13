from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..domain import ReviewState
from ..services import ReviewService


class ReviewView(QWidget):
    def __init__(self, service: ReviewService, navigate: Callable[[int], None]) -> None:
        super().__init__(); self.setObjectName("reviewStep")
        self.service = service; self.navigate = navigate; self.contract_id: str | None = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 8, 0); root.setSpacing(14)
        title = QLabel("Revue avant génération"); title.setObjectName("screenTitle"); root.addWidget(title)
        intro = QLabel("Vérifiez les informations du brouillon et la disponibilité technique de la génération.")
        intro.setObjectName("screenDescription"); intro.setWordWrap(True); root.addWidget(intro)
        self.overall = QLabel(); self.overall.setWordWrap(True); root.addWidget(self.overall)
        self.blocks_host = QWidget(); self.blocks_layout = QVBoxLayout(self.blocks_host)
        self.blocks_layout.setContentsMargins(0, 0, 0, 0); self.blocks_layout.setSpacing(10); root.addWidget(self.blocks_host)
        generation = QFrame(); generation.setObjectName("generationAvailability"); generation_layout = QVBoxLayout(generation)
        heading = QLabel("Disponibilité de la génération"); heading.setObjectName("sectionTitle"); generation_layout.addWidget(heading)
        self.checks_host = QWidget(); self.checks_layout = QVBoxLayout(self.checks_host); self.checks_layout.setContentsMargins(0, 0, 0, 0)
        generation_layout.addWidget(self.checks_host); root.addWidget(generation)
        self.distinction = QLabel(); self.distinction.setWordWrap(True); root.addWidget(self.distinction)
        number = QLabel("Numéro attribué après génération réussie"); number.setObjectName("screenDescription"); root.addWidget(number)
        self.generate_button = QPushButton("Générer le DOCX et le PDF")
        self.generate_button.setObjectName("primaryButton"); self.generate_button.setEnabled(False)
        root.addWidget(self.generate_button); root.addStretch(1)
        self.block_cards: list[QFrame] = []; self.modify_buttons: list[QPushButton] = []
        self.generation_checks: list[QFrame] = []

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def load(self, contract_id: str) -> None:
        self.contract_id = contract_id; result = self.service.review(contract_id)
        self._clear(self.blocks_layout); self.block_cards = []; self.modify_buttons = []
        for block in result.blocks:
            card = QFrame(); card.setObjectName("reviewBlock"); card.setProperty("state", block.state.value)
            layout = QVBoxLayout(card); header = QHBoxLayout()
            state = QLabel("✓ Valide" if block.state is ReviewState.VALID else "! À corriger")
            state.setObjectName("reviewState"); title = QLabel(block.title); title.setObjectName("sectionTitle")
            modify = QPushButton("Modifier"); modify.setObjectName("secondaryButton")
            modify.clicked.connect(lambda checked=False, step=block.target_step: self.navigate(step))
            header.addWidget(state); header.addWidget(title); header.addStretch(1); header.addWidget(modify); layout.addLayout(header)
            summary = QLabel(block.summary); summary.setWordWrap(True); layout.addWidget(summary)
            for issue in block.issues:
                label = QLabel(f"• {issue.message}"); label.setObjectName("reviewIssue"); label.setWordWrap(True); layout.addWidget(label)
            self.blocks_layout.addWidget(card); self.block_cards.append(card); self.modify_buttons.append(modify)
        self.overall.setObjectName("successFeedback" if result.data_complete else "formError")
        self.overall.setText("Toutes les informations nécessaires sont complètes" if result.data_complete
                             else "Informations à compléter avant génération")
        self.overall.style().unpolish(self.overall); self.overall.style().polish(self.overall)
        self._clear(self.checks_layout); self.generation_checks = []
        for check in result.generation.checks:
            row = QFrame(); row.setObjectName("generationCheck"); line = QHBoxLayout(row)
            label = QLabel(check.label); value = QLabel(("✓ " if check.available else "! ") + check.detail)
            value.setObjectName("generationCheckState"); line.addWidget(label); line.addStretch(1); line.addWidget(value)
            self.checks_layout.addWidget(row); self.generation_checks.append(row)
        if result.data_complete and not result.generation.generation_available:
            self.distinction.setText("Le contrat est complet, mais la génération est indisponible.")
        elif not result.data_complete:
            self.distinction.setText("Complétez les informations du contrat avant une future génération.")
        else: self.distinction.setText("Les informations et capacités techniques sont disponibles.")
