from __future__ import annotations

import json
import struct
import zlib
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "spike" / "fixtures"
ASSETS = ROOT / "spike" / "assets"


def equipment(count: int, observations: set[int] = frozenset()) -> list[dict]:
    result = []
    for i in range(1, count + 1):
        item = {
            "position": i,
            "type": "Unité murale" if i % 3 else "Groupe extérieur",
            "brand": "Marque Démo",
            "model": f"MODELE-{i:02d}",
            "serial_number": f"DEMO-SN-{i:04d}",
            "power_kw": str(2 + (i % 5) * 0.5),
            "location": f"Zone fictive {i:02d}",
            "install_date": f"202{1 + i % 5}-0{1 + i % 9}-{10 + i % 18:02d}",
            "observations": ("Accès par trappe technique.\nPrévoir une protection de sol." if i in observations else ""),
            "internal_notes": f"NOTE INTERNE INTERDITE {i:02d}",
        }
        result.append(item)
    return result


def base(kind: str = "CONTRACT") -> dict:
    return {
        "template": {
            "document_kind": kind,
            "version_status": "TO_VALIDATE",
            "context_authorizations": [],
            **({"contract_type_code": "CLIMATE_MAINTENANCE", "allowed_client_regimes": []} if kind == "CONTRACT" else {}),
        },
        "company": {
            "legal_name": "Entreprise Démonstration Documentaire",
            "trade_name": "Prestataire Démo",
            "registered_address": "10 rue de la Démonstration, 75000 Paris, France",
            "phone": "01 00 00 00 00", "email": "contact@example.invalid",
            "siren": "000 000 000", "siret": "000 000 000 00000",
            "signatory_name": "Alex Démo", "signatory_role": "Responsable fictif",
            "privacy_contact": "privacy@example.invalid", "complaints_contact": "reclamations@example.invalid",
            "withdrawal_contact": "retractation@example.invalid",
            "mediator_name": "Médiateur Fictif", "mediator_address": "1 voie Exemple, 75000 Paris",
            "mediator_website": "https://example.invalid/mediateur",
            "insurer_name": "Assureur Fictif", "insurance_policy_number": "DEMO-RC-001",
            "insurance_scope": "Responsabilité civile professionnelle - donnée de test",
            "insurance_valid_until": "2027-12-31",
        },
        "client": {
            "party_type": "PERSON", "regime": "CONSUMER", "first_name": "Camille", "last_name": "Exemple",
            "postal_address": "20 avenue du Test, 75000 Paris, France", "billing_address": "",
            "email": "camille@example.invalid", "signatory_name": "Camille Exemple", "signatory_role": "Client fictif",
        },
        "site": {"label": "Site démonstration", "address_line1": "20 avenue du Test", "address_line2": "", "postal_code": "75000", "city": "Paris", "country": "France"},
        "contract": {
            "type_code": "CLIMATE_MAINTENANCE",
            "number": "DEMO-2026-0001", "issue_date": "2026-08-12", "start_date": "2026-09-01",
            "initial_duration_mode": "STANDARD", "initial_duration_months": 12, "visits_per_year": 1,
            "conclusion_mode": "IN_PREMISES", "early_performance_requested": False, "signature_city": "Paris",
            "special_terms": "", "renewal_mode": "NONE", "renewal_period_months": 12,
            "non_renewal_notice_days": 30, "non_renewal_notice_channels": ["POSTAL_MAIL"],
            "breach_cure_period_days": 15, "equipment_items": equipment(1),
        },
        "service": {
            "included_area": "Paris intra-muros", "business_hours": "du lundi au vendredi, 9 h - 17 h",
            "travel_included": True, "priority_breakdown": False, "priority_breakdown_delay": "48 heures",
            "included_options": [], "additional_exclusions": "", "refrigerant_handling_mode": "EXCLUDED",
        },
        "pricing": {
            "annual_ht": "1000.00", "vat_rate": "0.20",
            "payment_terms_code": "TEST_NET_DAYS",
            "payment_due_days": 30, "payment_methods": ["BANK_TRANSFER"], "missed_appointment_fee": None,
        },
        "document": {"revision": "R01"},
    }


