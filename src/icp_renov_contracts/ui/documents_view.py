from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from PySide6.QtWidgets import (
    QComboBox,QDialog,QDialogButtonBox,QFormLayout,QFrame,QHBoxLayout,QLabel,QLineEdit,
    QPlainTextEdit,QPushButton,QVBoxLayout,QWidget,
)

from ..domain import ContractEventType,ContractStatus
from ..errors import ApplicationError
from ..services import ContractLifecycleService


def _date_fr(value: str | None, timestamp: bool = False) -> str:
    if not value:return ""
    try:
        parsed=datetime.fromisoformat(value).astimezone() if timestamp else date.fromisoformat(value)
        return parsed.strftime("%d/%m/%Y %H:%M" if timestamp else "%d/%m/%Y")
    except ValueError:return value


class CorrectionConfirmationDialog(QDialog):
    def __init__(self,parent:QWidget|None=None)->None:
        super().__init__(parent);self.setObjectName("correctionConfirmation");self.setWindowTitle("Corriger le contrat")
        root=QVBoxLayout(self);title=QLabel("Corriger ce contrat ?");title.setObjectName("sectionTitle");root.addWidget(title)
        text=QLabel("La révision actuelle restera conservée. Le contrat repassera en Brouillon afin de permettre les modifications. Aucune nouvelle révision n’est créée maintenant : elle n’existera qu’après une nouvelle génération DOCX/PDF réussie.")
        text.setWordWrap(True);root.addWidget(text)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Corriger le contrat")
        buttons.rejected.connect(self.reject);buttons.accepted.connect(self.accept);root.addWidget(buttons)


class SendRecordingDialog(QDialog):
    def __init__(self,revisions,parent:QWidget|None=None)->None:
        super().__init__(parent);self.setObjectName("sendRecordingDialog");self.setWindowTitle("Enregistrer un envoi")
        root=QVBoxLayout(self);title=QLabel("Enregistrer un envoi effectué");title.setObjectName("sectionTitle");root.addWidget(title)
        info=QLabel("Cette action enregistre un envoi effectué hors de l’application. Aucun message n’est envoyé par ICP Renov.");info.setWordWrap(True);root.addWidget(info)
        form=QFormLayout();self.revision=QComboBox();self.revision.setObjectName("sentRevision")
        for document in revisions:self.revision.addItem(document.revision,document.id)
        self.sent_date=QLineEdit(date.today().strftime("%d/%m/%Y"));self.sent_date.setObjectName("sentDate")
        self.note=QPlainTextEdit();self.note.setObjectName("sentNote");self.note.setMaximumHeight(90)
        form.addRow("Révision envoyée",self.revision);form.addRow("Date d’envoi",self.sent_date);form.addRow("Note",self.note);root.addLayout(form)
        self.error=QLabel();self.error.setObjectName("formError");self.error.hide();root.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer")
        buttons.rejected.connect(self.reject);buttons.accepted.connect(self._accept);root.addWidget(buttons)

    def _accept(self)->None:
        if self.revision.currentData() is None:self.error.setText("Choisissez la révision envoyée.");self.error.show();return
        try:date.fromisoformat(datetime.strptime(self.sent_date.text().strip(),"%d/%m/%Y").date().isoformat())
        except ValueError:self.error.setText("Renseignez une date d’envoi valide.");self.error.show();return
        self.accept()

    def values(self)->tuple[str,str,str]:
        effective=datetime.strptime(self.sent_date.text().strip(),"%d/%m/%Y").date().isoformat()
        return self.revision.currentData(),effective,self.note.toPlainText()


