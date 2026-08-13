from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReviewState(str, Enum):
    VALID = "VALID"
    ERROR = "ERROR"


class ReviewBlockId(str, Enum):
    CLIENT_SIGNATORY = "CLIENT_SIGNATORY"
    SITE_EQUIPMENT = "SITE_EQUIPMENT"
    CONTRACT_CONTEXT = "CONTRACT_CONTEXT"
    MODEL_SERVICES = "MODEL_SERVICES"
    PERIOD = "PERIOD"
    INTERVENTION = "INTERVENTION"
    PRICE_PAYMENT = "PRICE_PAYMENT"
    RENEWAL_END = "RENEWAL_END"
    SPECIAL_TERMS = "SPECIAL_TERMS"


@dataclass(frozen=True)
class ReviewIssue:
    message: str


@dataclass(frozen=True)
class ReviewBlockResult:
    id: ReviewBlockId
    title: str
    state: ReviewState
    summary: str
    issues: tuple[ReviewIssue, ...]
    target_step: int


@dataclass(frozen=True)
class GenerationCheck:
    key: str
    label: str
    available: bool
    detail: str


@dataclass(frozen=True)
class GenerationReadiness:
    checks: tuple[GenerationCheck, ...]

    @property
    def generation_available(self) -> bool:
        return all(check.available for check in self.checks)


@dataclass(frozen=True)
class ReviewResult:
    blocks: tuple[ReviewBlockResult, ...]
    generation: GenerationReadiness

    @property
    def data_complete(self) -> bool:
        return all(block.state is ReviewState.VALID for block in self.blocks)

    @property
    def generation_available(self) -> bool:
        return self.data_complete and self.generation.generation_available
