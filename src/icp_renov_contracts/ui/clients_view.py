from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..domain import ClientMaster, EquipmentMaster, SiteMaster
from ..errors import ApplicationError, MasterDataValidationError
from ..services import MasterDataService
from .master_forms import ClientEditor, EquipmentEditor, SiteEditor
from .styles import SPACING


def _button(text: str, handler: Callable, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primaryButton" if primary else "secondaryButton")
    button.clicked.connect(handler)
    return button


class ClientsInstallationsView(QWidget):
    title = "Clients & installations"

    def __init__(self, service: MasterDataService) -> None:
        super().__init__()
        self.service = service
        self.selected_client_id: str | None = None
        self.active_editor = None
        self.setObjectName("clientsInstallationsView")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACING["lg"])

        header = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading = QLabel(self.title)
        heading.setObjectName("screenTitle")
        description = QLabel("Gérez les fiches clients, leurs sites et leurs équipements réutilisables.")
        description.setObjectName("screenDescription")
        heading_box.addWidget(heading)
        heading_box.addWidget(description)
        self.new_client_button = _button("Nouveau client", self.open_new_client, True)
        header.addLayout(heading_box)
        header.addStretch(1)
        header.addWidget(self.new_client_button)
        outer.addLayout(header)

        self.workspace_container = QWidget()
        self.workspace_container.setObjectName("clientsWorkspace")
        split = QHBoxLayout(self.workspace_container)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(SPACING["lg"])
        left = QFrame()
        left.setObjectName("panel")
        left.setFixedWidth(330)
        left_layout = QVBoxLayout(left)
        self.search = QLineEdit()
        self.search.setObjectName("clientSearch")
        self.search.setPlaceholderText("Rechercher un client…")
        self.search.textChanged.connect(self.refresh_clients)
        self.filter = QComboBox()
        self.filter.setObjectName("clientArchiveFilter")
        self.filter.addItem("Actifs", False)
        self.filter.addItem("Archivés", True)
        self.filter.currentIndexChanged.connect(self._filter_changed)
        self.client_list = QListWidget()
        self.client_list.setObjectName("clientList")
        self.client_list.currentItemChanged.connect(self._selection_changed)
        self.list_empty = QLabel("Aucun client. Créez votre première fiche.")
        self.list_empty.setObjectName("emptyState")
        self.list_empty.setWordWrap(True)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.filter)
        left_layout.addWidget(self.client_list, 1)
        left_layout.addWidget(self.list_empty)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("clientDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail = QWidget()
        self.detail.setObjectName("contentSurface")
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"], SPACING["lg"])
        self.detail_scroll.setWidget(self.detail)
        split.addWidget(left)
        split.addWidget(self.detail_scroll, 1)
        self.drawer_host = QFrame(self.workspace_container)
        self.drawer_host.setObjectName("masterDataDrawer")
        self.drawer_layout = QVBoxLayout(self.drawer_host)
        self.drawer_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_host.hide()
        outer.addWidget(self.workspace_container, 1)
        self.feedback = QLabel()
        self.feedback.setObjectName("successFeedback")
        self.feedback.hide()
        outer.addWidget(self.feedback)
        self.refresh_clients()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_drawer()

    def _position_drawer(self) -> None:
        if not hasattr(self, "drawer_host"):
            return
        available = self.workspace_container.size()
        width = min(520, max(390, int(available.width() * 0.48)))
        self.drawer_host.setGeometry(max(0, available.width() - width), 0, width, available.height())
        self.drawer_host.raise_()

    @property
    def archived_filter(self) -> bool:
        return bool(self.filter.currentData())

    def _filter_changed(self) -> None:
        self.selected_client_id = None
        self.refresh_clients()

    def refresh_clients(self, select_id: str | None = None) -> None:
        target = select_id or self.selected_client_id
        summaries = self.service.list_clients(self.search.text(), self.archived_filter)
        self.client_list.blockSignals(True)
        self.client_list.clear()
        target_row = -1
        for row, summary in enumerate(summaries):
            type_label = "Personne" if summary.client.party_type == "PERSON" else "Organisation"
            archive = " · Archivé" if summary.client.archived else ""
            item = QListWidgetItem(
                f"{summary.client.display_name}\n{type_label}{archive} · "
                f"{summary.site_count} site(s) · {summary.equipment_count} équipement(s)"
            )
            item.setData(Qt.ItemDataRole.UserRole, summary.client.id)
            item.setSizeHint(item.sizeHint().expandedTo(self.client_list.sizeHint()).boundedTo(item.sizeHint()))
            self.client_list.addItem(item)
            if summary.client.id == target:
                target_row = row
        self.client_list.blockSignals(False)
        self.list_empty.setVisible(not summaries)
        self.client_list.setVisible(bool(summaries))
        if summaries:
            self.client_list.setCurrentRow(target_row if target_row >= 0 else 0)
        else:
            self.selected_client_id = None
            self.render_detail(None)

    def _selection_changed(self, current: QListWidgetItem | None, previous=None) -> None:
        self.selected_client_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.render_detail(self.service.get_client(self.selected_client_id) if self.selected_client_id else None)

    def _clear_detail(self) -> None:
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget(): child.widget().deleteLater()

    def render_detail(self, client: ClientMaster | None) -> None:
        self._clear_detail()
        if client is None:
            empty = QLabel("Sélectionnez un client pour consulter ses sites et équipements.")
            empty.setObjectName("emptyState")
            self.detail_layout.addWidget(empty)
            self.detail_layout.addStretch(1)
            return
        title_row = QHBoxLayout()
        title = QLabel(client.display_name)
        title.setObjectName("detailTitle")
        title_row.addWidget(title)
        if client.archived:
            archived = QLabel("ARCHIVÉ")
            archived.setObjectName("archivedBadge")
            title_row.addWidget(archived)
        title_row.addStretch(1)
        title_row.addWidget(_button("Modifier", lambda: self.open_edit_client(client)))
        title_row.addWidget(_button("Restaurer" if client.archived else "Archiver", lambda: self.toggle_client(client)))
        self.detail_layout.addLayout(title_row)
        type_label = "Personne" if client.party_type == "PERSON" else "Organisation"
        lines = [f"Type : {type_label}", f"Adresse : {client.rendered_address}"]
        if client.phone: lines.append(f"Téléphone : {client.phone}")
        if client.email: lines.append(f"E-mail : {client.email}")
        if client.internal_reference: lines.append(f"Référence interne : {client.internal_reference}")
        if client.party_type == "ORGANIZATION":
            if client.legal_form: lines.append(f"Forme juridique : {client.legal_form}")
            if client.siret: lines.append(f"SIRET : {client.siret}")
            if client.billing_address: lines.append(f"Adresse de facturation : {client.billing_address}")
            if client.proposed_contact_name:
                contact = client.proposed_contact_name + (f" — {client.proposed_contact_role}" if client.proposed_contact_role else "")
                lines.append(f"Contact proposé (à confirmer dans un futur contrat) : {contact}")
        identity = QLabel("\n".join(lines))
        identity.setWordWrap(True)
        identity.setObjectName("clientIdentity")
        self.detail_layout.addWidget(identity)
        if client.internal_notes:
            notes = QLabel(f"Notes internes : {client.internal_notes}")
            notes.setWordWrap(True)
            notes.setObjectName("internalNotes")
            self.detail_layout.addWidget(notes)
        sites_header = QHBoxLayout()
        sites_title = QLabel("Sites et équipements")
        sites_title.setObjectName("sectionTitle")
        sites_header.addWidget(sites_title)
        sites_header.addStretch(1)
        sites_header.addWidget(_button("Ajouter un site", lambda: self.open_new_site(client.id), True))
        self.detail_layout.addLayout(sites_header)
        sites = self.service.list_sites(client.id)
        if not sites:
            empty = QLabel("Aucun site. Utilisez « Ajouter un site » pour commencer.")
            empty.setObjectName("emptySites")
            self.detail_layout.addWidget(empty)
        for site in sites:
            self.detail_layout.addWidget(self._site_card(site))
        contracts = QLabel("Contrats liés\nAucun contrat lié")
        contracts.setObjectName("linkedContractsEmpty")
        self.detail_layout.addWidget(contracts)
        self.detail_layout.addStretch(1)

    def _site_card(self, site: SiteMaster) -> QWidget:
        card = QFrame()
        card.setObjectName("siteCard")
        card.setProperty("siteId", site.id)
        layout = QVBoxLayout(card)
        row = QHBoxLayout()
        label = QLabel(site.label + (" · ARCHIVÉ" if site.archived else ""))
        label.setObjectName("siteTitle")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(_button("Modifier", lambda: self.open_edit_site(site)))
        row.addWidget(_button("Restaurer" if site.archived else "Archiver", lambda: self.toggle_site(site)))
        layout.addLayout(row)
        details = site.rendered_address
        if site.contact_name:
            details += f"\nContact : {site.contact_name}" + (f" · {site.contact_phone}" if site.contact_phone else "")
        site_details = QLabel(details)
        site_details.setWordWrap(True)
        layout.addWidget(site_details)
        equipment = self.service.list_equipment(site.id)
        if not equipment:
            empty = QLabel("Aucun équipement sur ce site.")
            empty.setObjectName("emptyEquipment")
            layout.addWidget(empty)
        for item in equipment:
            layout.addWidget(self._equipment_row(item))
        layout.addWidget(_button("Ajouter un équipement", lambda: self.open_new_equipment(site.id)))
        return card

    def _equipment_row(self, equipment: EquipmentMaster) -> QWidget:
        row_widget = QFrame()
        row_widget.setObjectName("equipmentRow")
        row_widget.setProperty("equipmentId", equipment.id)
        row = QHBoxLayout(row_widget)
        details = equipment.display_name + f"\n{equipment.location}"
        if equipment.serial_number: details += f" · Série {equipment.serial_number}"
        if equipment.archived: details += " · ARCHIVÉ"
        label = QLabel(details)
        label.setWordWrap(True)
        row.addWidget(label, 1)
        row.addWidget(_button("Modifier", lambda: self.open_edit_equipment(equipment)))
        row.addWidget(_button("Restaurer" if equipment.archived else "Archiver", lambda: self.toggle_equipment(equipment)))
        return row_widget

    def _open_editor(self, editor, save: Callable, success: str, selected_client_id: str | None = None) -> None:
        self.close_editor()
        self.active_editor = editor
        self.drawer_layout.addWidget(editor)

        def submit() -> None:
            try:
                saved = save(editor.draft())
            except MasterDataValidationError as exc:
                editor.show_validation(exc)
                return
            except ApplicationError as exc:
                editor.error_label.setText(exc.user_message)
                editor.error_label.show()
                return
            self.close_editor()
            self.feedback.setText(success)
            self.feedback.show()
            refresh_target = selected_client_id or (
                saved.id if isinstance(saved, ClientMaster) else self.selected_client_id
            )
            self.refresh_clients(refresh_target)
        editor.save_requested.connect(submit)
        editor.cancel_requested.connect(self.close_editor)
        self._position_drawer()
        self.drawer_host.show()
        self.drawer_host.raise_()
        editor.show()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def close_editor(self) -> None:
        if self.active_editor is not None:
            self.drawer_layout.removeWidget(self.active_editor)
            self.active_editor.deleteLater()
            self.active_editor = None
        self.drawer_host.hide()

    def open_new_client(self) -> None:
        editor = ClientEditor(parent=self.drawer_host)
        self._open_editor(editor, self.service.create_client, "Client enregistré.")

    def open_edit_client(self, client: ClientMaster) -> None:
        editor = ClientEditor(client, self.drawer_host)
        self._open_editor(editor, lambda draft: self.service.update_client(client.id, draft), "Client modifié.", client.id)

    def toggle_client(self, client: ClientMaster) -> None:
        (self.service.restore_client if client.archived else self.service.archive_client)(client.id)
        self.feedback.setText("Client restauré." if client.archived else "Client archivé.")
        self.feedback.show()
        self.refresh_clients()

    def open_new_site(self, client_id: str) -> None:
        editor = SiteEditor(parent=self.drawer_host)
        self._open_editor(editor, lambda draft: self.service.create_site(client_id, draft), "Site enregistré.", client_id)

    def open_edit_site(self, site: SiteMaster) -> None:
        editor = SiteEditor(site, self.drawer_host)
        self._open_editor(editor, lambda draft: self.service.update_site(site.id, draft), "Site modifié.", site.client_id)

    def toggle_site(self, site: SiteMaster) -> None:
        (self.service.restore_site if site.archived else self.service.archive_site)(site.id)
        self.refresh_clients(site.client_id)

    def open_new_equipment(self, site_id: str) -> None:
        site = self.service.get_site(site_id)
        editor = EquipmentEditor(parent=self.drawer_host)
        self._open_editor(editor, lambda draft: self.service.create_equipment(site_id, draft), "Équipement enregistré.", site.client_id)

    def open_edit_equipment(self, equipment: EquipmentMaster) -> None:
        site = self.service.get_site(equipment.site_id)
        editor = EquipmentEditor(equipment, self.drawer_host)
        self._open_editor(editor, lambda draft: self.service.update_equipment(equipment.id, draft), "Équipement modifié.", site.client_id)

    def toggle_equipment(self, equipment: EquipmentMaster) -> None:
        site = self.service.get_site(equipment.site_id)
        (self.service.restore_equipment if equipment.archived else self.service.archive_equipment)(equipment.id)
        self.refresh_clients(site.client_id)