class DocumentsView(QWidget):
    LABELS={
        ContractEventType.CREATED:"Contrat créé",ContractEventType.DOCUMENT_GENERATED:"Révision générée",
        ContractEventType.CONTRACT_SENT:"Envoi enregistré",ContractEventType.REOPENED_FOR_CORRECTION:"Correction ouverte",
    }
    def __init__(self,lifecycle:ContractLifecycleService,changed:Callable[[str,bool],None])->None:
        super().__init__();self.setObjectName("documentsStep");self.lifecycle=lifecycle;self.changed=changed;self.contract_id=None
        self.root=QVBoxLayout(self);self.root.setContentsMargins(0,0,8,0);self.root.setSpacing(12)

    def _clear(self)->None:
        while self.root.count():
            item=self.root.takeAt(0)
            if item.widget():item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child=item.layout().takeAt(0)
                    if child.widget():child.widget().deleteLater()

    def load(self,contract_id:str)->None:
        self.contract_id=contract_id;self._clear();contract=self.lifecycle.contracts.get(contract_id);revisions=self.lifecycle.revisions(contract_id)
        header=QHBoxLayout();words=QVBoxLayout();title=QLabel("Documents & suivi");title.setObjectName("screenTitle");words.addWidget(title)
        words.addWidget(QLabel("Consultez les révisions et enregistrez les actions réellement effectuées."));header.addLayout(words);header.addStretch(1)
        if revisions:
            folder=QPushButton("Ouvrir le dossier");folder.setObjectName("secondaryButton");folder.clicked.connect(self._open_folder);header.addWidget(folder)
            send=QPushButton("Enregistrer un envoi");send.setObjectName("secondaryButton");send.clicked.connect(self._send);header.addWidget(send)
        if contract.status is ContractStatus.TO_SIGN:
            correction=QPushButton("Corriger le contrat");correction.setObjectName("primaryButton");correction.clicked.connect(self._reopen);header.addWidget(correction)
        self.root.addLayout(header)
        heading=QLabel("Révisions contractuelles");heading.setObjectName("sectionTitle");self.root.addWidget(heading)
        if not revisions:
            empty=QLabel("Aucun document");empty.setObjectName("emptyState");self.root.addWidget(empty)
        for index,document in enumerate(revisions):self.root.addWidget(self._revision(document,index==0))
        history_title=QLabel("Historique");history_title.setObjectName("sectionTitle");self.root.addWidget(history_title)
        for event in self.lifecycle.history(contract_id):
            label=self.LABELS.get(event.type)
            if not label:continue
            suffix=""
            if event.document_id:
                linked=next((item for item in revisions if item.id==event.document_id),None)
                if linked:suffix=f" · {linked.revision}"
            if event.type is ContractEventType.CONTRACT_SENT:suffix+=f" · {_date_fr(event.effective_date)}"
            row=QLabel(f"{label}{suffix} · {_date_fr(event.occurred_at,True)}");row.setObjectName("historyEntry");self.root.addWidget(row)
            if event.note:self.root.addWidget(QLabel(event.note))
        other=QLabel("Autres documents");other.setObjectName("sectionTitle");self.root.addWidget(other);self.root.addWidget(QLabel("Aucun autre document"));self.root.addStretch(1)

    def _revision(self,document,latest:bool)->QFrame:
        card=QFrame();card.setObjectName("revisionCard");layout=QVBoxLayout(card);head=QHBoxLayout()
        title=QLabel(document.revision);title.setObjectName("sectionTitle");head.addWidget(title)
        head.addWidget(QLabel("Dernière révision" if latest else "Révision antérieure conservée"));head.addStretch(1);layout.addLayout(head)
        try:version=self.lifecycle.contracts.template_catalog.get_version(document.template_version_id);template=version.display_name
        except Exception:template="Modèle historique"
        layout.addWidget(QLabel(f"Générée le {_date_fr(document.generated_at_utc,True)} · {template}"))
        sent=self.lifecycle.latest_send(document.id);layout.addWidget(QLabel(f"Envoyée le {_date_fr(sent.effective_date)}" if sent else "Non envoyée"))
        actions=QHBoxLayout()
        for kind,text,relpath in (("docx","Ouvrir le DOCX",document.docx_relpath),("pdf","Ouvrir le PDF",document.pdf_relpath)):
            path=self.lifecycle.resolve_document_path(relpath);button=QPushButton(text);button.setObjectName("secondaryButton");button.setEnabled(path is not None)
            button.clicked.connect(lambda checked=False,did=document.id,k=kind:self._open(did,k));actions.addWidget(button)
            if path is None:
                warning=QLabel(f"Un fichier du contrat est introuvable ({kind.upper()})");warning.setObjectName("formError");layout.addWidget(warning)
        actions.addStretch(1);layout.addLayout(actions);return card

    def _open(self,document_id:str,kind:str)->None:
        try:self.lifecycle.open_document(document_id,kind)
        except ApplicationError as error:self.changed(error.user_message,True)

    def _open_folder(self)->None:
        try:self.lifecycle.open_contract_folder(self.contract_id)
        except ApplicationError as error:self.changed(error.user_message,True)

    def _reopen(self)->None:
        dialog=CorrectionConfirmationDialog(self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.reopen_for_correction(self.contract_id)
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.changed("Le contrat est de nouveau en Brouillon. La révision précédente reste conservée.",False)

    def _send(self)->None:
        dialog=SendRecordingDialog(self.lifecycle.revisions(self.contract_id),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.record_send(self.contract_id,*dialog.values())
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("L’envoi effectué hors de l’application a été enregistré.",False)
