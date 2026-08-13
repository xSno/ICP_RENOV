from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..domain import ContractConditions, DurationMode, RenewalMode, TemplateVersionStatus
from ..errors import ApplicationError, ContractConditionsValidationError
from ..services import ContractService


REGIMES = (("Consommateur", "CONSUMER"), ("Non-professionnel", "NON_PROFESSIONAL"), ("Professionnel", "PROFESSIONAL"))
CONCLUSIONS = (
    ("Dans les locaux du professionnel", "IN_PREMISES"), ("Hors établissement", "OFF_PREMISES"),
    ("À distance par e-mail", "DISTANCE_EMAIL"), ("Interface en ligne", "ONLINE_INTERFACE"),
    ("Autre conclusion à distance", "OTHER_DISTANCE"),
)
REFRIGERANT = (
    ("Gestion en interne autorisée", "IN_HOUSE_AUTHORIZED"), ("Partenaire", "PARTNER"), ("Exclue", "EXCLUDED"),
)


def _combo(options: tuple[tuple[str, object], ...], blank: str = "À compléter") -> QComboBox:
    result = QComboBox(); result.addItem(blank, None)
    for label, value in options: result.addItem(label, value)
    return result


def _set_combo(combo: QComboBox, value: object) -> None:
    index = combo.findData(value); combo.setCurrentIndex(index if index >= 0 else 0)


def _row_visible(form: QFormLayout, field: QWidget, visible: bool) -> None:
    field.setVisible(visible)
    label = form.labelForField(field)
    if label: label.setVisible(visible)


class EarlyTerminationEditor(QWidget):
    def __init__(self, service: ContractService, contract_id: str, saved, closed) -> None:
        super().__init__(); self.setObjectName("earlyTerminationEditor")
        self.service = service; self.contract_id = contract_id; self.saved = saved
        conditions = service.get_conditions(contract_id); version = service.selected_template_version(contract_id)
        root = QVBoxLayout(self); header = QHBoxLayout()
        title = QLabel("Conditions de fin anticipée"); title.setObjectName("drawerTitle")
        close = QPushButton("Fermer"); close.setObjectName("secondaryButton"); close.clicked.connect(closed)
        header.addWidget(title); header.addStretch(1); header.addWidget(close); root.addLayout(header)
        root.addWidget(QLabel("Ces informations modifient uniquement les conditions du brouillon."))
        self.reason_checks: dict[str, QCheckBox] = {}
        catalogs = version.catalogs.early_termination_reasons if version else ()
        reasons = QFrame(); reasons_layout = QVBoxLayout(reasons)
        if catalogs:
            for option in catalogs:
                box = QCheckBox(option.label); box.setChecked(option.code in conditions.early_termination_reason_codes)
                self.reason_checks[option.code] = box; reasons_layout.addWidget(box)
        else: reasons_layout.addWidget(QLabel("Motifs : À configurer"))
        root.addWidget(reasons)
        form = QFormLayout(); self.custom = QPlainTextEdit(conditions.early_termination_custom_text)
        self.custom.setMaximumHeight(100); self.cure_days = QLineEdit("" if conditions.breach_cure_period_days is None else str(conditions.breach_cure_period_days))
        form.addRow("Motifs supplémentaires", self.custom); form.addRow("Délai de régularisation (jours)", self.cure_days)
        root.addLayout(form); root.addStretch(1)
        footer = QHBoxLayout(); footer.addStretch(1)
        cancel = QPushButton("Annuler"); cancel.setObjectName("secondaryButton"); cancel.clicked.connect(closed)
        save = QPushButton("Enregistrer"); save.setObjectName("primaryButton"); save.clicked.connect(self._save)
        footer.addWidget(cancel); footer.addWidget(save); root.addLayout(footer)

    def _save(self) -> None:
        current = self.service.get_conditions(self.contract_id); values = asdict(current)
        values.update(
            early_termination_reason_codes=tuple(code for code, box in self.reason_checks.items() if box.isChecked()),
            early_termination_custom_text=self.custom.toPlainText(), breach_cure_period_days=self.cure_days.text(),
        )
        try: self.service.save_conditions(self.contract_id, ContractConditions(**values))
        except ApplicationError as error: self.saved(False, error.user_message); return
        self.saved(True, "Enregistré")


