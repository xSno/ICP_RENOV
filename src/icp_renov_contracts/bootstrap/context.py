from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ..config import BootstrapConfig, MachineConfigStore
from ..database import DatabaseService
from ..repositories import (
    ContractConditionsRepository, ContractDocumentRepository, ContractEventRepository, ContractRepository, MasterDataRepository, TemplateCatalogRepository,
)
from ..documents import (LibreOfficeConverter, ProductionDocxRenderer, TemplateSourceStore,
                         UnavailableCompanyDocumentDataProvider, UnavailableContractNumberAllocator)
from ..services import ContractLifecycleService, ContractService, DocumentGenerationService, MasterDataService, ReviewService, TemplateCatalogService
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
    master_data: MasterDataService | None
    contracts: ContractService | None
    template_catalog: TemplateCatalogService | None
    review: ReviewService | None
    generation: DocumentGenerationService | None
    lifecycle: ContractLifecycleService | None
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
            None,
            None,
            None,
            None,
            None,
            None,
            application_logger,
        )

    workspace = workspaces.ensure(config.active_workspace)
    database = DatabaseService(workspace.database_path)
    database.initialize()
    master_data = MasterDataService(MasterDataRepository(database))
    template_catalog = TemplateCatalogService(TemplateCatalogRepository(database))
    contracts = ContractService(
        ContractRepository(database), master_data, ContractConditionsRepository(database), template_catalog
    )
    review = ReviewService(contracts, workspaces, workspace)
    generation = DocumentGenerationService(
        database, contracts, ContractDocumentRepository(database), review, TemplateSourceStore(workspace.root),
        ProductionDocxRenderer(), LibreOfficeConverter(), UnavailableCompanyDocumentDataProvider(),
        UnavailableContractNumberAllocator(), workspace.root, application_logger,
    )
    lifecycle = ContractLifecycleService(
        database, contracts, ContractDocumentRepository(database), ContractEventRepository(database), workspace.root
    )
    review.generation_ready = generation.available
    application_logger.info("Local workspace and database initialized")
    return ApplicationContext(
        ApplicationState.READY,
        config,
        store,
        workspaces,
        workspace,
        database,
        master_data,
        contracts,
        template_catalog,
        review,
        generation,
        lifecycle,
        application_logger,
    )
