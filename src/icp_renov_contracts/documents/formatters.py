from __future__ import annotations

from copy import deepcopy
from datetime import date,datetime
from decimal import Decimal,ROUND_HALF_UP

MONTHS=("","janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre")
REGIMES={"CONSUMER":"Consommateur","NON_PROFESSIONAL":"Non-professionnel","PROFESSIONAL":"Professionnel"}
PAYMENTS={"BANK_TRANSFER":"virement bancaire","TRANSFER":"virement bancaire","CHEQUE":"chèque","DIRECT_DEBIT":"prélèvement"}
CHANNELS={"POSTAL_MAIL":"courrier","EMAIL":"courriel","ELECTRONIC":"voie électronique"}
OPTIONS={"DEEP_CLEANING":"nettoyage approfondi","DISINFECTION":"désinfection"}
def french_date(value):
    if not value:return ""
    item=value if isinstance(value,date) else datetime.strptime(value,"%Y-%m-%d").date();return f"{item.day} {MONTHS[item.month]} {item.year}"
def french_money(value):
    if value in (None,""):return ""
    amount=Decimal(str(value)).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    return f"{amount:,.2f}".replace(",","X").replace(".",",").replace("X","\u202f")
def join(values):return ", ".join(str(value) for value in values if value)
def lookup(context,key,equipment=None):
    if equipment is not None and key.startswith("equipment."):return equipment.get(key.split(".",1)[1],"")
    value=context
    for part in key.split("."):
        if not isinstance(value,dict):return ""
        value=value.get(part,"")
    return value
def prepare_context(raw:dict)->dict:
    ctx=deepcopy(raw);company=ctx.setdefault("company",{});client=ctx.setdefault("client",{});site=ctx.setdefault("site",{})
    contract=ctx.setdefault("contract",{});service=ctx.setdefault("service",{});pricing=ctx.setdefault("pricing",{});intervention=ctx.setdefault("intervention",{})
    company["display_name"]=company.get("trade_name") or company.get("legal_name","")
    company["registration_identifiers_summary"]=join((f"SIREN {company.get('siren')}" if company.get('siren') else "",f"SIRET {company.get('siret')}" if company.get('siret') else ""))
    company["full_address"]=join((company.get("address_line1"),company.get("address_line2"),join((company.get("postal_code"),company.get("city"))),company.get("country")))
    company["registered_address"]=company.get("registered_address") or company["full_address"]
    company["insurance_summary"]=join((company.get("insurer_name",""),f"police {company.get('insurance_policy_number')}" if company.get("insurance_policy_number") else "",company.get("insurance_scope","")))
    company["refrigerant_capacity_summary"]=join((company.get("refrigerant_capacity_number",""),company.get("refrigerant_capacity_body","")))
    client["contract_name"]=(join((client.get("first_name"),client.get("last_name"))) if client.get("party_type")=="PERSON" else client.get("organization_name",""))
    client["representative_summary"]=join((client.get("representative_name"),client.get("representative_role")));client["regime_label"]=REGIMES.get(client.get("regime"),"")
    site["full_address"]=join((site.get("address_line1"),site.get("address_line2"),join((site.get("postal_code"),site.get("city"))),site.get("country")))
    for key in ("issue_date","start_date","initial_end_date"):contract[key]=french_date(contract.get(key))
    contract["signature_city"]=(str(contract.get("signature_city") or "").strip() or "____________________")
    contract["renewal_period_label"]=f"{contract.get('renewal_period_months')} mois" if contract.get("renewal_period_months") else ""
    days=contract.get("non_renewal_notice_days");contract["non_renewal_notice_label"]=f"{days} jour"+("s" if days!=1 else "") if days is not None else ""
    contract["non_renewal_notice_channels_label"]=join(CHANNELS.get(value,value) for value in contract.get("non_renewal_notice_channels",()))
    contract["equipment_count"]=len(contract.get("equipment_items",()))
    service["included_options_summary"]=join(OPTIONS.get(value,value) for value in service.get("included_options",()))
    service["travel_summary"]="déplacements inclus" if service.get("travel_included") else "déplacements facturés séparément"
    service["priority_breakdown_label"]=(f"intervention prioritaire sous {service.get('priority_breakdown_delay')}" if service.get("priority_breakdown") else "non incluse")
    service["refrigerant_handling_summary"]={"IN_HOUSE_AUTHORIZED":"Manipulation réalisée en interne dans la limite des habilitations détenues.","PARTNER":"Manipulation confiée à un partenaire habilité lorsque nécessaire.","EXCLUDED":"Manipulation de fluide exclue du forfait."}.get(service.get("refrigerant_handling_mode"),"")
    ht=Decimal(str(pricing.get("annual_ht",0)));rate=Decimal(str(pricing.get("vat_rate",0)));vat=(ht*rate).quantize(Decimal("0.01"));ttc=ht+vat
    pricing["annual_ht"]=french_money(ht);pricing["vat_amount"]=french_money(vat);pricing["annual_ttc"]=french_money(ttc)
    pricing["vat_rate"]=(f"{rate*100:f}".rstrip("0").rstrip(".").replace(".",","));pricing["missed_appointment_fee"]=french_money(pricing.get("missed_appointment_fee"))
    pricing["missed_appointment_fee_label"]=(f"{pricing['missed_appointment_fee']} € TTC" if pricing["missed_appointment_fee"] else "")
    pricing["payment_methods_label"]=join(PAYMENTS.get(value,value) for value in pricing.get("payment_methods",()))
    due=pricing.get("payment_due_days");pricing["payment_terms_label"]=pricing.get("payment_terms_custom_text") or (f"paiement sous {due} jours" if due else "paiement à réception")
    pricing["renewal_price_rule_label"]={"FIXED":"prix inchangé","NEW_PRICE_ON_RENEWAL":"nouveau prix communiqué au renouvellement"}.get(pricing.get("renewal_price_rule"),"")
    items=[]
    for item in contract.get("equipment_items",()):
        item=deepcopy(item);item["display_name"]=" - ".join(value for value in (item.get("type"),item.get("brand"),item.get("model")) if value)
        item["power_label"]=(f"{str(item['power_kw']).replace('.',',')} kW" if item.get("power_kw") not in (None,"") else "");item["install_date"]=french_date(item.get("install_date"));items.append(item)
    contract["equipment_items"]=sorted(items,key=lambda value:int(value["position"]))
    intervention["date"]=french_date(intervention.get("date"))
    quote=intervention.get("quote_recommended")
    intervention["quote_recommended"]="Oui" if quote is True else "Non" if quote is False else ""
    return ctx
