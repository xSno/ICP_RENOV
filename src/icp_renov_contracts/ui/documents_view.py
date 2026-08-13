from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,QDialog,QDialogButtonBox,QFileDialog,QFormLayout,QFrame,QHBoxLayout,QLabel,QLineEdit,
    QPlainTextEdit,QPushButton,QVBoxLayout,QWidget,
)

from ..domain import ContractEventType,ContractStatus,SignedCopyState
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
        try:datetime.strptime(self.sent_date.text().strip(),"%d/%m/%Y")
        except ValueError:self.error.setText("Renseignez une date d’envoi valide.");self.error.show();return
        self.accept()

    def values(self)->tuple[str,str,str]:
        effective=datetime.strptime(self.sent_date.text().strip(),"%d/%m/%Y").date().isoformat()
        return self.revision.currentData(),effective,self.note.toPlainText()


class SignatureRecordingDialog(QDialog):
    def __init__(self,revisions,business_date:date,parent:QWidget|None=None)->None:
        super().__init__(parent);self.setObjectName("signatureRecordingDialog");self.setWindowTitle("Enregistrer la signature")
        self.revisions=tuple(revisions);self.business_date=business_date;self.pdf_path:Path|None=None
        root=QVBoxLayout(self);title=QLabel("Enregistrer la signature");title.setObjectName("sectionTitle");root.addWidget(title)
        info=QLabel("Cette action enregistre une signature réalisée hors de l’application. ICP Renov ne réalise pas de signature électronique et ne vérifie pas la signature.")
        info.setWordWrap(True);root.addWidget(info)
        form=QFormLayout();self.revision=QComboBox();self.revision.setObjectName("signedRevision")
        for document in self.revisions:self.revision.addItem(document.revision,document.id)
        self.signature_date=QLineEdit(business_date.strftime("%d/%m/%Y"));self.signature_date.setObjectName("signatureDate")
        self.start_date=QLineEdit();self.start_date.setObjectName("signedStartDate");self.start_date.setReadOnly(True)
        pdf_row=QHBoxLayout();self.pdf=QLineEdit();self.pdf.setObjectName("signedPdf");self.pdf.setReadOnly(True)
        choose=QPushButton("Choisir…");choose.setObjectName("secondaryButton");choose.clicked.connect(self._choose);pdf_row.addWidget(self.pdf,1);pdf_row.addWidget(choose)
        form.addRow("Révision réellement signée",self.revision);form.addRow("Date de signature",self.signature_date)
        form.addRow("Date de prise d’effet",self.start_date);form.addRow("PDF signé — facultatif",pdf_row);root.addLayout(form)
        self.warning=QLabel();self.warning.setObjectName("formError");self.warning.setWordWrap(True);self.warning.hide();root.addWidget(self.warning)
        self.consequence=QLabel();self.consequence.setObjectName("successFeedback");root.addWidget(self.consequence)
        self.error=QLabel();self.error.setObjectName("formError");self.error.hide();root.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer la signature")
        buttons.rejected.connect(self.reject);buttons.accepted.connect(self._accept);root.addWidget(buttons)
        self.revision.currentIndexChanged.connect(self._refresh);self._refresh()

    def _selected(self):return next(item for item in self.revisions if item.id==self.revision.currentData())
    def _refresh(self)->None:
        selected=self._selected();raw=selected.snapshot.get("contract",{}).get("start_date")
        try:start=date.fromisoformat(raw);self.start_date.setText(_date_fr(raw));self.consequence.setText("Conséquence : Actif" if start<=self.business_date else "Conséquence : Signé")
        except (TypeError,ValueError):self.start_date.setText("Date invalide");self.consequence.setText("")
        latest=self.revisions[0]
        self.warning.setVisible(selected.id!=latest.id)
        if selected.id!=latest.id:self.warning.setText(f"Vous enregistrez la signature de {selected.revision} alors que {latest.revision} est la dernière révision générée.\nLa révision signée sera {selected.revision}.")

    def _choose(self)->None:
        value,_=QFileDialog.getOpenFileName(self,"Choisir le PDF signé","","Documents PDF (*.pdf)")
        if value:self.pdf_path=Path(value);self.pdf.setText(value)

    def _accept(self)->None:
        try:datetime.strptime(self.signature_date.text().strip(),"%d/%m/%Y")
        except ValueError:self.error.setText("Renseignez une date de signature valide.");self.error.show();return
        if self.start_date.text()=="Date invalide":self.error.setText("La date de prise d’effet de la révision signée est invalide.");self.error.show();return
        self.accept()

    def values(self)->tuple[str,str,Path|None]:
        effective=datetime.strptime(self.signature_date.text().strip(),"%d/%m/%Y").date().isoformat()
        return self.revision.currentData(),effective,self.pdf_path


