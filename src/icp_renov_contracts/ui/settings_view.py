from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from ..domain import CompanySettings
from ..services import AlertSettingsService, BackupError, BackupService, CompanySettingsService, NumberingSettingsService, RestoreError, RestoreService, TemplateCatalogService
from ..storage import WorkspaceService
from .models_settings_view import ModelsSettingsPage
from .numbering_alerts_view import NumberingAlertsSettingsPage
from .generation_diagnostic_view import GenerationDiagnosticPage
from .styles import SPACING


class CompanySettingsView(QWidget):
    """Bounded Settings navigation for the company profile and governed model catalog."""

    title = "Param\u00e8tres"
    SECTION_LABELS = ("Soci\u00e9t\u00e9", "Mod\u00e8les", "Num\u00e9rotation & alertes", "Stockage & sauvegarde", "Diagnostic g\u00e9n\u00e9ration")

    def __init__(self, service: CompanySettingsService, workspaces: WorkspaceService,
                 template_catalog: TemplateCatalogService | None = None,
                 numbering: NumberingSettingsService | None = None,
                 alerts: AlertSettingsService | None = None, backup: BackupService | None = None,
                 restore: RestoreService | None = None, diagnostic=None) -> None:
        super().__init__()
        self.setObjectName("contentSurface")
        self.service = service
        self.workspaces = workspaces
        self.backup_service = backup; self.restore_service = restore; self.diagnostic_service = diagnostic
        self._fields: dict[str, QLineEdit] = {}
        self._editable: list[QWidget] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(SPACING["xl"], SPACING["xl"], SPACING["xl"], SPACING["xl"])
        root.setSpacing(SPACING["xl"])
        navigation_panel = QFrame(); navigation_panel.setObjectName("settingsNavigationPanel"); navigation_panel.setMinimumWidth(236)
        navigation = QVBoxLayout(navigation_panel); navigation.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"]); navigation.setSpacing(SPACING["xs"])
        nav_title = QLabel("Param\u00e8tres"); nav_title.setObjectName("settingsNavigationTitle"); navigation.addWidget(nav_title)
        navigation.addSpacing(SPACING["lg"])
        self._section_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self); group.setExclusive(True)
        for label in self.SECTION_LABELS:
            button = QPushButton(label.replace("&", "&&")); button.setObjectName("settingsSection")
            button.setCheckable(True); button.clicked.connect(lambda checked=False, target=label: self._show_section(target))
            group.addButton(button); self._section_buttons[label] = button; navigation.addWidget(button)
        navigation.addStretch(1); root.addWidget(navigation_panel, 0, Qt.AlignmentFlag.AlignTop)

        self.stack = QStackedWidget(); self.stack.setObjectName("settingsContentStack"); self._indices: dict[str, int] = {}
        self._indices["Soci\u00e9t\u00e9"] = self.stack.addWidget(self._company_page())
        self.models_page = ModelsSettingsPage(template_catalog, open_diagnostic=self.open_diagnostic) if template_catalog else None
        self._indices["Mod\u00e8les"] = self.stack.addWidget(self.models_page or self._unavailable_page("Mod\u00e8les"))
        self._indices["Numérotation & alertes"] = self.stack.addWidget(NumberingAlertsSettingsPage(numbering, alerts) if numbering and alerts else self._unavailable_page("Numérotation & alertes"))
        self._indices["Stockage & sauvegarde"] = self.stack.addWidget(self._storage_page() if backup and restore else self._unavailable_page("Stockage & sauvegarde"))
        self.diagnostic_page = GenerationDiagnosticPage(diagnostic, template_catalog, open_company=lambda:self._show_section("Société"), open_model=self._open_model_detail) if diagnostic and template_catalog else None
        self._indices["Diagnostic génération"] = self.stack.addWidget(self.diagnostic_page or self._unavailable_page("Diagnostic génération"))
        root.addWidget(self.stack, 1)
        self._show_section("Soci\u00e9t\u00e9")
        self._load()

    def _company_page(self) -> QWidget:
        page = QWidget(); root = QVBoxLayout(page); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(SPACING["md"])
        heading = QLabel("Soci\u00e9t\u00e9"); heading.setObjectName("screenTitle"); root.addWidget(heading)
        description = QLabel("Ces informations sont r\u00e9utilis\u00e9es dans les futurs documents g\u00e9n\u00e9r\u00e9s. Le profil peut rester partiel\u00a0: les champs vides s\u2019affichent comme \u00ab\u00a0\u00c0 compl\u00e9ter\u00a0\u00bb.")
        description.setObjectName("screenDescription"); description.setWordWrap(True); root.addWidget(description)
        self.feedback = QLabel(""); self.feedback.setObjectName("companyFeedback"); self.feedback.setWordWrap(True); self.feedback.hide(); root.addWidget(self.feedback)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(SPACING["md"])
        identity = self._group("Identit\u00e9", (
            ("legal_name", "Raison sociale"), ("trade_name", "Nom commercial"), ("legal_form", "Forme juridique"), ("share_capital", "Capital social"),
            ("siren", "SIREN"), ("siret", "SIRET"), ("registration_summary", "Immatriculation / registre"), ("ape_code", "Code APE / NAF"),
            ("address_line1", "Adresse"), ("address_line2", "Compl\u00e9ment d\u2019adresse"), ("postal_code", "Code postal"), ("city", "Ville"), ("country", "Pays"),
            ("correspondence_address", "Adresse de correspondance"), ("vat_number", "N\u00b0 TVA intracommunautaire"),
        )); self._append_logo_controls(identity); layout.addWidget(identity)
        layout.addWidget(self._group("Contacts / signataire", (("phone", "T\u00e9l\u00e9phone"), ("email", "E-mail"), ("signatory_name", "Nom du signataire par d\u00e9faut"), ("signatory_role", "Fonction / qualit\u00e9 du signataire"))))
        layout.addWidget(self._group("Assurance", (("insurer_name", "Assureur"), ("insurance_policy_number", "N\u00b0 de police"), ("insurance_scope", "P\u00e9rim\u00e8tre / couverture"), ("insurance_valid_until", "Valable jusqu\u2019au"))))
        layout.addWidget(self._group("Fluides frigorig\u00e8nes", (("refrigerant_capacity_number", "N\u00b0 d\u2019attestation / capacit\u00e9"), ("refrigerant_capacity_body", "Organisme"), ("refrigerant_capacity_until", "Valable jusqu\u2019au"), ("refrigerant_partner_name", "Partenaire fluides"))))
        layout.addWidget(self._group("Informations consommateur", (
            ("mediator_name", "M\u00e9diateur"), ("mediator_address", "Adresse du m\u00e9diateur"), ("mediator_website", "Site du m\u00e9diateur"),
            ("complaints_contact", "Contact r\u00e9clamations"), ("withdrawal_contact", "Contact r\u00e9tractation"), ("privacy_contact", "Contact protection des donn\u00e9es"),
        )))
        footer = QHBoxLayout(); footer.addStretch(1); self.save_button = QPushButton("Enregistrer"); self.save_button.setObjectName("primaryButton"); self.save_button.clicked.connect(self._save); self._editable.append(self.save_button); footer.addWidget(self.save_button); layout.addLayout(footer)
        layout.addStretch(1); scroll.setWidget(body); root.addWidget(scroll, 1)
        return page

    def _group(self, title: str, rows: tuple[tuple[str, str], ...]) -> QFrame:
        frame = QFrame(); frame.setObjectName("companyGroup"); layout = QVBoxLayout(frame); heading = QLabel(title); heading.setObjectName("sectionTitle"); layout.addWidget(heading)
        form = QFormLayout(); form.setSpacing(SPACING["sm"])
        required = self.service.required_fields()
        for key, label in rows:
            field = QLineEdit(); field.setObjectName(f"company_{key}"); field.setPlaceholderText("\u00c0 compl\u00e9ter"); self._fields[key] = field; self._editable.append(field)
            form.addRow(QLabel(label + (" \u00b7 Requis par les mod\u00e8les utilis\u00e9s" if key in required else "")), field)
        layout.addLayout(form); return frame

    def _append_logo_controls(self, frame: QFrame) -> None:
        layout = frame.layout(); heading = QLabel("Logo"); heading.setObjectName("sectionTitle"); layout.addWidget(heading)
        self.logo_status = QLabel(""); self.logo_status.setObjectName("screenDescription"); self.logo_status.setWordWrap(True); layout.addWidget(self.logo_status)
        actions = QHBoxLayout(); self.import_logo_button = QPushButton("Choisir un logo"); self.import_logo_button.setObjectName("secondaryButton"); self.import_logo_button.clicked.connect(self._choose_logo); self._editable.append(self.import_logo_button)
        self.remove_logo_button = QPushButton("Retirer le logo"); self.remove_logo_button.setObjectName("secondaryButton"); self.remove_logo_button.clicked.connect(self._remove_logo); self._editable.append(self.remove_logo_button)
        actions.addWidget(self.import_logo_button); actions.addWidget(self.remove_logo_button); actions.addStretch(1); layout.addLayout(actions); return frame

    def _unavailable_page(self, label: str) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(label); heading.setObjectName("screenTitle"); message = QLabel("Cette section sera disponible dans une prochaine version."); message.setObjectName("screenDescription")
        layout.addWidget(heading); layout.addWidget(message); layout.addStretch(1); return page

    def _storage_page(self) -> QWidget:
        page=QWidget(); root=QVBoxLayout(page); root.setContentsMargins(0,0,0,0); root.setSpacing(SPACING["md"])
        title=QLabel("Stockage & sauvegarde"); title.setObjectName("screenTitle"); root.addWidget(title)
        self.storage_feedback=QLabel(); self.storage_feedback.setObjectName("companyFeedback"); self.storage_feedback.setWordWrap(True); self.storage_feedback.hide(); root.addWidget(self.storage_feedback)
        workspace=self._group("Dossier de travail", ()); workspace_path = QLabel(str(self.service.workspace_root)); workspace_path.setObjectName("readOnlyValue"); workspace_path.setWordWrap(True); workspace.layout().addWidget(workspace_path)
        open_workspace=QPushButton("Ouvrir le dossier"); open_workspace.setObjectName("tertiaryButton"); open_workspace.clicked.connect(lambda: self._open_path(self.service.workspace_root)); workspace.layout().addWidget(open_workspace); root.addWidget(workspace)
        backup=self._group("Sauvegardes", ()); self.backup_folder=QLabel(); self.backup_latest=QLabel(); self.backup_state=QLabel(); backup.layout().addWidget(self.backup_folder); backup.layout().addWidget(self.backup_latest); backup.layout().addWidget(self.backup_state)
        actions=QHBoxLayout(); self.choose_backup=QPushButton("Choisir le dossier de sauvegarde"); self.choose_backup.setObjectName("secondaryButton"); self.open_backup=QPushButton("Ouvrir le dossier"); self.open_backup.setObjectName("tertiaryButton"); self.create_backup=QPushButton("Sauvegarder maintenant"); self.create_backup.setObjectName("primaryButton"); actions.addWidget(self.choose_backup);actions.addWidget(self.open_backup);actions.addWidget(self.create_backup);actions.addStretch(1);backup.layout().addLayout(actions);root.addWidget(backup)
        restore=self._group("Restauration", ()); note=QLabel("Les sauvegardes contiennent les contrats, documents et paramètres du dossier de travail. Conservez-les dans un emplacement approprié. La restauration crée un nouveau dossier et ne remplace jamais le dossier courant.");note.setWordWrap(True);restore.layout().addWidget(note);self.restore_backup=QPushButton("Restaurer une sauvegarde");restore.layout().addWidget(self.restore_backup);root.addWidget(restore);root.addStretch(1)
        self.restore_backup.setObjectName("secondaryButton")
        self.choose_backup.clicked.connect(self._choose_backup);self.open_backup.clicked.connect(lambda:self._open_path(self.backup_service.config_store.load().backup_directory));self.create_backup.clicked.connect(self._create_backup);self.restore_backup.clicked.connect(self._restore_backup);self._refresh_storage();return page

    def _refresh_storage(self):
        c=self.backup_service.config_store.load();self.backup_folder.setText("Dossier de sauvegarde : " + (str(c.backup_directory) if c.backup_directory else "Non configuré"))
        from ..services import RealBackupSummaryProvider
        s=RealBackupSummaryProvider(self.backup_service).summary();self.backup_latest.setText(s.label);self.backup_state.setText(s.reminder);self.open_backup.setVisible(bool(c.backup_directory));self.create_backup.setEnabled(s.can_create_now)
    def _storage_message(self,text,ok=False):self.storage_feedback.setText(text);self.storage_feedback.setProperty("success",ok);self.storage_feedback.show()
    def _choose_backup(self):
        value=QFileDialog.getExistingDirectory(self,"Choisir le dossier de sauvegarde")
        if not value:return
        try:self.backup_service.set_destination(Path(value));self._refresh_storage();self._storage_message("Dossier de sauvegarde configuré.",True)
        except BackupError:self._storage_message("Le dossier de sauvegarde n’est pas accessible en écriture.")
    def _create_backup(self):
        try:path=self.backup_service.create_now();self._refresh_storage();self._storage_message(f"Sauvegarde créée : {path.name}",True)
        except BackupError:self._storage_message("La sauvegarde n’a pas pu être créée. Les données de l’application sont conservées.")
    def _restore_backup(self):
        archive,_=QFileDialog.getOpenFileName(self,"Restaurer une sauvegarde","","Sauvegardes ICP Renov (*.icprenovbackup)")
        if not archive:return
        target=QFileDialog.getExistingDirectory(self,"Choisir un nouveau dossier de restauration",str(Path(archive).parent.parent))
        if not target:return
        target_path=Path(target)
        if target_path.exists() and any(target_path.iterdir()):
            self._storage_message("Le dossier de restauration doit être vide. Choisissez un nouveau dossier ou un dossier vide."); return
        box=QMessageBox(self);box.setWindowTitle("Restaurer la sauvegarde ?")
        box.setText("Le dossier de travail actuel ne sera pas remplacé. La restauration crée un nouveau dossier ; après succès, il sera utilisé au prochain redémarrage.")
        cancel=box.addButton("Annuler",QMessageBox.ButtonRole.RejectRole);confirm=box.addButton("Restaurer la sauvegarde",QMessageBox.ButtonRole.AcceptRole);box.exec()
        if box.clickedButton() is not confirm:return
        try:self.restore_service.restore(Path(archive),target_path);self._storage_message("Restauration terminée. Redémarrez l’application pour utiliser le dossier restauré.",True)
        except RestoreError:self._storage_message("La sauvegarde ne peut pas être restaurée. Le dossier courant est conservé.")
    @staticmethod
    def _open_path(path):
        if path:
            try: __import__("os").startfile(path)
            except OSError: pass

    def _show_section(self, label: str) -> None:
        self.stack.setCurrentIndex(self._indices[label])
        for name, button in self._section_buttons.items(): button.setChecked(name == label)

    def open_diagnostic(self, version_id: str | None = None, validation_mode: bool = False) -> None:
        self._show_section("Diagnostic génération")
        if self.diagnostic_page:
            self.diagnostic_page.refresh()
            if version_id: self.diagnostic_page.preselect(version_id, validation_mode)

    def _open_model_detail(self, version_id: str) -> None:
        self._show_section("Modèles")
        if self.models_page: self.models_page.open_version(version_id)

    def _load(self) -> None:
        self.current = self.service.get()
        for key, field in self._fields.items(): field.setText(self._display_value(key, getattr(self.current, key)))
        self._set_logo_status(); inspection = self.workspaces.inspect(self.service.workspace_root)
        if not inspection.writable:
            for widget in self._editable: widget.setEnabled(False)
            self._feedback("Espace de travail en lecture seule\u00a0: les informations soci\u00e9t\u00e9 ne peuvent pas \u00eatre modifi\u00e9es.", False)

    def _set_logo_status(self) -> None:
        if not self.current.logo_relpath: self.logo_status.setText("Aucun logo enregistr\u00e9.")
        elif self.service.logo_integrity(): self.logo_status.setText("Logo enregistr\u00e9 et int\u00e8gre.")
        else: self.logo_status.setText("Le logo enregistr\u00e9 est introuvable ou alt\u00e9r\u00e9.")
        configured = bool(self.current.logo_relpath)
        self.remove_logo_button.setVisible(configured)
        self.remove_logo_button.setEnabled(configured)

    def _choose_logo(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choisir un logo", "", "Images PNG ou JPEG (*.png *.jpg *.jpeg)")
        if not filename: return
        try: self.current = self.service.import_logo(Path(filename)); self._set_logo_status(); self._feedback("Logo enregistr\u00e9.", True)
        except (OSError, ValueError): self._feedback("Le logo doit \u00eatre un fichier PNG ou JPEG valide.", False)

    def _remove_logo(self) -> None:
        try: self.current = self.service.remove_logo(); self._set_logo_status(); self._feedback("Logo supprim\u00e9.", True)
        except OSError: self._feedback("Le logo n\u2019a pas pu \u00eatre supprim\u00e9.", False)

    def _save(self) -> None:
        values = {key: field.text() for key, field in self._fields.items()}
        try:
            self.current = self.service.save(replace(self.current, **values)); self._load(); self._feedback("Informations soci\u00e9t\u00e9 enregistr\u00e9es.", True)
        except (OSError, ValueError):
            self._feedback("Les informations soci\u00e9t\u00e9 n\u2019ont pas pu \u00eatre enregistr\u00e9es. Les valeurs pr\u00e9c\u00e9demment enregistr\u00e9es sont conserv\u00e9es.", False)

    def _feedback(self, message: str, success: bool) -> None:
        self.feedback.setText(message); self.feedback.setProperty("success", success); self.feedback.show()

    @staticmethod
    def _display_value(key: str, value: str | Decimal | None) -> str:
        if key == "share_capital" and isinstance(value, Decimal): return format(value, "f").replace(".", ",")
        if value is None: return ""
        value = str(value)
        if key == "siren" and len(value) == 9 and value.isdigit(): return f"{value[:3]} {value[3:6]} {value[6:]}"
        if key == "siret" and len(value) == 14 and value.isdigit(): return f"{value[:3]} {value[3:6]} {value[6:9]} {value[9:]}"
        return value
