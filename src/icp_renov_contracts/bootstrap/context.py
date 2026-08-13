from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ..config import BootstrapConfig, MachineConfigStore
from ..database import DatabaseService
from ..storage import Workspace, WorkspaceService


class ApplicationState(Enum):
    BOOTSTRAP_REQUIRED = "bootstrap_required"
    READY = "ready"


@dataclass(frozen=True)
class ApplicationContext:
    state: ApplicationState
    config: BootstrapConfig
    config_store: MachineConfigStore
    workspace_service: WorkspaceService
    workspace: Workspace | None
    database: DatabaseService | None
    logger: logging.Logger


def build_application_context(
    config_store: MachineConfigStore | None = None,
    workspace_service: WorkspaceService | None = None,
    logger: logging.Logger | None = None,
) -> ApplicationContext:
    store = config_store or MachineConfigStore()
    workspaces = workspace_service or WorkspaceService()
    application_logger = logger or logging.getLogger("icp_renov_contracts")
    config = store.load()
    if config.active_workspace is None:
        application_logger.info("Startup requires local workstation configuration")
        return ApplicationContext(
            ApplicationState.BOOTSTRAP_REQUIRED,
            config,
            store,
            workspaces,
            None,
            None,
            application_logger,
        )

    workspace = workspaces.ensure(config.active_workspace)
    database = DatabaseService(workspace.database_path)
    database.initialize()
    application_logger.info("Local workspace and database initialized")
    return ApplicationContext(
        ApplicationState.READY,
        config,
        store,
        workspaces,
        workspace,
        database,
        application_logger,
    )

