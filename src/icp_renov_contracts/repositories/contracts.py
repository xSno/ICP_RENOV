from __future__ import annotations

import sqlite3

from ..database import DatabaseService
from ..domain import (
    ClientSnapshot, Contract, ContractEquipmentItem, ContractEvent, ContractListItem, ContractRegime,
    ContractStatus, ContractType, EquipmentSnapshot, SiteSnapshot,
)


CONTRACT_COLUMNS = """
    id, number, COALESCE(generation_status,status) AS status, type_code, client_source_id, site_source_id, client_snapshot_json,
    site_snapshot_json, signatory_name, signatory_role, regime, template_id,
    template_version_id, created_at_utc, updated_at_utc
"""


def _contract(row: sqlite3.Row, items: tuple[ContractEquipmentItem, ...]) -> Contract:
    return Contract(
        id=row["id"], number=row["number"], status=ContractStatus(row["status"]), type_code=ContractType(row["type_code"]),
        client_source_id=row["client_source_id"], site_source_id=row["site_source_id"],
        client_snapshot=ClientSnapshot.from_json(row["client_snapshot_json"]) if row["client_snapshot_json"] else None,
        site_snapshot=SiteSnapshot.from_json(row["site_snapshot_json"]) if row["site_snapshot_json"] else None,
        signatory_name=row["signatory_name"], signatory_role=row["signatory_role"],
        regime=ContractRegime(row["regime"]) if row["regime"] else None,
        template_id=row["template_id"], template_version_id=row["template_version_id"],
        equipment_items=items, created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
    )


class ContractRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    @staticmethod
    def _rows(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row

    def create(self, contract_id: str, now: str, created_event: ContractEvent | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO contracts(id,status,type_code,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?)",
                (contract_id, ContractStatus.DRAFT.value, ContractType.CLIMATE_MAINTENANCE.value, now, now),
            )
            connection.execute(
                "INSERT INTO contract_conditions(contract_id,updated_at_utc) VALUES (?,?)", (contract_id, now)
            )
            if created_event is not None:
                from .events import ContractEventRepository
                ContractEventRepository.insert(connection, created_event)

    def get(self, contract_id: str) -> Contract | None:
        with self.database.connection() as connection:
            self._rows(connection)
            row = connection.execute(f"SELECT {CONTRACT_COLUMNS} FROM contracts WHERE id=?", (contract_id,)).fetchone()
            if row is None:
                return None
            item_rows = connection.execute(
                "SELECT id,contract_id,source_equipment_id,position,equipment_snapshot_json,observation "
                "FROM contract_equipment_items WHERE contract_id=? ORDER BY position", (contract_id,),
            ).fetchall()
            items = tuple(ContractEquipmentItem(
                id=item["id"], contract_id=item["contract_id"], source_equipment_id=item["source_equipment_id"],
                position=item["position"], snapshot=EquipmentSnapshot.from_json(item["equipment_snapshot_json"]),
                observation=item["observation"],
            ) for item in item_rows)
            return _contract(row, items)

    def list_drafts(self) -> list[ContractListItem]:
        with self.database.connection() as connection:
            self._rows(connection)
            rows = connection.execute(
                "SELECT id,number,COALESCE(generation_status,status) AS status,client_snapshot_json,site_snapshot_json,updated_at_utc,"
                "(SELECT MAX(revision_index) FROM contract_documents d WHERE d.contract_id=contracts.id AND d.document_kind='CONTRACT') AS latest_revision "
                "FROM contracts ORDER BY updated_at_utc DESC,id"
            ).fetchall()
            return [ContractListItem(
                id=row["id"], number=row["number"], status=ContractStatus(row["status"]),
                client_name=(ClientSnapshot.from_json(row["client_snapshot_json"]).display_name
                             if row["client_snapshot_json"] else "Client à sélectionner"),
                site_label=(SiteSnapshot.from_json(row["site_snapshot_json"]).label
                            if row["site_snapshot_json"] else "Site à sélectionner"),
                updated_at_utc=row["updated_at_utc"], latest_revision=(f"R{row['latest_revision']:02d}" if row["latest_revision"] else None),
            ) for row in rows]

    def select_client(self, contract_id: str, source_id: str, snapshot: ClientSnapshot,
                      signatory_name: str, signatory_role: str, now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE contracts SET client_source_id=?,client_snapshot_json=?,site_source_id=NULL,"
                "site_snapshot_json=NULL,signatory_name=?,signatory_role=?,regime=NULL,template_id=NULL,"
                "template_version_id=NULL,updated_at_utc=? WHERE id=?",
                (source_id, snapshot.to_json(), signatory_name, signatory_role, now, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(contract_id)
            connection.execute("DELETE FROM contract_equipment_items WHERE contract_id=?", (contract_id,))
            connection.execute(
                "UPDATE contract_conditions SET conclusion_mode=NULL,early_performance_requested=NULL WHERE contract_id=?",
                (contract_id,),
            )

    def select_site(self, contract_id: str, source_id: str, snapshot: SiteSnapshot, now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE contracts SET site_source_id=?,site_snapshot_json=?,updated_at_utc=? WHERE id=?",
                (source_id, snapshot.to_json(), now, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(contract_id)
            connection.execute("DELETE FROM contract_equipment_items WHERE contract_id=?", (contract_id,))

    def add_equipment(self, item: ContractEquipmentItem, now: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO contract_equipment_items(id,contract_id,source_equipment_id,position,equipment_snapshot_json,observation) "
                "VALUES (?,?,?,?,?,?)", (item.id, item.contract_id, item.source_equipment_id, item.position,
                                          item.snapshot.to_json(), item.observation),
            )
            self._touch(connection, item.contract_id, now)

    def remove_equipment(self, contract_id: str, item_id: str, ordered_ids: list[str], now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM contract_equipment_items WHERE id=? AND contract_id=?", (item_id, contract_id)
            )
            if cursor.rowcount != 1: raise LookupError(item_id)
            self._persist_positions(connection, contract_id, ordered_ids)
            self._touch(connection, contract_id, now)

    def reorder(self, contract_id: str, ordered_ids: list[str], now: str) -> None:
        with self.database.transaction() as connection:
            self._persist_positions(connection, contract_id, ordered_ids)
            self._touch(connection, contract_id, now)

    @staticmethod
    def _persist_positions(connection: sqlite3.Connection, contract_id: str, ordered_ids: list[str]) -> None:
        # A high temporary range avoids the per-contract uniqueness constraint during swaps.
        connection.execute("UPDATE contract_equipment_items SET position=position+1000000 WHERE contract_id=?", (contract_id,))
        for position, item_id in enumerate(ordered_ids):
            cursor = connection.execute(
                "UPDATE contract_equipment_items SET position=? WHERE id=? AND contract_id=?",
                (position, item_id, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(item_id)

    def update_observation(self, contract_id: str, item_id: str, observation: str, now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE contract_equipment_items SET observation=? WHERE id=? AND contract_id=?",
                (observation, item_id, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(item_id)
            self._touch(connection, contract_id, now)

    def update_signatory(self, contract_id: str, name: str, role: str, now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE contracts SET signatory_name=?,signatory_role=?,updated_at_utc=? WHERE id=?",
                (name, role, now, contract_id),
            )
            if cursor.rowcount != 1: raise LookupError(contract_id)

    @staticmethod
    def _touch(connection: sqlite3.Connection, contract_id: str, now: str) -> None:
        cursor = connection.execute("UPDATE contracts SET updated_at_utc=? WHERE id=?", (now, contract_id))
        if cursor.rowcount != 1: raise LookupError(contract_id)
