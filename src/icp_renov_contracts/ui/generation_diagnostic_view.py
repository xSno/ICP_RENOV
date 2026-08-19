from __future__ import annotations

from pathlib import Path
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..services import FileOpener
from .models_settings_view import COMPANY_FIELD_LABELS, DOCUMENT_LABELS, STATUS_LABELS
from .styles import SPACING


class GenerationDiagnosticPage(QWidget):
    """The two bounded S15 diagnostic groups; state is intentionally session-only."""
    def __init__(self, service, catalog, opener: FileOpener | None = None, navigate=None, open_company=None, open_model=None) -> None:
        super().__init__(); self.service=service; self.catalog=catalog; self.opener=opener or FileOpener(); self.navigate=navigate
        self.version_id=None; self.validation_mode=False; self.result=None; self.open_company=open_company; self.open_model=open_model
        root=QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(SPACING['md'])
        title=QLabel('Diagnostic génération'); title.setObjectName('screenTitle'); root.addWidget(title)
        self.workstation=self._group('État du poste'); root.addWidget(self.workstation)
        self.model_group=self._group('Test d’un modèle'); root.addWidget(self.model_group); root.addStretch(1)
        self._build_model(); self.refresh()
    def _group(self,title):
        frame=QFrame(); frame.setObjectName('companyGroup'); layout=QVBoxLayout(frame); h=QLabel(title); h.setObjectName('sectionTitle'); layout.addWidget(h); return frame
    def refresh(self):
        status=self.service.workstation_status(); layout=self.workstation.layout()
        while layout.count()>1:
            item=layout.takeAt(1); w=item.widget(); w and w.deleteLater()
        workspace='Accessible' if status.workspace_available else 'Inaccessible en écriture'
        layout.addWidget(QLabel(f'Dossier de travail → {workspace}'))
        layout.addWidget(QLabel(f'Génération DOCX → {"Disponible" if status.docx_available else "Indisponible"}'))
        pdf='Disponible · LibreOffice 26.2.5.2' if status.pdf_available else ('Version non conforme' if status.libreoffice_version else 'Indisponible')
        layout.addWidget(QLabel(f'Conversion PDF → {pdf}'))
        if not status.pdf_available and status.libreoffice_version: layout.addWidget(QLabel(f'Version attendue : 26.2.5.2 · Version détectée : {status.libreoffice_version}'))
        row=QHBoxLayout(); actual=QPushButton('Actualiser l’état du poste'); actual.clicked.connect(self.refresh); row.addWidget(actual)
        open_folder=QPushButton('Ouvrir le dossier'); open_folder.clicked.connect(lambda:self.opener.open(status.workspace_path)); row.addWidget(open_folder); row.addStretch(1); layout.addLayout(row)
        self._populate_versions()
    def _build_model(self):
        layout=self.model_group.layout(); row=QHBoxLayout(); self.model=QComboBox(); self.model.setObjectName('diagnosticModelSelector'); self.version=QComboBox(); self.version.setObjectName('diagnosticVersionSelector'); row.addWidget(QLabel('Modèle')); row.addWidget(self.model,1); row.addWidget(QLabel('Version')); row.addWidget(self.version); layout.addLayout(row)
        self.source=QLabel(); self.source.setWordWrap(True); layout.addWidget(self.source); self.feedback=QLabel(''); self.feedback.setWordWrap(True); layout.addWidget(self.feedback)
        self.resolution=QHBoxLayout(); layout.addLayout(self.resolution)
        self.test=QPushButton('Tester la génération'); self.test.setObjectName('primaryButton'); self.test.clicked.connect(self._confirm); layout.addWidget(self.test)
        self.actions=QHBoxLayout(); layout.addLayout(self.actions); layout.addWidget(QLabel('Ce test vérifie la chaîne documentaire locale. Il ne valide pas juridiquement le contenu du modèle.'))
        self.model.currentIndexChanged.connect(self._versions); self.version.currentIndexChanged.connect(self._selection)
    def _populate_versions(self):
        current=self.version_id; self.model.blockSignals(True); self.model.clear(); families={}
        for item in self.catalog.list_all(): families.setdefault(item.template_name,[]).append(item)
        for name, values in families.items(): self.model.addItem(name, values)
        self.model.blockSignals(False)
        if current:
            self._versions(); self.preselect(current, self.validation_mode)
        else:
            # Direct Settings entry is deliberately an empty diagnostic target.
            self.model.setCurrentIndex(-1); self.version.clear(); self._selection()
    def _versions(self):
        values=self.model.currentData() or []; self.version.blockSignals(True); self.version.clear()
        for item in values: self.version.addItem(f'{item.version} · {STATUS_LABELS[item.status]} · {DOCUMENT_LABELS[item.document_kind]}',item.id)
        self.version.blockSignals(False); self._selection()
    def _selection(self):
        self.version_id=self.version.currentData();
        if not self.version_id: self.source.setText('Sélectionnez un modèle et une version pour lancer un test.'); self.test.setEnabled(False); return
        while self.resolution.count():
            item=self.resolution.takeAt(0); w=item.widget(); w and w.deleteLater()
        source_code,message=self.service.source_state(self.version_id); self.source.setText(f'Source du modèle → {message}')
        if source_code != 'VALID':
            self.test.setEnabled(False)
            if self.open_model:
                button=QPushButton('Ouvrir le modèle'); button.clicked.connect(lambda:self.open_model(self.version_id)); self.resolution.addWidget(button)
            self.resolution.addStretch(1); return
        missing=self.service.missing_company_fields(self.version_id)
        if missing:
            labels=', '.join(COMPANY_FIELD_LABELS.get(key,key) for key in missing)
            self.feedback.setText('Des informations société requises par ce modèle sont à compléter.\n'+labels)
            self.test.setEnabled(False)
            if self.open_company:
                button=QPushButton('Ouvrir Société'); button.clicked.connect(self.open_company); self.resolution.addWidget(button)
            self.resolution.addStretch(1); return
        self.feedback.setText(''); self.test.setEnabled(True)
    def preselect(self, version_id, validation_mode=False):
        for i in range(self.model.count()):
            values=self.model.itemData(i) or []
            for j,item in enumerate(values):
                if item.id==version_id: self.model.setCurrentIndex(i); self.version.setCurrentIndex(j); self.version_id=version_id; self.validation_mode=validation_mode; return
    def _confirm(self):
        if not self.version_id:return
        box=QMessageBox(self); box.setWindowTitle('Tester ce modèle ?'); box.setText('Données fictives uniquement. Aucun contrat officiel, numéro ou révision ne sera créé. Un DOCX et un PDF de test peuvent être créés ; LibreOffice peut être invoqué localement.')
        box.addButton('Annuler',QMessageBox.ButtonRole.RejectRole); go=box.addButton('Lancer le test',QMessageBox.ButtonRole.AcceptRole); box.exec()
        if box.clickedButton() is not go:return
        self.test.setEnabled(False); self.test.setText('Test en cours…')
        try: self.result=self.service.run(self.version_id,validation_mode=self.validation_mode); self._show_result()
        finally: self.test.setText('Tester la génération'); self.test.setEnabled(True)
    def _show_result(self):
        while self.actions.count():
            item=self.actions.takeAt(0); w=item.widget(); w and w.deleteLater()
        if not self.result.succeeded: self.feedback.setText(self.result.issue+' Les données de l’application sont conservées.'); return
        self.feedback.setText('Résultat → Test réussi\nDOCX → Créé\nPDF → Créé\nDonnées documentaires → Toutes traitées\nContrôle visuel → À contrôler visuellement')
        if self.result.docx_paths:
            b=QPushButton('Ouvrir le DOCX'); b.clicked.connect(lambda:self.opener.open(self.result.docx_paths[0])); self.actions.addWidget(b)
        if self.result.pdf_paths:
            b=QPushButton('Ouvrir le PDF'); b.clicked.connect(lambda:self.opener.open(self.result.pdf_paths[0])); self.actions.addWidget(b)
        if self.result.output_folder:
            b=QPushButton('Ouvrir le dossier de test'); b.clicked.connect(lambda:self.opener.open(self.result.output_folder)); self.actions.addWidget(b)
        self.actions.addStretch(1)
