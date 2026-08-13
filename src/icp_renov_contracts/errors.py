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

