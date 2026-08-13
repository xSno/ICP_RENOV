from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json


class DocumentKind(str, Enum):
    CONTRACT = "CONTRACT"


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
    revision_index: int
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

    @property
    def revision(self) -> str:
        return f"R{self.revision_index:02d}"

    @property
    def snapshot(self) -> dict:
        return json.loads(self.snapshot_json)
