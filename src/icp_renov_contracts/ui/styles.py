COLORS = {
    "app_background": "#F3F6F7",
    "surface": "#FFFFFF",
    "sidebar": "#172A36",
    "sidebar_hover": "#223B49",
    "accent": "#167D83",
    "accent_hover": "#116B70",
    "text": "#20313B",
    "muted": "#657680",
    "border": "#D8E1E4",
}

SPACING = {"xs": 6, "sm": 10, "md": 16, "lg": 24, "xl": 32}
RADII = {"sm": 6, "md": 10}


def application_stylesheet() -> str:
    return f"""
    QWidget {{
        color: {COLORS['text']};
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: 14px;
    }}
    QMainWindow, QWidget#applicationRoot {{ background: {COLORS['app_background']}; }}
    QWidget#sidebar {{ background: {COLORS['sidebar']}; }}
    QLabel#brand {{ color: white; font-size: 20px; font-weight: 700; }}
    QLabel#brandSubtitle {{ color: #AAC0CB; font-size: 12px; }}
    QPushButton#navButton {{
        color: #DCE8ED;
        background: transparent;
        border: 0;
        border-radius: {RADII['sm']}px;
        padding: 12px 14px;
        text-align: left;
        font-weight: 600;
    }}
    QPushButton#navButton:hover {{ background: {COLORS['sidebar_hover']}; }}
    QPushButton#navButton[active="true"] {{
        color: white;
        background: {COLORS['accent']};
    }}
    QWidget#contentSurface {{
        background: {COLORS['surface']};
        border: 1px solid {COLORS['border']};
        border-radius: {RADII['md']}px;
    }}
    QLabel#screenTitle {{ font-size: 28px; font-weight: 700; }}
    QLabel#screenDescription {{ color: {COLORS['muted']}; font-size: 15px; }}
    QPushButton#primaryButton {{
        color: white;
        background: {COLORS['accent']};
        border: 0;
        border-radius: {RADII['sm']}px;
        padding: 10px 16px;
        font-weight: 600;
    }}
    QPushButton#primaryButton:hover {{ background: {COLORS['accent_hover']}; }}
    QPushButton#secondaryButton {{
        color: {COLORS['text']};
        background: white;
        border: 1px solid {COLORS['border']};
        border-radius: {RADII['sm']}px;
        padding: 10px 16px;
        font-weight: 600;
    }}
    """

