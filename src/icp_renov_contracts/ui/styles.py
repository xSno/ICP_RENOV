"""Shared DS-01 visual foundation for native Qt surfaces."""

from pathlib import Path


COLORS = {
    "app_background": "#EEF3F6", "surface": "#FFFFFF", "surface_soft": "#F7F9FA",
    "sidebar": "#192C3A", "sidebar_hover": "#294152", "brand": "#176CA8",
    "brand_hover": "#115B90", "accent": "#E9A23B", "text": "#1F303A", "muted": "#5D6C75",
    "border": "#D6DFE4", "success": "#267651", "info": "#246F9D",
    "warning": "#93620D", "danger": "#B13D3D",
}

# Keep established semantic keys while aligning their values to the shared scale.
SPACING = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32}
RADII = {"sm": 6, "md": 9, "lg": 12}


def _control_asset_url(name: str) -> str:
    return (Path(__file__).resolve().parent / "assets" / name).as_posix()


def application_stylesheet() -> str:
    """One light, token-aligned QSS foundation; screens retain their structure."""
    chevron_down = _control_asset_url("chevron-down.svg")
    chevron_up = _control_asset_url("chevron-up.svg")
    checkmark = _control_asset_url("check.svg")
    return f"""
    * {{ font-family: "Segoe UI Variable", "Segoe UI", Arial, sans-serif; font-size: 14px; color: {COLORS['text']}; }}
    QMainWindow, QWidget#applicationRoot, QWidget#contentRoot, QStackedWidget, QScrollArea {{ background: {COLORS['app_background']}; }}
    QScrollArea {{ border: 0; }} QScrollArea > QWidget > QWidget {{ background: {COLORS['app_background']}; }}
    QDialog, QMessageBox {{ background: {COLORS['surface']}; }}
    QWidget#sidebar {{ background: {COLORS['sidebar']}; }}
    QWidget#sidebarBrandRow {{ margin: 4px 4px 2px 4px; }}
    QLabel#sidebarBrandMark {{ background: {COLORS['brand']}; border-radius: {RADII['md']}px; }}
    QLabel#brand {{ color: white; font-size: 18px; font-weight: 700; }}
    QLabel#brandSubtitle, QLabel#localApplicationIndicator, QLabel#backupStatusIndicator {{ color: #B7C8D1; font-size: 12px; }}
    QWidget#sidebarLocalBlock {{ background: #243B4A; border: 1px solid #365060; border-radius: {RADII['md']}px; padding: 10px; }}
    QWidget#sidebarStatusArea {{ border-top: 1px solid #365060; padding: 12px 4px 0 4px; }}
    QWidget#sidebarStatusRow {{ background: transparent; }} QLabel#sidebarStatusIcon {{ background: transparent; }}
    QLabel#sidebarStatusLabel {{ color: #DCE8ED; font-size: 12px; font-weight: 700; }}
    QLabel#localApplicationIndicator {{ padding: 2px 0 0 0; }}
    QLabel#backupStatusIndicator {{ padding: 2px 0 0 0; color: white; font-weight: 600; }}
    QPushButton#navButton {{ color: #DCE8ED; background: transparent; border: 0; border-radius: {RADII['sm']}px; padding: 11px 14px; text-align: left; font-weight: 600; icon-size: 18px; }}
    QPushButton#navButton:hover {{ background: {COLORS['sidebar_hover']}; }}
    QPushButton#navButton[active="true"] {{ color: white; background: #29495C; border-left: 3px solid {COLORS['accent']}; padding-left: 11px; }}
    QWidget#contentSurface, QFrame#panel, QFrame#siteCard, QFrame#reviewBlock, QFrame#generationAvailability, QFrame#generationCheck {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px; }}
    QFrame#companyGroup, QFrame#equipmentRow {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px; }}
    QFrame#masterDataDrawer {{ background: {COLORS['surface']}; border-left: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px 0 0 {RADII['md']}px; }}
    QFrame#operationalBanner {{ background: #EAF3F8; border: 1px solid #B9D5E5; border-radius: {RADII['md']}px; padding: 8px; }}
    QLabel#screenTitle {{ font-size: 28px; font-weight: 700; }} QLabel#screenDescription {{ color: {COLORS['muted']}; font-size: 15px; }}
    QLabel#drawerTitle, QLabel#detailTitle {{ font-size: 22px; font-weight: 700; }} QLabel#sectionTitle, QLabel#siteTitle {{ font-size: 16px; font-weight: 700; }}
    QPushButton#settingsSection {{ color: {COLORS['text']}; background: transparent; border: 0; border-radius: {RADII['sm']}px; padding: 9px 10px; text-align: left; }}
    QPushButton#settingsSection:hover {{ background: #EAF3F8; }} QPushButton#settingsSection:checked {{ background: #DCECF6; color: #125B90; font-weight: 700; }}
    QFrame#settingsNavigationPanel {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px; }}
    QLabel#settingsNavigationTitle {{ font-size: 18px; font-weight: 700; }}
    QStackedWidget#settingsContentStack {{ background: transparent; border: 0; }}
    QLabel#readOnlyValue, QLabel#numberingPreview {{ background: #EAF0F3; color: {COLORS['muted']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 8px 10px; font-weight: 600; }}
    QPushButton#tertiaryButton {{ color: {COLORS['brand']}; background: transparent; border: 1px solid transparent; border-radius: {RADII['sm']}px; padding: 8px 10px; font-weight: 600; }}
    QPushButton#tertiaryButton:hover {{ background: #EAF3F8; border-color: #B9D5E5; }}
    QPushButton#sensitiveButton {{ color: #8E3030; background: #FFF8F2; border: 1px solid #E7C9B6; border-radius: {RADII['sm']}px; padding: 9px 12px; font-weight: 600; }}
    QPushButton#sensitiveButton:hover {{ background: #FCEFE5; border-color: #D99D76; }}
    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QDateEdit {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 7px; min-height: 18px; selection-background-color: {COLORS['brand']}; selection-color: white; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border-left: 1px solid {COLORS['border']}; background: {COLORS['surface_soft']}; border-top-right-radius: {RADII['sm']}px; border-bottom-right-radius: {RADII['sm']}px; }}
    QComboBox::down-arrow {{ image: url("{chevron_down}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; selection-background-color: #DCECF6; selection-color: {COLORS['text']}; outline: 0; }}
    QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 4px 8px; }} QComboBox QAbstractItemView::item:hover {{ background: #EAF3F8; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 18px; border: 0; background: {COLORS['surface_soft']}; }}
    QAbstractSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; border-top-right-radius: {RADII['sm']}px; }} QAbstractSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; border-bottom-right-radius: {RADII['sm']}px; }}
    QAbstractSpinBox::up-arrow {{ image: url("{chevron_up}"); width: 10px; height: 10px; }} QAbstractSpinBox::down-arrow {{ image: url("{chevron_down}"); width: 10px; height: 10px; }}
    QCheckBox {{ spacing: 7px; }} QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #AABCC6; border-radius: 4px; background: {COLORS['surface']}; }} QCheckBox::indicator:hover {{ border-color: {COLORS['brand']}; }} QCheckBox::indicator:checked {{ background: {COLORS['brand']}; border-color: {COLORS['brand']}; image: url("{checkmark}"); }} QCheckBox::indicator:disabled {{ background: #E7EDF0; border-color: {COLORS['border']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 1px; }} QScrollBar::handle:vertical {{ background: #B8C7CF; min-height: 28px; border-radius: 5px; }} QScrollBar::handle:vertical:hover {{ background: #8FA6B3; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px 3px; }} QScrollBar::handle:horizontal {{ background: #B8C7CF; min-width: 28px; border-radius: 5px; }} QScrollBar::handle:horizontal:hover {{ background: #8FA6B3; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: transparent; border: 0; }} QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QLineEdit:read-only, QPlainTextEdit:read-only, QTextEdit:read-only {{ background: #EAF0F3; color: {COLORS['muted']}; }}
    QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QDateEdit:disabled {{ background: #E7EDF0; color: {COLORS['muted']}; border-color: {COLORS['border']}; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateEdit:focus, QPushButton:focus {{ border: 2px solid {COLORS['brand']}; }}
    QPushButton#primaryButton {{ color: white; background: {COLORS['brand']}; border: 1px solid {COLORS['brand']}; border-radius: {RADII['sm']}px; padding: 10px 16px; font-weight: 600; }}
    QPushButton#primaryButton:hover {{ background: {COLORS['brand_hover']}; border-color: {COLORS['brand_hover']}; }}
    QPushButton#primaryButton:disabled {{ color: {COLORS['muted']}; background: #D8E1E4; border-color: {COLORS['border']}; }}
    QPushButton#secondaryButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 10px 16px; font-weight: 600; }}
    QPushButton#secondaryButton:hover {{ background: {COLORS['surface_soft']}; border-color: #AABCC6; }}
    QWidget#contentSurface QPushButton, QDialog QPushButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 8px 12px; min-height: 18px; max-width: 360px; font-weight: 600; }}
    QWidget#contentSurface QPushButton:hover, QDialog QPushButton:hover {{ background: {COLORS['surface_soft']}; border-color: #AABCC6; }}
    QWidget#contentSurface QPushButton:disabled, QDialog QPushButton:disabled {{ color: {COLORS['muted']}; background: #E7EDF0; border-color: {COLORS['border']}; }}
    QWidget#contentSurface QPushButton#primaryButton, QDialog QPushButton#primaryButton {{ color: white; background: {COLORS['brand']}; border-color: {COLORS['brand']}; padding: 10px 16px; }}
    QWidget#contentSurface QPushButton#primaryButton:hover, QDialog QPushButton#primaryButton:hover {{ background: {COLORS['brand_hover']}; border-color: {COLORS['brand_hover']}; }}
    QWidget#contentSurface QPushButton#primaryButton:disabled, QDialog QPushButton#primaryButton:disabled {{ color: {COLORS['muted']}; background: #D8E1E4; border-color: {COLORS['border']}; }}
    QWidget#contentSurface QPushButton#secondaryButton, QDialog QPushButton#secondaryButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border-color: {COLORS['border']}; padding: 10px 16px; }}
    QWidget#contentSurface QPushButton#tertiaryButton, QDialog QPushButton#tertiaryButton {{ color: {COLORS['brand']}; background: transparent; border-color: transparent; padding: 8px 10px; }}
    QWidget#contentSurface QPushButton#tertiaryButton:hover, QDialog QPushButton#tertiaryButton:hover {{ background: #EAF3F8; border-color: #B9D5E5; }}
    QWidget#contentSurface QPushButton#sensitiveButton, QDialog QPushButton#sensitiveButton {{ color: #8E3030; background: #FFF8F2; border-color: #E7C9B6; padding: 9px 12px; }}
    QWidget#contentSurface QPushButton#sensitiveButton:hover, QDialog QPushButton#sensitiveButton:hover {{ background: #FCEFE5; border-color: #D99D76; }}
    QWidget#contentSurface QPushButton[busy="true"], QDialog QPushButton[busy="true"] {{ color: {COLORS['brand']}; background: #EAF3F8; border-color: #B9D5E5; }}
    QPushButton:disabled {{ color: {COLORS['muted']}; background: #E7EDF0; border: 1px solid {COLORS['border']}; }}
    QLabel#companyFeedback[success="true"], QLabel#successFeedback {{ color: #1C5C3F; background: #E4F1EA; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#companyFeedback[success="false"], QLabel#formError {{ color: #8E3030; background: #F9E8E8; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#infoFeedback {{ color: #185981; background: #EAF3F8; border: 1px solid #B9D5E5; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#warningFeedback {{ color: #79500B; background: #FFF8E9; border: 1px solid #E7D29A; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#diagnosticFeedback[tone="success"] {{ color: #1C5C3F; background: #E4F1EA; border: 1px solid #B9DDC8; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#diagnosticFeedback[tone="warning"] {{ color: #79500B; background: #FFF8E9; border: 1px solid #E7D29A; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#diagnosticFeedback[tone="error"] {{ color: #8E3030; background: #F9E8E8; border: 1px solid #EDC4C4; padding: 10px; border-radius: {RADII['sm']}px; }}
    QFrame#diagnosticCapabilityRow {{ background: {COLORS['surface_soft']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; }}
    QFrame#diagnosticCapabilityRow[tone="success"] {{ border-left: 3px solid {COLORS['success']}; }}
    QFrame#diagnosticCapabilityRow[tone="warning"] {{ border-left: 3px solid {COLORS['warning']}; }}
    QFrame#diagnosticCapabilityRow[tone="error"] {{ border-left: 3px solid {COLORS['danger']}; }}
    QLabel#reviewIssue {{ color: {COLORS['danger']}; }} QLabel#archivedBadge {{ color: #5D6C75; background: #E7EDF0; padding: 4px 8px; border-radius: 999px; }}
    QLabel#emptyState, QLabel#emptySites, QLabel#emptyEquipment {{ color: {COLORS['muted']}; padding: 14px; }}
    QLabel#actionCounter {{ color: {COLORS['success']}; font-size: 18px; font-weight: 700; }}
    QPushButton#registerFilter {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 8px 12px; font-weight: 600; }}
    QPushButton#registerFilter:checked {{ color: white; background: {COLORS['brand']}; border-color: {COLORS['brand']}; }}
    QWidget#contractStepOne {{ background: transparent; }}
    QTableWidget#contractRegisterTable {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; gridline-color: {COLORS['border']}; selection-background-color: #DCECF6; }}
    QTableWidget#contractRegisterTable::item {{ padding: 8px; }} QTableWidget#contractRegisterTable::item:hover {{ background: #F1F7FA; }} QTableWidget#contractRegisterTable::item:selected {{ color: {COLORS['text']}; background: #DCECF6; }}
    QHeaderView::section {{ background: {COLORS['surface_soft']}; color: {COLORS['muted']}; border: 0; border-bottom: 1px solid {COLORS['border']}; padding: 9px; font-weight: 700; }}
    QTableWidget#modelsTable {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px; gridline-color: #E7EDF0; selection-background-color: #DCECF6; }}
    QTableWidget#modelsTable::item {{ padding: 8px 10px; border-bottom: 1px solid #E7EDF0; }}
    QTableWidget#modelsTable::item:selected {{ color: {COLORS['text']}; background: #DCECF6; }}
    """