class DocumentsView(QWidget):
    LABELS={ContractEventType.CREATED:"Contrat créé",ContractEventType.DOCUMENT_GENERATED:"Révision générée",
            ContractEventType.CONTRACT_SENT:"Envoi enregistré",ContractEventType.REOPENED_FOR_CORRECTION:"Correction ouverte"}
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
        self.contract_id=contract_id;failures=self.lifecycle.reconcile_due_activations();self._clear()
        contract=self.lifecycle.contracts.get(contract_id);revisions=self.lifecycle.revisions(contract_id);authority=self.lifecycle.signature_authority(contract_id)
        header=QHBoxLayout();words=QVBoxLayout();title=QLabel("Documents & suivi");title.setObjectName("screenTitle");words.addWidget(title)
        words.addWidget(QLabel("Consultez les révisions et enregistrez les actions réellement effectuées."));header.addLayout(words);header.addStretch(1)
        if revisions:
            folder=QPushButton("Ouvrir le dossier");folder.setObjectName("secondaryButton");folder.clicked.connect(self._open_folder);header.addWidget(folder)
            send=QPushButton("Enregistrer un envoi");send.setObjectName("secondaryButton");send.clicked.connect(self._send);header.addWidget(send)
        if contract.status is ContractStatus.TO_SIGN:
            sign=QPushButton("Enregistrer la signature");sign.setObjectName("primaryButton");sign.clicked.connect(self._sign);header.addWidget(sign)
            correction=QPushButton("Corriger le contrat");correction.setObjectName("secondaryButton");correction.clicked.connect(self._reopen);header.addWidget(correction)
        self.root.addLayout(header)
        if contract_id in failures:
            notice=QLabel("La date de prise d’effet est atteinte, mais le changement d’état n’a pas pu être enregistré. Vérifiez le stockage.");notice.setObjectName("formError");notice.setWordWrap(True);self.root.addWidget(notice)
        if authority:self.root.addWidget(self._signature_summary(authority,contract.status))
        heading=QLabel("Révisions contractuelles");heading.setObjectName("sectionTitle");self.root.addWidget(heading)
        if not revisions:
            empty=QLabel("Aucun document");empty.setObjectName("emptyState");self.root.addWidget(empty)
        for index,document in enumerate(revisions):self.root.addWidget(self._revision(document,index==0,authority))
        history_title=QLabel("Historique");history_title.setObjectName("sectionTitle");self.root.addWidget(history_title)
        for event in self.lifecycle.history(contract_id):
            linked=next((item for item in revisions if item.id==event.document_id),None)
            if event.type is ContractEventType.SIGNATURE_RECORDED:
                row=QLabel(f"Signature enregistrée · {linked.revision if linked else ''} · {_date_fr(event.effective_date)}")
            elif event.type is ContractEventType.ACTIVATED:
                row=QLabel(f"Contrat actif depuis le {_date_fr(event.effective_date)}")
            else:
                label=self.LABELS.get(event.type)
                if not label:continue
                suffix=f" · {linked.revision}" if linked else ""
                if event.type is ContractEventType.CONTRACT_SENT:suffix+=f" · {_date_fr(event.effective_date)}"
                row=QLabel(f"{label}{suffix} · {_date_fr(event.occurred_at,True)}")
            row.setObjectName("historyEntry");self.root.addWidget(row)
            if event.note:self.root.addWidget(QLabel(event.note))
        if authority and authority.document.signed_pdf_attached_at:
            row=QLabel(f"Copie signée archivée · {authority.document.revision} · {_date_fr(authority.document.signed_pdf_attached_at,True)}")
            row.setObjectName("historyEntry");self.root.addWidget(row)
        other=QLabel("Autres documents");other.setObjectName("sectionTitle");self.root.addWidget(other);self.root.addWidget(QLabel("Aucun autre document"));self.root.addStretch(1)

    def _signature_summary(self,authority,status)->QFrame:
        card=QFrame();card.setObjectName("signatureSummary");layout=QVBoxLayout(card)
        title=QLabel("Signature enregistrée");title.setObjectName("sectionTitle");layout.addWidget(title)
        layout.addWidget(QLabel(f"Révision signée : {authority.document.revision}"));layout.addWidget(QLabel(f"Date de signature : {_date_fr(authority.event.effective_date)}"))
        layout.addWidget(QLabel(f"Date de prise d’effet : {_date_fr(authority.start_date.isoformat())}"))
        if status is ContractStatus.SIGNED:layout.addWidget(QLabel(f"Prise d’effet prévue le {_date_fr(authority.start_date.isoformat())}"))
        state=self.lifecycle.signed_copy_state(authority.document)
        if state is SignedCopyState.NONE:
            layout.addWidget(QLabel("Signature enregistrée — copie signée non archivée"));button=QPushButton("Ajouter le PDF signé");button.clicked.connect(self._add_copy);layout.addWidget(button)
        elif state is SignedCopyState.VALID:layout.addWidget(QLabel("Copie signée"))
        else:
            text="Copie signée introuvable dans le dossier de travail" if state is SignedCopyState.MISSING else "La copie signée ne correspond plus au fichier archivé."
            warning=QLabel(text);warning.setObjectName("formError");layout.addWidget(warning);row=QHBoxLayout()
            locate=QPushButton("Localiser le fichier");locate.clicked.connect(self._locate_copy);replace=QPushButton("Ajouter une nouvelle copie");replace.clicked.connect(self._replace_copy);row.addWidget(locate);row.addWidget(replace);layout.addLayout(row)
        return card

    def _revision(self,document,latest:bool,authority)->QFrame:
        signed=bool(authority and authority.document.id==document.id);card=QFrame();card.setObjectName("revisionCard");layout=QVBoxLayout(card);head=QHBoxLayout()
        title=QLabel(document.revision);title.setObjectName("sectionTitle");head.addWidget(title)
        if latest:head.addWidget(QLabel("Dernière révision"))
        elif not signed:head.addWidget(QLabel("Révision antérieure conservée"))
        if signed:head.addWidget(QLabel("Révision signée"))
        head.addStretch(1);layout.addLayout(head)
        try:version=self.lifecycle.contracts.template_catalog.get_version(document.template_version_id);template=version.display_name
        except Exception:template="Modèle historique"
        layout.addWidget(QLabel(f"Générée le {_date_fr(document.generated_at_utc,True)} · {template}"))
        sent=self.lifecycle.latest_send(document.id);layout.addWidget(QLabel(f"Envoyée le {_date_fr(sent.effective_date)}" if sent else "Non envoyée"))
        if signed:layout.addWidget(QLabel(f"Signée le {_date_fr(authority.event.effective_date)}"))
        actions=QHBoxLayout()
        for kind,text,relpath in (("docx","Ouvrir le DOCX",document.docx_relpath),("pdf","Ouvrir le PDF généré",document.pdf_relpath)):
            path=self.lifecycle.resolve_document_path(relpath);button=QPushButton(text);button.setObjectName("secondaryButton");button.setEnabled(path is not None)
            button.clicked.connect(lambda checked=False,did=document.id,k=kind:self._open(did,k));actions.addWidget(button)
            if path is None:
                warning=QLabel(f"Un fichier du contrat est introuvable ({kind.upper()})");warning.setObjectName("formError");layout.addWidget(warning)
        if signed and self.lifecycle.signed_copy_state(document) is SignedCopyState.VALID:
            button=QPushButton("Ouvrir le PDF signé");button.setObjectName("secondaryButton");button.clicked.connect(lambda checked=False,did=document.id:self._open(did,"signed"));actions.addWidget(button)
        actions.addStretch(1);layout.addLayout(actions);return card

    def _pick(self,title:str)->Path|None:
        value,_=QFileDialog.getOpenFileName(self,title,"","Documents PDF (*.pdf)");return Path(value) if value else None
    def _sign(self)->None:
        dialog=SignatureRecordingDialog(self.lifecycle.revisions(self.contract_id),self.lifecycle.date_provider.today(),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.record_signature(self.contract_id,*dialog.values())
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("La signature réalisée hors de l’application a été enregistrée.",False)
    def _add_copy(self)->None:
        if not (path:=self._pick("Ajouter le PDF signé")):return
        try:self.lifecycle.add_signed_copy(self.contract_id,path)
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("Le PDF signé a été archivé.",False)
    def _locate_copy(self)->None:
        if not (path:=self._pick("Localiser le fichier")):return
        try:self.lifecycle.locate_signed_copy(self.contract_id,path)
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("La copie signée originale a été restaurée.",False)
    def _replace_copy(self)->None:
        if not (path:=self._pick("Ajouter une nouvelle copie")):return
        try:self.lifecycle.replace_signed_copy(self.contract_id,path)
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("La nouvelle copie signée a été archivée.",False)
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
