from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..documents.registry import FIELDS
from ..domain import (
    ContextAuthorization, EXTERNAL_GATE_CODES, EXTERNAL_GATE_LABELS, ExternalGateStatus,
    ReviewEvidenceStatus, TemplateVersionStatus,
)
from ..errors import ContractValidationError
from ..services import FileOpener, TemplateCatalogService
from .styles import SPACING


STATUS_LABELS = {
    TemplateVersionStatus.TO_VALIDATE: "À valider",
    TemplateVersionStatus.AVAILABLE: "Disponible",
    TemplateVersionStatus.ARCHIVED: "Archivé",
}
REGIME_LABELS = {
    "CONSUMER": "Consommateur",
    "NON_PROFESSIONAL": "Non-professionnel",
    "PROFESSIONAL": "Professionnel",
}
DOCUMENT_LABELS = {"CONTRACT": "Contrat d’entretien", "INTERVENTION_SHEET": "Fiche d’intervention"}
CONTRACT_TYPE_LABELS = {"CLIMATE_MAINTENANCE": "Entretien de climatisation"}
COMPANY_FIELD_LABELS = {
    "ape_code": "Code APE / NAF",
    "complaints_contact": "Contact réclamations",
    "correspondence_address": "Adresse de correspondance",
    "display_name": "Nom d’affichage",
    "email": "E-mail",
    "insurance_policy_number": "N° de police",
    "insurance_scope": "Périmètre / couverture",
    "insurance_summary": "Synthèse de l’assurance",
    "insurance_valid_until": "Assurance valable jusqu’au",
    "insurer_name": "Assureur",
    "legal_form": "Forme juridique",
    "legal_name": "Raison sociale",
    "logo": "Logo",
    "mediator_address": "Adresse du médiateur",
    "mediator_name": "Médiateur",
    "mediator_website": "Site du médiateur",
    "phone": "Téléphone",
    "privacy_contact": "Contact protection des données",
    "refrigerant_capacity_body": "Organisme fluides",
    "refrigerant_capacity_number": "N° d’attestation / capacité",
    "refrigerant_capacity_summary": "Synthèse de la capacité fluides",
    "refrigerant_capacity_until": "Capacité fluides valable jusqu’au",
    "refrigerant_partner_name": "Partenaire fluides",
    "registered_address": "Adresse du siège",
    "registration": "Immatriculation",
    "registration_identifiers_summary": "Synthèse des identifiants d’immatriculation",
    "registration_summary": "Immatriculation / registre",
    "share_capital": "Capital social",
    "signatory_name": "Nom du signataire par défaut",
    "signatory_role": "Fonction / qualité du signataire",
    "siren": "SIREN",
    "siren_siret": "SIREN / SIRET",
    "siret": "SIRET",
    "trade_name": "Nom commercial",
    "vat_number": "N° TVA intracommunautaire",
    "withdrawal_contact": "Contact rétractation",
}
GATE_STATUS_LABELS = {
    ExternalGateStatus.TO_REVIEW: "À examiner",
    ExternalGateStatus.CONFIRMED: "Confirmé",
    ExternalGateStatus.REJECTED: "Rejeté",
    ExternalGateStatus.NOT_APPLICABLE: "Non applicable",
}


def _format_french_timestamp(value: str | None, *, date_only: bool = False) -> str:
    if not value:
        return "Jamais"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%d/%m/%Y" if date_only else "%d/%m/%Y %H:%M")


def _contract_type_label(code: str) -> str:
    if not code:
        return "Non applicable"
    label = CONTRACT_TYPE_LABELS.get(code, "Type de contrat")
    return f"{label} ({code})"


class AddModelDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent); self.setWindowTitle("Ajouter un modèle"); self.setObjectName("addModelDialog")
        root = QVBoxLayout(self); form = QFormLayout()
        self.name = QLineEdit(); self.name.setObjectName("modelFunctionalName")
        self.kind = QComboBox(); self.kind.setObjectName("modelDocumentKind")
        self.kind.addItem("Contrat d’entretien", "CONTRACT"); self.kind.addItem("Fiche d’intervention", "INTERVENTION_SHEET")
        self.version = QLineEdit("1"); self.version.setObjectName("modelInitialVersion")
        self.version.setProperty("required", True)
        self.source = QLineEdit(); self.source.setObjectName("modelInitialSource"); self.source.setReadOnly(True)
        choose = QPushButton("Choisir le DOCX"); choose.clicked.connect(self._choose)
        source_row = QHBoxLayout(); source_row.addWidget(self.source, 1); source_row.addWidget(choose)
        form.addRow("Nom fonctionnel", self.name); form.addRow("Type de document", self.kind)
        form.addRow("Version initiale", self.version); form.addRow("DOCX initial", source_row)
        root.addLayout(form)
        target = QFrame(); target.setObjectName("companyGroup"); target_layout = QVBoxLayout(target)
        target_layout.addWidget(QLabel("Régimes visés · intention de travail, non confirmée"))
        self.targets: dict[str, QCheckBox] = {}
        for code, label in REGIME_LABELS.items():
            box = QCheckBox(label); box.setObjectName(f"target_{code}"); self.targets[code] = box; target_layout.addWidget(box)
        root.addWidget(target); self.target_frame = target
        self.kind.currentIndexChanged.connect(lambda: target.setVisible(self.kind.currentData() == "CONTRACT"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ajouter le modèle")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _choose(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choisir le DOCX initial", "", "Documents Word (*.docx)")
        if filename: self.source.setText(filename)

    def values(self) -> tuple[str, str, str, Path, tuple[str, ...]]:
        return (self.name.text(), self.kind.currentData(), self.version.text(), Path(self.source.text()),
                tuple(code for code, box in self.targets.items() if box.isChecked()) if self.kind.currentData() == "CONTRACT" else ())


class NewVersionDialog(QDialog):
    def __init__(self, family_name: str, parent=None) -> None:
        super().__init__(parent); self.setWindowTitle("Nouvelle version"); self.setObjectName("newVersionDialog")
        root = QVBoxLayout(self); root.addWidget(QLabel(f"Famille source : {family_name}"))
        form = QFormLayout(); self.version = QLineEdit(); self.version.setObjectName("newVersionValue")
        self.source = QLineEdit(); self.source.setObjectName("newVersionSource"); self.source.setReadOnly(True)
        choose = QPushButton("Choisir le nouveau DOCX"); choose.clicked.connect(self._choose)
        row = QHBoxLayout(); row.addWidget(self.source, 1); row.addWidget(choose)
        form.addRow("Nouvelle version", self.version); form.addRow("Nouveau DOCX", row); root.addLayout(form)
        note = QLabel("La nouvelle version démarre À valider. Les confirmations et preuves de la version précédente ne sont pas reprises.")
        note.setWordWrap(True); note.setObjectName("screenDescription"); root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Créer la version")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _choose(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choisir le nouveau DOCX", "", "Documents Word (*.docx)")
        if filename: self.source.setText(filename)


