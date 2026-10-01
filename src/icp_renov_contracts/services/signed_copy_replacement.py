from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


PAYLOAD_VERSION = "SIGNED_PDF_REPLACEMENT_V1"


@dataclass(frozen=True)
class SignedCopyReplacementAudit:
    previous_path: str
    previous_hash: str
    previous_attached_at: str
    previous_state: str
    replacement_path: str
    replacement_hash: str
    replacement_attached_at: str


def _relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    windows = PureWindowsPath(value)
    if windows.is_absolute() or windows.drive or windows.root:
        return None
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def encode_signed_pdf_replacement(audit: SignedCopyReplacementAudit) -> str:
    payload = {
        "version": PAYLOAD_VERSION,
        "previous": {"path": audit.previous_path, "sha256": audit.previous_hash, "attached_at": audit.previous_attached_at, "state": audit.previous_state},
        "replacement": {"path": audit.replacement_path, "sha256": audit.replacement_hash, "attached_at": audit.replacement_attached_at},
    }
    if decode_signed_pdf_replacement(json.dumps(payload, sort_keys=True, separators=(",", ":"))) is None:
        raise ValueError("invalid signed-copy replacement audit")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_signed_pdf_replacement(note: object) -> SignedCopyReplacementAudit | None:
    try:
        payload = json.loads(note) if isinstance(note, str) else None
        if not isinstance(payload, dict) or payload.get("version") != PAYLOAD_VERSION:
            return None
        previous, replacement = payload.get("previous"), payload.get("replacement")
        if not isinstance(previous, dict) or not isinstance(replacement, dict):
            return None
        previous_path, replacement_path = _relative_path(previous.get("path")), _relative_path(replacement.get("path"))
        previous_hash, replacement_hash = previous.get("sha256"), replacement.get("sha256")
        previous_at, replacement_at, state = previous.get("attached_at"), replacement.get("attached_at"), previous.get("state")
        if not all(isinstance(value, str) and value for value in (previous_path, replacement_path, previous_hash, replacement_hash, previous_at, replacement_at, state)):
            return None
        if not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in (previous_hash, replacement_hash)):
            return None
        if state not in {"VALID", "MISSING", "HASH_MISMATCH"}:
            return None
        try:
            datetime.fromisoformat(previous_at)
            datetime.fromisoformat(replacement_at)
        except ValueError:
            return None
        return SignedCopyReplacementAudit(previous_path, previous_hash, previous_at, state, replacement_path, replacement_hash, replacement_at)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def resolve_signed_copy_audit_path(workspace_root: Path, relative_path: str) -> Path | None:
    safe = _relative_path(relative_path)
    if safe is None:
        return None
    root = workspace_root.resolve()
    candidate = (root / Path(safe)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