def fixtures() -> dict[str, dict]:
    f1 = base(); f1["contract"]["number"] = "DEMO-HAB-001"

    f2 = base(); f2["contract"].update({
        "number": "DEMO-HAB-010", "renewal_mode": "TACIT", "conclusion_mode": "DISTANCE_EMAIL",
        "early_performance_requested": True,
        "special_terms": "Accès au local technique sur rendez-vous.\nProtéger le parquet lors de chaque visite.",
        "equipment_items": equipment(10, {2, 7}),
    }); f2["company"]["logo"] = "../assets/logo.png"
    f2["service"]["included_options"] = ["DEEP_CLEANING", "DISINFECTION"]
    f2["service"]["additional_exclusions"] = "Accès en toiture hors périmètre."
    f2["pricing"]["renewal_price_rule"] = "FIXED"
    f2["template"]["context_authorizations"] = [
        {
            "client_regime": "CONSUMER", "conclusion_mode": "DISTANCE_EMAIL",
            "authorized_blocks": ["BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE", "BLOCK_ELECTRONIC_TERMINATION"],
        },
        {"client_regime": "CONSUMER", "conclusion_mode": "IN_PREMISES", "authorized_blocks": []},
    ]

    f3 = base(); f3["client"] = {
        "party_type": "ORGANIZATION", "regime": "PROFESSIONAL", "organization_name": "Société Boréale Démonstration",
        "legal_form": "Société fictive", "siret": "000 000 000 00001", "representative_name": "Morgan Exemple",
        "representative_role": "Direction fictive", "postal_address": "30 boulevard du Scenario, 69000 Lyon, France",
        "billing_address": "31 boulevard du Scenario, 69000 Lyon, France", "email": "contact@boreale.example.invalid",
        "signatory_name": "Morgan Exemple", "signatory_role": "Direction fictive",
    }; f3["site"].update({"address_line1": "30 boulevard du Scenario", "postal_code": "69000", "city": "Lyon"}); f3["contract"].update({"number": "DEMO-PRO-030", "renewal_mode": "MANUAL", "equipment_items": equipment(30, {5, 15, 25})}); f3["service"]["refrigerant_handling_mode"] = "IN_HOUSE_AUTHORIZED"; f3["pricing"].update({"missed_appointment_fee": "90.00", "renewal_price_rule": "NEW_PRICE_ON_RENEWAL"}); f3["company"].update({"refrigerant_capacity_number": "CAP-DEMO-001", "refrigerant_capacity_body": "Organisme fictif", "refrigerant_capacity_until": "2027-10-31"})

    f4 = deepcopy(f3); f4["contract"].update({"number": "DEMO-PRO-PARTNER", "renewal_mode": "TACIT", "equipment_items": equipment(4)}); f4["service"].update({"refrigerant_handling_mode": "PARTNER", "included_options": [], "additional_exclusions": ""}); f4["company"]["refrigerant_partner_name"] = "Partenaire Frigorifique Fictif"; f4["pricing"].update({"missed_appointment_fee": None, "renewal_price_rule": "FIXED"}); f4["client"]["billing_address"] = ""; f4["contract"]["special_terms"] = ""

    f5 = deepcopy(f3); f5["client"].update({"regime": "NON_PROFESSIONAL", "organization_name": "Résidence Démonstration Le Levant"}); f5["contract"].update({"number": "DEMO-NPRO-005", "renewal_mode": "NONE", "equipment_items": equipment(5)}); f5["service"]["refrigerant_handling_mode"] = "EXCLUDED"; f5["pricing"]["missed_appointment_fee"] = None; f5["pricing"].pop("renewal_price_rule", None)

    f6 = base("INTERVENTION_SHEET"); f6["contract"].update({"number": "DEMO-EXISTANT-042", "equipment_items": equipment(6, {1, 4})}); f6["intervention"] = {"date": "2026-08-12", "technician": "Technicien Démo", "other": "Mesure complémentaire fictive", "notes": "Filtres nettoyés.\nEssai de fonctionnement réalisé.", "issues": "Bruit intermittent sur l'unité 4.\nContrôle complémentaire conseillé.", "quote_recommended": True}; f6["company"]["logo"] = "../assets/logo.png"
    f7 = deepcopy(f6); f7["contract"]["number"] = "DEMO-EXISTANT-043"; f7["intervention"].update({"technician": "", "other": "", "notes": "", "issues": "", "quote_recommended": False}); f7["company"].pop("logo", None)
    return {
        "01_consumer_none_1": f1, "02_consumer_tacit_10_logo": f2,
        "03_professional_manual_30": f3, "04_professional_tacit_partner": f4,
        "05_non_professional_technical": f5, "06_intervention_present": f6,
        "07_intervention_optional_absent": f7,
    }


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def make_logo(path: Path) -> None:
    width, height = 480, 144
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            teal = x < 110 or (120 < x < 450 and 35 < y < 108)
            row.extend((20, 112, 120, 255) if teal else (255, 255, 255, 0))
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + _png_chunk(b"IDAT", zlib.compress(raw, 9)) + _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True); ASSETS.mkdir(parents=True, exist_ok=True)
    make_logo(ASSETS / "logo.png")
    for name, data in fixtures().items():
        (FIXTURES / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Created {len(fixtures())} deterministic fixtures and logo.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
