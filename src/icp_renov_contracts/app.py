from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .bootstrap import build_application_context
from .errors import ApplicationError
from .logging_setup import configure_logging
from .ui import MainWindow


def create_application(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    application = QApplication(argv if argv is not None else sys.argv)
    application.setApplicationName("ICP Renov — Contrats")
    application.setOrganizationName("ICP Renov")
    return application


def main(argv: list[str] | None = None) -> int:
    application = create_application(argv)
    logger = configure_logging()
    logger.info("Application startup")
    try:
        context = build_application_context(logger=logger)
    except ApplicationError as exc:
        logger.exception("Application bootstrap failed")
        QMessageBox.critical(None, "ICP Renov", exc.user_message)
        return 1

    window = MainWindow(context)
    window.show()
    exit_code = application.exec()
    logger.info("Application shutdown with code %s", exit_code)
    return exit_code

