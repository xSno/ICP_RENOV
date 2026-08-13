from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..domain import ReviewState
from ..documents.validation import DocumentGenerationError
from ..services import DocumentGenerationService, ReviewService


class GenerationConfirmationDialog(QDialog):
    def __init__(self, preview: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.setObjectName("generationConfirmation"); self.setWindowTitle("Confirmer la génération")
        root = QVBoxLayout(self); title = QLabel("Créer la première révision officielle ?"); title.setObjectName("sectionTitle"); root.addWidget(title)
        text = QLabel("Les données du contrat seront figées. Le DOCX et le PDF seront créés. Le numéro officiel sera attribué seulement après leur création complète. La première génération réussie créera R01 et placera le contrat À signer. En cas d’échec, aucun numéro ni aucune révision ne sera créé.")
        text.setWordWrap(True); root.addWidget(text)
        self.preview_label = QLabel(f"Numéro prévu : {preview}") if preview else QLabel("")
        self.preview_label.setVisible(bool(preview)); root.addWidget(self.preview_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler"); buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Générer")
        buttons.rejected.connect(self.reject); buttons.accepted.connect(self.accept); root.addWidget(buttons)


class ReviewView(QWidget):
    def __init__(self, service: ReviewService, navigate: Callable[[int], None],
                 generation: DocumentGenerationService | None = None,
                 finished: Callable[[str, bool], None] | None = None) -> None:
        super().__init__(); self.setObjectName("reviewStep")
        self.service = service; self.navigate = navigate; self.generation = generation; self.finished = finished
        self.contract_id: str | None = None
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
        self.generate_button.clicked.connect(self.confirm_generation)
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
        self.generate_button.setEnabled(bool(result.generation_available and self.generation and self.generation.available(contract_id)))
        contract = self.service.contracts.get(contract_id)
        locked = contract.status.value == "TO_SIGN"
        for button in self.modify_buttons: button.setEnabled(not locked)
        if locked:
            self.generate_button.setEnabled(False); self.distinction.setText("R01 est créée. Le contrat est verrouillé et À signer.")

    def confirm_generation(self) -> None:
        if not self.contract_id or not self.generation or not self.generation.available(self.contract_id): return
        dialog = GenerationConfirmationDialog(self.generation.preview_number(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            result = self.generation.generate(self.contract_id)
        except DocumentGenerationError as error:
            if self.finished: self.finished(error.user_message + " Les données du contrat sont conservées.", True)
            return
        if self.finished:
            self.finished(f"{result.contract_number} · R01 · DOCX et PDF créés · À signer", False)
