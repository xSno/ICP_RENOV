from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json


class DocumentKind(str, Enum):
    CONTRACT = "CONTRACT"
    INTERVENTION_SHEET = "INTERVENTION_SHEET"


class SignedCopyState(str, Enum):
    NONE = "NONE"
    VALID = "VALID"
    MISSING = "MISSING"
    HASH_MISMATCH = "HASH_MISMATCH"


@dataclass(frozen=True)
class ContractDocument:
    id: str
    contract_id: str
    document_kind: DocumentKind
    revision_index: int | None
    generated_at_utc: str
    template_version_id: str
    docx_relpath: str
    pdf_relpath: str
    snapshot_json: str
    docx_sha256: str
    pdf_sha256: str
    signed_pdf_path: str | None = None
    signed_pdf_hash: str | None = None
    signed_pdf_attached_at: str | None = None

    def __post_init__(self) -> None:
        if self.document_kind is DocumentKind.CONTRACT and (self.revision_index is None or self.revision_index < 1):
            raise ValueError("CONTRACT document requires a positive revision")
        if self.document_kind is DocumentKind.INTERVENTION_SHEET:
            if self.revision_index is not None:
                raise ValueError("INTERVENTION_SHEET document has no revision")
            if any((self.signed_pdf_path, self.signed_pdf_hash, self.signed_pdf_attached_at)):
                raise ValueError("INTERVENTION_SHEET document has no signed-copy metadata")

    @property
    def revision(self) -> str | None:
        if self.revision_index is None:return None
        return f"R{self.revision_index:02d}"

    @property
    def snapshot(self) -> dict:
        return json.loads(self.snapshot_json)
