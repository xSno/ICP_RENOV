from __future__ import annotations

import sqlite3

from ..database import DatabaseService
from ..domain import (
    ClientMaster,
    ClientSummary,
    EquipmentMaster,
    SiteMaster,
)


CLIENT_COLUMNS = """
    id, party_type, first_name, last_name, organization_name, legal_form, siret,
    address_line1, address_line2, postal_code, city, country, billing_address,
    phone, email, internal_reference, internal_notes, proposed_contact_name,
    proposed_contact_role, archived, created_at_utc, updated_at_utc
"""
SITE_COLUMNS = """
    id, client_id, label, address_line1, address_line2, postal_code, city, country,
    contact_name, contact_phone, internal_notes, archived, created_at_utc, updated_at_utc
"""
EQUIPMENT_COLUMNS = """
    id, site_id, equipment_type, brand, model, serial_number, power_kw, location,
    installation_date, internal_reference, internal_notes, archived, created_at_utc, updated_at_utc
"""


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _client(row: sqlite3.Row) -> ClientMaster:
    return ClientMaster(
        id=row["id"], party_type=row["party_type"], first_name=_text(row["first_name"]),
        last_name=_text(row["last_name"]), organization_name=_text(row["organization_name"]),
        legal_form=_text(row["legal_form"]), siret=_text(row["siret"]),
        address_line1=row["address_line1"], address_line2=_text(row["address_line2"]),
        postal_code=row["postal_code"], city=row["city"], country=row["country"],
        billing_address=_text(row["billing_address"]), phone=_text(row["phone"]),
        email=_text(row["email"]), internal_reference=_text(row["internal_reference"]),
        internal_notes=_text(row["internal_notes"]),
        proposed_contact_name=_text(row["proposed_contact_name"]),
        proposed_contact_role=_text(row["proposed_contact_role"]),
        archived=bool(row["archived"]), created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


def _site(row: sqlite3.Row) -> SiteMaster:
    return SiteMaster(
        id=row["id"], client_id=row["client_id"], label=row["label"],
        address_line1=row["address_line1"], address_line2=_text(row["address_line2"]),
        postal_code=row["postal_code"], city=row["city"], country=row["country"],
        contact_name=_text(row["contact_name"]), contact_phone=_text(row["contact_phone"]),
        internal_notes=_text(row["internal_notes"]), archived=bool(row["archived"]),
        created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
    )


def _equipment(row: sqlite3.Row) -> EquipmentMaster:
    return EquipmentMaster(
        id=row["id"], site_id=row["site_id"], equipment_type=row["equipment_type"],
        brand=_text(row["brand"]), model=_text(row["model"]),
        serial_number=_text(row["serial_number"]), power_kw=row["power_kw"],
        location=row["location"], installation_date=_text(row["installation_date"]),
        internal_reference=_text(row["internal_reference"]),
        internal_notes=_text(row["internal_notes"]), archived=bool(row["archived"]),
        created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
    )


class MasterDataRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    @staticmethod
    def _rows(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row

    def create_client(self, client: ClientMaster) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                f"INSERT INTO clients ({CLIENT_COLUMNS}) VALUES ({','.join('?' for _ in range(22))})",
                self._client_values(client),
            )

    def update_client(self, client: ClientMaster) -> None:
        assignments = ", ".join(f"{column.strip()} = ?" for column in CLIENT_COLUMNS.split(",")[1:])
        values = self._client_values(client)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE clients SET {assignments} WHERE id = ?",
                values[1:] + (client.id,),
            )
            if cursor.rowcount != 1:
                raise LookupError(client.id)

    @staticmethod
    def _client_values(client: ClientMaster) -> tuple[object, ...]:
        return (
            client.id, client.party_type, client.first_name or None, client.last_name or None,
            client.organization_name or None, client.legal_form or None, client.siret or None,
            client.address_line1, client.address_line2 or None, client.postal_code, client.city,
            client.country, client.billing_address or None, client.phone or None, client.email or None,
            client.internal_reference or None, client.internal_notes or None,
            client.proposed_contact_name or None, client.proposed_contact_role or None,
            int(client.archived), client.created_at_utc, client.updated_at_utc,
        )

    def get_client(self, client_id: str) -> ClientMaster | None:
        with self.database.connection() as connection:
            self._rows(connection)
            row = connection.execute(
                f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id = ?", (client_id,)
            ).fetchone()
            return _client(row) if row else None

    def list_clients(self, search: str = "", archived: bool = False) -> list[ClientSummary]:
        term = f"%{search.strip().lower()}%"
        with self.database.connection() as connection:
            self._rows(connection)
            rows = connection.execute(
                f"""
                SELECT {', '.join('c.' + value.strip() for value in CLIENT_COLUMNS.split(','))},
                       COUNT(DISTINCT s.id) AS site_count,
                       COUNT(DISTINCT e.id) AS equipment_count
                FROM clients c
                LEFT JOIN sites s ON s.client_id = c.id
                LEFT JOIN equipment e ON e.site_id = s.id
                WHERE c.archived = ?
                  AND LOWER(CASE WHEN c.party_type = 'PERSON'
                                 THEN c.first_name || ' ' || c.last_name
                                 ELSE c.organization_name END) LIKE ?
                GROUP BY c.id
                ORDER BY LOWER(CASE WHEN c.party_type = 'PERSON'
                                    THEN c.last_name || ' ' || c.first_name
                                    ELSE c.organization_name END), c.id
                """,
                (int(archived), term),
            ).fetchall()
            return [ClientSummary(_client(row), row["site_count"], row["equipment_count"]) for row in rows]

    def set_client_archived(self, client_id: str, archived: bool, updated_at: str) -> None:
        self._set_archived("clients", client_id, archived, updated_at)

    def create_site(self, site: SiteMaster) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                f"INSERT INTO sites ({SITE_COLUMNS}) VALUES ({','.join('?' for _ in range(14))})",
                self._site_values(site),
            )

    def update_site(self, site: SiteMaster) -> None:
        assignments = ", ".join(f"{column.strip()} = ?" for column in SITE_COLUMNS.split(",")[1:])
        values = self._site_values(site)
        with self.database.transaction() as connection:
            cursor = connection.execute(f"UPDATE sites SET {assignments} WHERE id = ?", values[1:] + (site.id,))
            if cursor.rowcount != 1:
                raise LookupError(site.id)

    @staticmethod
    def _site_values(site: SiteMaster) -> tuple[object, ...]:
        return (
            site.id, site.client_id, site.label, site.address_line1, site.address_line2 or None,
            site.postal_code, site.city, site.country, site.contact_name or None,
            site.contact_phone or None, site.internal_notes or None, int(site.archived),
            site.created_at_utc, site.updated_at_utc,
        )

    def get_site(self, site_id: str) -> SiteMaster | None:
        with self.database.connection() as connection:
            self._rows(connection)
            row = connection.execute(f"SELECT {SITE_COLUMNS} FROM sites WHERE id = ?", (site_id,)).fetchone()
            return _site(row) if row else None

    def list_sites(self, client_id: str) -> list[SiteMaster]:
        with self.database.connection() as connection:
            self._rows(connection)
            rows = connection.execute(
                f"SELECT {SITE_COLUMNS} FROM sites WHERE client_id = ? ORDER BY archived, LOWER(label), id",
                (client_id,),
            ).fetchall()
            return [_site(row) for row in rows]

    def set_site_archived(self, site_id: str, archived: bool, updated_at: str) -> None:
        self._set_archived("sites", site_id, archived, updated_at)

    def create_equipment(self, equipment: EquipmentMaster) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                f"INSERT INTO equipment ({EQUIPMENT_COLUMNS}) VALUES ({','.join('?' for _ in range(14))})",
                self._equipment_values(equipment),
            )

    def update_equipment(self, equipment: EquipmentMaster) -> None:
        assignments = ", ".join(f"{column.strip()} = ?" for column in EQUIPMENT_COLUMNS.split(",")[1:])
        values = self._equipment_values(equipment)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE equipment SET {assignments} WHERE id = ?", values[1:] + (equipment.id,)
            )
            if cursor.rowcount != 1:
                raise LookupError(equipment.id)

    @staticmethod
    def _equipment_values(equipment: EquipmentMaster) -> tuple[object, ...]:
        return (
            equipment.id, equipment.site_id, equipment.equipment_type, equipment.brand or None,
            equipment.model or None, equipment.serial_number or None, equipment.power_kw,
            equipment.location, equipment.installation_date or None,
            equipment.internal_reference or None, equipment.internal_notes or None,
            int(equipment.archived), equipment.created_at_utc, equipment.updated_at_utc,
        )

    def get_equipment(self, equipment_id: str) -> EquipmentMaster | None:
        with self.database.connection() as connection:
            self._rows(connection)
            row = connection.execute(
                f"SELECT {EQUIPMENT_COLUMNS} FROM equipment WHERE id = ?", (equipment_id,)
            ).fetchone()
            return _equipment(row) if row else None

    def list_equipment(self, site_id: str) -> list[EquipmentMaster]:
        with self.database.connection() as connection:
            self._rows(connection)
            rows = connection.execute(
                f"SELECT {EQUIPMENT_COLUMNS} FROM equipment WHERE site_id = ? "
                "ORDER BY archived, LOWER(equipment_type), LOWER(location), id",
                (site_id,),
            ).fetchall()
            return [_equipment(row) for row in rows]

    def set_equipment_archived(self, equipment_id: str, archived: bool, updated_at: str) -> None:
        self._set_archived("equipment", equipment_id, archived, updated_at)

    def _set_archived(self, table: str, record_id: str, archived: bool, updated_at: str) -> None:
        if table not in {"clients", "sites", "equipment"}:
            raise ValueError(table)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET archived = ?, updated_at_utc = ? WHERE id = ?",
                (int(archived), updated_at, record_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(record_id)

