from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP


MONTHS = ("", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
REGIME_LABELS = {"CONSUMER": "Consommateur", "NON_PROFESSIONAL": "Non-professionnel", "PROFESSIONAL": "Professionnel"}
PAYMENT_LABELS = {"BANK_TRANSFER": "virement bancaire", "CHEQUE": "chèque", "DIRECT_DEBIT": "prélèvement"}
CHANNEL_LABELS = {"POSTAL_MAIL": "courrier", "EMAIL": "courriel", "ELECTRONIC": "voie électronique"}
RENEWAL_PRICE_LABELS = {
    "FIXED": "prix inchangé",
    "NEW_PRICE_ON_RENEWAL": "nouveau prix communiqué au renouvellement",
}
INCLUDED_OPTION_LABELS = {
    "DEEP_CLEANING": "nettoyage approfondi",
    "DISINFECTION": "désinfection",
}


def french_date(value: str | date | None) -> str:
    if not value:
        return ""
    d = value if isinstance(value, date) else datetime.strptime(value, "%Y-%m-%d").date()
    return f"{d.day} {MONTHS[d.month]} {d.year}"


def french_money(value: object | None) -> str:
    if value in (None, ""):
        return ""
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    raw = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "\u202f")
    return raw


def french_percent(value: object | None) -> str:
    if value in (None, ""):
        return ""
    percent = Decimal(str(value)) * Decimal("100")
    rendered = f"{percent.quantize(Decimal('0.01')):f}".rstrip("0").rstrip(".")
    return rendered.replace(".", ",")


def months_label(value: object | None) -> str:
    return "" if value in (None, "") else f"{int(value)} mois"


def days_label(value: object | None) -> str:
    if value in (None, ""):
        return ""
    n = int(value)
    return f"{n} jour" if n == 1 else f"{n} jours"


def add_months(iso_date: str, months: int) -> str:
    d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    target_month = d.month - 1 + months
    year = d.year + target_month // 12
    month = target_month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def standard_initial_end_date(iso_date: str, months: int) -> str:
    """Registry rule: clamped calendar anniversary minus one calendar day."""
    anniversary = datetime.strptime(add_months(iso_date, months), "%Y-%m-%d").date()
    return (anniversary - timedelta(days=1)).isoformat()


def _join(values: list[str]) -> str:
    return ", ".join(v for v in values if v)


