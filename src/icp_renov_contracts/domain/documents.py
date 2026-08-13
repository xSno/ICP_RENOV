from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json


class DocumentKind(str, Enum):
    CONTRACT = "CONTRACT"


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

    @property
    def revision(self) -> str:
        return f"R{self.revision_index:02d}"

    @property
    def snapshot(self) -> dict:
        return json.loads(self.snapshot_json)
