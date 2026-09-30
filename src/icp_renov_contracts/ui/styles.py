"""Shared DS-01 visual foundation for native Qt surfaces."""

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


def application_stylesheet() -> str:
    """One light, token-aligned QSS foundation; screens retain their structure."""
    return f"""
    * {{ font-family: "Segoe UI Variable", "Segoe UI", Arial, sans-serif; font-size: 14px; color: {COLORS['text']}; }}
    QMainWindow, QWidget#applicationRoot, QWidget#contentRoot, QStackedWidget, QScrollArea {{ background: {COLORS['app_background']}; }}
    QScrollArea {{ border: 0; }} QScrollArea > QWidget > QWidget {{ background: {COLORS['app_background']}; }}
    QDialog, QMessageBox {{ background: {COLORS['surface']}; }}
    QWidget#sidebar {{ background: {COLORS['sidebar']}; }}
    QWidget#sidebarBrandRow {{ margin: 2px 4px 0 4px; }}
    QLabel#sidebarBrandMark {{ background: {COLORS['brand']}; color: white; border-radius: {RADII['md']}px; font-size: 10px; font-weight: 800; }}
    QLabel#brand {{ color: white; font-size: 18px; font-weight: 700; }}
    QLabel#brandSubtitle, QLabel#localApplicationIndicator, QLabel#backupStatusIndicator {{ color: #B7C8D1; font-size: 12px; }}
    QWidget#sidebarStatusArea {{ border-top: 1px solid #365060; padding: 10px 4px 0 4px; }}
    QLabel#localApplicationIndicator {{ padding: 0; }}
    QLabel#backupStatusIndicator {{ padding: 2px 0 0 0; color: white; font-weight: 600; }}
    QPushButton#navButton {{ color: #DCE8ED; background: transparent; border: 0; border-radius: {RADII['sm']}px; padding: 11px 14px; text-align: left; font-weight: 600; }}
    QPushButton#navButton:hover {{ background: {COLORS['sidebar_hover']}; }}
    QPushButton#navButton[active="true"] {{ color: white; background: #29495C; border-left: 3px solid {COLORS['accent']}; padding-left: 11px; }}
    QWidget#contentSurface, QFrame#panel, QFrame#siteCard, QFrame#reviewBlock, QFrame#generationAvailability, QFrame#generationCheck {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px; }}
    QFrame#companyGroup, QFrame#equipmentRow {{ background: {COLORS['surface_soft']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; }}
    QFrame#masterDataDrawer {{ background: {COLORS['surface']}; border-left: 1px solid {COLORS['border']}; border-radius: {RADII['md']}px 0 0 {RADII['md']}px; }}
    QFrame#operationalBanner {{ background: #EAF3F8; border: 1px solid #B9D5E5; border-radius: {RADII['md']}px; padding: 8px; }}
    QLabel#screenTitle {{ font-size: 28px; font-weight: 700; }} QLabel#screenDescription {{ color: {COLORS['muted']}; font-size: 15px; }}
    QLabel#drawerTitle, QLabel#detailTitle {{ font-size: 22px; font-weight: 700; }} QLabel#sectionTitle, QLabel#siteTitle {{ font-size: 16px; font-weight: 700; }}
    QPushButton#settingsSection {{ color: {COLORS['text']}; background: transparent; border: 0; border-radius: {RADII['sm']}px; padding: 9px 10px; text-align: left; }}
    QPushButton#settingsSection:hover {{ background: #EAF3F8; }} QPushButton#settingsSection:checked {{ background: #DCECF6; color: #125B90; font-weight: 700; }}
    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QDateEdit {{ background: {COLORS['surface']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 7px; selection-background-color: {COLORS['brand']}; selection-color: white; }}
    QLineEdit:read-only, QPlainTextEdit:read-only, QTextEdit:read-only {{ background: #EAF0F3; color: {COLORS['muted']}; }}
    QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QDateEdit:disabled {{ background: #E7EDF0; color: {COLORS['muted']}; border-color: {COLORS['border']}; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateEdit:focus, QPushButton:focus {{ border: 2px solid {COLORS['brand']}; }}
    QPushButton#primaryButton {{ color: white; background: {COLORS['brand']}; border: 1px solid {COLORS['brand']}; border-radius: {RADII['sm']}px; padding: 10px 16px; font-weight: 600; }}
    QPushButton#primaryButton:hover {{ background: {COLORS['brand_hover']}; border-color: {COLORS['brand_hover']}; }}
    QPushButton#primaryButton:disabled {{ color: {COLORS['muted']}; background: #D8E1E4; border-color: {COLORS['border']}; }}
    QPushButton#secondaryButton {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 10px 16px; font-weight: 600; }}
    QPushButton#secondaryButton:hover {{ background: {COLORS['surface_soft']}; border-color: #AABCC6; }}
    QPushButton:disabled {{ color: {COLORS['muted']}; background: #E7EDF0; border: 1px solid {COLORS['border']}; }}
    QLabel#companyFeedback[success="true"], QLabel#successFeedback {{ color: #1C5C3F; background: #E4F1EA; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#companyFeedback[success="false"], QLabel#formError {{ color: #8E3030; background: #F9E8E8; padding: 10px; border-radius: {RADII['sm']}px; }}
    QLabel#reviewIssue {{ color: {COLORS['danger']}; }} QLabel#archivedBadge {{ color: #5D6C75; background: #E7EDF0; padding: 4px 8px; border-radius: 999px; }}
    QLabel#emptyState, QLabel#emptySites, QLabel#emptyEquipment {{ color: {COLORS['muted']}; padding: 14px; }}
    QLabel#actionCounter {{ color: {COLORS['success']}; font-size: 18px; font-weight: 700; }}
    QPushButton#registerFilter {{ color: {COLORS['text']}; background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['sm']}px; padding: 8px 12px; font-weight: 600; }}
    QPushButton#registerFilter:checked {{ color: white; background: {COLORS['brand']}; border-color: {COLORS['brand']}; }}
    QWidget#contractStepOne {{ background: transparent; }}
    QTableWidget#contractRegisterTable {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; gridline-color: {COLORS['border']}; selection-background-color: #DCECF6; }}
    QTableWidget#contractRegisterTable::item {{ padding: 8px; }} QTableWidget#contractRegisterTable::item:hover {{ background: #F1F7FA; }} QTableWidget#contractRegisterTable::item:selected {{ color: {COLORS['text']}; background: #DCECF6; }}
    QHeaderView::section {{ background: {COLORS['surface_soft']}; color: {COLORS['muted']}; border: 0; border-bottom: 1px solid {COLORS['border']}; padding: 9px; font-weight: 700; }}
    """
