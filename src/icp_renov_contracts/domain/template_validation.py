from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ValidationCheckStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"


class ExternalGateStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    TO_REVIEW = "TO_REVIEW"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ReviewEvidenceStatus(str, Enum):
    TO_REVIEW = "TO_REVIEW"
    CONFIRMED = "CONFIRMED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


EXTERNAL_GATE_CODES = (
    "LEGAL_BASE_CONTRACT_TERMS",
    "LEGAL_CLIENT_REGIME_CLASSIFICATION",
    "LEGAL_B2C_CONSUMER_TERMS",
    "LEGAL_NON_PROFESSIONAL_TERMS",
    "LEGAL_TACIT_RENEWAL",
    "LEGAL_WITHDRAWAL_INFORMATION",
    "LEGAL_WITHDRAWAL_FORM",
    "LEGAL_EARLY_PERFORMANCE_REQUEST",
    "LEGAL_ELECTRONIC_TERMINATION",
    "LEGAL_ELECTRONIC_WITHDRAWAL",
    "LEGAL_MEDIATOR",
    "LEGAL_B2B_PAYMENT",
    "LEGAL_B2B_JURISDICTION",
    "LEGAL_PRICE_REVISION",
    "LEGAL_REFRIGERANT_SCOPE",
    "LEGAL_INSURANCE_REPRESENTATION",
    "LEGAL_PRIVACY_NOTICE",
    "LEGAL_SPECIAL_TERMS_PRIORITY",
)

DEFERRED_EXTERNAL_GATE_CODES = ("LEGAL_PRICE_INDEXATION",)

EXTERNAL_GATE_LABELS = {
    "LEGAL_BASE_CONTRACT_TERMS": "Conditions contractuelles de base",
    "LEGAL_CLIENT_REGIME_CLASSIFICATION": "Classification du régime client",
    "LEGAL_B2C_CONSUMER_TERMS": "Clauses consommateur",
    "LEGAL_NON_PROFESSIONAL_TERMS": "Clauses non-professionnel",
    "LEGAL_TACIT_RENEWAL": "Reconduction tacite",
    "LEGAL_WITHDRAWAL_INFORMATION": "Information sur la rétractation",
    "LEGAL_WITHDRAWAL_FORM": "Formulaire de rétractation",
    "LEGAL_EARLY_PERFORMANCE_REQUEST": "Demande d’exécution anticipée",
    "LEGAL_ELECTRONIC_TERMINATION": "Résiliation électronique",
    "LEGAL_ELECTRONIC_WITHDRAWAL": "Rétractation électronique",
    "LEGAL_MEDIATOR": "Médiateur de la consommation",
    "LEGAL_B2B_PAYMENT": "Paiement professionnel",
    "LEGAL_B2B_JURISDICTION": "Juridiction professionnelle",
    "LEGAL_PRICE_REVISION": "Révision du prix",
    "LEGAL_REFRIGERANT_SCOPE": "Périmètre fluides frigorigènes",
    "LEGAL_INSURANCE_REPRESENTATION": "Présentation de l’assurance",
    "LEGAL_PRIVACY_NOTICE": "Information sur les données personnelles",
    "LEGAL_SPECIAL_TERMS_PRIORITY": "Priorité des conditions particulières",
}


@dataclass(frozen=True)
class ExternalGateEvidence:
    code: str
    status: ExternalGateStatus
    reference: str = ""


@dataclass(frozen=True)
class RegimeConfirmation:
    regime: str
    reference: str
    confirmed_at: str


@dataclass(frozen=True)
class TemplateValidationRecord:
    version_id: str
    structure_status: ValidationCheckStatus = ValidationCheckStatus.NOT_RUN
    structure_checked_at: str | None = None
    structure_issues: tuple[str, ...] = ()
    render_status: ValidationCheckStatus = ValidationCheckStatus.NOT_RUN
    render_tested_at: str | None = None
    render_cases: tuple[str, ...] = ()
    docx_status: ValidationCheckStatus = ValidationCheckStatus.NOT_RUN
    pdf_status: ValidationCheckStatus = ValidationCheckStatus.NOT_RUN
    postflight_status: ValidationCheckStatus = ValidationCheckStatus.NOT_RUN
    equipment_coverage: tuple[int, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    visual_review_status: ReviewEvidenceStatus = ReviewEvidenceStatus.TO_REVIEW
    context_review_status: ReviewEvidenceStatus = ReviewEvidenceStatus.TO_REVIEW
    external_content_status: ExternalGateStatus = ExternalGateStatus.TO_REVIEW
    external_validator: str = ""
    external_validation_date: str | None = None
    external_scope: str = ""
    external_reference: str = ""
    external_reservations: str = ""
    last_validation_at_utc: str | None = None
    external_gates: tuple[ExternalGateEvidence, ...] = ()
    regime_confirmations: tuple[RegimeConfirmation, ...] = ()


@dataclass(frozen=True)
class AvailabilityItem:
    code: str
    label: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class AvailabilityEvaluation:
    version_id: str
    items: tuple[AvailabilityItem, ...]

    @property
    def ready(self) -> bool:
        return all(item.passed for item in self.items)

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(item.reason or item.label for item in self.items if not item.passed)
