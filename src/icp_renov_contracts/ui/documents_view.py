from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,QDialog,QDialogButtonBox,QFileDialog,QFormLayout,QFrame,QHBoxLayout,QLabel,QLineEdit,
    QPlainTextEdit,QPushButton,QVBoxLayout,QWidget,
)

from ..domain import ContractEventType,ContractStatus,SignedCopyState,standard_end_date
from ..errors import ApplicationError
from ..services import ContractLifecycleService,LifecycleProjection


def _date_fr(value: str | None, timestamp: bool = False) -> str:
    if not value:return ""
    try:
        parsed=datetime.fromisoformat(value).astimezone() if timestamp else date.fromisoformat(value)
        return parsed.strftime("%d/%m/%Y %H:%M" if timestamp else "%d/%m/%Y")
    except ValueError:return value


def _money(value)->str:
    return f"{Decimal(str(value)):.2f}".replace(".",",")


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


class LinkedDraftConfirmationDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName("linkedDraftConfirmation");self.setWindowTitle("Préparer un nouveau contrat ?")
        root=QVBoxLayout(self);title=QLabel("Préparer un nouveau contrat ?");title.setObjectName("sectionTitle");root.addWidget(title)
        text=QLabel("Un nouveau Brouillon lié sera prérempli depuis le contrat signé. Vérifiez les dates, les équipements, le régime et les conditions. Le contrat actuel reste inchangé ; aucun document ni numéro n’est créé maintenant.");text.setWordWrap(True);root.addWidget(text)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok);buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Créer le brouillon lié");buttons.rejected.connect(self.reject);buttons.accepted.connect(self.accept);root.addWidget(buttons)


class RenewalConfirmationDialog(QDialog):
    def __init__(self,projection:LifecycleProjection,parent=None):
        super().__init__(parent);self.projection=projection;self.setObjectName("renewalConfirmation");self.setWindowTitle("Confirmer la reconduction")
        root=QVBoxLayout(self);title=QLabel("Confirmer la reconduction");title.setObjectName("sectionTitle");root.addWidget(title);form=QFormLayout()
        start=projection.period.end+timedelta(days=1);end=date.fromisoformat(standard_end_date(start.isoformat(),projection.renewal_period_months))
        form.addRow("Période actuelle",QLabel(f"{_date_fr(projection.period.start.isoformat())} → {_date_fr(projection.period.end.isoformat())}"));form.addRow("Prochaine période",QLabel(f"{_date_fr(start.isoformat())} → {_date_fr(end.isoformat())}"));form.addRow("Durée",QLabel(f"{projection.renewal_period_months} mois"));form.addRow("Règle de prix",QLabel("Prix fixe" if projection.renewal_price_rule=="FIXED" else "Nouveau prix à la reconduction"))
        self.annual_ht=QLineEdit();self.annual_ht.setObjectName("renewalAnnualHt");self.vat_rate=QLineEdit();self.vat_rate.setObjectName("renewalVatRate")
        if projection.renewal_price_rule=="FIXED":form.addRow("Prix reconduit",QLabel(f"{_money(projection.price.annual_ht)} € HT · {_money(projection.price.vat_amount)} € TVA · {_money(projection.price.annual_ttc)} € TTC"))
        else:form.addRow("Nouveau prix annuel HT",self.annual_ht);form.addRow("Taux de TVA",self.vat_rate)
        root.addLayout(form);self.error=QLabel();self.error.setObjectName("formError");self.error.hide();root.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok);buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirmer la reconduction");buttons.rejected.connect(self.reject);buttons.accepted.connect(self._accept);root.addWidget(buttons)
    def _accept(self):
        if self.projection.renewal_price_rule=="NEW_PRICE_ON_RENEWAL":
            try:
                if Decimal(self.annual_ht.text().replace(",","."))<0 or Decimal(self.vat_rate.text().replace(",","."))<0:raise InvalidOperation
            except (InvalidOperation,ValueError):self.error.setText("Renseignez un prix annuel HT et un taux de TVA valides.");self.error.show();return
        self.accept()
    def values(self):return (self.annual_ht.text().replace(",",".") or None,self.vat_rate.text().replace(",",".") or None)


