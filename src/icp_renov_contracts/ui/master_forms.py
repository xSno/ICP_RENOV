from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..domain import ClientDraft, ClientMaster, EquipmentDraft, EquipmentMaster, SiteDraft, SiteMaster
from ..errors import MasterDataValidationError


class BaseEditor(QWidget):
    """Shared editor content hosted by the in-application master-data drawer."""

    save_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor_title = title
        self.setObjectName("masterDataEditor")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("drawerTitle")
        self.close_button = QPushButton("Fermer")
        self.close_button.setObjectName("secondaryButton")
        self.close_button.clicked.connect(self.cancel_requested)
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(self.close_button)
        root.addLayout(header)

        self.error_label = QLabel()
        self.error_label.setObjectName("formError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("editorScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        form_content = QWidget()
        form_content.setObjectName("editorFormContent")
        self.form = QFormLayout(form_content)
        self.form.setContentsMargins(0, 0, 8, 0)
        self.form.setSpacing(12)
        self.scroll.setWidget(form_content)
        root.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setObjectName("secondaryButton")
        self.save_button = QPushButton("Enregistrer")
        self.save_button.setObjectName("primaryButton")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.save_button.clicked.connect(self.save_requested)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        root.addLayout(footer)

        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.cancel_requested)

    def show_validation(self, error: MasterDataValidationError) -> None:
        self.error_label.setText("\n".join(error.field_errors.values()))
        self.error_label.show()


