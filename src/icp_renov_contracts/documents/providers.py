from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
import sqlite3


class CompanyDocumentDataProvider(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def get(self) -> dict[str, object]: ...


class UnavailableCompanyDocumentDataProvider(CompanyDocumentDataProvider):
    def available(self) -> bool: return False
    def get(self) -> dict[str, object]: raise RuntimeError("company document data unavailable")


class StaticCompanyDocumentDataProvider(CompanyDocumentDataProvider):
    def __init__(self, data: dict[str, object]) -> None: self._data = deepcopy(data)
    def available(self) -> bool: return True
    def get(self) -> dict[str, object]: return deepcopy(self._data)


class ContractNumberAllocator(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def preview_next(self) -> str | None: ...

    @abstractmethod
    def allocate(self, connection: sqlite3.Connection) -> str: ...


class UnavailableContractNumberAllocator(ContractNumberAllocator):
    def available(self) -> bool: return False
    def preview_next(self) -> str | None: return None
    def allocate(self, connection: sqlite3.Connection) -> str:
        raise RuntimeError("contract numbering unavailable")
