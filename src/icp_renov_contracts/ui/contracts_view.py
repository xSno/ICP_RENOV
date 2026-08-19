from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractScrollArea, QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain import Contract, ContractStatus, SignedCopyState
from ..errors import ApplicationError, MasterDataValidationError
from ..services import (
    AlertSettingsService, BackupSummaryProvider, ContractLifecycleService, ContractOperationalSignalKind,
    ContractRegisterFilter, ContractRegisterService, ContractService, DocumentGenerationService,
    InterventionSheetGenerationService, ReviewService,
)
from .master_forms import BaseEditor, ClientEditor, EquipmentEditor, SiteEditor
from .conditions_view import ConditionsView
from .review_view import ReviewView
from .documents_view import DocumentsView
from .styles import SPACING


def _button(text: str, handler: Callable, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primaryButton" if primary else "secondaryButton")
    button.clicked.connect(handler)
    return button


class ContractRegisterTable(QTableWidget):
    """Table-first register with a single mouse/keyboard row-open path."""
    row_open_requested = Signal(str)

    def count(self) -> int:
        """Compatibility with the accepted pre-S10 list assertion."""
        return self.rowCount()

    def item(self, row: int, column: int | None = None):
        if column is not None:
            return super().item(row, column)
        # Legacy callers received one textual list item. Preserve that read-only shape
        # while the visible surface is now a real table.
        values = [super().item(row, index).text() for index in range(self.columnCount()) if super().item(row, index)]
        return QTableWidgetItem(" · ".join(values))

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentRow() >= 0:
            item = self.item(self.currentRow(), 0)
            if item is not None:
                self.row_open_requested.emit(item.data(Qt.ItemDataRole.UserRole))
                event.accept()
                return
        super().keyPressEvent(event)


class SelectionPanel(QWidget):
    def __init__(self, title: str, searchable: bool = False) -> None:
        super().__init__()
        self.setObjectName("contractDrawerPanel")
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        heading = QLabel(title); heading.setObjectName("drawerTitle")
        self.close_button = _button("Fermer", lambda: None)
        header.addWidget(heading); header.addStretch(1); header.addWidget(self.close_button)
        root.addLayout(header)
        self.search = QLineEdit(); self.search.setPlaceholderText("Rechercher…")
        self.search.setVisible(searchable); root.addWidget(self.search)
        self.list = QListWidget(); root.addWidget(self.list, 1)
        self.cancel_button = _button("Annuler", lambda: None); root.addWidget(self.cancel_button)


class ObservationEditor(QWidget):
    def __init__(self, value: str) -> None:
        super().__init__()
        self.setObjectName("contractObservationEditor")
        root = QVBoxLayout(self)
        header = QHBoxLayout(); title = QLabel("Observation de l’équipement"); title.setObjectName("drawerTitle")
        self.close_button = _button("Fermer", lambda: None)
        header.addWidget(title); header.addStretch(1); header.addWidget(self.close_button); root.addLayout(header)
        note = QLabel("Cette observation appartient uniquement à ce contrat."); note.setObjectName("screenDescription")
        root.addWidget(note)
        self.text = QPlainTextEdit(value); self.text.setObjectName("contractEquipmentObservation"); root.addWidget(self.text, 1)
        footer = QHBoxLayout(); footer.addStretch(1)
        self.cancel_button = _button("Annuler", lambda: None)
        self.save_button = _button("Enregistrer", lambda: None, True)
        footer.addWidget(self.cancel_button); footer.addWidget(self.save_button); root.addLayout(footer)


class ContractsView(QWidget):
    title = "Contrats"
    STEP_LABELS = ("Client, site & équipements", "Conditions du contrat", "Revue", "Documents & suivi")

    def __init__(self, service: ContractService, review_service: ReviewService,
                 generation_service: DocumentGenerationService | None = None,
                 lifecycle_service: ContractLifecycleService | None = None,
                 intervention_service: InterventionSheetGenerationService | None = None,
                 register_service: ContractRegisterService | None = None,
                 backup_provider: BackupSummaryProvider | None = None,
                 alert_settings: AlertSettingsService | None = None, open_diagnostic=None) -> None:
        super().__init__()
        self.service = service
        self.review_service = review_service
        self.generation_service = generation_service
        self.lifecycle_service = lifecycle_service
        self.intervention_service = intervention_service
        self.open_diagnostic_callback = open_diagnostic
        self.register_service = register_service or (
            ContractRegisterService(service, review_service, lifecycle_service, review_service.workspace_service, backup_provider, alert_settings)
            if lifecycle_service else None
        )
        self.contract_id: str | None = None
        self.register_rows = ()
        self.selected_filter = ContractRegisterFilter.ALL
        self.active_drawer: QWidget | None = None
        self.setObjectName("contractsView")
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget(); root.addWidget(self.pages)
        self.landing = self._build_landing(); self.workspace = self._build_workspace()
        self.pages.addWidget(self.landing); self.pages.addWidget(self.workspace)
        self.drawer_host = QFrame(self); self.drawer_host.setObjectName("masterDataDrawer")
        self.drawer_layout = QVBoxLayout(self.drawer_host); self.drawer_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_host.hide()
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.close_drawer)
        self.refresh_drafts()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event); self._position_drawer()
        if hasattr(self, "draft_list"):
            self._apply_register_width()

    def _position_drawer(self) -> None:
        width = min(520, max(390, int(self.width() * .46)))
        self.drawer_host.setGeometry(max(0, self.width() - width), 0, width, self.height())
        self.drawer_host.raise_()

    def _build_landing(self) -> QWidget:
        page = QWidget(); page.setObjectName("contentSurface")
        layout = QVBoxLayout(page); layout.setContentsMargins(SPACING["xl"], SPACING["xl"], SPACING["xl"], SPACING["xl"])
        header = QHBoxLayout(); words = QVBoxLayout()
        heading = QLabel("Contrats"); heading.setObjectName("screenTitle")
        description = QLabel("Retrouvez vos contrats et les actions à traiter."); description.setObjectName("screenDescription")
        words.addWidget(heading); words.addWidget(description); header.addLayout(words); header.addStretch(1)
        self.new_contract_button = _button("Nouveau contrat", self.create_contract, True); header.addWidget(self.new_contract_button)
        layout.addLayout(header)
        self.workspace_notice = QLabel("Le dossier de travail n’est pas accessible en écriture.")
        self.workspace_notice.setObjectName("formError"); self.workspace_notice.setWordWrap(True); self.workspace_notice.hide()
        layout.addWidget(self.workspace_notice)

        banner = QFrame(); banner.setObjectName("operationalBanner"); banner_layout = QHBoxLayout(banner)
        banner_words = QVBoxLayout(); banner_title = QLabel("Actions à traiter maintenant ou prochainement"); banner_title.setObjectName("sectionTitle")
        self.action_counter = QLabel("0 contrat à traiter"); self.action_counter.setObjectName("actionCounter")
        banner_words.addWidget(banner_title); banner_words.addWidget(self.action_counter); banner_layout.addLayout(banner_words, 1)
        self.actions_filter_button = _button("Voir les actions", lambda: self._select_filter(ContractRegisterFilter.ACTIONS))
        banner_layout.addWidget(self.actions_filter_button)
        backup_words = QVBoxLayout(); backup_title = QLabel("Sauvegarde"); backup_title.setObjectName("sectionTitle")
        self.backup_summary_label = QLabel(); self.backup_summary_label.setObjectName("screenDescription")
        self.backup_reminder_label = QLabel(); self.backup_reminder_label.setObjectName("screenDescription")
        backup_words.addWidget(backup_title); backup_words.addWidget(self.backup_summary_label); backup_words.addWidget(self.backup_reminder_label)
        banner_layout.addLayout(backup_words)
        self.backup_button = _button("Sauvegarder maintenant", self._create_backup)
        banner_layout.addWidget(self.backup_button); layout.addWidget(banner)

        self.search_input = QLineEdit(); self.search_input.setObjectName("contractRegisterSearch")
        self.search_input.setPlaceholderText("Rechercher par numéro, client ou site")
        self.search_input.setClearButtonEnabled(True); self.search_input.textChanged.connect(lambda _value: self._render_register())
        layout.addWidget(self.search_input)
        filters = QHBoxLayout(); self.filter_buttons = {}; self.filter_group = QButtonGroup(self); self.filter_group.setExclusive(True)
        for selected, label in ((ContractRegisterFilter.ALL, "Tous"), (ContractRegisterFilter.ACTIONS, "Actions à traiter"),
                                (ContractRegisterFilter.DRAFTS, "Brouillons"), (ContractRegisterFilter.ACTIVE, "Actifs"),
                                (ContractRegisterFilter.TERMINAL, "Terminés")):
            button = _button(label, lambda checked=False, value=selected: self._select_filter(value))
            button.setCheckable(True); button.setObjectName("registerFilter")
            self.filter_group.addButton(button); self.filter_buttons[selected] = button; filters.addWidget(button)
        self.filter_buttons[ContractRegisterFilter.ALL].setChecked(True); filters.addStretch(1); layout.addLayout(filters)

        self.draft_list = ContractRegisterTable(0, 6); self.draft_list.setObjectName("contractRegisterTable")
        self.draft_list.setHorizontalHeaderLabels(("Contrat", "Client & site", "Statut", "Échéance", "Action ou information", "Documents"))
        self.draft_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.draft_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.draft_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.draft_list.setAlternatingRowColors(True); self.draft_list.verticalHeader().hide()
        self.draft_list.setWordWrap(True)
        self.draft_list.setMinimumSize(0, 0)
        self.draft_list.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.draft_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.draft_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.draft_list.horizontalHeader().setStretchLastSection(False)
        self.draft_list.cellClicked.connect(self._open_row)
        self.draft_list.row_open_requested.connect(self.open_contract)
        layout.addWidget(self.draft_list, 1)
        self.draft_empty = QLabel(); self.draft_empty.setObjectName("emptyState"); self.draft_empty.setWordWrap(True); self.draft_empty.hide(); layout.addWidget(self.draft_empty)
        self.open_button = QPushButton(); self.open_button.hide()
        return page

    def _build_workspace(self) -> QWidget:
        page = QWidget(); page.setObjectName("contentSurface")
        layout = QVBoxLayout(page); layout.setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"], SPACING["lg"])
        header = QHBoxLayout(); self.back_button = _button("Retour aux contrats", self.show_landing)
        header.addWidget(self.back_button)
        titles = QVBoxLayout(); self.number_label = QLabel("Brouillon sans numéro"); self.number_label.setObjectName("detailTitle")
        self.workspace_context = QLabel(); self.workspace_context.setObjectName("screenDescription")
        titles.addWidget(self.number_label); titles.addWidget(self.workspace_context); header.addLayout(titles, 1)
        self.status_label = QLabel("Brouillon"); self.status_label.setObjectName("archivedBadge")
        self.save_state = QLabel("Enregistré"); self.save_state.setObjectName("successFeedback")
        header.addWidget(self.status_label); header.addWidget(self.save_state); layout.addLayout(header)
        self.linked_draft_notice = QLabel(
            "Brouillon prérempli depuis le contrat précédent.\n"
            "Vérifiez les dates, les équipements, le régime et les conditions."
        )
        self.linked_draft_notice.setObjectName("successFeedback")
        self.linked_draft_notice.setWordWrap(True)
        self.linked_draft_notice.hide()
        layout.addWidget(self.linked_draft_notice)
        steps = QHBoxLayout(); self.step_buttons = []
        for index, label in enumerate(self.STEP_LABELS):
            button = QPushButton(f"{index + 1}. {label.replace('&', '&&')}"); button.setObjectName("secondaryButton")
            button.setEnabled(True)
            button.clicked.connect(lambda checked=False, target=index: self.navigate_step(target))
            self.step_buttons.append(button); steps.addWidget(button)
        layout.addLayout(steps)
        self.step_pages = QStackedWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.step_content = QWidget(); self.step_content.setObjectName("contractStepOne")
        self.step_layout = QVBoxLayout(self.step_content); scroll.setWidget(self.step_content)
        self.conditions_view = ConditionsView(self.service, self._condition_feedback, self._conditions_drawer)
        conditions_scroll = QScrollArea(); conditions_scroll.setWidgetResizable(True); conditions_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        conditions_scroll.setWidget(self.conditions_view)
        self.review_view = ReviewView(self.review_service, self.navigate_step, self.generation_service, self._generation_finished, self.open_diagnostic_callback)
        review_scroll = QScrollArea(); review_scroll.setWidgetResizable(True); review_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        review_scroll.setWidget(self.review_view)
        self.documents_view = DocumentsView(self.lifecycle_service,self._documents_changed,self._linked_created,self.intervention_service) if self.lifecycle_service else QWidget()
        documents_scroll=QScrollArea();documents_scroll.setWidgetResizable(True);documents_scroll.setFrameShape(QScrollArea.Shape.NoFrame);documents_scroll.setWidget(self.documents_view)
        self.step_pages.addWidget(scroll); self.step_pages.addWidget(conditions_scroll); self.step_pages.addWidget(review_scroll);self.step_pages.addWidget(documents_scroll)
        layout.addWidget(self.step_pages, 1)
        self.feedback = QLabel(); self.feedback.setWordWrap(True); self.feedback.hide(); layout.addWidget(self.feedback)
        return page

    def refresh_drafts(self) -> None:
        if self.register_service is None:
            # S0–S5 isolated UI construction did not inject lifecycle services. Keep
            # that bounded construction path readable while production always uses S10.
            drafts = self.service.list_drafts()
            self.draft_list.setRowCount(len(drafts))
            labels = {ContractStatus.DRAFT: "Brouillon", ContractStatus.TO_SIGN: "À signer", ContractStatus.SIGNED: "Signé",
                      ContractStatus.ACTIVE: "Actif", ContractStatus.TERMINATED: "Résilié", ContractStatus.EXPIRED: "Expiré", ContractStatus.ABANDONED: "Abandonné"}
            for row, draft in enumerate(drafts):
                for column, value in enumerate((draft.number or "Brouillon sans numéro", f"{draft.client_name}\n{draft.site_label}", labels[draft.status], "—", "—", draft.latest_revision and f"{draft.latest_revision} · non signé" or "Aucun document")):
                    item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, draft.id); self.draft_list.setItem(row, column, item)
            self.draft_list.setVisible(bool(drafts)); self.draft_empty.setVisible(not drafts)
            if not drafts: self.draft_empty.setText("Aucun contrat. Créez votre premier contrat.")
            return
        self.register_rows = self.register_service.rows()
        writable = self.register_service.workspace_writable()
        self.workspace_notice.setVisible(not writable); self.new_contract_button.setEnabled(writable)
        self._render_backup(writable); self._render_register()

    def _render_backup(self, writable: bool) -> None:
        if self.register_service is None:
            return
        summary = self.register_service.backup_summary()
        self.backup_summary_label.setText(summary.label); self.backup_reminder_label.setText(summary.reminder)
        self.backup_reminder_label.setVisible(bool(summary.reminder)); self.backup_button.setEnabled(writable and summary.can_create_now)

    def _render_register(self) -> None:
        if self.register_service is None:
            return
        visible = self.register_service.filter_rows(self.register_rows, self.selected_filter, self.search_input.text())
        count = self.register_service.action_count(self.register_rows)
        self.action_counter.setText(f"{count} contrat{'s' if count != 1 else ''} à traiter")
        self.draft_list.setRowCount(len(visible))
        for index, row in enumerate(visible):
            values = (
                row.number_label, f"{row.client_name}\n{row.site_label}", row.status_label,
                row.due_date.strftime("%d/%m/%Y") if row.due_date else "—",
                ("Action —\n" if row.signal.kind is ContractOperationalSignalKind.ACTION else "Information —\n") + row.signal.label
                if row.signal.kind is not ContractOperationalSignalKind.NONE else "—",
                "\n".join(row.document_lines),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, row.contract_id)
                item.setToolTip(value); self.draft_list.setItem(index, column, item)
            self.draft_list.setRowHeight(index, 58)
        empty = not visible
        if empty and not self.register_rows:
            self.draft_empty.setText("Aucun contrat. Créez votre premier contrat.")
        elif empty:
            self.draft_empty.setText("Aucun contrat trouvé\nModifiez la recherche ou le filtre.")
        self.draft_empty.setVisible(empty); self.draft_list.setVisible(not empty)
        self._apply_register_width()
        self.draft_list.resizeRowsToContents()
        for index in range(self.draft_list.rowCount()):
            self.draft_list.setRowHeight(index, max(58, self.draft_list.rowHeight(index)))

    def _select_filter(self, selected: ContractRegisterFilter) -> None:
        self.selected_filter = selected; self.filter_buttons[selected].setChecked(True); self._render_register()

    def _open_row(self, row: int, _column: int) -> None:
        item = self.draft_list.item(row, 0)
        if item is not None:
            self.open_contract(item.data(Qt.ItemDataRole.UserRole))

    def _create_backup(self) -> None:
        provider = self.register_service.backup_provider if self.register_service else None
        create = getattr(provider, "create_now", None)
        if callable(create):
            create(); self.refresh_drafts()

    def _apply_register_width(self) -> None:
        documents_visible = self.window().width() >= 1320
        self.draft_list.setColumnHidden(5, not documents_visible)
        # Keep the operational signal legible first. The document column is allowed
        # to collapse at 1280 px; normal desktop widths retain both document truths.
        available = max(900, self.draft_list.viewport().width())
        fixed = (120, 180, 92, 115)
        document_width = 200 if documents_visible else 0
        signal_width = max(280, available - sum(fixed) - document_width)
        for column, width in enumerate((*fixed, signal_width, document_width)):
            if column != 5 or documents_visible:
                self.draft_list.setColumnWidth(column, width)

    def create_contract(self) -> None:
        if self.register_service and not self.register_service.workspace_writable():
            return
        contract = self.service.create_draft(); self.open_contract(contract.id)

    def _open_selected(self) -> None:
        if self.draft_list.currentRow() >= 0:
            self._open_row(self.draft_list.currentRow(), 0)

    def open_contract(self, contract_id: str) -> None:
        if self.lifecycle_service:self.lifecycle_service.reconcile_due_activations()
        self.contract_id = contract_id; self.pages.setCurrentWidget(self.workspace); self.step_pages.setCurrentIndex(0); self.render_contract()

    def navigate_step(self, index: int) -> None:
        if index not in (0, 1, 2, 3): return
        self.step_pages.setCurrentIndex(index)
        if index == 0: self.render_contract()
        elif index == 1 and self.contract_id:
            self.conditions_view.load(self.contract_id)
            self.conditions_view.setEnabled(self.service.get(self.contract_id).status is ContractStatus.DRAFT)
        if index == 2 and self.contract_id: self.review_view.load(self.contract_id)
        if index == 3 and self.contract_id and self.lifecycle_service:self.documents_view.load(self.contract_id)

    def _condition_feedback(self, success: bool, message: str) -> None:
        self.save_state.setText("Enregistré" if success else "Non enregistré")
        self._feedback(message, not success)

    def _conditions_drawer(self, editor: QWidget | None) -> None:
        if editor is None: self.close_drawer()
        else: self._open_drawer(editor)

    def show_landing(self) -> None:
        self.close_drawer(); self.refresh_drafts(); self.pages.setCurrentWidget(self.landing)

    def _clear_step(self) -> None:
        while self.step_layout.count():
            item = self.step_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget(): child.widget().deleteLater()

    def render_contract(self, notice: str = "") -> None:
        if self.contract_id is None: return
        contract = self.service.get(self.contract_id); self._clear_step()
        locked = contract.status is not ContractStatus.DRAFT
        self.number_label.setText(contract.number or "Brouillon sans numéro")
        self.status_label.setText({ContractStatus.DRAFT:"Brouillon",ContractStatus.TO_SIGN:"À signer",ContractStatus.SIGNED:"Signé",ContractStatus.ACTIVE:"Actif",ContractStatus.TERMINATED:"Résilié",ContractStatus.EXPIRED:"Expiré",ContractStatus.ABANDONED:"Abandonné"}[contract.status])
        self.linked_draft_notice.setVisible(
            contract.status is ContractStatus.DRAFT and contract.predecessor_contract_id is not None
        )
        client_name = contract.client_snapshot.display_name if contract.client_snapshot else "Client à sélectionner"
        site_name = contract.site_snapshot.label if contract.site_snapshot else "Site à sélectionner"
        predecessor=""
        if contract.predecessor_contract_id:
            try:predecessor=f" · Créé depuis le contrat {self.service.get(contract.predecessor_contract_id).number or contract.predecessor_contract_id}"
            except ApplicationError:predecessor=" · Créé depuis un contrat précédent"
        self.workspace_context.setText(f"{client_name} · {site_name}{predecessor}")
        self._section("Client", client_name,
                      [("Changer le client" if contract.client_snapshot else "Sélectionner le client", self.open_client_selector),
                       ("Créer un nouveau client", self.open_new_client)])
        self._signatory(contract)
        site_actions = []
        if contract.client_snapshot:
            site_actions = [("Changer le site" if contract.site_snapshot else "Sélectionner le site", self.open_site_selector),
                            ("Ajouter un site", self.open_new_site)]
        self._section("Site", site_name, site_actions)
        self._equipment_section(contract)
        self.step_layout.addStretch(1)
        for widget_type in (QPushButton, QLineEdit, QCheckBox):
            for widget in self.step_content.findChildren(widget_type): widget.setEnabled(not locked)
        self._feedback(notice, False) if notice else self.feedback.hide()

    def _generation_finished(self, message: str, error: bool) -> None:
        if not error and self.contract_id:
            self.render_contract(message); self.review_view.load(self.contract_id)
        else:
            self._feedback(message, error)

    def _documents_changed(self,message:str,error:bool)->None:
        if not error and self.contract_id:
            current=self.step_pages.currentIndex();self.render_contract();self.step_pages.setCurrentIndex(current)
            if self.lifecycle_service:self.documents_view.load(self.contract_id)
            self.review_view.load(self.contract_id);self.refresh_drafts()
        self._feedback(message,error)

    def _linked_created(self,contract_id:str,message:str)->None:
        self.open_contract(contract_id);self.render_contract(message);self.refresh_drafts()

    def _section(self, title: str, value: str, actions: list[tuple[str, Callable]]) -> None:
        frame = QFrame(); frame.setObjectName("panel"); layout = QVBoxLayout(frame)
        heading = QLabel(title); heading.setObjectName("sectionTitle"); layout.addWidget(heading); layout.addWidget(QLabel(value))
        row = QHBoxLayout()
        for text, handler in actions: row.addWidget(_button(text, handler))
        row.addStretch(1); layout.addLayout(row); self.step_layout.addWidget(frame)

    def _signatory(self, contract: Contract) -> None:
        frame = QFrame(); frame.setObjectName("panel"); layout = QVBoxLayout(frame)
        heading = QLabel("Signataire pour ce contrat"); heading.setObjectName("sectionTitle"); layout.addWidget(heading)
        row = QHBoxLayout(); self.signatory_name = QLineEdit(contract.signatory_name); self.signatory_name.setPlaceholderText("Nom")
        self.signatory_role = QLineEdit(contract.signatory_role); self.signatory_role.setPlaceholderText("Rôle / qualité")
        row.addWidget(self.signatory_name); row.addWidget(self.signatory_role); row.addWidget(_button("Enregistrer", self.save_signatory, True))
        layout.addLayout(row); self.step_layout.addWidget(frame)

    def _equipment_section(self, contract: Contract) -> None:
        frame = QFrame(); frame.setObjectName("panel"); layout = QVBoxLayout(frame)
        heading = QHBoxLayout(); title = QLabel(f"Équipements · {len(contract.equipment_items)} sélectionné(s)"); title.setObjectName("sectionTitle")
        heading.addWidget(title); heading.addStretch(1)
        if contract.site_snapshot: heading.addWidget(_button("Ajouter un équipement", self.open_new_equipment, True))
        layout.addLayout(heading)
        if not contract.site_snapshot:
            layout.addWidget(QLabel("Sélectionnez d’abord un site."))
        else:
            selected = {item.source_equipment_id: item for item in contract.equipment_items}
            active = self.service.selectable_equipment(contract.id)
            rows = [(item.source_equipment_id, item.snapshot.display_name or item.snapshot.equipment_type, item)
                    for item in contract.equipment_items]
            rows.extend((equipment.id, equipment.display_name or equipment.equipment_type, None)
                        for equipment in active if equipment.id not in selected)
            for equipment_id, display_name, item in rows:
                row = QFrame(); row.setObjectName("equipmentRow"); line = QHBoxLayout(row)
                check = QCheckBox(display_name); check.setObjectName("contractEquipmentCheckbox")
                check.setChecked(item is not None); check.toggled.connect(lambda checked, eid=equipment_id: self.toggle_equipment(eid, checked))
                line.addWidget(check, 1)
                if item:
                    line.addWidget(QLabel(f"N° {item.position + 1}"))
                    up = _button("Monter", lambda checked=False, iid=item.id: self.move(iid, -1)); up.setEnabled(item.position > 0)
                    down = _button("Descendre", lambda checked=False, iid=item.id: self.move(iid, 1)); down.setEnabled(item.position < len(contract.equipment_items)-1)
                    line.addWidget(up); line.addWidget(down); line.addWidget(_button("Observation", lambda checked=False, iid=item.id: self.open_observation(iid)))
                layout.addWidget(row)
            if not contract.equipment_items:
                anomaly = QLabel("Aucun équipement sélectionné"); anomaly.setObjectName("formError"); layout.addWidget(anomaly)
        self.step_layout.addWidget(frame)

    def save_signatory(self) -> None:
        self._action(lambda: self.service.update_signatory(self.contract_id, self.signatory_name.text(), self.signatory_role.text()))

    def toggle_equipment(self, equipment_id: str, checked: bool) -> None:
        self._action(lambda: (self.service.select_equipment(self.contract_id, equipment_id) if checked
                              else self.service.deselect_equipment(self.contract_id, equipment_id)))

    def move(self, item_id: str, delta: int) -> None:
        self._action(lambda: self.service.move_equipment(self.contract_id, item_id, delta))

    def _action(self, operation: Callable, notice: str = "") -> None:
        try:
            self.save_state.setText("Enregistrement…"); operation(); self.save_state.setText("Enregistré")
            self.render_contract(notice)
        except ApplicationError as error:
            self.save_state.setText("Non enregistré"); self._feedback(error.user_message, True)

    def _feedback(self, text: str, error: bool) -> None:
        self.feedback.setObjectName("formError" if error else "successFeedback"); self.feedback.setText(text); self.feedback.show()
        self.feedback.style().unpolish(self.feedback); self.feedback.style().polish(self.feedback)

    def _open_drawer(self, editor: QWidget) -> None:
        self.close_drawer()
        while self.drawer_layout.count():
            old = self.drawer_layout.takeAt(0)
            if old.widget(): old.widget().deleteLater()
        self.active_drawer = editor; self.drawer_layout.addWidget(editor); self.drawer_host.show(); self._position_drawer(); editor.setFocus()
        self.escape_shortcut.setEnabled(not isinstance(editor, BaseEditor))

    def close_drawer(self) -> None:
        self.drawer_host.hide(); self.active_drawer = None; self.escape_shortcut.setEnabled(True)

    def open_client_selector(self) -> None:
        panel = SelectionPanel("Sélectionner un client", True)
        def fill():
            panel.list.clear()
            for summary in self.service.selectable_clients(panel.search.text()):
                item = QListWidgetItem(summary.client.display_name); item.setData(Qt.ItemDataRole.UserRole, summary.client.id); panel.list.addItem(item)
        def select(item):
            old = self.service.get(self.contract_id); self.service.select_client(self.contract_id, item.data(Qt.ItemDataRole.UserRole)); self.close_drawer()
            notice = "Le site, les équipements et leurs observations ont été retirés du brouillon." if old.client_source_id else ""
            self.render_contract(notice)
        panel.search.textChanged.connect(fill); panel.list.itemClicked.connect(select)
        panel.close_button.clicked.connect(self.close_drawer); panel.cancel_button.clicked.connect(self.close_drawer); fill(); self._open_drawer(panel)

    def open_site_selector(self) -> None:
        panel = SelectionPanel("Sélectionner un site")
        for site in self.service.selectable_sites(self.contract_id):
            item = QListWidgetItem(site.label); item.setData(Qt.ItemDataRole.UserRole, site.id); panel.list.addItem(item)
        def select(item):
            old = self.service.get(self.contract_id); self.service.select_site(self.contract_id, item.data(Qt.ItemDataRole.UserRole)); self.close_drawer()
            notice = "Les équipements et leurs observations ont été retirés du brouillon." if old.site_source_id else ""
            self.render_contract(notice)
        panel.list.itemClicked.connect(select); panel.close_button.clicked.connect(self.close_drawer); panel.cancel_button.clicked.connect(self.close_drawer)
        self._open_drawer(panel)

    def open_new_client(self) -> None:
        editor = ClientEditor()
        editor.cancel_requested.connect(self.close_drawer)
        def save():
            try: self.service.create_and_select_client(self.contract_id, editor.draft())
            except MasterDataValidationError as error: editor.show_validation(error); return
            except ApplicationError as error: editor.error_label.setText(error.user_message); editor.error_label.show(); return
            self.close_drawer(); self.render_contract()
        editor.save_requested.connect(save); self._open_drawer(editor)

    def open_new_site(self) -> None:
        editor = SiteEditor(); editor.cancel_requested.connect(self.close_drawer)
        def save():
            try: self.service.create_and_select_site(self.contract_id, editor.draft())
            except MasterDataValidationError as error: editor.show_validation(error); return
            except ApplicationError as error: editor.error_label.setText(error.user_message); editor.error_label.show(); return
            self.close_drawer(); self.render_contract()
        editor.save_requested.connect(save); self._open_drawer(editor)

    def open_new_equipment(self) -> None:
        editor = EquipmentEditor(); editor.cancel_requested.connect(self.close_drawer)
        def save():
            try: self.service.create_and_select_equipment(self.contract_id, editor.draft())
            except MasterDataValidationError as error: editor.show_validation(error); return
            except ApplicationError as error: editor.error_label.setText(error.user_message); editor.error_label.show(); return
            self.close_drawer(); self.render_contract()
        editor.save_requested.connect(save); self._open_drawer(editor)

    def open_observation(self, item_id: str) -> None:
        item = next(item for item in self.service.get(self.contract_id).equipment_items if item.id == item_id)
        editor = ObservationEditor(item.observation)
        editor.close_button.clicked.connect(self.close_drawer); editor.cancel_button.clicked.connect(self.close_drawer)
        editor.save_button.clicked.connect(lambda: self._save_observation(item_id, editor.text.toPlainText()))
        self._open_drawer(editor)

    def _save_observation(self, item_id: str, value: str) -> None:
        try: self.service.update_observation(self.contract_id, item_id, value)
        except ApplicationError as error: self._feedback(error.user_message, True); return
        self.close_drawer(); self.render_contract()
