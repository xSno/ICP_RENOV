from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from icp_renov_contracts.services.capabilities import DocumentCapabilities
from icp_renov_contracts.services.generation_diagnostic import GenerationDiagnosticService


class _Probe:
    def __init__(self, value): self.value=value
    def probe(self): return self.value


class _WorkspaceService:
    def __init__(self, writable=True): self.writable=writable
    def inspect(self, root): return type('Inspection', (), {'available': True, 'writable': self.writable})()


class _Catalog:
    def source_integrity(self, version_id): return ('VALID', 'Source conforme')


class GenerationDiagnosticS15Tests(unittest.TestCase):
    def test_workstation_uses_exact_converter_authority_without_rendering(self):
        root=Path(tempfile.mkdtemp())
        service=GenerationDiagnosticService(type('Workspace', (), {'root':root})(), _WorkspaceService(), _Catalog(), None,
            _Probe(DocumentCapabilities(True, True, 'Conversion PDF disponible', '26.2.5.2')))
        status=service.workstation_status()
        self.assertTrue(status.workspace_available); self.assertTrue(status.docx_available); self.assertTrue(status.pdf_available)
        self.assertEqual(status.libreoffice_version, '26.2.5.2')

    def test_wrong_converter_is_blocked_before_any_model_test(self):
        root=Path(tempfile.mkdtemp())
        service=GenerationDiagnosticService(type('Workspace', (), {'root':root})(), _WorkspaceService(), _Catalog(), None,
            _Probe(DocumentCapabilities(True, False, 'Version non conforme', '26.2.6.0')))
        result=service.run('v1')
        self.assertFalse(result.succeeded); self.assertIn('Version attendue', result.issue)

    def test_workspace_failure_is_blocked_before_any_model_test(self):
        root=Path(tempfile.mkdtemp())
        service=GenerationDiagnosticService(type('Workspace', (), {'root':root})(), _WorkspaceService(False), _Catalog(), None,
            _Probe(DocumentCapabilities(True, True, 'Conversion PDF disponible', '26.2.5.2')))
        result=service.run('v1')
        self.assertFalse(result.succeeded); self.assertIn('dossier de travail', result.issue)
