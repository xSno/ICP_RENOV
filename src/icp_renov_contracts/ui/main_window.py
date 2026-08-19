from pathlib import Path
import sqlite3
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from ..bootstrap import ApplicationContext, ApplicationState
from ..bootstrap import build_application_context
from ..config import BootstrapConfig
from ..database import DatabaseService
from ..database.service import MIGRATIONS
from ..errors import ApplicationError
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
            self.shell: ApplicationShell | None = ApplicationShell(context.master_data, context.contracts, context.review, context.generation, context.lifecycle,context.intervention_generation,context.company,context.template_catalog,context.numbering,context.alerts,context.backup,context.restore,context.diagnostic)
            self.bootstrap_view: BootstrapView | None = None
            self.setCentralWidget(self.shell)
        else:
            self.shell = None
            self.bootstrap_view = BootstrapView(self._restore_first_use, self._configure_first_use)
            self.setCentralWidget(self.bootstrap_view)

    def _configure_first_use(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choisir le dossier de travail ICP Renov")
        if not selected: return
        path = Path(selected)
        previous_config = self.context.config_store.load(); committed = False; candidate = None; existed_before = False; fresh_attempt = False
        try:
            candidate = self.context.workspace_service.validate_candidate(path)
            existed_before = candidate.exists()
            existing = candidate.exists() and any(candidate.iterdir())
            database_path = candidate / "data" / "icp-renov.sqlite3"
            valid_existing = existing and database_path.is_file()
            fresh_attempt = not valid_existing
            if existing and not valid_existing:
                QMessageBox.warning(self, "Dossier de travail", "Ce dossier contient déjà des fichiers et n’est pas reconnu comme un dossier de travail ICP Renov.\n\nChoisissez un dossier vide ou un dossier de travail ICP Renov existant.")
                return
            if valid_existing:
                uri = f"file:{database_path.as_posix()}?mode=ro"
                try:
                    with sqlite3.connect(uri, uri=True) as connection:
                        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ApplicationError()
                        if connection.execute("PRAGMA user_version").fetchone()[0] > MIGRATIONS[-1].version:
                            QMessageBox.warning(self, "Dossier de travail", "Ce dossier de travail a été créé avec une version plus récente de l’application. Aucune donnée n’a été modifiée.")
                            return
                except sqlite3.Error:
                    QMessageBox.warning(self, "Dossier de travail", "Le dossier de travail ne peut pas être ouvert. Aucune donnée n’a été modifiée. Choisissez un autre dossier ou restaurez une sauvegarde.")
                    return
                box = QMessageBox(self); box.setWindowTitle("Utiliser ce dossier de travail ?")
                box.setText(f"Ce dossier de travail ICP Renov existant sera utilisé sans être recréé.\n\n{candidate}")
                confirm = box.addButton("Utiliser ce dossier", QMessageBox.ButtonRole.AcceptRole); box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole); box.exec()
                if box.clickedButton() is not confirm: return
            else:
                box = QMessageBox(self); box.setWindowTitle("Créer le dossier de travail ici ?")
                box.setText(f"Ce dossier contiendra la base de données, les contrats, les documents et les ressources locales ICP Renov.\n\n{candidate}")
                confirm = box.addButton("Créer le dossier de travail", QMessageBox.ButtonRole.AcceptRole); box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole); box.exec()
                if box.clickedButton() is not confirm: return
            workspace = self.context.workspace_service.ensure(candidate)
            database = DatabaseService(workspace.database_path); database.initialize()
            with database.connection() as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok": raise ApplicationError()
            self.context.config_store.save(BootstrapConfig(workspace.root))
            committed = True
            new_context = build_application_context(self.context.config_store, self.context.workspace_service, self.context.logger)
            if new_context.state is not ApplicationState.READY: raise ApplicationError()
            self.context = new_context
            self.shell = ApplicationShell(new_context.master_data, new_context.contracts, new_context.review, new_context.generation, new_context.lifecycle,new_context.intervention_generation,new_context.company,new_context.template_catalog,new_context.numbering,new_context.alerts,new_context.backup,new_context.restore,new_context.diagnostic)
            self.bootstrap_view = None; self.setCentralWidget(self.shell)
        except ApplicationError as error:
            if committed: self.context.config_store.save(previous_config)
            if fresh_attempt: self._cleanup_failed_workspace_attempt(candidate, existed_before)
            QMessageBox.warning(self, "Dossier de travail", error.user_message + "\n\nAucune donnée existante n’a été modifiée. Choisissez un autre dossier ou restaurez une sauvegarde.")
        except Exception:
            if committed: self.context.config_store.save(previous_config)
            if fresh_attempt: self._cleanup_failed_workspace_attempt(candidate, existed_before)
            QMessageBox.warning(self, "Dossier de travail", "Le dossier de travail ne peut pas être ouvert. Aucune donnée existante n’a été modifiée. Choisissez un autre dossier ou restaurez une sauvegarde.")

    @staticmethod
    def _cleanup_failed_workspace_attempt(candidate: Path | None, existed_before: bool) -> None:
        if candidate is None or not candidate.exists(): return
        # Only exact workspace paths owned by this attempt are considered; rmdir
        # is deliberately used so a user file prevents deletion.
        for name in ("icp-renov.sqlite3", "icp-renov.sqlite3-wal", "icp-renov.sqlite3-shm"):
            try: (candidate / "data" / name).unlink(missing_ok=True)
            except OSError: pass
        for name in ("data", "documents", "backups"):
            path = candidate / name
            try: path.rmdir()
            except OSError: pass
        if not existed_before:
            try: candidate.rmdir()
            except OSError: pass

    def _restore_first_use(self) -> None:
        archive, _ = QFileDialog.getOpenFileName(self, "Restaurer une sauvegarde", "", "Sauvegardes ICP Renov (*.icprenovbackup)")
        if not archive: return
        target = QFileDialog.getExistingDirectory(self, "Choisir un dossier vide de restauration")
        if not target: return
        try: self.context.restore.restore(Path(archive), Path(target))
        except Exception: QMessageBox.warning(self, "Restauration", "La sauvegarde ne peut pas être restaurée.")
        else: QMessageBox.information(self, "Restauration", "Restauration terminée. Redémarrez l’application pour utiliser le dossier restauré.")
