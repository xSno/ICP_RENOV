from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from .bootstrap import build_application_context
from .errors import ApplicationError
from .logging_setup import configure_logging
from .ui import MainWindow
from .ui.styles import application_stylesheet


def _light_palette() -> QPalette:
    """Provide deterministic light native colors before any widget is created."""
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#EEF3F6",
        QPalette.ColorRole.WindowText: "#1F303A",
        QPalette.ColorRole.Base: "#FFFFFF",
        QPalette.ColorRole.AlternateBase: "#F7F9FA",
        QPalette.ColorRole.ToolTipBase: "#FFFFFF",
        QPalette.ColorRole.ToolTipText: "#1F303A",
        QPalette.ColorRole.Text: "#1F303A",
        QPalette.ColorRole.Button: "#F7F9FA",
        QPalette.ColorRole.ButtonText: "#1F303A",
        QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Highlight: "#176CA8",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.Link: "#176CA8",
    }
    for role, value in colors.items():
        color = QColor(value)
        palette.setColor(QPalette.ColorGroup.Active, role, color)
        palette.setColor(QPalette.ColorGroup.Inactive, role, color)
        palette.setColor(QPalette.ColorGroup.Disabled, role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5D6C75"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#E7EDF0"))
    return palette


def create_application(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    application = QApplication(argv if argv is not None else sys.argv)
    application.setApplicationName("ICP Renov — Contrats")
    application.setOrganizationName("ICP Renov")
    application.setPalette(_light_palette())
    application.setStyleSheet(application_stylesheet())
    return application


def main(argv: list[str] | None = None) -> int:
    application = None
    logger = None
    try:
        application = create_application(argv)
        logger = configure_logging()
        logger.info("Application startup")
        context = build_application_context(logger=logger)
    except ApplicationError as exc:
        if logger is not None:
            logger.exception("Application bootstrap failed")
        QMessageBox.critical(None, "ICP Renov", exc.user_message)
        return 1
    except Exception:
        if logger is not None:
            logger.exception("Unexpected application startup failure")
        else:
            logging.getLogger("icp_renov_contracts").exception("Unexpected application startup failure before local logging")
        if application is not None:
            QMessageBox.critical(
                None,
                "ICP Renov",
                "ICP Renov n’a pas pu démarrer. Réessayez après avoir vérifié le dossier de travail. "
                "Les détails techniques ont été enregistrés dans le journal local lorsque disponible.",
            )
        return 1

    try:
        window = MainWindow(context)
        window.show()
        exit_code = application.exec()
        logger.info("Application shutdown with code %s", exit_code)
        return exit_code
    except Exception:
        logger.exception("Unexpected application startup failure")
        QMessageBox.critical(
            None,
            "ICP Renov",
            "ICP Renov n’a pas pu démarrer. Réessayez après avoir vérifié le dossier de travail. "
            "Les détails techniques ont été enregistrés dans le journal local.",
        )
        return 1

