from PySide6.QtWidgets import QMainWindow

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
            self.shell: ApplicationShell | None = ApplicationShell(context.master_data, context.contracts)
            self.bootstrap_view: BootstrapView | None = None
            self.setCentralWidget(self.shell)
        else:
            self.shell = None
            self.bootstrap_view = BootstrapView()
            self.setCentralWidget(self.bootstrap_view)
