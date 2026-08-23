from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerPackagingTests(unittest.TestCase):
    def test_inno_script_packages_the_complete_one_dir_bundle_without_user_data(self):
        source = (ROOT / "packaging" / "installer" / "icp_renov_contrats.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{D0A5EB7C-5F81-46F1-91E5-D3780ADFB071}", source)
        self.assertIn('#define AppName "ICP Renov — Contrats"', source)
        self.assertIn("AppName={#AppName}", source)
        self.assertIn('#define AppVersion "0.1.0"', source)
        self.assertIn("AppVersion={#AppVersion}", source)
        self.assertIn("DefaultDirName={autopf}\\ICP Renov\\Contrats", source)
        self.assertIn('Source: "..\\..\\dist\\ICP Renov - Contrats\\*"', source)
        self.assertIn("recursesubdirs createallsubdirs", source)
        self.assertIn("ICP-Renov-Contrats-Setup-0.1.0", source)
        self.assertIn("{autoprograms}\\ICP Renov", source)
        self.assertIn("{autodesktop}\\ICP Renov", source)
        self.assertNotIn("[UninstallDelete]", source)
        for forbidden in ("LOCALAPPDATA", "workspace", "sqlite", "backup", "soffice.exe path"):
            self.assertNotIn(forbidden, source.lower())

    def test_installer_build_reuses_clean_bundle_then_invokes_inno(self):
        source = (ROOT / "packaging" / "build_windows_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("build_windows_bundle.ps1", source)
        self.assertIn("ICP Renov - Contrats.exe", source)
        self.assertIn("ISCC.exe", source)
        self.assertIn("Inno Setup 7", source)
        self.assertIn("ICP-Renov-Contrats-Setup-0.1.0.exe", source)
