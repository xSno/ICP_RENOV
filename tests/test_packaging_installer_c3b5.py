from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerPackagingTests(unittest.TestCase):
    def test_release_version_authorities_are_aligned(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package = (ROOT / "src" / "icp_renov_contracts" / "__init__.py").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "installer" / "icp_renov_contrats.iss").read_text(encoding="utf-8")
        build = (ROOT / "packaging" / "build_windows_installer.ps1").read_text(encoding="utf-8")

        self.assertEqual(project["project"]["version"], "1.0.0")
        self.assertIn('__version__ = "1.0.0"', package)
        self.assertIn('#define AppVersion "1.0.0"', installer)
        self.assertIn("ICP-Renov-Contrats-Setup-1.0.0", installer)
        self.assertIn("ICP-Renov-Contrats-Setup-1.0.0.exe", build)

    def test_inno_script_packages_the_complete_one_dir_bundle_without_user_data(self):
        source = (ROOT / "packaging" / "installer" / "icp_renov_contrats.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{D0A5EB7C-5F81-46F1-91E5-D3780ADFB071}", source)
        self.assertIn('#define AppName "ICP Renov — Contrats"', source)
        self.assertIn("AppName={#AppName}", source)
        self.assertIn('#define AppVersion "1.0.0"', source)
        self.assertIn("AppVersion={#AppVersion}", source)
        self.assertIn("DefaultDirName={autopf}\\ICP Renov\\Contrats", source)
        self.assertIn('Source: "..\\..\\dist\\ICP Renov - Contrats\\*"', source)
        self.assertIn("recursesubdirs createallsubdirs", source)
        self.assertIn("ICP-Renov-Contrats-Setup-1.0.0", source)
        self.assertIn("{autoprograms}\\ICP Renov", source)
        self.assertIn("{autodesktop}\\ICP Renov", source)
        self.assertIn("SignTool=icp-renov", source)
        self.assertIn("SignedUninstaller=yes", source)
        self.assertNotIn('[SignTools]', source)
        self.assertNotIn("[UninstallDelete]", source)
        for forbidden in ("LOCALAPPDATA", "workspace", "sqlite", "backup", "soffice.exe path"):
            self.assertNotIn(forbidden, source.lower())

    def test_signed_installer_build_reuses_clean_bundle_then_invokes_inno(self):
        source = (ROOT / "packaging" / "build_windows_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("build_windows_bundle.ps1", source)
        self.assertIn("ICP Renov - Contrats.exe", source)
        self.assertIn("ISCC.exe", source)
        self.assertIn("Inno Setup 7", source)
        self.assertIn("ICP-Renov-Contrats-Setup-1.0.0.exe", source)
        self.assertIn("CertificateThumbprint", source)
        self.assertIn("Cert:\\CurrentUser\\My", source)
        self.assertIn("1.3.6.1.5.5.7.3.3", source)
        self.assertIn('$_.ObjectId -eq "1.3.6.1.5.5.7.3.3"', source)
        self.assertIn('"sign", "/sha1", $CertificateThumbprint, "/fd", "SHA256"', source)
        self.assertIn('"--signtool=icp-renov=$innoSignToolCommand"', source)
        self.assertIn("$q", source)
        self.assertIn("$f", source)
        self.assertIn("Assert-AuthenticodeSignature", source)
        self.assertNotIn(".pfx", source.lower())
