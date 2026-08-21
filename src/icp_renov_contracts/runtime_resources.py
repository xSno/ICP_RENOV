from __future__ import annotations

from importlib.resources import files
from pathlib import Path, PurePosixPath


PACKAGE_NAME = "icp_renov_contracts"
FIELD_TEMPLATE_RESOURCE = "resources/FIELD_TEMPLATE_CONTRACT_V1_1.txt"
WEB_UI_RESOURCE = "ui_web"


def _resource_root():
    return files(PACKAGE_NAME)


def resource_path(relative_path: str) -> Path:
    """Return a packaged, read-only runtime resource without relying on cwd."""
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("runtime resource escapes package")
    resource = _resource_root().joinpath(*relative.parts)
    if not resource.is_file():
        raise FileNotFoundError(relative_path)
    return Path(resource)


def field_template_path() -> Path:
    return resource_path(FIELD_TEMPLATE_RESOURCE)


def web_ui_root() -> Path:
    root = resource_path(f"{WEB_UI_RESOURCE}/index.html").parent
    if not root.is_dir():
        raise FileNotFoundError(WEB_UI_RESOURCE)
    return root
