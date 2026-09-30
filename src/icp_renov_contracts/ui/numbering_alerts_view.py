from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..domain import AlertSettings, AnnualNumberingPolicy, NumberFormatMode, NumberingSettings
from ..errors import NumberingCollisionError, NumberingValidationError
from ..services import AlertSettingsService, NumberingSettingsService
from .styles import SPACING


class NextCounterCorrectionDialog(QDialog):
    def __init__(self, service: NumberingSettingsService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.current = service.get()
        self.setObjectName("nextCounterCorrectionDialog")
        self.setWindowTitle("Corriger le prochain numéro ?")
        root = QVBoxLayout(self)
        title = QLabel("Corriger le prochain numéro ?"); title.setObjectName("sectionTitle"); root.addWidget(title)
        text = QLabel("Les numéros existants et leurs fichiers ne changeront pas. Cette correction concerne uniquement le prochain contrat généré pour la première fois. Les collisions sont vérifiées dans cet espace de travail ; tenez compte manuellement des contrats externes à l’application.")
        text.setWordWrap(True); root.addWidget(text)
        self.current_preview = QLabel(f"Aperçu actuel : {service.preview() if self.current else '—'}")
        root.addWidget(self.current_preview)
        form = QFormLayout(); self.counter = QSpinBox(); self.counter.setRange(1, 999999999)
        self.counter.setValue(service.effective_counter(self.current) if self.current else 1)
        form.addRow("Nouveau prochain compteur", self.counter); root.addLayout(form)
        self.new_preview = QLabel(); root.addWidget(self.new_preview)
        self.lower_warning = QLabel("Le nouveau compteur est inférieur au compteur actuellement prévu. Vérifiez la continuité de votre numérotation.")
        self.lower_warning.setObjectName("formError"); self.lower_warning.setWordWrap(True); root.addWidget(self.lower_warning)
        self.feedback = QLabel(); self.feedback.setObjectName("formError"); self.feedback.setWordWrap(True); self.feedback.hide(); root.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Corriger le prochain numéro")
        buttons.rejected.connect(self.reject); buttons.accepted.connect(self._correct); root.addWidget(buttons)
        self.counter.valueChanged.connect(self._refresh); self._refresh()

    def _candidate_settings(self) -> NumberingSettings | None:
        if self.current is None:
            return None
        year = self.service.date_provider.today().year
        return replace(self.current, next_counter=self.counter.value(),
                       counter_year=year if self.current.annual_policy is AnnualNumberingPolicy.RESET_ANNUALLY else self.current.counter_year)

    def _refresh(self) -> None:
        planned = self.service.effective_counter(self.current) if self.current else 1
        self.lower_warning.setVisible(self.counter.value() < planned)
        try:
            value = self._candidate_settings()
            self.new_preview.setText(f"Nouvel aperçu : {self.service.candidate(value) if value else '—'}")
        except NumberingValidationError:
            self.new_preview.setText("Nouvel aperçu : Numérotation à configurer")

    def _correct(self) -> None:
        try:
            self.service.correct_next_counter(self.counter.value())
        except NumberingCollisionError as exc:
            self.feedback.setText(exc.user_message); self.feedback.show()
        except (NumberingValidationError, OSError):
            self.feedback.setText("Le prochain numéro n’a pas pu être corrigé. La configuration précédente est conservée."); self.feedback.show()
        else:
            self.accept()


class NumberingAlertsSettingsPage(QWidget):
    FORMAT_CHOICES = (
        ("Choisir un format", None),
        ("Préfixe + compteur", NumberFormatMode.PREFIX_COUNTER),
        ("Préfixe + année + compteur", NumberFormatMode.PREFIX_YEAR_COUNTER),
    )
    POLICY_CHOICES = (
        ("Choisir une politique", None),
        ("Compteur continu", AnnualNumberingPolicy.CONTINUOUS),
        ("Remise à zéro annuelle", AnnualNumberingPolicy.RESET_ANNUALLY),
    )

    def __init__(self, numbering: NumberingSettingsService, alerts: AlertSettingsService) -> None:
        super().__init__(); self.numbering = numbering; self.alerts = alerts; self.current: NumberingSettings | None = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(SPACING["md"])
        heading = QLabel("Numérotation & alertes"); heading.setObjectName("screenTitle"); root.addWidget(heading)
        intro = QLabel("Configurez la future série de contrats et les seuils de rappel locaux. Aucun rappel n’envoie de message à l’extérieur de l’application.")
        intro.setObjectName("screenDescription"); intro.setWordWrap(True); root.addWidget(intro)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0,0,0,0); layout.setSpacing(SPACING["md"])
        layout.addWidget(self._numbering_group()); layout.addWidget(self._alerts_group()); layout.addWidget(self._integrity_group()); layout.addStretch(1)
        scroll.setWidget(body); root.addWidget(scroll, 1); self._load()

    def _numbering_group(self) -> QFrame:
        frame = QFrame(); frame.setObjectName("companyGroup"); root = QVBoxLayout(frame)
        title = QLabel("Numérotation des contrats"); title.setObjectName("sectionTitle"); root.addWidget(title)
        self.numbering_state = QLabel(); self.numbering_state.setObjectName("infoFeedback"); self.numbering_state.setWordWrap(True); root.addWidget(self.numbering_state)
        notice = QLabel("Ces réglages s’appliquent uniquement aux futurs contrats. Les numéros existants ne sont jamais modifiés.")
        notice.setObjectName("screenDescription"); notice.setWordWrap(True); root.addWidget(notice)
        form = QFormLayout(); self.format_combo = QComboBox(); self.policy_combo = QComboBox()
        for label, value in self.FORMAT_CHOICES: self.format_combo.addItem(label, value)
        for label, value in self.POLICY_CHOICES: self.policy_combo.addItem(label, value)
        from PySide6.QtWidgets import QLineEdit
        self.prefix = QLineEdit(); self.prefix.setMaxLength(50)
        self.width = QSpinBox(); self.width.setRange(1, 8); self.width.setValue(4)
        self.start_counter = QSpinBox(); self.start_counter.setRange(1, 999999999); self.start_counter.setValue(1)
        self.next_counter = QSpinBox(); self.next_counter.setRange(1, 999999999); self.next_counter.setValue(1)
        self.next_counter_readonly = QLabel(); self.next_counter_readonly.setObjectName("readOnlyValue")
        self.next_host = QWidget(); next_layout = QVBoxLayout(self.next_host); next_layout.setContentsMargins(0,0,0,0)
        next_layout.addWidget(self.next_counter); next_layout.addWidget(self.next_counter_readonly)
        form.addRow("Format", self.format_combo); form.addRow("Préfixe", self.prefix)
        form.addRow("Nombre de chiffres du compteur", self.width); form.addRow("Politique annuelle", self.policy_combo)
        form.addRow("Compteur de départ", self.start_counter); form.addRow("Prochain compteur", self.next_host); root.addLayout(form)
        self.preview = QLabel("Numérotation à configurer"); self.preview.setObjectName("numberingPreview"); root.addWidget(QLabel("Aperçu du prochain numéro")); root.addWidget(self.preview)
        self.numbering_feedback = QLabel(); self.numbering_feedback.setObjectName("companyFeedback"); self.numbering_feedback.setWordWrap(True); self.numbering_feedback.hide(); root.addWidget(self.numbering_feedback)
        actions = QHBoxLayout(); self.save_numbering = QPushButton("Enregistrer la numérotation"); self.save_numbering.setObjectName("primaryButton")
        self.correct_number = QPushButton("Corriger le prochain numéro"); self.correct_number.setObjectName("sensitiveButton")
        actions.addStretch(1); actions.addWidget(self.correct_number); actions.addWidget(self.save_numbering); root.addLayout(actions)
        for widget in (self.format_combo, self.prefix, self.width, self.policy_combo, self.start_counter, self.next_counter):
            signal = widget.textChanged if hasattr(widget, "textChanged") else widget.currentIndexChanged if isinstance(widget, QComboBox) else widget.valueChanged
            signal.connect(self._refresh_preview)
        self.save_numbering.clicked.connect(self._save_numbering); self.correct_number.clicked.connect(self._open_correction)
        return frame

    def _alerts_group(self) -> QFrame:
        frame = QFrame(); frame.setObjectName("companyGroup"); root = QVBoxLayout(frame)
        title = QLabel("Alertes"); title.setObjectName("sectionTitle"); root.addWidget(title)
        text = QLabel("Seuils configurables — 0 jour signifie un rappel immédiat à la date de référence. Décochez un seuil pour le désactiver.")
        text.setWordWrap(True); root.addWidget(text); form = QFormLayout(); self.alert_controls = {}
        for key, label in (("default_internal_alert_days", "Anticipation d’échéance par défaut — jours"),
                           ("signature_followup_days", "Relance signature après — jours"),
                           ("backup_reminder_days", "Rappel de sauvegarde après — jours")):
            host = QWidget(); row = QHBoxLayout(host); row.setContentsMargins(0,0,0,0); enabled = QCheckBox("Activé"); value = QSpinBox(); value.setRange(0,3650)
            enabled.toggled.connect(value.setEnabled); row.addWidget(enabled); row.addWidget(value); row.addStretch(1); form.addRow(label, host); self.alert_controls[key] = (enabled, value)
        root.addLayout(form); backup = QLabel("Le seuil de sauvegarde est conservé pour le futur module de sauvegarde ; aucune date ou réussite de sauvegarde n’est inventée.")
        backup.setObjectName("screenDescription"); backup.setWordWrap(True); root.addWidget(backup)
        self.alert_feedback = QLabel(); self.alert_feedback.setObjectName("companyFeedback"); self.alert_feedback.hide(); root.addWidget(self.alert_feedback)
        footer = QHBoxLayout(); footer.addStretch(1); self.save_alerts = QPushButton("Enregistrer les alertes"); self.save_alerts.setObjectName("primaryButton"); footer.addWidget(self.save_alerts); root.addLayout(footer)
        self.save_alerts.clicked.connect(self._save_alert_settings); return frame

    @staticmethod
    def _integrity_group() -> QFrame:
        frame = QFrame(); frame.setObjectName("companyGroup"); root = QVBoxLayout(frame)
        title = QLabel("Anomalies toujours visibles"); title.setObjectName("sectionTitle"); root.addWidget(title)
        text = QLabel("La reconduction à confirmer, une copie signée introuvable ou invalide, un problème de stockage et une génération indisponible restent toujours visibles. Les seuils ci-dessus ne peuvent pas masquer ces anomalies.")
        text.setWordWrap(True); root.addWidget(text); return frame

    def _draft(self) -> NumberingSettings | None:
        mode = self.format_combo.currentData(); policy = self.policy_combo.currentData()
        if mode is None or policy is None: return None
        return NumberingSettings(NumberFormatMode(mode), self.prefix.text(), self.width.value(), AnnualNumberingPolicy(policy),
                                 self.start_counter.value(), self.next_counter.value(), self.current.counter_year if self.current else None)

    def _refresh_preview(self, *args) -> None:
        try:
            draft = self._draft(); value = self.numbering.preview(draft) if draft else None
            self.preview.setText(value or "Numérotation à configurer"); self.numbering_feedback.hide()
        except NumberingCollisionError as exc:
            self.preview.setText("Collision détectée"); self._number_feedback(exc.user_message, False)
        except NumberingValidationError as exc:
            self.preview.setText("Numérotation à configurer")
            if exc.detail == "annual_year": self._number_feedback(exc.user_message, False)

    def _load(self) -> None:
        self.current = self.numbering.get(); used = self.numbering.has_official_numbers()
        if self.current:
            self.format_combo.setCurrentIndex(self.format_combo.findData(self.current.format_mode)); self.prefix.setText(self.current.prefix)
            self.width.setValue(self.current.counter_width); self.policy_combo.setCurrentIndex(self.policy_combo.findData(self.current.annual_policy))
            self.start_counter.setValue(self.current.series_start_counter); self.next_counter.setValue(self.numbering.effective_counter(self.current))
            self.numbering_state.setText("Numérotation configurée")
        else:
            suffix = " Un historique de numéros existe déjà ; aucune politique n’en a été déduite." if used else ""
            self.numbering_state.setText("Numérotation non configurée — Aucun numéro officiel ne sera attribué tant que la numérotation n’est pas configurée." + suffix)
        self.next_counter.setVisible(not used); self.next_counter_readonly.setVisible(used)
        self.next_counter_readonly.setText(str(self.numbering.effective_counter(self.current)) if used and self.current else "À configurer")
        self.correct_number.setVisible(bool(used and self.current)); self._refresh_preview()
        alerts = self.alerts.get()
        for key, (enabled, value) in self.alert_controls.items():
            stored = getattr(alerts, key); enabled.setChecked(stored is not None); value.setEnabled(stored is not None); value.setValue(stored or 0)

    def _save_numbering(self) -> None:
        draft = self._draft()
        if draft is None:
            self._number_feedback("Choisissez le format et la politique annuelle.", False); return
        if self.current and self.numbering.has_official_numbers():
            structural = any((draft.format_mode != self.current.format_mode, draft.prefix.strip() != self.current.prefix,
                              draft.counter_width != self.current.counter_width, draft.annual_policy != self.current.annual_policy))
            if structural:
                box = QMessageBox(self); box.setWindowTitle("Modifier la numérotation des futurs contrats ?")
                box.setText("Les contrats existants conservent leur numéro actuel. Seuls les futurs contrats utiliseront cette configuration.")
                cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole); save = box.addButton("Enregistrer pour les futurs contrats", QMessageBox.ButtonRole.AcceptRole)
                box.exec()
                if box.clickedButton() is not save: return
            draft = replace(draft, next_counter=self.current.next_counter, counter_year=self.current.counter_year)
        try:
            self.numbering.save(draft); self._load(); self._number_feedback("Configuration de numérotation enregistrée pour les futurs contrats.", True)
        except NumberingCollisionError as exc: self._number_feedback(exc.user_message, False)
        except (NumberingValidationError, OSError): self._number_feedback("La configuration de numérotation n’a pas pu être enregistrée. La configuration précédente est conservée.", False)

    def _open_correction(self) -> None:
        dialog = NextCounterCorrectionDialog(self.numbering, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load(); self._number_feedback("Prochain numéro corrigé pour les futurs contrats.", True)

    def _save_alert_settings(self) -> None:
        values = {key: value.value() if enabled.isChecked() else None for key, (enabled, value) in self.alert_controls.items()}
        try:
            self.alerts.save(AlertSettings(**values)); self._load(); self._alert_feedback("Alertes enregistrées.", True)
        except (ValueError, OSError): self._alert_feedback("Les alertes n’ont pas pu être enregistrées. Les réglages précédents sont conservés.", False)

    def _number_feedback(self, message: str, success: bool) -> None:
        self.numbering_feedback.setText(message); self.numbering_feedback.setProperty("success", success); self.numbering_feedback.show()

    def _alert_feedback(self, message: str, success: bool) -> None:
        self.alert_feedback.setText(message); self.alert_feedback.setProperty("success", success); self.alert_feedback.show()
