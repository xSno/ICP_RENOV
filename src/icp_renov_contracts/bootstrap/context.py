from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ..config import BootstrapConfig, MachineConfigStore
from ..database import DatabaseService
from ..repositories import (
    AlertSettingsRepository, CompanySettingsRepository, ContractConditionsRepository, ContractDocumentRepository, ContractEventRepository, ContractRepository, MasterDataRepository, NumberingSettingsRepository, TemplateCatalogRepository, TemplateValidationRepository,
)
from ..documents import (LibreOfficeConverter, NonOfficialModelValidationRunner, PersistedCompanyDocumentDataProvider, ProductionDocxRenderer, TemplateSourceStore,
                         )
from ..services import AlertSettingsService, BackupService, CompanySettingsService, ContractLifecycleService, ContractService, DocumentGenerationService, InterventionSheetGenerationService, LocalBusinessDateProvider, MasterDataService, NumberingSettingsService, PersistedContractNumberAllocator, RealBackupSummaryProvider, ReviewService, RestoreService, TemplateCatalogService
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
    intervention_generation: InterventionSheetGenerationService | None
    lifecycle: ContractLifecycleService | None
    company: CompanySettingsService | None
    numbering: NumberingSettingsService | None
    alerts: AlertSettingsService | None
    backup: BackupService | None
    restore: RestoreService
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
            None,
            None,
            None,
            None,
            None,
            RestoreService(store),
            application_logger,
        )

    workspace = workspaces.ensure(config.active_workspace)
    database = DatabaseService(workspace.database_path)
    database.initialize()
    master_data = MasterDataService(MasterDataRepository(database))
    company_repository = CompanySettingsRepository(database)
    source_store = TemplateSourceStore(workspace.root)
    renderer = ProductionDocxRenderer()
    converter = LibreOfficeConverter()
    company_provider = PersistedCompanyDocumentDataProvider(company_repository, workspace.root)
    template_catalog = TemplateCatalogService(
        TemplateCatalogRepository(database), source_store, TemplateValidationRepository(database),
        validation_runner=NonOfficialModelValidationRunner(workspace.root, renderer, converter, company_provider),
        company_provider=company_provider,
    )
    date_provider = LocalBusinessDateProvider()
    alerts = AlertSettingsService(AlertSettingsRepository(database))
    backup = BackupService(database, workspace, store)
    numbering = NumberingSettingsService(NumberingSettingsRepository(database), date_provider)
    contracts = ContractService(
        ContractRepository(database), master_data, ContractConditionsRepository(database), template_catalog, alerts
    )
    review = ReviewService(contracts, workspaces, workspace)
    company = CompanySettingsService(company_repository, TemplateCatalogRepository(database), workspace.root)
    generation = DocumentGenerationService(
        database, contracts, ContractDocumentRepository(database), review, source_store,
        renderer, converter, company_provider,
        PersistedContractNumberAllocator(numbering, lambda: workspaces.inspect(workspace.root).writable), workspace.root, application_logger,
    )
    lifecycle = ContractLifecycleService(
        database, contracts, ContractDocumentRepository(database), ContractEventRepository(database), workspace.root,
        date_provider=date_provider,
    )
    intervention_generation=InterventionSheetGenerationService(
        database,contracts,ContractDocumentRepository(database),source_store,
        renderer,converter,company_provider,workspace.root,application_logger,
    )
    failures = lifecycle.reconcile_due_activations()
    if failures:
        application_logger.error("Due contract activation reconciliation failed for %s", ",".join(failures))
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
        intervention_generation,
        lifecycle,
        company,
        numbering,
        alerts,
        backup,
        RestoreService(store),
        application_logger,
    )