class NonRenewalDialog(QDialog):
    def __init__(self,projection:LifecycleProjection,business_date:date,parent=None):
        super().__init__(parent);self.setObjectName("nonRenewalDialog");self.setWindowTitle("Enregistrer le non-renouvellement");root=QVBoxLayout(self)
        title=QLabel("Enregistrer le non-renouvellement");title.setObjectName("sectionTitle");root.addWidget(title);info=QLabel("Cette action enregistre une notification réellement effectuée hors de l’application. ICP Renov n’envoie aucun message automatiquement.");info.setWordWrap(True);root.addWidget(info)
        form=QFormLayout();form.addRow("Fin de période",QLabel(_date_fr(projection.period.end.isoformat())));form.addRow("Échéance contractuelle",QLabel(_date_fr(projection.non_renewal_deadline.isoformat()) if projection.non_renewal_deadline else "Non renseignée"));form.addRow("Canaux prévus",QLabel(", ".join(projection.notice_channels) or "Non renseignés"));self.notification_date=QLineEdit(business_date.strftime("%d/%m/%Y"));self.notification_date.setObjectName("nonRenewalDate");self.note=QPlainTextEdit();self.note.setMaximumHeight(80);form.addRow("Date réelle de notification",self.notification_date);form.addRow("Note",self.note);root.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok);buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer");buttons.rejected.connect(self.reject);buttons.accepted.connect(self._accept);root.addWidget(buttons)
    def _accept(self):
        try:datetime.strptime(self.notification_date.text(),"%d/%m/%Y")
        except ValueError:return
        self.accept()
    def values(self):return datetime.strptime(self.notification_date.text(),"%d/%m/%Y").date().isoformat(),self.note.toPlainText()


class TerminationDialog(QDialog):
    def __init__(self,business_date:date,parent=None):
        super().__init__(parent);self.setObjectName("terminationDialog");self.setWindowTitle("Programmer une résiliation");root=QVBoxLayout(self);title=QLabel("Programmer une résiliation");title.setObjectName("sectionTitle");root.addWidget(title);form=QFormLayout()
        self.notification=QLineEdit();self.notification.setObjectName("terminationNotificationDate");self.effective=QLineEdit(business_date.strftime("%d/%m/%Y"));self.effective.setObjectName("terminationEffectiveDate");self.reason=QLineEdit();self.reason.setObjectName("terminationReason");self.note=QPlainTextEdit();self.note.setMaximumHeight(80)
        form.addRow("Date de notification — facultatif",self.notification);form.addRow("Date effective de fin",self.effective);form.addRow("Motif",self.reason);form.addRow("Note",self.note);root.addLayout(form);self.error=QLabel();self.error.setObjectName("formError");self.error.hide();root.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok);buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer");buttons.rejected.connect(self.reject);buttons.accepted.connect(self._accept);root.addWidget(buttons)
    def _accept(self):
        try:datetime.strptime(self.effective.text(),"%d/%m/%Y");datetime.strptime(self.notification.text(),"%d/%m/%Y") if self.notification.text().strip() else None
        except ValueError:self.error.setText("Renseignez des dates valides.");self.error.show();return
        if not self.reason.text().strip():self.error.setText("Renseignez le motif de la résiliation.");self.error.show();return
        self.accept()
    def values(self):return (datetime.strptime(self.effective.text(),"%d/%m/%Y").date().isoformat(),self.reason.text(),datetime.strptime(self.notification.text(),"%d/%m/%Y").date().isoformat() if self.notification.text().strip() else None,self.note.toPlainText())


class AbandonConfirmationDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName("abandonConfirmation");self.setWindowTitle("Abandonner le contrat");root=QVBoxLayout(self);title=QLabel("Abandonner ce contrat ?");title.setObjectName("sectionTitle");root.addWidget(title);text=QLabel("Le contrat deviendra Abandonné. Les révisions et l’historique resteront conservés. Les données Client, Site et Équipement ne seront pas supprimées.");text.setWordWrap(True);root.addWidget(text);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok);buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler");buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Abandonner le contrat");buttons.rejected.connect(self.reject);buttons.accepted.connect(self.accept);root.addWidget(buttons)


