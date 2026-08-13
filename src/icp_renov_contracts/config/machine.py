from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import InvalidBootstrapConfigurationError


CONFIG_VERSION = 1


@dataclass(frozen=True)
class BootstrapConfig:
    active_workspace: Path | None = None


class MachineConfigStore:
    """Stores only machine bootstrap state, never business data."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or self.default_path()

    @staticmethod
    def default_path() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "ICP Renov" / "Contrats" / "bootstrap.json"

    def load(self) -> BootstrapConfig:
        if not self.path.exists():
            return BootstrapConfig()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidBootstrapConfigurationError(str(self.path)) from exc
        if not isinstance(payload, dict) or payload.get("version") != CONFIG_VERSION:
            raise InvalidBootstrapConfigurationError(str(self.path))
        value = payload.get("active_workspace")
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InvalidBootstrapConfigurationError(str(self.path))
        return BootstrapConfig(Path(value) if value else None)

    def save(self, config: BootstrapConfig) -> None:
        payload = {
            "version": CONFIG_VERSION,
            "active_workspace": str(config.active_workspace) if config.active_workspace else None,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise InvalidBootstrapConfigurationError(str(self.path)) from exc

