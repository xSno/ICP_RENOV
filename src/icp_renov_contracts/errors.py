class ApplicationError(Exception):
    """Base error with a bounded message suitable for the desktop UI."""

    user_message = "Une erreur technique empêche le démarrage de l’application."


class InvalidBootstrapConfigurationError(ApplicationError):
    user_message = "La configuration locale de ce poste est invalide."


class WorkspaceUnavailableError(ApplicationError):
    user_message = "Le dossier de travail est indisponible."


class WorkspaceNotWritableError(ApplicationError):
    user_message = "Le dossier de travail n’est pas accessible en écriture."


class NetworkWorkspaceRejectedError(ApplicationError):
    user_message = "Le dossier de travail doit être situé sur ce poste, et non sur le réseau."


class DatabaseInitializationError(ApplicationError):
    user_message = "La base de données locale ne peut pas être initialisée."


class MasterDataValidationError(ApplicationError):
    user_message = "Certaines informations sont manquantes ou invalides."

    def __init__(self, field_errors: dict[str, str]) -> None:
        self.field_errors = field_errors
        super().__init__(self.user_message)


class MasterDataNotFoundError(ApplicationError):
    user_message = "La fiche demandée est introuvable."


class MasterDataPersistenceError(ApplicationError):
    user_message = "Les données ne peuvent pas être enregistrées."


class ContractValidationError(ApplicationError):
    user_message = "Cette action n’est pas valide pour ce brouillon."


class ContractNotFoundError(ApplicationError):
    user_message = "Le brouillon demandé est introuvable."


class ContractPersistenceError(ApplicationError):
    user_message = "Le brouillon ne peut pas être enregistré."


class ContractLifecycleError(ApplicationError):
    user_message = "Le suivi du contrat ne peut pas être enregistré."

    def __init__(self, detail: str = "", user_message: str | None = None) -> None:
        self.detail = detail
        if user_message is not None:
            self.user_message = user_message
        super().__init__(detail or self.user_message)


class ContractConditionsValidationError(ApplicationError):
    user_message = "Certaines conditions sont invalides."

    def __init__(self, field_errors: dict[str, str]) -> None:
        self.field_errors = field_errors
        super().__init__(self.user_message)


class NumberingValidationError(ApplicationError):
    user_message = "La configuration de numérotation est invalide."

    def __init__(self, detail: str, user_message: str | None = None) -> None:
        self.detail = detail
        if user_message is not None:
            self.user_message = user_message
        super().__init__(detail)


class NumberingCollisionError(NumberingValidationError):
    user_message = "Le prochain numéro configuré existe déjà. Corrigez le prochain numéro avant de générer un nouveau contrat."

    def __init__(self) -> None:
        super().__init__("collision", self.user_message)