class ConditionsView(QWidget):
    def __init__(self, service: ContractService, feedback, open_drawer) -> None:
        super().__init__(); self.setObjectName("conditionsStep")
        self.service = service; self.feedback = feedback; self.open_drawer = open_drawer
        self.contract_id: str | None = None; self.loading = False
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 8, 0); root.setSpacing(16)
        self.error = QLabel(); self.error.setObjectName("formError"); self.error.setWordWrap(True); self.error.hide(); root.addWidget(self.error)
        self._build_context(root); self._build_services(root); self._build_period(root)
        self._build_intervention(root); self._build_pricing(root); self._build_renewal(root); self._build_special(root)
        save = QPushButton("Enregistrer les conditions"); save.setObjectName("primaryButton"); save.clicked.connect(self.save)
        root.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight); root.addStretch(1)

    @staticmethod
    def _section(root: QVBoxLayout, title: str) -> tuple[QFrame, QFormLayout]:
        frame = QFrame(); frame.setObjectName("panel"); layout = QVBoxLayout(frame)
        heading = QLabel(title); heading.setObjectName("sectionTitle"); layout.addWidget(heading)
        form = QFormLayout(); form.setSpacing(12); layout.addLayout(form); root.addWidget(frame)
        return frame, form

    def _build_context(self, root: QVBoxLayout) -> None:
        _, self.context_form = self._section(root, "Cadre du contrat")
        self.regime = _combo(REGIMES, "Choisir un régime"); self.regime.setObjectName("contractRegime")
        self.model = QComboBox(); self.model.setObjectName("contractTemplateVersion")
        self.model_state = QLabel(); self.model_state.setWordWrap(True)
        self.conclusion = _combo(CONCLUSIONS); self.conclusion.setObjectName("contractConclusionMode")
        self.early_performance = QCheckBox("Démarrage avant la fin du délai de rétractation")
        self.early_performance.setObjectName("earlyPerformanceRequested")
        self.context_form.addRow("Régime", self.regime); self.context_form.addRow("Modèle / version", self.model)
        self.context_form.addRow("", self.model_state); self.context_form.addRow("Mode de conclusion", self.conclusion)
        self.context_form.addRow("", self.early_performance)
        self.regime.currentIndexChanged.connect(self._regime_changed)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.conclusion.currentIndexChanged.connect(self._dynamic)

    def _build_services(self, root: QVBoxLayout) -> None:
        _, self.services_form = self._section(root, "Modèle & prestations")
        self.visits = QLineEdit(); self.visits.setObjectName("visitsPerYear")
        self.refrigerant = _combo(REFRIGERANT); self.refrigerant.setObjectName("refrigerantHandlingMode")
        baseline = QLabel("Entretien courant — Inclus"); baseline.setObjectName("baselineServiceIncluded")
        options = QWidget(); options_layout = QHBoxLayout(options); options_layout.setContentsMargins(0, 0, 0, 0)
        self.deep_cleaning = QCheckBox("Nettoyage approfondi"); self.disinfection = QCheckBox("Désinfection")
        options_layout.addWidget(self.deep_cleaning); options_layout.addWidget(self.disinfection); options_layout.addStretch(1)
        self.priority = _combo((("Non inclus", False), ("Inclus", True))); self.priority.setObjectName("priorityBreakdown")
        self.priority_delay = QLineEdit(); self.priority_delay.setObjectName("priorityBreakdownDelay")
        self.exclusions = QPlainTextEdit(); self.exclusions.setMaximumHeight(90)
        self.services_form.addRow("Nombre de visites par an", self.visits); self.services_form.addRow("Gestion des fluides", self.refrigerant)
        self.services_form.addRow("Prestation de base", baseline); self.services_form.addRow("Prestations incluses", options)
        self.services_form.addRow("Dépannage prioritaire", self.priority); self.services_form.addRow("Délai d’intervention", self.priority_delay)
        self.services_form.addRow("Exclusions complémentaires", self.exclusions)
        self.priority.currentIndexChanged.connect(self._dynamic)

    def _build_period(self, root: QVBoxLayout) -> None:
        _, self.period_form = self._section(root, "Période")
        self.issue_date = QLineEdit(); self.start_date = QLineEdit(); self.duration_mode = _combo((("Standard", "STANDARD"), ("Personnalisée", "CUSTOM")))
        self.duration_months = QLineEdit(); self.end_date = QLineEdit(); self.signature_city = QLineEdit()
        for field in (self.issue_date, self.start_date, self.end_date): field.setPlaceholderText("AAAA-MM-JJ")
        self.end_hint = QLabel()
        self.period_form.addRow("Date d’émission", self.issue_date); self.period_form.addRow("Date de prise d’effet", self.start_date)
        self.period_form.addRow("Durée", self.duration_mode); self.period_form.addRow("Durée standard (mois)", self.duration_months)
        self.period_form.addRow("Date de fin", self.end_date); self.period_form.addRow("", self.end_hint)
        self.period_form.addRow("Ville de signature", self.signature_city)
        self.duration_mode.currentIndexChanged.connect(self._dynamic); self.start_date.textChanged.connect(self._update_end_date)
        self.duration_months.textChanged.connect(self._update_end_date)

    def _build_intervention(self, root: QVBoxLayout) -> None:
        _, form = self._section(root, "Conditions d’intervention")
        self.included_area = QLineEdit(); self.business_hours = QLineEdit()
        self.travel = _combo((("Non", False), ("Oui", True))); self.travel.setObjectName("travelIncluded")
        self.missed_fee = QLineEdit(); self.missed_fee.setPlaceholderText("Aucun ou montant")
        form.addRow("Zone géographique incluse", self.included_area); form.addRow("Horaires habituels", self.business_hours)
        form.addRow("Déplacements inclus", self.travel); form.addRow("Frais de rendez-vous non réalisable", self.missed_fee)

    def _build_pricing(self, root: QVBoxLayout) -> None:
        _, self.pricing_form = self._section(root, "Prix & paiement")
        self.annual_ht = QLineEdit(); self.annual_ht.setObjectName("annualHt")
        self.vat_rate = QComboBox(); self.vat_rate.setObjectName("vatRate")
        self.vat_amount = QLabel("À compléter"); self.vat_amount.setObjectName("vatAmount")
        self.annual_ttc = QLabel("À compléter"); self.annual_ttc.setObjectName("annualTtc")
        self.payment_term = QComboBox(); self.payment_due = QLineEdit(); self.payment_custom = QLineEdit()
        self.payment_methods_host = QWidget(); self.payment_methods_layout = QHBoxLayout(self.payment_methods_host)
        self.payment_methods_layout.setContentsMargins(0, 0, 0, 0); self.payment_method_checks = {}
        self.pricing_form.addRow("Prix annuel HT", self.annual_ht); self.pricing_form.addRow("Taux de TVA", self.vat_rate)
        self.pricing_form.addRow("TVA", self.vat_amount); self.pricing_form.addRow("Total annuel TTC", self.annual_ttc)
        self.pricing_form.addRow("Modalité de paiement", self.payment_term); self.pricing_form.addRow("Échéance (jours)", self.payment_due)
        self.pricing_form.addRow("Précision de paiement", self.payment_custom); self.pricing_form.addRow("Moyens de paiement", self.payment_methods_host)
        self.annual_ht.textChanged.connect(self._update_totals); self.vat_rate.currentIndexChanged.connect(self._update_totals)
        self.payment_term.currentIndexChanged.connect(self._dynamic)

    def _build_renewal(self, root: QVBoxLayout) -> None:
        _, self.renewal_form = self._section(root, "Renouvellement")
        self.renewal_mode = _combo((("Aucun", "NONE"), ("Manuel", "MANUAL"), ("Reconduction tacite", "TACIT")))
        self.renewal_period = QLineEdit(); self.notice_days = QLineEdit()
        self.channels_host = QWidget(); self.channels_layout = QHBoxLayout(self.channels_host); self.channels_layout.setContentsMargins(0, 0, 0, 0)
        self.channel_checks = {}; self.alert_days = QLineEdit()
        self.renewal_price = _combo((("Même prix", "FIXED"), ("Nouveau prix défini au renouvellement", "NEW_PRICE_ON_RENEWAL")))
        self.renewal_form.addRow("Mode", self.renewal_mode); self.renewal_form.addRow("Durée de renouvellement (mois)", self.renewal_period)
        self.renewal_form.addRow("Préavis de non-renouvellement (jours)", self.notice_days)
        self.renewal_form.addRow("Canaux de non-renouvellement", self.channels_host)
        self.renewal_form.addRow("Alerte interne (jours)", self.alert_days); self.renewal_form.addRow("Prix au renouvellement", self.renewal_price)
        self.renewal_mode.currentIndexChanged.connect(self._dynamic)

    def _build_special(self, root: QVBoxLayout) -> None:
        _, form = self._section(root, "Conditions particulières")
        self.special_terms = QPlainTextEdit(); self.special_terms.setObjectName("specialTerms"); self.special_terms.setMaximumHeight(130)
        self.early_button = QPushButton("Modifier les conditions de fin anticipée"); self.early_button.setObjectName("secondaryButton")
        self.early_button.clicked.connect(self._open_early)
        form.addRow("Conditions particulières", self.special_terms); form.addRow("", self.early_button)

    def load(self, contract_id: str) -> None:
        self.contract_id = contract_id; self.loading = True; contract = self.service.get(contract_id)
        conditions = self.service.get_conditions(contract_id); _set_combo(self.regime, contract.regime.value if contract.regime else None)
        self._populate_models(contract); self._populate_catalogs(conditions)
        _set_combo(self.conclusion, conditions.conclusion_mode); self.early_performance.setChecked(bool(conditions.early_performance_requested))
        self.visits.setText("" if conditions.visits_per_year is None else str(conditions.visits_per_year)); _set_combo(self.refrigerant, conditions.refrigerant_handling_mode)
        self.deep_cleaning.setChecked("DEEP_CLEANING" in conditions.included_options); self.disinfection.setChecked("DISINFECTION" in conditions.included_options)
        _set_combo(self.priority, conditions.priority_breakdown); self.priority_delay.setText(conditions.priority_breakdown_delay or "")
        self.exclusions.setPlainText(conditions.additional_exclusions); self.issue_date.setText(conditions.issue_date or ""); self.start_date.setText(conditions.start_date or "")
        _set_combo(self.duration_mode, conditions.initial_duration_mode); self.duration_months.setText("" if conditions.initial_duration_months is None else str(conditions.initial_duration_months))
        self.end_date.setText(conditions.initial_end_date or ""); self.signature_city.setText(conditions.signature_city)
        self.included_area.setText(conditions.included_area); self.business_hours.setText(conditions.business_hours); _set_combo(self.travel, conditions.travel_included)
        self.missed_fee.setText(conditions.missed_appointment_fee or ""); self.annual_ht.setText(conditions.annual_ht or ""); _set_combo(self.vat_rate, conditions.vat_rate)
        _set_combo(self.payment_term, conditions.payment_terms_code); self.payment_due.setText("" if conditions.payment_due_days is None else str(conditions.payment_due_days))
        self.payment_custom.setText(conditions.payment_terms_custom_text)
        for code, box in self.payment_method_checks.items(): box.setChecked(code in conditions.payment_methods)
        _set_combo(self.renewal_mode, conditions.renewal_mode); self.renewal_period.setText("" if conditions.renewal_period_months is None else str(conditions.renewal_period_months))
        self.notice_days.setText("" if conditions.non_renewal_notice_days is None else str(conditions.non_renewal_notice_days))
        for code, box in self.channel_checks.items(): box.setChecked(code in conditions.non_renewal_notice_channels)
        self.alert_days.setText("" if conditions.internal_alert_days is None else str(conditions.internal_alert_days)); _set_combo(self.renewal_price, conditions.renewal_price_rule)
        self.special_terms.setPlainText(conditions.special_terms); self.loading = False; self.error.hide(); self._dynamic(); self._update_totals()

    def _populate_models(self, contract) -> None:
        self.model.blockSignals(True); self.model.clear(); self.model.addItem("Choisir un modèle", None)
        versions = self.service.compatible_template_versions(contract.id) if contract.regime else []
        for version in versions: self.model.addItem(version.display_name, version.id)
        selected = self.service.selected_template_version(contract.id)
        if selected and self.model.findData(selected.id) < 0:
            self.model.addItem(f"{selected.display_name} · indisponible pour une nouvelle sélection", selected.id)
        _set_combo(self.model, contract.template_version_id); self.model.blockSignals(False)
        has_regime = contract.regime is not None; _row_visible(self.context_form, self.model, has_regime)
        if not has_regime: self.model_state.setText("Choisissez d’abord un régime.")
        elif not versions and selected is None: self.model_state.setText("Aucun modèle disponible pour ce régime")
        elif selected and selected.status is not TemplateVersionStatus.AVAILABLE:
            self.model_state.setText("Le modèle sélectionné n’est plus disponible pour une nouvelle sélection.")
        else: self.model_state.setText("")

    def _populate_catalogs(self, conditions: ContractConditions) -> None:
        version = self.service.selected_template_version(self.contract_id); catalogs = version.catalogs if version else None
        self.vat_rate.clear(); self.vat_rate.addItem("À configurer" if not catalogs or not catalogs.vat_rates else "À compléter", None)
        for rate in catalogs.vat_rates if catalogs else (): self.vat_rate.addItem(f"{rate} %", rate)
        self.payment_term.clear(); self.payment_term.addItem("À configurer" if not catalogs or not catalogs.payment_terms else "À compléter", None)
        for option in catalogs.payment_terms if catalogs else (): self.payment_term.addItem(option.label, option.code)
        self._replace_checks(self.payment_methods_layout, self.payment_method_checks, catalogs.payment_methods if catalogs else ())
        self._replace_checks(self.channels_layout, self.channel_checks, catalogs.non_renewal_channels if catalogs else ())

    @staticmethod
    def _replace_checks(layout: QHBoxLayout, target: dict, options) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        target.clear()
        if not options: layout.addWidget(QLabel("À configurer")); return
        for option in options:
            box = QCheckBox(option.label); target[option.code] = box; layout.addWidget(box)
        layout.addStretch(1)

    def _regime_changed(self) -> None:
        if self.loading or self.contract_id is None: return
        try: self.service.change_regime(self.contract_id, self.regime.currentData())
        except ApplicationError as error: self._show_error(error); return
        self.feedback(True, "Enregistré"); self.load(self.contract_id)

    def _model_changed(self) -> None:
        if self.loading or self.contract_id is None or self.model.currentData() is None: return
        current = self.service.get(self.contract_id)
        if current.template_version_id == self.model.currentData(): return
        try: self.service.select_template_version(self.contract_id, self.model.currentData())
        except ApplicationError as error: self._show_error(error); return
        self.feedback(True, "Enregistré"); self.load(self.contract_id)

    def _dynamic(self) -> None:
        version = self.service.selected_template_version(self.contract_id) if self.contract_id else None
        contract = self.service.get(self.contract_id) if self.contract_id else None
        regime = contract.regime.value if contract and contract.regime else None
        conclusion_visible = bool(version and version.validation.requires_conclusion(regime))
        _row_visible(self.context_form, self.conclusion, conclusion_visible)
        blocks = version.validation.blocks_for(regime, self.conclusion.currentData()) if version else frozenset()
        self.early_performance.setVisible(conclusion_visible and {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE"}.issubset(blocks))
        _row_visible(self.services_form, self.priority_delay, self.priority.currentData() is True)
        standard = self.duration_mode.currentData() == DurationMode.STANDARD.value
        custom = self.duration_mode.currentData() == DurationMode.CUSTOM.value
        _row_visible(self.period_form, self.duration_months, standard); _row_visible(self.period_form, self.end_date, standard or custom)
        self.end_date.setReadOnly(standard); self.end_hint.setVisible(standard); self._update_end_date()
        term = next((item for item in version.catalogs.payment_terms if item.code == self.payment_term.currentData()), None) if version else None
        _row_visible(self.pricing_form, self.payment_due, bool(term and term.requires_day_count))
        _row_visible(self.pricing_form, self.payment_custom, bool(term and term.allows_custom_text))
        renewal = self.renewal_mode.currentData(); active = renewal in {RenewalMode.MANUAL.value, RenewalMode.TACIT.value}
        tacit = renewal == RenewalMode.TACIT.value
        _row_visible(self.renewal_form, self.renewal_period, active); _row_visible(self.renewal_form, self.alert_days, active)
        _row_visible(self.renewal_form, self.renewal_price, active); _row_visible(self.renewal_form, self.notice_days, tacit)
        _row_visible(self.renewal_form, self.channels_host, tacit)

    def _update_end_date(self) -> None:
        if self.loading or self.duration_mode.currentData() != DurationMode.STANDARD.value: return
        try:
            value = ContractConditions(start_date=self.start_date.text(), initial_duration_mode="STANDARD",
                                       initial_duration_months=int(self.duration_months.text())).resolved_end_date
        except (TypeError, ValueError): value = None
        self.end_date.setText(value or ""); self.end_hint.setText("Calculée automatiquement" if value else "")

    def _update_totals(self) -> None:
        try:
            conditions = ContractConditions(annual_ht=self.annual_ht.text().replace(",", ".") or None, vat_rate=self.vat_rate.currentData())
            vat, total = conditions.vat_amount, conditions.annual_ttc
        except Exception: vat = total = None
        self.vat_amount.setText(f"{vat:.2f} €" if vat is not None else "À compléter")
        self.annual_ttc.setText(f"{total:.2f} €" if total is not None else "À compléter")

    def values(self) -> ContractConditions:
        return ContractConditions(
            conclusion_mode=self.conclusion.currentData(), early_performance_requested=self.early_performance.isChecked() if self.early_performance.isVisible() else None,
            visits_per_year=self.visits.text(), refrigerant_handling_mode=self.refrigerant.currentData(), included_area=self.included_area.text(),
            business_hours=self.business_hours.text(), travel_included=self.travel.currentData(), priority_breakdown=self.priority.currentData(),
            priority_breakdown_delay=self.priority_delay.text() or None, included_options=tuple(code for code, box in (("DEEP_CLEANING", self.deep_cleaning), ("DISINFECTION", self.disinfection)) if box.isChecked()),
            additional_exclusions=self.exclusions.toPlainText(), issue_date=self.issue_date.text() or None, start_date=self.start_date.text() or None,
            initial_duration_mode=self.duration_mode.currentData(), initial_duration_months=self.duration_months.text(), initial_end_date=self.end_date.text() or None,
            signature_city=self.signature_city.text(), annual_ht=self.annual_ht.text() or None, vat_rate=self.vat_rate.currentData(),
            payment_terms_code=self.payment_term.currentData(), payment_due_days=self.payment_due.text(), payment_terms_custom_text=self.payment_custom.text(),
            payment_methods=tuple(code for code, box in self.payment_method_checks.items() if box.isChecked()), missed_appointment_fee=self.missed_fee.text() or None,
            renewal_mode=self.renewal_mode.currentData(), renewal_period_months=self.renewal_period.text(), non_renewal_notice_days=self.notice_days.text(),
            non_renewal_notice_channels=tuple(code for code, box in self.channel_checks.items() if box.isChecked()), internal_alert_days=self.alert_days.text(),
            renewal_price_rule=self.renewal_price.currentData(), special_terms=self.special_terms.toPlainText(),
            early_termination_reason_codes=self.service.get_conditions(self.contract_id).early_termination_reason_codes,
            early_termination_custom_text=self.service.get_conditions(self.contract_id).early_termination_custom_text,
            breach_cure_period_days=self.service.get_conditions(self.contract_id).breach_cure_period_days,
        )

    def save(self) -> bool:
        try: saved = self.service.save_conditions(self.contract_id, self.values())
        except ApplicationError as error: self._show_error(error); self.feedback(False, error.user_message); return False
        self.error.hide(); self.feedback(True, "Enregistré"); self.load(self.contract_id); return True

    def _show_error(self, error: ApplicationError) -> None:
        text = "\n".join(error.field_errors.values()) if isinstance(error, ContractConditionsValidationError) else error.user_message
        self.error.setText(text); self.error.show()

    def _open_early(self) -> None:
        if not self.save(): return
        self.open_drawer(EarlyTerminationEditor(self.service, self.contract_id, self._early_saved, lambda: self.open_drawer(None)))

    def _early_saved(self, success: bool, message: str) -> None:
        self.feedback(success, message)
        if success: self.open_drawer(None); self.load(self.contract_id)