class ModelsSettingsPage(QWidget):
    COLUMNS = ("Modèle", "Régime confirmé", "Version", "État", "Dernière utilisation", "Actions")

    def __init__(self, service: TemplateCatalogService, opener: FileOpener | None = None) -> None:
        super().__init__(); self.service = service; self.opener = opener or FileOpener(); self.current_version_id: str | None = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(); self.overview = self._overview(); self.stack.addWidget(self.overview); root.addWidget(self.stack)
        self.refresh()

    def _overview(self) -> QWidget:
        page = QWidget(); root = QVBoxLayout(page); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(SPACING["md"])
        top = QHBoxLayout(); title_box = QVBoxLayout(); title = QLabel("Modèles"); title.setObjectName("screenTitle")
        description = QLabel("Catalogue gouverné des familles et versions de documents."); description.setObjectName("screenDescription")
        title_box.addWidget(title); title_box.addWidget(description); top.addLayout(title_box); top.addStretch(1)
        self.new_version_button = QPushButton("Nouvelle version"); self.new_version_button.setObjectName("secondaryButton"); self.new_version_button.clicked.connect(self._new_version)
        self.add_button = QPushButton("Ajouter un modèle"); self.add_button.setObjectName("primaryButton"); self.add_button.clicked.connect(self._add_model)
        top.addWidget(self.new_version_button); top.addWidget(self.add_button); root.addLayout(top)
        self.feedback = QLabel(""); self.feedback.setObjectName("formError"); self.feedback.setWordWrap(True); self.feedback.hide(); root.addWidget(self.feedback)
        self.empty = QLabel("Aucun modèle configuré"); self.empty.setObjectName("emptyState"); root.addWidget(self.empty)
        self.table = QTableWidget(0, len(self.COLUMNS)); self.table.setObjectName("modelsTable"); self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.verticalHeader().hide()
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for index in (0, 1, 4): header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        for index, width in ((2, 70), (3, 85), (5, 90)):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed); header.resizeSection(index, width)
        self.table.itemSelectionChanged.connect(self._selection); self.table.cellDoubleClicked.connect(lambda row, column: self.open_version(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)))
        root.addWidget(self.table, 1); return page

    def refresh(self) -> None:
        versions = self.service.list_all(); self.table.setRowCount(len(versions)); self.empty.setVisible(not versions); self.table.setVisible(bool(versions))
        for row, version in enumerate(versions):
            regimes = ("Non applicable" if version.document_kind == "INTERVENTION_SHEET" else
                       ", ".join(REGIME_LABELS[value] for value in version.allowed_client_regimes) or "Aucun régime confirmé")
            last_label = _format_french_timestamp(self.service.last_use(version.id))
            values = (version.template_name, regimes, version.version, STATUS_LABELS[version.status], last_label)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, version.id); self.table.setItem(row, column, item)
            actions = QWidget(); layout = QHBoxLayout(actions); layout.setContentsMargins(2, 2, 2, 2)
            open_button = QPushButton("Ouvrir"); open_button.setObjectName("secondaryButton"); open_button.clicked.connect(lambda checked=False, item=version.id: self.open_version(item))
            layout.addWidget(open_button); self.table.setCellWidget(row, 5, actions)
        self.table.resizeRowsToContents()
        self._selection()

    def _selection(self) -> None:
        row = self.table.currentRow(); self.current_version_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and self.table.item(row, 0) else None
        self.new_version_button.setEnabled(self.current_version_id is not None)

    def _add_model(self) -> None:
        dialog = AddModelDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try: self.service.add_model(*dialog.values()); self._message("Modèle ajouté à l’état À valider.", True); self.refresh()
        except Exception as exc: self._message(str(exc), False)

    def _new_version(self) -> None:
        if not self.current_version_id: return
        previous = self.service.get_version(self.current_version_id); dialog = NewVersionDialog(previous.template_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try: self.service.create_new_version(previous.id, dialog.version.text(), Path(dialog.source.text())); self._message("Nouvelle version créée à l’état À valider.", True); self.refresh()
        except Exception as exc: self._message(str(exc), False)

    def open_version(self, version_id: str) -> None:
        if self.stack.count() > 1:
            old = self.stack.widget(1); self.stack.removeWidget(old); old.deleteLater()
        detail = ModelVersionDetail(self.service, version_id, self._close_detail, self.opener)
        self.stack.addWidget(detail); self.stack.setCurrentWidget(detail)

    def _close_detail(self) -> None:
        self.refresh(); self.stack.setCurrentWidget(self.overview)

    def _message(self, text: str, success: bool) -> None:
        self.feedback.setText(text); self.feedback.setObjectName("successFeedback" if success else "formError"); self.feedback.show()


class ModelVersionDetail(QWidget):
    SECTION_TITLES = (
        "Identité & source", "Utilisation / régimes", "Données requises", "Blocs de contexte",
        "Contrôle de structure", "Test de génération", "Validation externe", "Conditions de mise à disposition",
    )

    def __init__(self, service: TemplateCatalogService, version_id: str, close_callback, opener: FileOpener) -> None:
        super().__init__(); self.service = service; self.version_id = version_id; self.close_callback = close_callback; self.opener = opener
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        back = QPushButton("Retour aux modèles"); back.setObjectName("secondaryButton"); back.clicked.connect(close_callback); root.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.feedback = QLabel(""); self.feedback.setWordWrap(True); self.feedback.hide(); root.addWidget(self.feedback)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); self.body = QWidget(); self.layout = QVBoxLayout(self.body); self.layout.setSpacing(SPACING["md"])
        scroll.setWidget(self.body); root.addWidget(scroll, 1); self.refresh()

    def refresh(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        version = self.service.get_version(self.version_id); record = self.service.validation_record(self.version_id)
        editable = version.status is TemplateVersionStatus.TO_VALIDATE and not self.service.repository.source_in_use(version.id)
        title = QLabel(f"{version.template_name} · {version.version}"); title.setObjectName("screenTitle"); self.layout.addWidget(title)
        badge = QLabel(STATUS_LABELS[version.status]); badge.setObjectName("archivedBadge" if version.status is TemplateVersionStatus.ARCHIVED else "screenDescription"); self.layout.addWidget(badge)
        identity, form = self._section("Identité & source", form=True)
        source_code, source_label = self.service.source_integrity(version.id)
        previous = self.service.get_version(version.previous_version_id).version if version.previous_version_id else "Aucune"
        for label, value in (("Famille", version.template_name), ("Type de document", DOCUMENT_LABELS[version.document_kind]),
                             ("Type de contrat", _contract_type_label(version.contract_type_code)), ("Version", version.version),
                             ("État", STATUS_LABELS[version.status]), ("Importé le", _format_french_timestamp(version.created_at_utc, date_only=True)),
                             ("Version précédente", previous), ("Fichier source", Path(version.source_relpath or "").name or "Absent"),
                             ("Intégrité source", source_label), ("Dernière validation", _format_french_timestamp(record.last_validation_at_utc)),
                             ("Dernière utilisation", _format_french_timestamp(self.service.last_use(version.id)))):
            form.addRow(label, QLabel(value))
        actions = QHBoxLayout(); open_source = QPushButton("Ouvrir le DOCX source"); open_source.setEnabled(source_code == "VALID"); open_source.clicked.connect(self._open_source); actions.addWidget(open_source)
        restore = QPushButton("Restaurer le fichier source"); restore.setVisible(source_code == "MISSING"); restore.clicked.connect(self._restore_source); actions.addWidget(restore); actions.addStretch(1); identity.layout().addLayout(actions)

        regimes = self._section("Utilisation / régimes")
        regimes.layout().addWidget(QLabel("Régimes visés · intention de travail"))
        target_row = QHBoxLayout(); self.target_boxes = {}
        for code, label in REGIME_LABELS.items():
            box = QCheckBox(label); box.setChecked(code in version.target_client_regimes); box.setEnabled(editable and version.document_kind == "CONTRACT"); self.target_boxes[code] = box; target_row.addWidget(box)
        regimes.layout().addLayout(target_row)
        confirmed = ("Non applicable" if version.document_kind == "INTERVENTION_SHEET" else ", ".join(REGIME_LABELS[item] for item in version.allowed_client_regimes) or "Aucun régime confirmé")
        regimes.layout().addWidget(QLabel(f"Régime confirmé : {confirmed}"))
        if editable and version.document_kind == "CONTRACT":
            save_targets = QPushButton("Enregistrer les régimes visés"); save_targets.clicked.connect(self._save_targets); regimes.layout().addWidget(save_targets)
            confirm_row = QHBoxLayout(); self.confirm_regime_combo = QComboBox()
            for code, label in REGIME_LABELS.items(): self.confirm_regime_combo.addItem(label, code)
            self.regime_reference = QLineEdit(); self.regime_reference.setPlaceholderText("Référence de confirmation")
            confirm_button = QPushButton("Confirmer ce régime"); confirm_button.clicked.connect(self._confirm_regime)
            confirm_row.addWidget(self.confirm_regime_combo); confirm_row.addWidget(self.regime_reference, 1); confirm_row.addWidget(confirm_button); regimes.layout().addLayout(confirm_row)

        required = self._section("Données requises")
        required.layout().addWidget(QLabel("La version définit la nécessité ; Paramètres > Société fournit la valeur actuelle."))
        self.company_boxes = {}
        company_grid = QFormLayout(); company = self.service.company_provider.get() if self.service.company_provider else {}
        prepared = __import__("icp_renov_contracts.documents.formatters", fromlist=["prepare_context"]).prepare_context({"company": company})["company"]
        for key in sorted(value.split(".", 1)[1] for value in FIELDS if value.startswith("company.")):
            box = QCheckBox("Requis"); box.setChecked(key in version.required_company_fields); box.setEnabled(editable); self.company_boxes[key] = box
            business_label = COMPANY_FIELD_LABELS.get(key, "Information société")
            company_grid.addRow(f"{business_label} (company.{key}) · {'présent' if prepared.get(key) not in (None, '') else 'manquant'}", box)
        required.layout().addLayout(company_grid)
        self.technician_required = QCheckBox("intervention.technician requis"); self.technician_required.setChecked("intervention.technician" in version.required_intervention_fields); self.technician_required.setVisible(version.document_kind == "INTERVENTION_SHEET"); self.technician_required.setEnabled(editable); required.layout().addWidget(self.technician_required)
        save_required = QPushButton("Enregistrer les données requises"); save_required.setVisible(editable); save_required.clicked.connect(self._save_required); required.layout().addWidget(save_required)

        context = self._section("Blocs de contexte")
        context.layout().addWidget(QLabel("Blocs sensibles fermés : rétractation, exécution anticipée, résiliation électronique."))
        summary = "; ".join(f"{item.regime}/{item.conclusion_mode}: {', '.join(item.blocks) or 'aucun'}" for item in version.validation.context_authorizations) or "Aucune autorisation sensible enregistrée"
        context.layout().addWidget(QLabel(summary))
        if version.document_kind == "CONTRACT":
            bounded = QFrame(); bounded.setObjectName("companyGroup"); bounded_layout = QVBoxLayout(bounded)
            bounded_layout.addWidget(QLabel("Autorisation explicite d’un contexte fermé"))
            row = QHBoxLayout(); self.context_regime = QComboBox()
            for code in version.allowed_client_regimes: self.context_regime.addItem(REGIME_LABELS[code], code)
            self.context_conclusion = QComboBox()
            for code, label in (("IN_PREMISES", "Dans les locaux"), ("OFF_PREMISES", "Hors établissement"), ("DISTANCE_EMAIL", "À distance par e-mail"), ("ONLINE_INTERFACE", "Interface en ligne"), ("OTHER_DISTANCE", "Autre distance")):
                self.context_conclusion.addItem(label, code)
            row.addWidget(self.context_regime); row.addWidget(self.context_conclusion); bounded_layout.addLayout(row)
            self.context_blocks = {}
            for code, label in (("BLOCK_WITHDRAWAL", "Rétractation"), ("BLOCK_EARLY_PERFORMANCE", "Exécution anticipée"), ("BLOCK_ELECTRONIC_TERMINATION", "Résiliation électronique")):
                box = QCheckBox(label); self.context_blocks[code] = box; bounded_layout.addWidget(box)
            save_row = QPushButton("Enregistrer ce contexte"); save_row.clicked.connect(self._save_context_row); bounded_layout.addWidget(save_row)
            for widget in (self.context_regime, self.context_conclusion, *self.context_blocks.values(), save_row): widget.setEnabled(editable and bool(version.allowed_client_regimes))
            context.layout().addWidget(bounded)
        self.context_status = QComboBox(); self.context_status.addItem("À examiner", ReviewEvidenceStatus.TO_REVIEW)
        self.context_status.addItem("Revue effectuée · aucun bloc sensible", ReviewEvidenceStatus.CONFIRMED)
        if version.document_kind == "INTERVENTION_SHEET": self.context_status.addItem("Non applicable", ReviewEvidenceStatus.NOT_APPLICABLE)
        index = self.context_status.findData(record.context_review_status); self.context_status.setCurrentIndex(max(index, 0)); self.context_status.setEnabled(editable)
        save_context = QPushButton("Enregistrer la revue du contexte"); save_context.setVisible(editable); save_context.clicked.connect(self._save_context); context.layout().addWidget(self.context_status); context.layout().addWidget(save_context)

        structure = self._section("Contrôle de structure")
        structure.layout().addWidget(QLabel("Conforme" if record.structure_status.value == "PASS" else "À corriger" if record.structure_status.value == "FAIL" else "Non contrôlée"))
        for issue in record.structure_issues: structure.layout().addWidget(QLabel(f"• {issue}"))
        control = QPushButton("Contrôler"); control.setEnabled(editable); control.clicked.connect(self._control); structure.layout().addWidget(control)

        test = self._section("Test de génération")
        test.layout().addWidget(QLabel("Test non officiel : aucun contrat, numéro, document métier, événement ou révision n’est créé."))
        test.layout().addWidget(QLabel(f"Résultat : {record.render_status.value} · Cas : {', '.join(record.render_cases) or 'aucun'}"))
        run = QPushButton("Tester la génération"); run.setEnabled(editable); run.clicked.connect(self._test); test.layout().addWidget(run)
        self.visual_status = QComboBox()
        for label, value in (("À contrôler visuellement", ReviewEvidenceStatus.TO_REVIEW), ("Confirmé", ReviewEvidenceStatus.CONFIRMED), ("Non requis", ReviewEvidenceStatus.NOT_APPLICABLE)): self.visual_status.addItem(label, value)
        self.visual_status.setCurrentIndex(max(self.visual_status.findData(record.visual_review_status), 0)); self.visual_status.setEnabled(editable)
        save_visual = QPushButton("Enregistrer le contrôle visuel"); save_visual.setVisible(editable); save_visual.clicked.connect(self._save_visual); test.layout().addWidget(self.visual_status); test.layout().addWidget(save_visual)

        external = self._section("Validation externe")
        external.layout().addWidget(QLabel("L’application enregistre les validations fournies ; elle ne valide pas le droit et ne certifie pas le contrat."))
        self.external_content_status = QComboBox()
        for status, label in GATE_STATUS_LABELS.items(): self.external_content_status.addItem(label, status)
        self.external_content_status.setCurrentIndex(max(self.external_content_status.findData(record.external_content_status), 0))
        self.validator = QLineEdit(record.external_validator); self.validation_date = QLineEdit(record.external_validation_date or "")
        self.scope = QLineEdit(record.external_scope); self.external_reference = QLineEdit(record.external_reference); self.reservations = QLineEdit(record.external_reservations)
        content_form = QFormLayout(); content_form.addRow("État global", self.external_content_status); content_form.addRow("Validateur / source", self.validator); content_form.addRow("Date", self.validation_date); content_form.addRow("Périmètre", self.scope); content_form.addRow("Référence", self.external_reference); content_form.addRow("Réserves", self.reservations); external.layout().addLayout(content_form)
        saved_gates = {item.code: item for item in record.external_gates}; self.gate_controls = {}
        gate_form = QFormLayout()
        for code in EXTERNAL_GATE_CODES:
            combo = QComboBox()
            for status, label in GATE_STATUS_LABELS.items(): combo.addItem(label, status)
            evidence = saved_gates.get(code)
            if evidence: combo.setCurrentIndex(max(combo.findData(evidence.status), 0))
            reference = QLineEdit(evidence.reference if evidence else ""); row = QHBoxLayout(); row.addWidget(combo); row.addWidget(reference, 1)
            gate_form.addRow(EXTERNAL_GATE_LABELS[code], row); self.gate_controls[code] = (combo, reference)
        external.layout().addLayout(gate_form)
        save_external = QPushButton("Enregistrer la validation externe"); save_external.setVisible(editable); save_external.clicked.connect(self._save_external); external.layout().addWidget(save_external)
        for widget in (self.external_content_status, self.validator, self.validation_date, self.scope, self.external_reference, self.reservations): widget.setEnabled(editable)
        for combo, reference in self.gate_controls.values(): combo.setEnabled(editable); reference.setEnabled(editable)

        availability = self._section("Conditions de mise à disposition")
        evaluation = self.service.evaluate_availability(version.id)
        for item in evaluation.items: availability.layout().addWidget(QLabel(f"{'✓' if item.passed else '•'} {item.label}" + (f" — {item.reason}" if not item.passed else "")))
        legal = QLabel("La mise à disposition enregistre les validations fournies. Elle ne constitue pas une certification juridique du contrat."); legal.setWordWrap(True); availability.layout().addWidget(legal)
        self.make_available_button = None
        if version.status is TemplateVersionStatus.TO_VALIDATE:
            self.make_available_button = QPushButton("Rendre disponible"); self.make_available_button.setObjectName("primaryButton")
            self.make_available_button.setEnabled(editable and evaluation.ready); self.make_available_button.clicked.connect(self._make_available)
            availability.layout().addWidget(self.make_available_button)
        self.archive_button = None
        if version.status is not TemplateVersionStatus.ARCHIVED:
            self.archive_button = QPushButton("Archiver"); self.archive_button.setObjectName("secondaryButton")
            self.archive_button.clicked.connect(self._archive); availability.layout().addWidget(self.archive_button)
        if version.status in {TemplateVersionStatus.AVAILABLE, TemplateVersionStatus.ARCHIVED}: availability.layout().addWidget(QLabel("Cette version est en lecture seule. Toute modification de contenu exige une Nouvelle version."))
        self.layout.addStretch(1)

    def _section(self, title: str, form: bool = False):
        frame = QFrame(); frame.setObjectName("companyGroup"); layout = QVBoxLayout(frame); heading = QLabel(title); heading.setObjectName("sectionTitle"); layout.addWidget(heading); self.layout.addWidget(frame)
        if form:
            value = QFormLayout(); layout.addLayout(value); return frame, value
        return frame

    def _run(self, operation, success: str) -> None:
        try: operation(); self._feedback(success, True); self.refresh()
        except Exception as exc: self._feedback(getattr(exc, "user_message", str(exc)), False)

    def _feedback(self, text: str, success: bool) -> None:
        self.feedback.setText(text); self.feedback.setObjectName("successFeedback" if success else "formError"); self.feedback.show()

    def _save_targets(self): self._run(lambda: self.service.set_target_regimes(self.version_id, tuple(code for code, box in self.target_boxes.items() if box.isChecked())), "Régimes visés enregistrés.")
    def _confirm_regime(self): self._run(lambda: self.service.confirm_regime(self.version_id, self.confirm_regime_combo.currentData(), self.regime_reference.text()), "Régime confirmé explicitement.")
    def _save_required(self): self._run(lambda: self.service.set_required_fields(self.version_id, tuple(key for key, box in self.company_boxes.items() if box.isChecked()), ("intervention.technician",) if self.technician_required.isVisible() and self.technician_required.isChecked() else ()), "Données requises enregistrées.")
    def _save_context(self):
        def save():
            version = self.service.get_version(self.version_id)
            self.service.set_context_review(self.version_id, self.context_status.currentData(), version.validation.context_authorizations, version.validation.conclusion_required_regimes)
        self._run(save, "Revue du contexte enregistrée.")

    def _save_context_row(self):
        def save():
            version = self.service.get_version(self.version_id); regime = self.context_regime.currentData(); conclusion = self.context_conclusion.currentData()
            blocks = tuple(code for code, box in self.context_blocks.items() if box.isChecked())
            rows = [item for item in version.validation.context_authorizations if (item.regime, item.conclusion_mode) != (regime, conclusion)]
            rows.append(ContextAuthorization(regime, conclusion, blocks))
            self.service.set_context_review(self.version_id, ReviewEvidenceStatus.CONFIRMED, tuple(rows), version.validation.conclusion_required_regimes)
        self._run(save, "Autorisation de contexte enregistrée.")
    def _control(self): self._run(lambda: self.service.control_structure(self.version_id), "Contrôle de structure terminé.")
    def _test(self): self._run(lambda: self.service.test_generation(self.version_id), "Test non officiel terminé.")
    def _save_visual(self): self._run(lambda: self.service.set_visual_review(self.version_id, self.visual_status.currentData()), "Contrôle visuel enregistré.")
    def _save_external(self):
        def save():
            self.service.set_external_content(self.version_id, self.external_content_status.currentData(), self.validator.text(), self.validation_date.text() or None, self.scope.text(), self.external_reference.text(), self.reservations.text())
            for code, (combo, reference) in self.gate_controls.items(): self.service.set_external_gate(self.version_id, code, combo.currentData(), reference.text())
        self._run(save, "Validation externe enregistrée.")

    def _open_source(self):
        version = self.service.get_version(self.version_id); self.opener.open(self.service.source_store.verify(version.source_relpath, version.source_hash))

    def _restore_source(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Restaurer le fichier source", "", "Documents Word (*.docx)")
        if filename: self._run(lambda: self.service.restore_source(self.version_id, Path(filename)), "Fichier source restauré.")

    def _make_available(self):
        message = ("Cette version deviendra sélectionnable pour les nouveaux documents compatibles. Ses régimes et contextes confirmés contrôleront l’éligibilité. "
                   "Toute modification future exigera une nouvelle version. Cette action ne constitue pas une certification juridique.")
        if QMessageBox.question(self, "Rendre disponible", message, QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok) == QMessageBox.StandardButton.Ok:
            self._run(lambda: self.service.make_available(self.version_id), "Version rendue disponible.")

    def _archive(self):
        message = "La version ne sera plus proposée pour de nouveaux documents. Les documents historiques, la source et les preuves restent conservés."
        if QMessageBox.question(self, "Archiver", message, QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok) == QMessageBox.StandardButton.Ok:
            self._run(lambda: self.service.archive(self.version_id), "Version archivée.")
