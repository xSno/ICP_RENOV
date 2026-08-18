from __future__ import annotations
import sqlite3
from decimal import Decimal
from ..database import DatabaseService
from ..domain.company import CompanySettings

class CompanySettingsRepository:
    def __init__(self, database: DatabaseService) -> None: self.database = database
    def get(self) -> CompanySettings:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row; row = connection.execute("SELECT * FROM company_settings WHERE singleton=1").fetchone()
            values = dict(row); values.pop("singleton"); values["share_capital"] = Decimal(values["share_capital"]) if values["share_capital"] else None
            return CompanySettings(**values)
    def save(self, settings: CompanySettings) -> None:
        values = settings.__dict__.copy(); values.pop("updated_at_utc", None); values.pop("logo_relpath", None); values.pop("logo_hash", None)
        assignments = ",".join(f"{key}=?" for key in values)
        with self.database.transaction() as connection:
            serialized = [format(value, "f") if isinstance(value, Decimal) else ("" if value is None else value) for value in values.values()]
            connection.execute(f"UPDATE company_settings SET {assignments},logo_relpath=?,logo_hash=?,updated_at_utc=CURRENT_TIMESTAMP WHERE singleton=1", (*serialized, settings.logo_relpath, settings.logo_hash))
