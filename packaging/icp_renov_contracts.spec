# -*- mode: python ; coding: utf-8 -*-
"""Tracked Windows x64 one-dir build input for ICP Renov — Contrats."""

from pathlib import Path
import platform
import struct

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
PACKAGE = SRC / "icp_renov_contracts"
WEB_UI = PACKAGE / "ui_web"
FIELD_TEMPLATE = PACKAGE / "resources" / "FIELD_TEMPLATE_CONTRACT_V1_1.txt"
UI_ASSETS = PACKAGE / "ui" / "assets"
WEB_UI_FILES = (
    "index.html",
    "app.js",
    "contract-workspace.js",
    "app.css",
    "clients.css",
    "contract-workspace.css",
    "contract-conditions.css",
    "contract-conditions-b2.css",
    "contract-conditions-b3.css",
    "contract-review.css",
    "contract-documents.css",
    "contract-workspace-ds01d.css",
)

if platform.system() != "Windows" or struct.calcsize("P") != 8:
    raise SystemExit("ICP Renov V1 packaging requires a Windows x64 build interpreter.")
for resource in (FIELD_TEMPLATE, *(WEB_UI / name for name in WEB_UI_FILES)):
    if not resource.is_file():
        raise SystemExit(f"Missing production packaging resource: {resource}")

datas = [(str(FIELD_TEMPLATE), "icp_renov_contracts/resources")]
datas.extend((str(path), "icp_renov_contracts/ui/assets") for path in UI_ASSETS.glob("*.svg"))
datas.extend((str(WEB_UI / name), "icp_renov_contracts/ui_web") for name in WEB_UI_FILES)
# PyInstaller's PySide6 hooks collect the Qt modules and binaries. These explicit data
# entries retain the WebEngine helper resources whose absence is otherwise opaque in a
# windowed build; no LibreOffice, user data, tests, templates, spike, or prototype is added.
datas.extend(collect_data_files("PySide6", includes=(
    "QtWebEngineProcess.exe",
    "resources/icudtl.dat",
    "resources/qtwebengine_resources*.pak",
    "resources/locales/*.pak",
    "translations/qtwebengine_locales/*.pak",
    "plugins/platforms/*",
)))

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch_icp_renov.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=("tests", "spike"),
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ICP Renov - Contrats",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ICP Renov - Contrats",
)