def prepare_context(raw: dict) -> dict:
    ctx = deepcopy(raw)
    company = ctx.setdefault("company", {})
    client = ctx.setdefault("client", {})
    site = ctx.setdefault("site", {})
    contract = ctx.setdefault("contract", {})
    service = ctx.setdefault("service", {})
    pricing = ctx.setdefault("pricing", {})
    document = ctx.setdefault("document", {})
    intervention = ctx.setdefault("intervention", {})

    company["display_name"] = company.get("trade_name") or company.get("legal_name", "")
    company["registration_identifiers_summary"] = _join([
        f"SIREN {company['siren']}" if company.get("siren") else "",
        f"SIRET {company['siret']}" if company.get("siret") else "",
    ])
    company["insurance_summary"] = _join([
        company.get("insurer_name", ""),
        f"police {company['insurance_policy_number']}" if company.get("insurance_policy_number") else "",
        company.get("insurance_scope", ""),
        f"valide jusqu'au {french_date(company['insurance_valid_until'])}" if company.get("insurance_valid_until") else "",
    ])
    company["refrigerant_capacity_summary"] = _join([
        company.get("refrigerant_capacity_number", ""), company.get("refrigerant_capacity_body", ""),
        f"valide jusqu'au {french_date(company['refrigerant_capacity_until'])}" if company.get("refrigerant_capacity_until") else "",
    ])

    if client.get("party_type") == "PERSON":
        client["contract_name"] = " ".join(x for x in (client.get("first_name"), client.get("last_name")) if x)
    else:
        client["contract_name"] = client.get("organization_name", "")
    client["representative_summary"] = _join([client.get("representative_name", ""), client.get("representative_role", "")])
    client["regime_label"] = REGIME_LABELS.get(client.get("regime", ""), "")
    site["full_address"] = _join([
        site.get("address_line1", ""), site.get("address_line2", ""),
        " ".join(x for x in (site.get("postal_code"), site.get("city")) if x), site.get("country", ""),
    ])

    if contract.get("initial_duration_mode") == "STANDARD":
        contract["initial_end_date"] = standard_initial_end_date(
            contract["start_date"], int(contract["initial_duration_months"])
        )
    for key in ("issue_date", "start_date", "initial_end_date"):
        contract[key] = french_date(contract.get(key))
    contract["renewal_period_label"] = months_label(contract.get("renewal_period_months"))
    contract["non_renewal_notice_label"] = days_label(contract.get("non_renewal_notice_days"))
    contract["non_renewal_notice_channels_label"] = _join([CHANNEL_LABELS.get(v, v) for v in contract.get("non_renewal_notice_channels", [])])
    contract["equipment_count"] = len(contract.get("equipment_items", []))

    service["included_options_summary"] = _join([
        INCLUDED_OPTION_LABELS[value] for value in service.get("included_options", [])
    ])
    mode = service.get("refrigerant_handling_mode")
    service["refrigerant_handling_summary"] = {
        "IN_HOUSE_AUTHORIZED": "Manipulation réalisée en interne dans la limite des habilitations détenues.",
        "PARTNER": "Manipulation confiée à un partenaire habilité lorsque nécessaire.",
        "EXCLUDED": "Manipulation de fluide exclue du forfait.",
    }.get(mode, "")
    service["travel_summary"] = "déplacements inclus" if service.get("travel_included") else "déplacements facturés séparément"
    service["priority_breakdown_label"] = (f"intervention prioritaire sous {service.get('priority_breakdown_delay')}" if service.get("priority_breakdown") else "non incluse")

    annual_ht = Decimal(str(pricing["annual_ht"]))
    vat_rate = Decimal(str(pricing["vat_rate"]))
    vat_amount = (annual_ht * vat_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    annual_ttc = (annual_ht + vat_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pricing["vat_amount"] = vat_amount
    pricing["annual_ttc"] = annual_ttc
    for key in ("annual_ht", "vat_amount", "annual_ttc", "missed_appointment_fee"):
        pricing[key] = french_money(pricing.get(key))
    pricing["vat_rate"] = french_percent(pricing.get("vat_rate"))
    pricing["missed_appointment_fee_label"] = f"{pricing['missed_appointment_fee']} € TTC" if pricing.get("missed_appointment_fee") else ""
    pricing["payment_methods_label"] = _join([PAYMENT_LABELS.get(v, v) for v in pricing.get("payment_methods", [])])
    due = pricing.get("payment_due_days")
    pricing["payment_terms_label"] = pricing.get("payment_terms_custom_text") or (f"paiement sous {days_label(due)}" if due else "paiement à réception")
    pricing["renewal_price_rule_label"] = RENEWAL_PRICE_LABELS.get(pricing.get("renewal_price_rule"), pricing.get("renewal_price_rule", ""))

    if intervention:
        intervention["date"] = french_date(intervention.get("date"))
        if "quote_recommended" in intervention:
            intervention["quote_recommended"] = "Oui" if intervention["quote_recommended"] else "Non"

    items = []
    for item in contract.get("equipment_items", []):
        eq = deepcopy(item)
        eq["display_name"] = " - ".join(x for x in (eq.get("type"), eq.get("brand"), eq.get("model")) if x)
        eq["power_label"] = (f"{str(eq['power_kw']).replace('.', ',')} kW" if eq.get("power_kw") not in (None, "") else "")
        eq["install_date"] = french_date(eq.get("install_date"))
        items.append(eq)
    contract["equipment_items"] = sorted(items, key=lambda item: int(item["position"]))
    return ctx


def lookup(ctx: dict, key: str, equipment: dict | None = None) -> object:
    if key.startswith("equipment.") and equipment is not None:
        return equipment.get(key.split(".", 1)[1], "")
    value: object = ctx
    for part in key.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part, "")
    return value