class DocumentsView(QWidget):
    LABELS={ContractEventType.CREATED:"Contrat créé",ContractEventType.DOCUMENT_GENERATED:"Révision générée",
            ContractEventType.CONTRACT_SENT:"Envoi enregistré",ContractEventType.REOPENED_FOR_CORRECTION:"Correction ouverte"}
    def __init__(self,lifecycle:ContractLifecycleService,changed:Callable[[str,bool],None],open_linked:Callable[[str,str],None]|None=None)->None:
        super().__init__();self.setObjectName("documentsStep");self.lifecycle=lifecycle;self.changed=changed;self.open_linked=open_linked;self.contract_id=None
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
        self.contract_id=contract_id;failures=self.lifecycle.reconcile_lifecycle();self._clear()
        contract=self.lifecycle.contracts.get(contract_id);revisions=self.lifecycle.revisions(contract_id);authority=self.lifecycle.signature_authority(contract_id)
        header=QHBoxLayout();words=QVBoxLayout();title=QLabel("Documents & suivi");title.setObjectName("screenTitle");words.addWidget(title)
        words.addWidget(QLabel("Consultez les révisions et enregistrez les actions réellement effectuées."));header.addLayout(words);header.addStretch(1)
        if revisions:
            folder=QPushButton("Ouvrir le dossier");folder.setObjectName("secondaryButton");folder.clicked.connect(self._open_folder);header.addWidget(folder)
            send=QPushButton("Enregistrer un envoi");send.setObjectName("secondaryButton");send.clicked.connect(self._send);header.addWidget(send)
        if contract.status is ContractStatus.TO_SIGN:
            sign=QPushButton("Enregistrer la signature");sign.setObjectName("primaryButton");sign.clicked.connect(self._sign);header.addWidget(sign)
            correction=QPushButton("Corriger le contrat");correction.setObjectName("secondaryButton");correction.clicked.connect(self._reopen);header.addWidget(correction)
        if contract.status in {ContractStatus.DRAFT,ContractStatus.TO_SIGN}:
            abandon=QPushButton("Abandonner le contrat");abandon.setObjectName("secondaryButton");abandon.clicked.connect(self._abandon);header.addWidget(abandon)
        if contract.status in {ContractStatus.SIGNED,ContractStatus.ACTIVE}:
            try:pending=self.lifecycle.lifecycle_projection(contract_id).pending_termination
            except ApplicationError:pending=True
            if not pending:
                termination=QPushButton("Programmer une résiliation");termination.setObjectName("secondaryButton");termination.clicked.connect(self._termination);header.addWidget(termination)
        self.root.addLayout(header)
        if contract_id in failures:
            notice=QLabel("La date de prise d’effet est atteinte, mais le changement d’état n’a pas pu être enregistré. Vérifiez le stockage.");notice.setObjectName("formError");notice.setWordWrap(True);self.root.addWidget(notice)
        if authority:self.root.addWidget(self._signature_summary(authority,contract.status))
        if authority:self.root.addWidget(self._lifecycle_group(contract.status))
        elif contract.status is ContractStatus.ABANDONED:
            terminal=QLabel("Contrat abandonné");terminal.setObjectName("sectionTitle");self.root.addWidget(terminal)
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
            elif event.type is ContractEventType.RENEWAL_NOTICE_RECORDED:row=QLabel(f"Non-renouvellement enregistré · {_date_fr(event.effective_date)}")
            elif event.type is ContractEventType.RENEWAL_CONFIRMED:
                row=QLabel(f"Reconduction confirmée · période du {_date_fr(event.period_start)} au {_date_fr(event.period_end)} · {_money(event.renewal_annual_ht)} € HT · TVA {_money(event.renewal_vat_rate)} % · {_money(event.renewal_vat_amount)} € · {_money(event.renewal_annual_ttc)} € TTC")
            elif event.type is ContractEventType.TERMINATION_SCHEDULED:row=QLabel(f"Résiliation programmée pour le {_date_fr(event.effective_date)}")
            elif event.type is ContractEventType.TERMINATED:row=QLabel(f"Contrat résilié depuis le {_date_fr(event.effective_date)}")
            elif event.type is ContractEventType.EXPIRED:row=QLabel(f"Contrat expiré le {_date_fr(event.effective_date)}")
            elif event.type is ContractEventType.ABANDONED:row=QLabel("Contrat abandonné")
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

    def _lifecycle_group(self,status:ContractStatus)->QFrame:
        card=QFrame();card.setObjectName("lifecycleGroup");layout=QVBoxLayout(card);title=QLabel("Cycle du contrat");title.setObjectName("sectionTitle");layout.addWidget(title)
        projection=self.lifecycle.lifecycle_projection(self.contract_id)
        if status in {ContractStatus.TERMINATED,ContractStatus.EXPIRED,ContractStatus.ABANDONED}:
            layout.addWidget(QLabel({ContractStatus.TERMINATED:"Résilié",ContractStatus.EXPIRED:"Expiré",ContractStatus.ABANDONED:"Abandonné"}[status]));return card
        layout.addWidget(QLabel(f"Période actuelle : {_date_fr(projection.period.start.isoformat())} → {_date_fr(projection.period.end.isoformat())}"))
        pending=projection.pending_termination
        if pending:
            layout.addWidget(QLabel(f"Résiliation programmée\nFin effective le {_date_fr(pending.effective_date)}"))
            if pending.notification_date:layout.addWidget(QLabel(f"Notification du {_date_fr(pending.notification_date)}"))
            if pending.reason_text:layout.addWidget(QLabel(f"Motif : {pending.reason_text}"))
        if status is ContractStatus.SIGNED:return card
        mode=projection.renewal_mode
        layout.addWidget(QLabel({"NONE":"Renouvellement\nAucun","MANUAL":"Renouvellement manuel","TACIT":"Reconduction tacite"}.get(mode,"Renouvellement non déterminé")))
        if mode=="NONE":layout.addWidget(QLabel(f"Fin contractuelle prévue le {_date_fr(projection.period.end.isoformat())}"))
        if projection.next_attention_date:layout.addWidget(QLabel(f"Attention interne : {_date_fr(projection.next_attention_date.isoformat())}"))
        if projection.non_renewal_deadline:layout.addWidget(QLabel(f"Échéance contractuelle de non-renouvellement : {_date_fr(projection.non_renewal_deadline.isoformat())}"))
        if projection.notice_channels:layout.addWidget(QLabel(f"Canaux prévus : {', '.join(projection.notice_channels)}"))
        if mode in {"MANUAL","TACIT"}:layout.addWidget(QLabel(f"Règle de prix : {'Prix fixe' if projection.renewal_price_rule=='FIXED' else 'Nouveau prix à la reconduction'}"))
        if projection.non_renewal_event:
            layout.addWidget(QLabel(f"Non-renouvellement enregistré\nDate de notification : {_date_fr(projection.non_renewal_event.effective_date)}\nFin prévue : {_date_fr(projection.period.end.isoformat())}"))
        elif mode=="TACIT" and projection.next_attention_date and self.lifecycle.date_provider.today()>=projection.next_attention_date:
            signal=QLabel("Reconduction à confirmer");signal.setObjectName("formError");layout.addWidget(signal)
        elif mode=="MANUAL" and projection.next_attention_date and self.lifecycle.date_provider.today()>=projection.next_attention_date:
            signal=QLabel("Renouvellement à préparer");signal.setObjectName("formError");layout.addWidget(signal)
        actions=QHBoxLayout()
        if mode=="MANUAL":
            button=QPushButton("Préparer le renouvellement");button.setObjectName("primaryButton");button.clicked.connect(self._linked_draft);actions.addWidget(button)
        elif mode=="TACIT" and not projection.non_renewal_event and not pending:
            renew=QPushButton("Confirmer la reconduction");renew.setObjectName("primaryButton");renew.clicked.connect(self._renew);actions.addWidget(renew)
            notice=QPushButton("Enregistrer une fin de contrat");notice.setObjectName("secondaryButton");notice.clicked.connect(self._nonrenewal);actions.addWidget(notice)
        if mode in {"NONE","TACIT"}:
            linked=QPushButton("Créer un nouveau contrat lié");linked.setObjectName("secondaryButton");linked.clicked.connect(self._linked_draft);actions.addWidget(linked)
        actions.addStretch(1);layout.addLayout(actions);return card

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
    def _linked_draft(self)->None:
        dialog=LinkedDraftConfirmationDialog(self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:linked=self.lifecycle.create_linked_draft(self.contract_id)
        except ApplicationError as error:self.changed(error.user_message,True);return
        message="Brouillon prérempli depuis le contrat précédent. Vérifiez les dates, les équipements, le régime et les conditions."
        if self.open_linked:self.open_linked(linked.id,message)
        else:self.changed(message,False)
    def _renew(self)->None:
        dialog=RenewalConfirmationDialog(self.lifecycle.lifecycle_projection(self.contract_id),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.confirm_renewal(self.contract_id,*dialog.values())
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("La reconduction a été confirmée sans créer de nouvelle révision documentaire.",False)
    def _nonrenewal(self)->None:
        dialog=NonRenewalDialog(self.lifecycle.lifecycle_projection(self.contract_id),self.lifecycle.date_provider.today(),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.record_non_renewal(self.contract_id,*dialog.values())
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("Le non-renouvellement effectué hors de l’application a été enregistré.",False)
    def _termination(self)->None:
        dialog=TerminationDialog(self.lifecycle.date_provider.today(),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.schedule_termination(self.contract_id,*dialog.values())
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("La résiliation a été enregistrée.",False)
    def _abandon(self)->None:
        dialog=AbandonConfirmationDialog(self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self.lifecycle.abandon(self.contract_id)
        except ApplicationError as error:self.changed(error.user_message,True);return
        self.load(self.contract_id);self.changed("Le contrat est abandonné. Ses documents et son historique restent conservés.",False)
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