class ClientEditor(BaseEditor):
    def __init__(self, client: ClientMaster | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Modifier le client" if client else "Nouveau client", parent)
        self.party_type = QComboBox()
        self.party_type.setObjectName("clientPartyType")
        self.party_type.addItem("Personne", "PERSON")
        self.party_type.addItem("Organisation", "ORGANIZATION")
        self.party_type.currentIndexChanged.connect(self._update_party_fields)
        self.first_name = QLineEdit()
        self.last_name = QLineEdit()
        self.organization_name = QLineEdit()
        self.legal_form = QLineEdit()
        self.siret = QLineEdit()
        self.address_line1 = QLineEdit()
        self.address_line2 = QLineEdit()
        self.postal_code = QLineEdit()
        self.city = QLineEdit()
        self.country = QLineEdit("France")
        self.billing_address = QLineEdit()
        self.phone = QLineEdit()
        self.email = QLineEdit()
        self.internal_reference = QLineEdit()
        self.internal_notes = QPlainTextEdit()
        self.internal_notes.setMaximumHeight(80)
        self.proposed_contact_name = QLineEdit()
        self.proposed_contact_role = QLineEdit()
        self._rows = {
            "first_name": ("Prénom *", self.first_name), "last_name": ("Nom *", self.last_name),
            "organization_name": ("Raison sociale *", self.organization_name),
            "legal_form": ("Forme juridique", self.legal_form), "siret": ("SIRET", self.siret),
            "billing_address": ("Adresse de facturation", self.billing_address),
            "proposed_contact_name": ("Contact proposé", self.proposed_contact_name),
            "proposed_contact_role": ("Rôle du contact proposé", self.proposed_contact_role),
        }
        self.form.addRow("Type *", self.party_type)
        for key in ("first_name", "last_name", "organization_name", "legal_form", "siret"):
            self.form.addRow(*self._rows[key])
        self.form.addRow("Adresse ligne 1 *", self.address_line1)
        self.form.addRow("Adresse ligne 2", self.address_line2)
        self.form.addRow("Code postal *", self.postal_code)
        self.form.addRow("Ville *", self.city)
        self.form.addRow("Pays *", self.country)
        self.form.addRow(*self._rows["billing_address"])
        self.form.addRow("Téléphone", self.phone)
        self.form.addRow("E-mail", self.email)
        self.form.addRow("Référence interne", self.internal_reference)
        for key in ("proposed_contact_name", "proposed_contact_role"):
            self.form.addRow(*self._rows[key])
        self.form.addRow("Notes internes", self.internal_notes)
        if client:
            self.party_type.setCurrentIndex(0 if client.party_type == "PERSON" else 1)
            self.party_type.setEnabled(False)
            for name in (
                "first_name", "last_name", "organization_name", "legal_form", "siret",
                "address_line1", "address_line2", "postal_code", "city", "country",
                "billing_address", "phone", "email", "internal_reference",
                "proposed_contact_name", "proposed_contact_role",
            ):
                getattr(self, name).setText(getattr(client, name))
            self.internal_notes.setPlainText(client.internal_notes)
        self._update_party_fields()

    def _update_party_fields(self) -> None:
        person = self.party_type.currentData() == "PERSON"
        for key in ("first_name", "last_name"):
            self._rows[key][1].setVisible(person)
            self.form.labelForField(self._rows[key][1]).setVisible(person)
        for key in (
            "organization_name", "legal_form", "siret", "billing_address",
            "proposed_contact_name", "proposed_contact_role",
        ):
            self._rows[key][1].setVisible(not person)
            self.form.labelForField(self._rows[key][1]).setVisible(not person)

    def draft(self) -> ClientDraft:
        return ClientDraft(
            party_type=self.party_type.currentData(), first_name=self.first_name.text(),
            last_name=self.last_name.text(), organization_name=self.organization_name.text(),
            legal_form=self.legal_form.text(), siret=self.siret.text(),
            address_line1=self.address_line1.text(), address_line2=self.address_line2.text(),
            postal_code=self.postal_code.text(), city=self.city.text(), country=self.country.text(),
            billing_address=self.billing_address.text(), phone=self.phone.text(), email=self.email.text(),
            internal_reference=self.internal_reference.text(), internal_notes=self.internal_notes.toPlainText(),
            proposed_contact_name=self.proposed_contact_name.text(),
            proposed_contact_role=self.proposed_contact_role.text(),
        )


class SiteEditor(BaseEditor):
    def __init__(self, site: SiteMaster | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Modifier le site" if site else "Ajouter un site", parent)
        self.fields = {name: QLineEdit() for name in (
            "label", "address_line1", "address_line2", "postal_code", "city", "country",
            "contact_name", "contact_phone",
        )}
        self.fields["country"].setText("France")
        labels = {
            "label": "Nom du site *", "address_line1": "Adresse ligne 1 *",
            "address_line2": "Adresse ligne 2", "postal_code": "Code postal *",
            "city": "Ville *", "country": "Pays *", "contact_name": "Contact sur site",
            "contact_phone": "Téléphone du contact",
        }
        for name, field in self.fields.items():
            self.form.addRow(labels[name], field)
        self.internal_notes = QPlainTextEdit()
        self.internal_notes.setMaximumHeight(90)
        self.form.addRow("Instructions internes d’accès", self.internal_notes)
        if site:
            for name, field in self.fields.items():
                field.setText(getattr(site, name))
            self.internal_notes.setPlainText(site.internal_notes)

    def draft(self) -> SiteDraft:
        return SiteDraft(
            **{name: field.text() for name, field in self.fields.items()},
            internal_notes=self.internal_notes.toPlainText(),
        )


class EquipmentEditor(BaseEditor):
    def __init__(self, equipment: EquipmentMaster | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Modifier l’équipement" if equipment else "Ajouter un équipement", parent)
        self.equipment_type = QComboBox()
        self.equipment_type.setEditable(True)
        self.equipment_type.addItems(("Unité murale", "Groupe extérieur", "Cassette", "Gainable", "Autre"))
        self.fields = {name: QLineEdit() for name in (
            "brand", "model", "serial_number", "power_kw", "location",
            "installation_date", "internal_reference",
        )}
        self.form.addRow("Type *", self.equipment_type)
        labels = {
            "brand": "Marque", "model": "Modèle", "serial_number": "N° de série",
            "power_kw": "Puissance kW", "location": "Localisation *",
            "installation_date": "Date d’installation (AAAA-MM-JJ)",
            "internal_reference": "Référence interne",
        }
        for name, field in self.fields.items():
            self.form.addRow(labels[name], field)
        self.internal_notes = QPlainTextEdit()
        self.internal_notes.setObjectName("equipmentInternalNotes")
        self.internal_notes.setMaximumHeight(90)
        self.form.addRow("Notes internes", self.internal_notes)
        if equipment:
            self.equipment_type.setCurrentText(equipment.equipment_type)
            for name, field in self.fields.items():
                value = getattr(equipment, name)
                field.setText("" if value is None else str(value))
            self.internal_notes.setPlainText(equipment.internal_notes)

    def draft(self) -> EquipmentDraft:
        power = self.fields["power_kw"].text().strip()
        return EquipmentDraft(
            equipment_type=self.equipment_type.currentText(), location=self.fields["location"].text(),
            brand=self.fields["brand"].text(), model=self.fields["model"].text(),
            serial_number=self.fields["serial_number"].text(), power_kw=power or None,
            installation_date=self.fields["installation_date"].text(),
            internal_reference=self.fields["internal_reference"].text(),
            internal_notes=self.internal_notes.toPlainText(),
        )
