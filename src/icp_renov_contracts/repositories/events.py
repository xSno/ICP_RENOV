from __future__ import annotations

import sqlite3

from ..database import DatabaseService
from ..domain import ContractEvent, ContractEventType


def _event(row: sqlite3.Row) -> ContractEvent:
    return ContractEvent(
        id=row["id"], contract_id=row["contract_id"], type=ContractEventType(row["type"]),
        occurred_at=row["occurred_at"], effective_date=row["effective_date"],
        document_id=row["document_id"], period_start=row["period_start"], period_end=row["period_end"],
        renewal_annual_ht=row["renewal_annual_ht"], renewal_vat_rate=row["renewal_vat_rate"],
        renewal_vat_amount=row["renewal_vat_amount"], renewal_annual_ttc=row["renewal_annual_ttc"],
        notification_date=row["notification_date"], reason_code=row["reason_code"],
        reason_text=row["reason_text"], note=row["note"],
    )


class ContractEventRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    def list_for_contract(self, contract_id: str) -> tuple[ContractEvent, ...]:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM contract_events WHERE contract_id=? ORDER BY occurred_at DESC,id DESC",
                (contract_id,),
            ).fetchall()
            return tuple(_event(row) for row in rows)

    def list_for_document(self, document_id: str, event_type: ContractEventType | None = None) -> tuple[ContractEvent, ...]:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            if event_type is None:
                rows = connection.execute(
                    "SELECT * FROM contract_events WHERE document_id=? ORDER BY occurred_at DESC,id DESC", (document_id,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM contract_events WHERE document_id=? AND type=? ORDER BY occurred_at DESC,id DESC",
                    (document_id,event_type.value),
                ).fetchall()
            return tuple(_event(row) for row in rows)

    @staticmethod
    def insert(connection: sqlite3.Connection, event: ContractEvent) -> None:
        connection.execute(
            "INSERT INTO contract_events(id,contract_id,type,occurred_at,effective_date,document_id,period_start,period_end,"
            "renewal_annual_ht,renewal_vat_rate,renewal_vat_amount,renewal_annual_ttc,notification_date,reason_code,reason_text,note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event.id,event.contract_id,event.type.value,event.occurred_at,event.effective_date,event.document_id,
             event.period_start,event.period_end,event.renewal_annual_ht,event.renewal_vat_rate,event.renewal_vat_amount,
             event.renewal_annual_ttc,event.notification_date,event.reason_code,event.reason_text,event.note),
        )
