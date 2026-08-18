from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from ..bootstrap import ApplicationContext, ApplicationState
from .bootstrap_view import BootstrapView
from .shell import ApplicationShell
from .styles import application_stylesheet


class MainWindow(QMainWindow):
    def __init__(self, context: ApplicationContext) -> None:
        super().__init__()
        self.context = context
        self.setWindowTitle("ICP Renov — Contrats d’entretien")
        self.resize(1280, 800)
        self.setMinimumSize(980, 640)
        self.setStyleSheet(application_stylesheet())
        if context.state is ApplicationState.READY:
            self.shell: ApplicationShell | None = ApplicationShell(context.master_data, context.contracts, context.review, context.generation, context.lifecycle,context.intervention_generation,context.company,context.template_catalog,context.numbering,context.alerts,context.backup,context.restore)
            self.bootstrap_view: BootstrapView | None = None
            self.setCentralWidget(self.shell)
        else:
            self.shell = None
            self.bootstrap_view = BootstrapView(self._restore_first_use)
            self.setCentralWidget(self.bootstrap_view)

    def _restore_first_use(self) -> None:
        archive, _ = QFileDialog.getOpenFileName(self, "Restaurer une sauvegarde", "", "Sauvegardes ICP Renov (*.icprenovbackup)")
        if not archive: return
        target = QFileDialog.getExistingDirectory(self, "Choisir un dossier vide de restauration")
        if not target: return
        try: self.context.restore.restore(Path(archive), Path(target))
        except Exception: QMessageBox.warning(self, "Restauration", "La sauvegarde ne peut pas être restaurée.")
        else: QMessageBox.information(self, "Restauration", "Restauration terminée. Redémarrez l’application pour utiliser le dossier restauré.")
