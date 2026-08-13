from __future__ import annotations

import json
import sqlite3

from ..database import DatabaseService
from ..domain import ContractConditions


CONDITION_COLUMNS = tuple(ContractConditions.__dataclass_fields__)
JSON_FIELDS = frozenset({
    "included_options", "payment_methods", "non_renewal_notice_channels", "early_termination_reason_codes",
})
BOOL_FIELDS = frozenset({"early_performance_requested", "travel_included", "priority_breakdown"})
DB_COLUMN = {name: f"{name}_json" if name in JSON_FIELDS else name for name in CONDITION_COLUMNS}


class ContractConditionsRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    def get(self, contract_id: str) -> ContractConditions | None:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                f"SELECT {','.join(f'{DB_COLUMN[name]} AS {name}' for name in CONDITION_COLUMNS)} "
                "FROM contract_conditions WHERE contract_id=?", (contract_id,)
            ).fetchone()
            if row is None: return None
            values = {}
            for name in CONDITION_COLUMNS:
                value = row[name]
                if name in JSON_FIELDS: value = tuple(json.loads(value))
                elif name in BOOL_FIELDS and value is not None: value = bool(value)
                values[name] = value
            return ContractConditions(**values)

    def save(self, contract_id: str, conditions: ContractConditions, now: str) -> None:
        assignments = ",".join(f"{DB_COLUMN[name]}=?" for name in CONDITION_COLUMNS)
        values = tuple(self._value(name, getattr(conditions, name)) for name in CONDITION_COLUMNS)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE contract_conditions SET {assignments},updated_at_utc=? WHERE contract_id=?",
                values + (now, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(contract_id)
            self._touch(connection, contract_id, now)

    def change_regime(self, contract_id: str, regime: str | None, keep_template: bool, now: str) -> None:
        with self.database.transaction() as connection:
            if keep_template:
                cursor = connection.execute("UPDATE contracts SET regime=?,updated_at_utc=? WHERE id=?", (regime, now, contract_id))
            else:
                cursor = connection.execute(
                    "UPDATE contracts SET regime=?,template_id=NULL,template_version_id=NULL,updated_at_utc=? WHERE id=?",
                    (regime, now, contract_id),
                )
            if cursor.rowcount != 1: raise LookupError(contract_id)
            connection.execute(
                "UPDATE contract_conditions SET conclusion_mode=NULL,early_performance_requested=NULL,updated_at_utc=? WHERE contract_id=?",
                (now, contract_id),
            )

    def select_template(self, contract_id: str, template_id: str, version_id: str,
                        conditions: ContractConditions, now: str) -> None:
        assignments = ",".join(f"{DB_COLUMN[name]}=?" for name in CONDITION_COLUMNS)
        values = tuple(self._value(name, getattr(conditions, name)) for name in CONDITION_COLUMNS)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE contracts SET template_id=?,template_version_id=?,updated_at_utc=? WHERE id=?",
                (template_id, version_id, now, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(contract_id)
            connection.execute(
                f"UPDATE contract_conditions SET {assignments},updated_at_utc=? WHERE contract_id=?",
                values + (now, contract_id),
            )

    @staticmethod
    def _value(name: str, value: object) -> object:
        if name in JSON_FIELDS: return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if name in BOOL_FIELDS and value is not None: return int(bool(value))
        return value

    @staticmethod
    def _touch(connection: sqlite3.Connection, contract_id: str, now: str) -> None:
        cursor = connection.execute("UPDATE contracts SET updated_at_utc=? WHERE id=?", (now, contract_id))
        if cursor.rowcount != 1: raise LookupError(contract_id)
