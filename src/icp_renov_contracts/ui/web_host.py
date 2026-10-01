from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..documents.validation import DocumentGenerationError
from ..runtime_resources import web_ui_root
from ..domain import (
    ClientDraft, ClientMaster, ContractConditions, ContractEventType, ContractStatus, EquipmentDraft, EquipmentMaster,
    DocumentKind, SignedCopyState, SiteDraft, SiteMaster, TemplateVersionStatus,
)
from ..errors import ApplicationError, ContractConditionsValidationError, ContractLifecycleError, MasterDataValidationError
from ..services import (
    ContractOperationalSignalKind,
    ContractRegisterFilter,
    ContractRegisterService,
    InterventionInput,
    RealBackupSummaryProvider,
)
from ..services.contract_register import STATUS_LABELS
from ..services.signed_copy_replacement import decode_signed_pdf_replacement


CLIENT_DTO_FIELDS = frozenset({
    "party_type", "first_name", "last_name", "organization_name", "legal_form", "siret",
    "address_line1", "address_line2", "postal_code", "city", "country", "billing_address",
    "phone", "email", "internal_reference", "internal_notes",
})
SITE_DTO_FIELDS = frozenset({
    "label", "address_line1", "address_line2", "postal_code", "city", "country",
    "contact_name", "contact_phone", "internal_notes",
})
EQUIPMENT_DTO_FIELDS = frozenset({
    "equipment_type", "brand", "model", "serial_number", "power_kw", "location",
    "installation_date", "internal_reference", "internal_notes",
})
CONTRACT_FRAMEWORK_DTO_FIELDS = frozenset({
    "regime", "conclusion_mode", "early_performance_requested",
})
CONTRACT_SERVICE_DTO_FIELDS = frozenset({
    "visits_per_year", "refrigerant_handling_mode", "included_options",
    "priority_breakdown", "priority_breakdown_delay",
})
CONTRACT_PERIOD_DTO_FIELDS = frozenset({
    "issue_date", "start_date", "initial_duration_mode", "initial_duration_months",
    "initial_end_date", "signature_city",
})
CONTRACT_INTERVENTION_DTO_FIELDS = frozenset({
    "included_area", "business_hours", "travel_included",
    "missed_appointment_fee", "additional_exclusions",
})
CONTRACT_PRICING_DTO_FIELDS = frozenset({
    "annual_ht", "vat_rate", "payment_terms_code", "payment_due_days",
    "payment_terms_custom_text", "payment_methods",
})
CONTRACT_RENEWAL_DTO_FIELDS = frozenset({
    "renewal_mode", "renewal_period_months", "non_renewal_notice_days",
    "non_renewal_notice_channels", "internal_alert_days", "renewal_price_rule",
})
CONTRACT_EARLY_TERMINATION_DTO_FIELDS = frozenset({
    "early_termination_reason_codes", "early_termination_custom_text", "breach_cure_period_days",
})
CONTRACT_SPECIAL_TERMS_DTO_FIELDS = frozenset({"special_terms"})


def _client_editor_payload(client: ClientMaster) -> dict[str, str]:
    return {field: getattr(client, field) for field in CLIENT_DTO_FIELDS}


def _client_draft(payload: object, current: ClientMaster | None = None) -> ClientDraft:
    if not isinstance(payload, dict):
        raise MasterDataValidationError({"payload": "Les informations du client sont invalides."})
    unsupported = set(payload) - CLIENT_DTO_FIELDS
    if unsupported:
        raise MasterDataValidationError({
            "payload": "Certaines informations ne sont pas autorisées dans la fiche client."
        })
    invalid = [field for field, value in payload.items() if not isinstance(value, str)]
    if invalid:
        raise MasterDataValidationError({
            invalid[0]: "Cette valeur doit être du texte."
        })
    values = {field: payload.get(field, "") for field in CLIENT_DTO_FIELDS}
    values["country"] = values["country"] or "France"
    return ClientDraft(
        **values,
        proposed_contact_name=current.proposed_contact_name if current else "",
        proposed_contact_role=current.proposed_contact_role if current else "",
    )


def _site_draft(payload: object) -> SiteDraft:
    values = _text_dto(payload, SITE_DTO_FIELDS, "site")
    values["country"] = values["country"] or "France"
    return SiteDraft(**values)


def _site_editor_payload(site: SiteMaster) -> dict[str, str]:
    return {field: getattr(site, field) for field in SITE_DTO_FIELDS}


def _equipment_editor_payload(equipment: EquipmentMaster) -> dict[str, str]:
    return {
        field: "" if getattr(equipment, field) is None else str(getattr(equipment, field))
        for field in EQUIPMENT_DTO_FIELDS
    }


def _equipment_draft(payload: object) -> EquipmentDraft:
    values = _text_dto(payload, EQUIPMENT_DTO_FIELDS, "équipement")
    values["power_kw"] = values["power_kw"] or None
    return EquipmentDraft(**values)


def _text_dto(payload: object, allowed: frozenset[str], label: str) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise MasterDataValidationError({"payload": f"Les informations du {label} sont invalides."})
    unsupported = set(payload) - allowed
    if unsupported:
        raise MasterDataValidationError({
            "payload": f"Certaines informations ne sont pas autorisées pour ce {label}."
        })
    invalid = [field for field, value in payload.items() if not isinstance(value, str)]
    if invalid:
        raise MasterDataValidationError({invalid[0]: "Cette valeur doit être du texte."})
    return {field: payload.get(field, "") for field in allowed}


def _master_error(error: ApplicationError) -> dict:
    fields = getattr(error, "field_errors", {})
    return {"ok": False, "message": error.user_message, "field_errors": fields}


def _closed_contract_dto(payload: object, allowed: frozenset[str]) -> dict:
    if not isinstance(payload, dict):
        raise ContractConditionsValidationError({"payload": "Les conditions transmises sont invalides."})
    unsupported = set(payload) - allowed
    if unsupported:
        raise ContractConditionsValidationError({"payload": "Certaines conditions ne sont pas autorisées."})
    return payload


def _date_fr(value: str | None) -> str:
    if not value:
        return ""
    return date.fromisoformat(value).strftime("%d/%m/%Y")


def _datetime_fr(value: str | None) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")


def _money_fr(value) -> str:
    return format(value, ".2f").replace(".", ",")


class UiBridge(QObject):
    """The deliberately small, presentation-only WebChannel contract."""

    stateChanged = Signal("QVariant")

    def __init__(self, context, navigate: Callable[[str], None]) -> None:
        super().__init__()
        self.context = context
        self.navigate_legacy = navigate
        self.filter = ContractRegisterFilter.ALL
        self.search = ""
        self.page_name = "CONTRACTS"
        self.client_search = ""
        self.client_archived = False
        self.selected_client_id: str | None = None
        self.show_archived_master_data = False
        self.contract_id: str | None = None
        self.contract_step = 1
        self.contract_focus_target: str | None = None
        self.contract_generation_feedback: dict | None = None
        self.contract_documents_feedback: dict | None = None
        self.contract_signed_pdf_selection: tuple[str, str, Path] | None = None
        self.register = ContractRegisterService(
            context.contracts, context.review, context.lifecycle, context.workspace_service,
            RealBackupSummaryProvider(context.backup, context.alerts), context.alerts,
        )

    def snapshot(self) -> dict:
        if self.page_name == "CONTRACT_WORKSPACE":
            return self.contract_workspace_snapshot()
        if self.page_name == "CLIENTS":
            return self.clients_snapshot()
        rows = self.register.rows()
        visible = self.register.filter_rows(rows, self.filter, self.search)
        summary = self.register.backup_summary()
        return {
            "action_count": self.register.action_count(rows),
            "search": self.search,
            "backup": summary.label,
            "filters": [
                {"id": selected.value, "label": label, "active": selected is self.filter}
                for selected, label in (
                    (ContractRegisterFilter.ALL, "Tous"),
                    (ContractRegisterFilter.ACTIONS, "Actions à traiter"),
                    (ContractRegisterFilter.DRAFTS, "Brouillons"),
                    (ContractRegisterFilter.ACTIVE, "Actifs"),
                    (ContractRegisterFilter.TERMINAL, "Terminés"),
                )
            ],
            "rows": [
                {
                    "id": row.contract_id,
                    "number": row.number_label,
                    "updated": "",
                    "client": row.client_name,
                    "site": row.site_label,
                    "status": row.status_label,
                    "status_code": row.status.value,
                    "deadline": row.due_date.strftime("%d/%m/%Y") if row.due_date else "—",
                    "signal": row.signal.label,
                    "needs_action": row.signal.kind is ContractOperationalSignalKind.ACTION,
                    "document": row.document_lines[0] if row.document_lines else "Aucun document",
                }
                for row in visible
            ],
        }

    @staticmethod
    def _address(value) -> str:
        return ", ".join(part for part in (
            value.address_line1, value.address_line2,
            " ".join(part for part in (value.postal_code, value.city) if part), value.country,
        ) if part)

    def contract_workspace_snapshot(self) -> dict:
        contract = self.context.contracts.get(self.contract_id)
        editable = contract.status is ContractStatus.DRAFT
        selected_by_source = {item.source_equipment_id: item for item in contract.equipment_items}
        available_equipment = self.context.contracts.selectable_equipment(contract.id) if editable else []
        available_by_id = {item.id: item for item in available_equipment}
        equipment = []
        for item in contract.equipment_items:
            equipment.append({
                "id": item.source_equipment_id, "item_id": item.id, "selected": True,
                "position": item.position, "name": item.snapshot.display_name or item.snapshot.equipment_type,
                "location": item.snapshot.location, "observation": item.observation,
                "available": item.source_equipment_id in available_by_id,
            })
        for master in available_equipment:
            if master.id not in selected_by_source:
                equipment.append({
                    "id": master.id, "item_id": None, "selected": False, "position": None,
                    "name": master.display_name or master.equipment_type, "location": master.location,
                    "observation": "", "available": True,
                })
        conditions = self.context.contracts.get_conditions(contract.id)
        version = self.context.contracts.selected_template_version(contract.id) if contract.template_version_id else None
        compatible_versions = self.context.contracts.compatible_template_versions(contract.id)
        regime_code = contract.regime.value if contract.regime else None
        selected_compatible = bool(
            version and version.document_kind == "CONTRACT"
            and version.status is TemplateVersionStatus.AVAILABLE
            and version.contract_type_code == contract.type_code.value
            and regime_code in version.allowed_client_regimes
        )
        conclusion_required = bool(
            selected_compatible and version.validation.requires_conclusion(regime_code)
        )
        conclusion_labels = {
            "IN_PREMISES": "Dans les locaux du professionnel",
            "OFF_PREMISES": "Hors établissement",
            "DISTANCE_EMAIL": "À distance par e-mail",
            "ONLINE_INTERFACE": "Interface en ligne",
            "OTHER_DISTANCE": "Autre conclusion à distance",
        }
        authorized_conclusions = tuple(dict.fromkeys(
            item.conclusion_mode for item in version.validation.context_authorizations
            if conclusion_required and item.regime == regime_code and item.conclusion_mode in conclusion_labels
        )) if version else ()
        early_performance_visible = bool(
            conclusion_required and {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE"}.issubset(
                version.validation.blocks_for(regime_code, conditions.conclusion_mode)
            )
        )
        catalogs = version.catalogs if selected_compatible else None
        resolved_end_date = conditions.resolved_end_date
        vat_amount = conditions.vat_amount
        annual_ttc = conditions.annual_ttc
        regime = {
            "CONSUMER": "Consommateur", "NON_PROFESSIONAL": "Non-professionnel",
            "PROFESSIONAL": "Professionnel",
        }.get(contract.regime.value if contract.regime else None, "Non configuré")
        conclusion = {
            "IN_PREMISES": "Dans les locaux", "OFF_PREMISES": "Hors établissement",
            "DISTANCE_EMAIL": "À distance — e-mail", "ONLINE_INTERFACE": "Interface en ligne",
            "OTHER_DISTANCE": "Autre vente à distance",
        }.get(conditions.conclusion_mode, "Non configuré")
        renewal = {
            "NONE": "Aucun", "MANUAL": "Renouvellement manuel", "TACIT": "Reconduction tacite",
        }.get(conditions.renewal_mode, "Non configuré")
        renewal_price = {
            "FIXED": "Même prix",
            "NEW_PRICE_ON_RENEWAL": "Nouveau prix défini au renouvellement",
        }.get(conditions.renewal_price_rule, "")
        complete_step_one = bool(
            contract.client_snapshot and contract.signatory_name.strip() and contract.signatory_role.strip()
            and contract.site_snapshot and contract.equipment_items
        )
        review = self.context.review.review(contract.id)
        generation_allowed = bool(
            contract.status is ContractStatus.DRAFT
            and review.generation_available
            and self.context.generation.available(contract.id)
        )
        preview_number = self.context.generation.preview_number(contract.id) if generation_allowed else None
        next_revision = self.context.generation.next_revision(contract.id) if generation_allowed else None
        backup = self.register.backup_summary().label
        revisions = self.context.lifecycle.revisions(contract.id)
        intervention_sheets = self.context.lifecycle.documents.list_for_contract_kind(
            contract.id, DocumentKind.INTERVENTION_SHEET
        )
        revision_by_document = {item.id: item.revision for item in revisions}
        signature = self.context.lifecycle.signature_authority(contract.id)
        lifecycle_projection = None
        try:
            if contract.status in {ContractStatus.SIGNED, ContractStatus.ACTIVE}:
                lifecycle_projection = self.context.lifecycle.lifecycle_projection(contract.id)
        except ContractLifecycleError:
            lifecycle_projection = None
        signed_document_id = signature.document.id if signature else None
        revision_rows = []
        for index,item in enumerate(revisions):
            template = self.context.template_catalog.get_version(item.template_version_id)
            sent = self.context.lifecycle.latest_send(item.id)
            signed = item.id == signed_document_id
            signed_copy_state = self.context.lifecycle.signed_copy_state(item) if signed else None
            start_date = item.snapshot.get("contract", {}).get("start_date")
            revision_rows.append({
                "revision": item.revision,
                "generated_display": _date_fr(item.generated_at_utc[:10]),
                "start_display": _date_fr(start_date) if isinstance(start_date, str) and start_date else "",
                "template_name": template.template_name,
                "template_version": template.version,
                "latest": index == 0,
                "replaced": index > 0,
                "sent_display": _date_fr(sent.effective_date) if sent else "",
                "signed": signed,
                "signed_display": _date_fr(signature.event.effective_date) if signed else "",
                "signed_copy_state": signed_copy_state.value if signed_copy_state else "",
                "signed_copy_attached_display": (
                    _date_fr(item.signed_pdf_attached_at[:10])
                    if signed and item.signed_pdf_attached_at else ""
                ),
                "signed_pdf_available": signed_copy_state is SignedCopyState.VALID,
                "docx_available": self.context.lifecycle.resolve_document_path(item.docx_relpath) is not None,
                "pdf_available": self.context.lifecycle.resolve_document_path(item.pdf_relpath) is not None,
            })
        intervention_rows = []
        for item in intervention_sheets:
            snapshot = item.snapshot
            intervention = snapshot.get("intervention", {})
            try:
                template = self.context.template_catalog.get_version(item.template_version_id)
                template_name, template_version = template.template_name, template.version
            except Exception:
                template_name, template_version = "Modèle historique", ""
            intervention_rows.append({
                "id": item.id,
                "intervention_date_display": _date_fr(intervention.get("date")),
                "technician": intervention.get("technician") or "",
                "generated_display": _datetime_fr(item.generated_at_utc),
                "template_name": template_name,
                "template_version": template_version,
                "docx_available": self.context.lifecycle.resolve_document_path(item.docx_relpath) is not None,
                "pdf_available": self.context.lifecycle.resolve_document_path(item.pdf_relpath) is not None,
            })
        event_labels = {
            ContractEventType.CREATED: "Contrat créé",
            ContractEventType.DOCUMENT_GENERATED: "Révision contractuelle générée",
            ContractEventType.REOPENED_FOR_CORRECTION: "Contrat rouvert pour correction",
            ContractEventType.CONTRACT_SENT: "Envoi de la révision enregistré",
            ContractEventType.SIGNATURE_RECORDED: "Signature enregistrée",
            ContractEventType.ACTIVATED: "Contrat activé",
            ContractEventType.RENEWAL_CONFIRMED: "Reconduction confirmée",
            ContractEventType.RENEWAL_NOTICE_RECORDED: "Non-renouvellement enregistré",
            ContractEventType.TERMINATION_SCHEDULED: "Résiliation programmée",
            ContractEventType.TERMINATED: "Contrat résilié",
            ContractEventType.EXPIRED: "Contrat expiré",
            ContractEventType.ABANDONED: "Contrat abandonné",
        }
        history = self.context.lifecycle.history(contract.id)
        replacement_audits = [
            (event, audit) for event in history
            if event.type is ContractEventType.ADMIN_CORRECTION and event.reason_code == "SIGNED_PDF_REPLACED"
            for audit in (decode_signed_pdf_replacement(event.note),) if audit is not None
        ]
        replacement_attachments = {(event.document_id, audit.replacement_attached_at) for event, audit in replacement_audits}
        timeline_entries = []
        for event in history:
            if event.type is ContractEventType.ADMIN_CORRECTION and event.reason_code == "SIGNED_PDF_REPLACED":
                audit = decode_signed_pdf_replacement(event.note)
                if audit is not None:
                    timeline_entries.append((event.occurred_at, event.id, {
                        "label": "PDF signé remplacé", "occurred_display": _datetime_fr(event.occurred_at),
                        "effective_display": "", "revision": revision_by_document.get(event.document_id or "", ""),
                        "note": f"La copie précédente, archivée le {_datetime_fr(audit.previous_attached_at)}, est conservée.",
                        "documentary": True,
                    }))
                continue
            if event.type not in event_labels:continue
            revision = revision_by_document.get(event.document_id or "", "")
            label = event_labels[event.type]
            if event.type is ContractEventType.DOCUMENT_GENERATED and event.document_id:
                document = self.context.lifecycle.documents.get(event.document_id)
                if document is not None and document.document_kind is DocumentKind.INTERVENTION_SHEET:
                    label = "Fiche d’intervention générée"
                    revision = ""
            if event.type is ContractEventType.RENEWAL_CONFIRMED:
                period = f"Du {_date_fr(event.period_start)} au {_date_fr(event.period_end)}"
                price = " · ".join(value for value in (
                    f"{event.renewal_annual_ht} € HT" if event.renewal_annual_ht else "",
                    f"TVA {event.renewal_vat_rate} %" if event.renewal_vat_rate else "",
                    f"{event.renewal_annual_ttc} € TTC" if event.renewal_annual_ttc else "",
                ) if value)
                timeline_entries.append((event.occurred_at, event.id, {
                    "label": event_labels[event.type], "occurred_display": _date_fr(event.occurred_at[:10]),
                    "effective_display": period, "revision": "", "note": price,
                }))
                continue
            detail = event.note or ""
            if event.type is ContractEventType.TERMINATION_SCHEDULED and event.reason_text:
                detail = " · ".join(value for value in (f"Motif : {event.reason_text}", detail) if value)
            timeline_entries.append((event.occurred_at, event.id, {
                "label": label,
                "occurred_display": _date_fr(event.occurred_at[:10]),
                "effective_display": _date_fr(event.effective_date) if event.effective_date else "",
                "revision": revision,
                "note": detail,
            }))
        if signature and signature.document.signed_pdf_attached_at and (signature.document.id, signature.document.signed_pdf_attached_at) not in replacement_attachments:
            timeline_entries.append((signature.document.signed_pdf_attached_at, signature.document.id, {
                "label": "Copie signée archivée",
                "occurred_display": _datetime_fr(signature.document.signed_pdf_attached_at),
                "effective_display": "",
                "revision": signature.document.revision,
                "note": "",
                "documentary": True,
            }))
        elif signature and replacement_audits:
            initial = min((audit for event, audit in replacement_audits if event.document_id == signature.document.id), key=lambda audit: audit.previous_attached_at, default=None)
            if initial is not None:
                timeline_entries.append((initial.previous_attached_at, f"initial-{signature.document.id}", {
                    "label": "Copie signée archivée", "occurred_display": _datetime_fr(initial.previous_attached_at),
                    "effective_display": "", "revision": signature.document.revision, "note": "", "documentary": True,
                }))
        timeline = [entry for _, _, entry in sorted(timeline_entries, key=lambda item: (item[0], item[1]), reverse=True)]
        feedback = self.contract_documents_feedback
        if (
            signature
            and self.context.lifecycle.signed_copy_state(signature.document) in {SignedCopyState.MISSING, SignedCopyState.HASH_MISMATCH}
            and feedback == {"kind": "success", "message": "Le PDF signé a été archivé."}
        ):
            feedback = None
        renewal_d3 = {
            "mode": conditions.renewal_mode or "",
            "linked_draft_allowed": bool(
                contract.status is ContractStatus.ACTIVE and conditions.renewal_mode in {"NONE", "MANUAL"}
            ),
            "linked_draft_label": (
                "Préparer le renouvellement" if conditions.renewal_mode == "MANUAL"
                else "Créer un nouveau contrat lié"
            ),
            "tacit": None,
        }
        lifecycle_d4 = {"nonrenewal": None, "termination": None, "abandon_allowed": contract.status in {ContractStatus.DRAFT, ContractStatus.TO_SIGN}}
        if lifecycle_projection:
            notice_allowed = bool(contract.status is ContractStatus.ACTIVE and lifecycle_projection.renewal_mode == "TACIT" and not lifecycle_projection.non_renewal_event and not lifecycle_projection.pending_termination)
            lifecycle_d4["nonrenewal"] = {
                "allowed": notice_allowed,
                "recorded": lifecycle_projection.non_renewal_event is not None,
                "current_period_display": f"Du {_date_fr(lifecycle_projection.period.start.isoformat())} au {_date_fr(lifecycle_projection.period.end.isoformat())}",
                "period_end_display": _date_fr(lifecycle_projection.period.end.isoformat()),
                "notice_days": lifecycle_projection.non_renewal_deadline and (lifecycle_projection.period.end - lifecycle_projection.non_renewal_deadline).days,
                "channels": [
                    dict(self.context.lifecycle.non_renewal_channel_options(contract.id)).get(code, "Canal configuré")
                    for code in lifecycle_projection.notice_channels
                ],
                "today": self.context.lifecycle.date_provider.today().isoformat(),
            }
            choices = self.context.lifecycle.termination_reason_options(contract.id)
            lifecycle_d4["termination"] = {
                "allowed": bool(contract.status in {ContractStatus.SIGNED, ContractStatus.ACTIVE} and not lifecycle_projection.pending_termination and choices),
                "scheduled": lifecycle_projection.pending_termination is not None,
                "scheduled_effective_display": (
                    _date_fr(lifecycle_projection.pending_termination.effective_date)
                    if lifecycle_projection.pending_termination and lifecycle_projection.pending_termination.effective_date else ""
                ),
                "today": self.context.lifecycle.date_provider.today().isoformat(),
                "reasons": [{"id": code, "label": label} for code, label in choices],
            }
        if lifecycle_projection and conditions.renewal_mode == "TACIT":
            try:
                preview = self.context.lifecycle.renewal_preview(contract.id)
            except ContractLifecycleError:
                preview = None
            if preview:
                renewal_d3["tacit"] = {
                    "confirmation_allowed": self.context.lifecycle.renewal_attention_due(contract.id),
                    "current_period_display": f"Du {_date_fr(preview.current_period.start.isoformat())} au {_date_fr(preview.current_period.end.isoformat())}",
                    "next_period_display": f"Du {_date_fr(preview.next_period.start.isoformat())} au {_date_fr(preview.next_period.end.isoformat())}",
                    "renewal_months": preview.renewal_months,
                    "price_rule": preview.price_rule,
                    "price_rule_label": (
                        "Même prix" if preview.price_rule == "FIXED" else "Nouveau prix défini au renouvellement"
                    ),
                    "current_annual_ht": _money_fr(preview.current_price.annual_ht),
                    "current_vat_rate": _money_fr(preview.current_price.vat_rate),
                    "current_vat_amount": _money_fr(preview.current_price.vat_amount),
                    "current_annual_ttc": _money_fr(preview.current_price.annual_ttc),
                    "vat_rates": list(preview.vat_rates),
                }
        initial_period = (
            f"Du {_date_fr(conditions.start_date)} au {_date_fr(resolved_end_date)}"
            if conditions.start_date and resolved_end_date else "Non configurée"
        )
        renewal_events = [event for event in history if event.type is ContractEventType.RENEWAL_CONFIRMED]
        current_period = (
            f"En cours · Du {_date_fr(lifecycle_projection.period.start.isoformat())} au {_date_fr(lifecycle_projection.period.end.isoformat())}"
            if lifecycle_projection and renewal_events else initial_period
        )
        return {
            "page": "CONTRACT_WORKSPACE", "backup": backup, "active_step": self.contract_step,
            "contract_focus_target": self.contract_focus_target,
            "contract": {
                "id": contract.id, "status": contract.status.value,
                "status_label": STATUS_LABELS[contract.status],
                "number": contract.number or "Brouillon sans numéro", "editable": editable,
                "client_id": contract.client_source_id,
                "client": contract.client_snapshot.display_name if contract.client_snapshot else "",
                "client_address": ({
                    field: getattr(contract.client_snapshot, field) for field in
                    ("address_line1", "address_line2", "postal_code", "city", "country")
                } if contract.client_snapshot else {}),
                "site_id": contract.site_source_id,
                "site": contract.site_snapshot.label if contract.site_snapshot else "",
                "signatory_name": contract.signatory_name, "signatory_role": contract.signatory_role,
                "saved_label": "Enregistré", "document_folder_available": False,
            },
            "clients": [{
                "id": row.client.id, "name": row.client.display_name,
                "type": "Personne" if row.client.party_type == "PERSON" else "Organisation",
                "secondary": row.client.email or row.client.phone or self._address(row.client),
            } for row in self.context.contracts.selectable_clients()],
            "sites": [{
                "id": site.id, "name": site.label, "address": self._address(site),
            } for site in (self.context.contracts.selectable_sites(contract.id) if editable else [])],
            "equipment": equipment,
            "conditions_b1": {
                "regime": regime_code,
                "regime_options": [
                    {"id": "CONSUMER", "label": "Consommateur"},
                    {"id": "NON_PROFESSIONAL", "label": "Non-professionnel"},
                    {"id": "PROFESSIONAL", "label": "Professionnel"},
                ],
                "templates": [{
                    "id": item.id, "template_id": item.template_id,
                    "name": item.template_name, "version": item.version,
                    "display": item.display_name,
                } for item in compatible_versions],
                "selected_template_id": contract.template_id,
                "selected_template_version_id": contract.template_version_id,
                "selected_template_display": version.display_name if version else "",
                "selected_template_compatible": selected_compatible,
                "model_state": (
                    "REGIME_REQUIRED" if regime_code is None else
                    "NO_MODEL" if not compatible_versions else "AVAILABLE"
                ),
                "conclusion_required": conclusion_required,
                "conclusion_mode": conditions.conclusion_mode,
                "conclusion_options": [
                    {"id": code, "label": conclusion_labels[code]} for code in authorized_conclusions
                ],
                "early_performance_visible": early_performance_visible,
                "early_performance_requested": conditions.early_performance_requested,
                "visits_per_year": conditions.visits_per_year,
                "refrigerant_handling_mode": conditions.refrigerant_handling_mode,
                "included_options": list(conditions.included_options),
                "priority_breakdown": conditions.priority_breakdown,
                "priority_breakdown_delay": conditions.priority_breakdown_delay or "",
            },
            "conditions_b2": {
                "issue_date": conditions.issue_date,
                "start_date": conditions.start_date,
                "initial_duration_mode": conditions.initial_duration_mode,
                "initial_duration_months": conditions.initial_duration_months,
                "initial_end_date": conditions.initial_end_date,
                "resolved_end_date": resolved_end_date,
                "signature_city": conditions.signature_city,
                "included_area": conditions.included_area,
                "business_hours": conditions.business_hours,
                "travel_included": conditions.travel_included,
                "missed_appointment_fee": (
                    _money_fr(Decimal(conditions.missed_appointment_fee))
                    if conditions.missed_appointment_fee is not None else None
                ),
                "additional_exclusions": conditions.additional_exclusions,
                "annual_ht": (
                    _money_fr(Decimal(conditions.annual_ht))
                    if conditions.annual_ht is not None else None
                ),
                "vat_rate": conditions.vat_rate,
                "vat_amount": _money_fr(vat_amount) if vat_amount is not None else "",
                "annual_ttc": _money_fr(annual_ttc) if annual_ttc is not None else "",
                "vat_rates": list(catalogs.vat_rates) if catalogs else [],
                "payment_terms_code": conditions.payment_terms_code,
                "payment_due_days": conditions.payment_due_days,
                "payment_terms_custom_text": conditions.payment_terms_custom_text,
                "payment_terms": [{
                    "id": item.code, "label": item.label,
                    "requires_day_count": item.requires_day_count,
                    "allows_custom_text": item.allows_custom_text,
                } for item in (catalogs.payment_terms if catalogs else ())],
                "payment_methods": list(conditions.payment_methods),
                "payment_method_options": [
                    {"id": item.code, "label": item.label}
                    for item in (catalogs.payment_methods if catalogs else ())
                ],
                "pricing_catalog_available": catalogs is not None,
            },
            "conditions_b3": {
                "renewal_mode": conditions.renewal_mode,
                "renewal_modes": [
                    {"id": "NONE", "label": "Aucun"},
                    {"id": "MANUAL", "label": "Renouvellement manuel"},
                    {"id": "TACIT", "label": "Reconduction tacite"},
                ],
                "renewal_period_months": conditions.renewal_period_months,
                "non_renewal_notice_days": conditions.non_renewal_notice_days,
                "non_renewal_notice_channels": list(conditions.non_renewal_notice_channels),
                "requires_non_renewal_notice_days": bool(
                    selected_compatible and version.validation.requires_non_renewal_notice_days
                ),
                "requires_non_renewal_notice_channels": bool(
                    selected_compatible and version.validation.requires_non_renewal_notice_channels
                ),
                "non_renewal_channel_options": [
                    {"id": item.code, "label": item.label}
                    for item in (catalogs.non_renewal_channels if catalogs else ())
                ],
                "internal_alert_days": conditions.internal_alert_days,
                "renewal_price_rule": conditions.renewal_price_rule,
                "renewal_price_rules": [
                    {"id": "FIXED", "label": "Même prix"},
                    {"id": "NEW_PRICE_ON_RENEWAL", "label": "Nouveau prix défini au renouvellement"},
                ],
                "early_termination_reason_codes": list(conditions.early_termination_reason_codes),
                "early_termination_custom_text": conditions.early_termination_custom_text,
                "early_termination_reason_options": [
                    {"id": item.code, "label": item.label}
                    for item in (catalogs.early_termination_reasons if catalogs else ())
                ],
                "breach_cure_period_required": bool(
                    selected_compatible and version.validation.requires_breach_cure_period_days
                ),
                "breach_cure_period_days": conditions.breach_cure_period_days,
                "special_terms": conditions.special_terms,
                "catalog_available": catalogs is not None,
            },
            "review": {
                "data_complete": review.data_complete,
                "generation_available": review.generation_available,
                "blocks": [{
                    "id": block.id.value,
                    "title": block.title,
                    "state": block.state.value,
                    "summary": block.summary,
                    "issues": [issue.message for issue in block.issues],
                    "target_step": block.target_step + 1,
                } for block in review.blocks],
                "generation_checks": [{
                    "key": check.key,
                    "label": check.label,
                    "available": check.available,
                    "detail": check.detail,
                } for check in review.generation.checks],
            },
            "official_generation": {
                "allowed": generation_allowed,
                "preview_number": preview_number,
                "next_revision": next_revision,
                "feedback": self.contract_generation_feedback,
            },
            "documents_d1": {
                "available": bool(revisions),
                "revisions": revision_rows,
                "timeline": timeline,
                "send_allowed": contract.status is ContractStatus.TO_SIGN,
                "correction_allowed": contract.status is ContractStatus.TO_SIGN,
                "today": self.context.lifecycle.date_provider.today().isoformat(),
                "feedback": feedback,
            },
            "documents_d2": {
                "signature_allowed": contract.status is ContractStatus.TO_SIGN and signature is None,
                "signature": None if signature is None else {
                    "revision": signature.document.revision,
                    "date_display": _date_fr(signature.event.effective_date),
                    "start_display": _date_fr(signature.start_date.isoformat()),
                    "copy_state": self.context.lifecycle.signed_copy_state(signature.document).value,
                },
            },
            "documents_d3": renewal_d3,
            "documents_d4": lifecycle_d4,
            "documents_d5": {
                "action_available": self.context.intervention_generation is not None,
                "templates": [{
                    "id": item.id, "name": item.template_name, "version": item.version,
                    "display": item.display_name,
                    "technician_required": "intervention.technician" in item.required_intervention_fields,
                } for item in (
                    self.context.intervention_generation.available_templates()
                    if self.context.intervention_generation is not None else []
                )],
                "documents": intervention_rows,
                "today": self.context.lifecycle.date_provider.today().isoformat(),
            },
            "summary": {
                "client": contract.client_snapshot.display_name if contract.client_snapshot else "Non sélectionné",
                "signatory": " · ".join(part for part in (contract.signatory_name, contract.signatory_role) if part) or "Non renseigné",
                "site": contract.site_snapshot.label if contract.site_snapshot else "Non sélectionné",
                "equipment": [item.snapshot.display_name or item.snapshot.equipment_type for item in contract.equipment_items],
                "regime": regime, "conclusion": conclusion,
                "period": current_period,
                "price": (
                    f"{_money_fr(annual_ttc)} € TTC / an" if annual_ttc is not None else "Non configuré"
                ),
                "renewal": " · ".join(value for value in (renewal, renewal_price) if value),
                "template": version.display_name if version else "Non configuré",
                "completion": "Revue complète" if review.data_complete else "Revue à corriger",
            },
        }

    def clients_snapshot(self) -> dict:
        clients = self.context.master_data.list_clients(self.client_search, self.client_archived)
        if self.selected_client_id is None or not any(item.client.id == self.selected_client_id for item in clients):
            self.selected_client_id = clients[0].client.id if clients else None
        selected = next((item.client for item in clients if item.client.id == self.selected_client_id), None)
        sites = [] if selected is None else self.context.master_data.list_sites(selected.id)
        all_contracts = [
            self.context.contracts.get(item.id)
            for item in self.context.contracts.list_drafts()
        ]
        linked = [] if selected is None else [
            contract for contract in all_contracts if contract.client_source_id == selected.id
        ]
        referenced_site_ids = {
            contract.site_source_id for contract in all_contracts if contract.site_source_id
        }
        referenced_equipment_ids = {
            item.source_equipment_id
            for contract in all_contracts
            for item in contract.equipment_items
        }
        archived_children = any(site.archived for site in sites) or any(
            equipment.archived
            for site in sites
            for equipment in self.context.master_data.list_equipment(site.id)
        )
        visible_sites = [
            site for site in sites if self.show_archived_master_data or not site.archived
        ]
        site_rows = []
        for site in visible_sites:
            equipment = [
                item for item in self.context.master_data.list_equipment(site.id)
                if self.show_archived_master_data or not item.archived
            ]
            site_rows.append({
                "id": site.id,
                "label": site.label,
                "address": site.rendered_address,
                "archived": site.archived,
                "referenced": site.id in referenced_site_ids,
                "active_equipment_count": sum(not item.archived for item in self.context.master_data.list_equipment(site.id)),
                "editor": _site_editor_payload(site),
                "equipment": [{
                    "id": item.id,
                    "name": item.display_name,
                    "location": item.location,
                    "archived": item.archived,
                    "referenced": item.id in referenced_equipment_ids,
                    "editor": _equipment_editor_payload(item),
                } for item in equipment],
            })
        return {
            "page": "CLIENTS",
            "search": self.client_search,
            "archived": self.client_archived,
            "show_archived_master_data": self.show_archived_master_data,
            "backup": self.register.backup_summary().label,
            "clients": [{
                "id": item.client.id,
                "name": item.client.display_name,
                "type": "Personne" if item.client.party_type == "PERSON" else "Organisation",
                "summary": f"{item.site_count} site(s) · {item.equipment_count} équipement(s)",
                "archived": item.client.archived,
                "selected": item.client.id == self.selected_client_id,
            } for item in clients],
            "selected": None if selected is None else {
                "id": selected.id,
                "name": selected.display_name,
                "type": "Personne" if selected.party_type == "PERSON" else "Organisation",
                "address": selected.rendered_address,
                "email": selected.email,
                "phone": selected.phone,
                "archived": selected.archived,
                "referenced": bool(linked),
                "has_archived_children": archived_children,
                "legal_form": selected.legal_form,
                "siret": selected.siret,
                "billing_address": selected.billing_address,
                "internal_reference": selected.internal_reference,
                "editor": _client_editor_payload(selected),
                "sites": site_rows,
                "linked": [{
                    "id": contract.id,
                    "number": contract.number or "Brouillon sans numéro",
                    "site": contract.site_snapshot.label if contract.site_snapshot else "Site à sélectionner",
                    "status_label": STATUS_LABELS[contract.status],
                    "status_tone": contract.status.value,
                    "secondary_display": f"Entretien annuel · {contract.site_snapshot.label if contract.site_snapshot else 'Site à sélectionner'}",
                } for contract in linked],
            },
        }

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit(self.snapshot())

    @Slot(str)
    def setFilter(self, value: str) -> None:
        self.filter = ContractRegisterFilter(value)
        self.refresh()

    @Slot(str)
    def setSearch(self, value: str) -> None:
        self.search = value
        self.refresh()

    @Slot(str, result="QVariant")
    def openContract(self, contract_id: str) -> dict:
        try:
            self.context.contracts.get(contract_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.page_name = "CONTRACT_WORKSPACE"
        self.contract_step = 1
        self.contract_focus_target = None
        self.contract_generation_feedback = None
        self.contract_documents_feedback = None
        self.contract_signed_pdf_selection = None
        self.refresh()
        return {"ok": True, "id": contract_id}

    @Slot(result="QVariant")
    def createContract(self) -> dict:
        try:
            contract = self.context.contracts.create_draft()
        except ApplicationError as error:
            return _master_error(error)
        self.openContract(contract.id)
        return {"ok": True, "id": contract.id}

    @Slot()
    def saveBackup(self) -> None:
        self.context.backup.create_now()
        self.refresh()

    @Slot(str)
    def navigate(self, destination: str) -> None:
        if destination == "CLIENTS":
            self.page_name = "CLIENTS"
            self.refresh()
            return
        if destination == "CONTRACTS":
            self.page_name = "CONTRACTS"
            self.contract_id = None
            self.refresh()
            return
        if destination in {"CLIENTS", "SETTINGS"}:
            self.navigate_legacy(destination)

    @Slot(str)
    def setClientSearch(self, value: str) -> None:
        self.client_search = value
        self.refresh()

    @Slot(bool)
    def setClientArchived(self, archived: bool) -> None:
        self.client_archived = archived
        self.show_archived_master_data = False
        self.refresh()

    @Slot(bool)
    def setShowArchivedMasterData(self, visible: bool) -> None:
        self.show_archived_master_data = visible
        self.refresh()

    @Slot(str)
    def selectClient(self, client_id: str) -> None:
        self.selected_client_id = client_id
        self.refresh()

    @Slot()
    def returnToContracts(self) -> None:
        self.page_name = "CONTRACTS"
        self.contract_id = None
        self.contract_step = 1
        self.contract_documents_feedback = None
        self.contract_signed_pdf_selection = None
        self.refresh()

    @Slot(str, int, result="QVariant")
    def setContractStep(self, contract_id: str, step: int) -> dict:
        if contract_id != self.contract_id or step not in {1, 2, 3, 4}:
            return {"ok": False, "message": "Cette étape n’est pas disponible."}
        contract = self.context.contracts.get(contract_id)
        if step == 4 and not self.context.lifecycle.revisions(contract_id) and contract.status not in {ContractStatus.DRAFT, ContractStatus.TO_SIGN} and self.context.intervention_generation is None:
            return {"ok": False, "message": "Aucune révision contractuelle n’est encore disponible."}
        self.contract_step = step
        self.contract_focus_target = None
        self.refresh()
        return {"ok": True, "id": contract_id, "step": step}

    def _contract_revision(self,contract_id:str,revision:str):
        if contract_id != self.contract_id:return None
        return next((item for item in self.context.lifecycle.revisions(contract_id) if item.revision==revision),None)

    @Slot(str, str, str, result="QVariant")
    def openContractDocument(self,contract_id:str,revision:str,kind:str)->dict:
        document=self._contract_revision(contract_id,revision)
        if document is None or kind not in {"docx","pdf","signed"}:
            return {"ok":False,"message":"Ce document n’est pas disponible."}
        try:self.context.lifecycle.open_document(document.id,kind)
        except (ContractLifecycleError,OSError):return {"ok":False,"message":"Le fichier est introuvable dans le dossier de travail."}
        return {"ok":True,"revision":revision,"kind":kind}

    def _intervention_document(self, contract_id: str, document_id: str):
        if contract_id != self.contract_id:
            return None
        document = self.context.lifecycle.documents.get(document_id)
        if document is None or document.contract_id != contract_id or document.document_kind is not DocumentKind.INTERVENTION_SHEET:
            return None
        return document

    @Slot(str, str, str, result="QVariant")
    def openInterventionDocument(self, contract_id: str, document_id: str, kind: str) -> dict:
        if self._intervention_document(contract_id, document_id) is None or kind not in {"docx", "pdf"}:
            return {"ok": False, "message": "Ce document n’est pas disponible."}
        try:
            self.context.lifecycle.open_document(document_id, kind)
        except (ContractLifecycleError, OSError):
            return {"ok": False, "message": "Le fichier est introuvable dans le dossier de travail."}
        return {"ok": True, "document_id": document_id, "kind": kind}

    @Slot(str, str, "QVariant", result="QVariant")
    def generateInterventionSheet(self, contract_id: str, template_version_id: str, payload: object) -> dict:
        if contract_id != self.contract_id or self.context.intervention_generation is None:
            return {"ok": False, "message": "La fiche d’intervention n’est pas disponible pour ce contrat."}
        if not isinstance(payload, dict) or set(payload) - {"date", "technician", "other", "notes", "issues", "quote_recommended"}:
            return {"ok": False, "message": "Les informations de la fiche d’intervention sont invalides."}
        text_fields = ("date", "technician", "other", "notes", "issues")
        if any(not isinstance(payload.get(field, ""), str) for field in text_fields):
            return {"ok": False, "message": "Les informations de la fiche d’intervention sont invalides."}
        quote = payload.get("quote_recommended")
        if quote is not None and not isinstance(quote, bool):
            return {"ok": False, "message": "Les informations de la fiche d’intervention sont invalides."}
        try:
            result = self.context.intervention_generation.generate(
                contract_id,
                template_version_id,
                InterventionInput(
                    payload.get("date", ""), payload.get("technician", ""), payload.get("other", ""),
                    payload.get("notes", ""), payload.get("issues", ""), quote,
                ),
            )
        except DocumentGenerationError as error:
            self.contract_documents_feedback = {"kind": "error", "message": error.user_message}
            self.refresh()
            return {"ok": False, "message": error.user_message}
        self.contract_step = 4
        self.contract_documents_feedback = {"kind": "success", "message": "La fiche d’intervention a été générée."}
        self.refresh()
        return {"ok": True, "document_id": result.document.id}

    def _signed_pdf_source(self, contract_id: str, intent: str) -> Path | None:
        selection = self.contract_signed_pdf_selection
        if selection is None or selection[0] != contract_id or selection[1] != intent:
            return None
        self.contract_signed_pdf_selection = None
        return selection[2]

    @Slot(str, str, result="QVariant")
    def clearContractSignedPdfSelection(self, contract_id: str, intent: str) -> dict:
        if contract_id != self.contract_id or intent != "REPLACE":
            return {"ok": False}
        self.contract_signed_pdf_selection = None
        return {"ok": True}

    @Slot(str, str, result="QVariant")
    def selectContractSignedPdf(self, contract_id: str, intent: str) -> dict:
        if contract_id != self.contract_id or intent not in {"SIGNATURE", "ADD", "LOCATE", "REPLACE"}:
            return {"ok": False, "message": "Cette sélection de fichier n’est pas disponible."}
        contract = self.context.contracts.get(contract_id)
        authority = self.context.lifecycle.signature_authority(contract_id)
        if intent == "SIGNATURE" and (contract.status is not ContractStatus.TO_SIGN or authority is not None):
            return {"ok": False, "message": "La signature ne peut pas être enregistrée pour ce contrat."}
        if intent != "SIGNATURE" and authority is None:
            return {"ok": False, "message": "Aucune révision signée n’est disponible."}
        if intent == "ADD" and authority is not None and authority.document.signed_pdf_path is not None:
            return {"ok": False, "message": "Une copie signée est déjà archivée."}
        if intent == "LOCATE" and authority is not None and self.context.lifecycle.signed_copy_state(authority.document) is SignedCopyState.VALID:
            return {"ok": False, "message": "La copie signée est déjà disponible."}
        selected, _ = QFileDialog.getOpenFileName(None, "Choisir le PDF signé", "", "Documents PDF (*.pdf)")
        if not selected:
            return {"ok": True, "selected": False}
        path = Path(selected)
        self.contract_signed_pdf_selection = (contract_id, intent, path)
        return {"ok": True, "selected": True, "name": path.name}

    @Slot(str, str, str, result="QVariant")
    def recordContractSignature(self, contract_id: str, revision: str, effective_date: str) -> dict:
        document = self._contract_revision(contract_id, revision)
        if document is None or not isinstance(effective_date, str):
            return {"ok": False, "message": "Choisissez une révision contractuelle et une date de signature valides."}
        source = self._signed_pdf_source(contract_id, "SIGNATURE")
        try:
            authority = self.context.lifecycle.record_signature(contract_id, document.id, effective_date, source)
        except ContractLifecycleError as error:
            return {"ok": False, "message": error.user_message}
        contract = self.context.contracts.get(contract_id)
        self.contract_step = 4
        self.contract_documents_feedback = {"kind": "success", "message": "La signature réalisée hors de l’application a été enregistrée."}
        self.refresh()
        return {"ok": True, "revision": authority.document.revision, "status": contract.status.value, "signature_date": authority.event.effective_date}

    def _publish_signed_copy(self, contract_id: str, intent: str) -> dict:
        source = self._signed_pdf_source(contract_id, intent)
        if source is None:
            return {"ok": False, "message": "Choisissez un PDF signé avant de continuer."}
        try:
            result = {
                "ADD": self.context.lifecycle.add_signed_copy,
                "LOCATE": self.context.lifecycle.locate_signed_copy,
                "REPLACE": self.context.lifecycle.replace_signed_copy,
            }[intent](contract_id, source)
        except ContractLifecycleError as error:
            return {"ok": False, "message": error.user_message}
        if intent == "REPLACE":
            if result.kind == "SAME_CONTENT":
                self.contract_documents_feedback = {"kind": "info", "message": "Ce PDF correspond déjà à la copie signée archivée. Aucun remplacement n’a été effectué."}
            else:
                self.contract_documents_feedback = {"kind": "success", "message": "Le PDF signé a été remplacé. La copie précédente est conservée dans l’historique documentaire."}
            document = result.document
        else:
            document = result
            self.contract_documents_feedback = {"kind": "success", "message": "Le PDF signé a été archivé."}
        self.refresh()
        return {"ok": True, "revision": document.revision, "outcome": result.kind if intent == "REPLACE" else "ARCHIVED"}

    @Slot(str, result="QVariant")
    def addContractSignedPdf(self, contract_id: str) -> dict:
        return self._publish_signed_copy(contract_id, "ADD") if contract_id == self.contract_id else {"ok": False, "message": "Ce contrat n’est plus ouvert."}

    @Slot(str, result="QVariant")
    def locateContractSignedPdf(self, contract_id: str) -> dict:
        return self._publish_signed_copy(contract_id, "LOCATE") if contract_id == self.contract_id else {"ok": False, "message": "Ce contrat n’est plus ouvert."}

    @Slot(str, result="QVariant")
    def replaceContractSignedPdf(self, contract_id: str) -> dict:
        return self._publish_signed_copy(contract_id, "REPLACE") if contract_id == self.contract_id else {"ok": False, "message": "Ce contrat n’est plus ouvert."}

    @Slot(str, str, result="QVariant")
    def prepareLinkedRenewal(self, contract_id: str, request_id: str) -> dict:
        if contract_id != self.contract_id or not request_id.strip():
            return {"ok": False, "message": "La préparation du nouveau contrat n’a pas pu commencer. Le contrat actuel reste inchangé."}
        source = self.context.contracts.get(contract_id)
        mode = self.context.contracts.get_conditions(contract_id).renewal_mode
        if source.status is not ContractStatus.ACTIVE or mode not in {"NONE", "MANUAL"}:
            return {"ok": False, "message": "Ce nouveau contrat lié ne peut pas être préparé dans cet état. Le contrat actuel reste inchangé."}
        try:
            draft = self.context.lifecycle.create_linked_draft(contract_id, request_id)
        except ContractLifecycleError as error:
            return {"ok": False, "message": f"Le nouveau brouillon lié n’a pas été créé. {error.user_message} Le contrat actuel et ses documents sont conservés."}
        self.contract_id = draft.id
        self.page_name = "CONTRACT_WORKSPACE"
        self.contract_step = 1
        self.contract_focus_target = None
        self.contract_documents_feedback = None
        self.contract_signed_pdf_selection = None
        self.refresh()
        return {"ok": True, "id": draft.id, "status": draft.status.value, "predecessor_id": contract_id}

    def _renewal_preview_payload(self, contract_id: str, annual_ht: str | None, vat_rate: str | None) -> dict:
        preview, price = self.context.lifecycle.renewal_price_preview(contract_id, annual_ht, vat_rate)
        return {
            "current_period": f"Du {_date_fr(preview.current_period.start.isoformat())} au {_date_fr(preview.current_period.end.isoformat())}",
            "next_period": f"Du {_date_fr(preview.next_period.start.isoformat())} au {_date_fr(preview.next_period.end.isoformat())}",
            "renewal_months": preview.renewal_months,
            "price_rule": preview.price_rule,
            "vat_rates": list(preview.vat_rates),
            "price": None if price is None else {
                "annual_ht": _money_fr(price.annual_ht), "vat_rate": _money_fr(price.vat_rate),
                "vat_amount": _money_fr(price.vat_amount), "annual_ttc": _money_fr(price.annual_ttc),
            },
        }

    @Slot(str, str, str, result="QVariant")
    def previewTacitRenewal(self, contract_id: str, annual_ht: str, vat_rate: str) -> dict:
        if contract_id != self.contract_id:
            return {"ok": False, "message": "Ce contrat n’est plus ouvert."}
        try:
            return {"ok": True, **self._renewal_preview_payload(contract_id, annual_ht or None, vat_rate or None)}
        except ContractLifecycleError as error:
            return {"ok": False, "message": error.user_message}

    @Slot(str, str, str, result="QVariant")
    def confirmTacitRenewal(self, contract_id: str, annual_ht: str, vat_rate: str) -> dict:
        if contract_id != self.contract_id:
            return {"ok": False, "message": "La reconduction n’a pas été enregistrée. Ce contrat n’est plus ouvert."}
        if not self.context.lifecycle.renewal_attention_due(contract_id):
            return {"ok": False, "message": "La reconduction n’est pas encore à confirmer. La période actuelle et les données existantes sont conservées."}
        try:
            event = self.context.lifecycle.confirm_renewal(contract_id, annual_ht or None, vat_rate or None)
        except ContractLifecycleError as error:
            return {"ok": False, "message": f"La reconduction n’a pas été enregistrée. {error.user_message} Les données existantes sont conservées."}
        self.contract_step = 4
        self.contract_documents_feedback = {"kind": "success", "message": "La reconduction a été confirmée. La nouvelle période est maintenant affichée."}
        self.refresh()
        return {"ok": True, "period_start": event.period_start, "period_end": event.period_end}

    @Slot(str, str, str, result="QVariant")
    def recordContractNonRenewal(self, contract_id: str, notification_date: str, note: str) -> dict:
        if contract_id != self.contract_id:return {"ok": False, "message": "Ce contrat n’est plus ouvert."}
        try:event=self.context.lifecycle.record_non_renewal(contract_id,notification_date,note)
        except ContractLifecycleError as error:return {"ok": False, "message": f"La fin de contrat n’a pas été enregistrée. {error.user_message} Les données existantes sont conservées."}
        self.contract_documents_feedback={"kind":"success","message":"Le non-renouvellement a été enregistré. Aucune communication n’a été envoyée par l’application."};self.refresh()
        return {"ok":True,"effective_date":event.effective_date}

    @Slot(str, str, str, str, str, result="QVariant")
    def scheduleContractTermination(self, contract_id: str, effective_date: str, reason_code: str, notification_date: str, note: str) -> dict:
        if contract_id != self.contract_id:return {"ok": False, "message": "Ce contrat n’est plus ouvert."}
        try:event=self.context.lifecycle.schedule_controlled_termination(contract_id,effective_date,reason_code,notification_date or None,note)
        except ContractLifecycleError as error:return {"ok": False, "message": f"La résiliation n’a pas été programmée. {error.user_message} Les données existantes sont conservées."}
        self.contract_documents_feedback={"kind":"success","message":"La résiliation a été enregistrée. Aucun message n’a été envoyé par l’application."};self.refresh()
        return {"ok":True,"effective_date":event.effective_date,"immediate":self.context.contracts.get(contract_id).status is ContractStatus.TERMINATED}

    @Slot(str, result="QVariant")
    def abandonContract(self, contract_id: str) -> dict:
        if contract_id != self.contract_id:return {"ok": False, "message": "Ce contrat n’est plus ouvert."}
        try:self.context.lifecycle.abandon(contract_id)
        except ContractLifecycleError as error:return {"ok": False, "message": f"Le contrat n’a pas été abandonné. {error.user_message} Les documents existants sont conservés."}
        self.contract_documents_feedback={"kind":"success","message":"Le contrat a été abandonné. Les documents et l’historique sont conservés."};self.refresh()
        return {"ok":True}

    @Slot(str, result="QVariant")
    def reopenContractForCorrection(self,contract_id:str)->dict:
        if contract_id != self.contract_id:return {"ok":False,"message":"Ce contrat n’est plus ouvert."}
        try:self.context.lifecycle.reopen_for_correction(contract_id)
        except ContractLifecycleError:return {"ok":False,"message":"Ce contrat ne peut pas être rouvert pour correction."}
        self.contract_step=4
        self.contract_documents_feedback={"kind":"success","message":"Le contrat est revenu à l’état Brouillon. Les révisions existantes sont conservées."}
        self.refresh()
        return {"ok":True,"id":contract_id,"status":"DRAFT","status_label":"Brouillon"}

    @Slot(str, str, str, str, result="QVariant")
    def recordContractSent(self,contract_id:str,revision:str,effective_date:str,note:str)->dict:
        document=self._contract_revision(contract_id,revision)
        if document is None or not all(isinstance(value,str) for value in (effective_date,note)):
            return {"ok":False,"message":"Choisissez une révision contractuelle existante."}
        try:event=self.context.lifecycle.record_send(contract_id,document.id,effective_date,note)
        except ContractLifecycleError:return {"ok":False,"message":"L’envoi ne peut pas être enregistré. Vérifiez la révision et la date."}
        self.contract_documents_feedback={"kind":"success","message":f"L’envoi de {revision} a été enregistré au {_date_fr(event.effective_date)}."}
        self.refresh()
        return {"ok":True,"revision":revision,"effective_date":event.effective_date}

    @Slot(str, str, result="QVariant")
    def openContractReviewBlock(self, contract_id: str, block_id: str) -> dict:
        targets = {
            "CLIENT_SIGNATORY": (1, "client-signatory"),
            "SITE_EQUIPMENT": (1, "site-equipment"),
            "CONTRACT_CONTEXT": (2, "contract-context"),
            "MODEL_SERVICES": (2, "model-services"),
            "PERIOD": (2, "period"),
            "INTERVENTION": (2, "intervention"),
            "PRICE_PAYMENT": (2, "price-payment"),
            "RENEWAL_END": (2, "renewal-end"),
            "SPECIAL_TERMS": (2, "special-terms"),
        }
        target = targets.get(block_id)
        if contract_id != self.contract_id or target is None:
            return {"ok": False, "message": "Cette section n’est pas disponible."}
        self.contract_step, self.contract_focus_target = target
        self.refresh()
        return {"ok": True, "id": contract_id, "step": target[0], "target": target[1]}

    @staticmethod
    def _generation_error(error: DocumentGenerationError) -> dict:
        titles = {
            "incomplete": "Données du contrat à corriger",
            "template_incompatible": "Modèle indisponible",
            "source_mismatch": "Modèle indisponible",
            "company_unavailable": "Informations société à compléter",
            "company_incomplete": "Informations société à compléter",
            "render_failure": "Génération DOCX impossible",
            "invalid_docx": "Génération DOCX impossible",
            "unresolved_token": "Génération DOCX impossible",
            "converter_unavailable": "Conversion PDF impossible",
            "converter_timeout": "Conversion PDF impossible",
            "converter_failure": "Conversion PDF impossible",
            "converter_wrong_version": "Conversion PDF impossible",
            "invalid_pdf": "Conversion PDF impossible",
            "publication_failure": "Enregistrement final impossible",
            "generation_in_progress": "Génération déjà en cours",
        }
        return {
            "ok": False,
            "code": error.code,
            "title": titles.get(error.code, "Génération interrompue"),
            "message": error.user_message,
            "guarantee": "Aucune nouvelle révision n’a été créée, aucun premier numéro officiel n’a été consommé et les données du contrat sont conservées.",
        }

    @Slot(str, result="QVariant")
    def generateOfficialContract(self, contract_id: str) -> dict:
        if contract_id != self.contract_id:
            return {"ok": False, "title": "Contrat indisponible", "message": "Ce contrat n’est plus ouvert.",
                    "guarantee": "Aucune donnée n’a été modifiée."}
        try:
            result = self.context.generation.generate(contract_id)
        except DocumentGenerationError as error:
            response = self._generation_error(error)
            self.contract_generation_feedback = {**response, "kind": "error"}
            self.refresh()
            return response
        self.contract_step = 3
        response = {
            "ok": True, "contract_number": result.contract_number,
            "revision": result.document.revision,
            "status": "TO_SIGN", "status_label": "À signer",
            "message": "Le DOCX et le PDF ont été générés avec succès.",
        }
        self.contract_generation_feedback = {**response, "kind": "success"}
        self.refresh()
        return response

    @Slot(str, "QVariant", result="QVariant")
    def updateContractFramework(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_FRAMEWORK_DTO_FIELDS)
            if not values:
                raise ContractConditionsValidationError({"payload": "Aucune condition à enregistrer."})
            contract = self.context.contracts.get(contract_id)
            if "regime" in values:
                regime = values["regime"]
                if regime is not None and not isinstance(regime, str):
                    raise ContractConditionsValidationError({"regime": "Le régime est invalide."})
                contract = self.context.contracts.change_regime(contract_id, regime)
                compatible = self.context.contracts.compatible_template_versions(contract_id)
                if contract.template_version_id is None and len(compatible) == 1:
                    contract = self.context.contracts.select_template_version(contract_id, compatible[0].id)
            context_fields = {"conclusion_mode", "early_performance_requested"} & set(values)
            if context_fields:
                current = self.context.contracts.get_conditions(contract_id)
                updated = asdict(current)
                if "conclusion_mode" in values:
                    conclusion = values["conclusion_mode"]
                    if conclusion is not None and not isinstance(conclusion, str):
                        raise ContractConditionsValidationError({"conclusion_mode": "Le mode de conclusion est invalide."})
                    updated["conclusion_mode"] = conclusion
                if "early_performance_requested" in values:
                    early = values["early_performance_requested"]
                    if early is not None and not isinstance(early, bool):
                        raise ContractConditionsValidationError({"early_performance_requested": "Cette réponse est invalide."})
                    updated["early_performance_requested"] = early
                self.context.contracts.save_conditions(contract_id, ContractConditions(**updated))
                contract = self.context.contracts.get(contract_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {
            "ok": True, "id": contract.id,
            "regime": contract.regime.value if contract.regime else None,
            "template_version_id": contract.template_version_id,
        }

    @Slot(str, str, result="QVariant")
    def selectContractTemplateVersion(self, contract_id: str, version_id: str) -> dict:
        try:
            contract = self.context.contracts.select_template_version(contract_id, version_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "template_version_id": contract.template_version_id}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractServiceOffer(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_SERVICE_DTO_FIELDS)
            missing = CONTRACT_SERVICE_DTO_FIELDS - set(values)
            if missing:
                raise ContractConditionsValidationError({"payload": "Complétez l’offre de services avant de l’enregistrer."})
            visits = values["visits_per_year"]
            if isinstance(visits, bool) or not isinstance(visits, (int, float)) or int(visits) != visits or visits < 1:
                raise ContractConditionsValidationError({"visits_per_year": "Le nombre de visites doit être au moins égal à 1."})
            refrigerant = values["refrigerant_handling_mode"]
            if refrigerant not in {"IN_HOUSE_AUTHORIZED", "PARTNER", "EXCLUDED"}:
                raise ContractConditionsValidationError({"refrigerant_handling_mode": "Choisissez la gestion des fluides."})
            options = values["included_options"]
            if not isinstance(options, list) or any(
                option not in {"DEEP_CLEANING", "DISINFECTION"} for option in options
            ):
                raise ContractConditionsValidationError({"included_options": "Une prestation incluse est inconnue."})
            priority = values["priority_breakdown"]
            if not isinstance(priority, bool):
                raise ContractConditionsValidationError({"priority_breakdown": "Indiquez si le dépannage prioritaire est inclus."})
            delay = values["priority_breakdown_delay"]
            if priority and (not isinstance(delay, str) or not delay.strip()):
                raise ContractConditionsValidationError({"priority_breakdown_delay": "Renseignez le délai d’intervention."})
            if not priority:
                delay = None
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(
                visits_per_year=int(visits), refrigerant_handling_mode=refrigerant,
                included_options=tuple(dict.fromkeys(options)), priority_breakdown=priority,
                priority_breakdown_delay=delay,
            )
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "visits_per_year": saved.visits_per_year, "message": "Prestations enregistrées."}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractPeriod(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_PERIOD_DTO_FIELDS)
            if set(values) != CONTRACT_PERIOD_DTO_FIELDS:
                raise ContractConditionsValidationError({"payload": "Complétez les informations de période transmises."})
            for field in ("issue_date", "start_date", "initial_duration_mode", "initial_end_date"):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur est invalide."})
            if not isinstance(values["signature_city"], str):
                raise ContractConditionsValidationError({"signature_city": "La ville de signature est invalide."})
            months = values["initial_duration_months"]
            if months is not None and (isinstance(months, bool) or not isinstance(months, (int, float)) or int(months) != months):
                raise ContractConditionsValidationError({"initial_duration_months": "Saisissez un nombre entier valide."})
            if (
                values["initial_duration_mode"] == "CUSTOM"
                and values["start_date"] and values["initial_end_date"]
            ):
                try:
                    incoherent = date.fromisoformat(values["initial_end_date"]) < date.fromisoformat(values["start_date"])
                except ValueError:
                    incoherent = False  # The authoritative service reports malformed dates below.
                if incoherent:
                    raise ContractConditionsValidationError({
                        "initial_end_date": "La date de fin doit être postérieure à la date de prise d’effet."
                    })
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            current["initial_duration_months"] = int(months) if months is not None else None
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "resolved_end_date": saved.resolved_end_date, "message": "Période enregistrée."}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractInterventionConditions(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_INTERVENTION_DTO_FIELDS)
            if set(values) != CONTRACT_INTERVENTION_DTO_FIELDS:
                raise ContractConditionsValidationError({"payload": "Complétez les conditions d’intervention transmises."})
            for field in ("included_area", "business_hours", "additional_exclusions"):
                if not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur doit être du texte."})
            if values["travel_included"] is not None and not isinstance(values["travel_included"], bool):
                raise ContractConditionsValidationError({"travel_included": "Choisissez les conditions de déplacement."})
            fee = values["missed_appointment_fee"]
            if fee is not None and not isinstance(fee, str):
                raise ContractConditionsValidationError({"missed_appointment_fee": "Le montant est invalide."})
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "travel_included": saved.travel_included, "message": "Conditions d’intervention enregistrées."}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractPricing(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_PRICING_DTO_FIELDS)
            if set(values) != CONTRACT_PRICING_DTO_FIELDS:
                raise ContractConditionsValidationError({"payload": "Complétez les informations de prix transmises."})
            for field in ("annual_ht", "vat_rate", "payment_terms_code"):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur est invalide."})
            if not isinstance(values["payment_terms_custom_text"], str):
                raise ContractConditionsValidationError({"payment_terms_custom_text": "Cette précision doit être du texte."})
            due_days = values["payment_due_days"]
            if due_days is not None and (
                isinstance(due_days, bool) or not isinstance(due_days, (int, float)) or int(due_days) != due_days
            ):
                raise ContractConditionsValidationError({"payment_due_days": "Saisissez un nombre entier valide."})
            methods = values["payment_methods"]
            if not isinstance(methods, list) or any(not isinstance(item, str) for item in methods):
                raise ContractConditionsValidationError({"payment_methods": "Les moyens de paiement sont invalides."})
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            current["payment_due_days"] = int(due_days) if due_days is not None else None
            current["payment_methods"] = tuple(methods)
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {
            "ok": True, "id": contract_id,
            "vat_amount": _money_fr(saved.vat_amount) if saved.vat_amount is not None else "",
            "annual_ttc": _money_fr(saved.annual_ttc) if saved.annual_ttc is not None else "",
            "message": "Prix et paiement enregistrés.",
        }

    @Slot(str, "QVariant", result="QVariant")
    def updateContractRenewal(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_RENEWAL_DTO_FIELDS)
            if set(values) != CONTRACT_RENEWAL_DTO_FIELDS:
                raise ContractConditionsValidationError({
                    "payload": "Complétez les informations de renouvellement transmises."
                })
            for field in ("renewal_mode", "renewal_price_rule"):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur est invalide."})
            for field in ("renewal_period_months", "non_renewal_notice_days", "internal_alert_days"):
                value = values[field]
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value
                ):
                    raise ContractConditionsValidationError({field: "Saisissez un nombre entier valide."})
                values[field] = int(value) if value is not None else None
            channels = values["non_renewal_notice_channels"]
            if not isinstance(channels, list) or any(not isinstance(item, str) for item in channels):
                raise ContractConditionsValidationError({
                    "non_renewal_notice_channels": "Les canaux de non-renouvellement sont invalides."
                })
            values["non_renewal_notice_channels"] = tuple(channels)
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "renewal_mode": saved.renewal_mode, "message": "Renouvellement enregistré."}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractEarlyTermination(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_EARLY_TERMINATION_DTO_FIELDS)
            if set(values) != CONTRACT_EARLY_TERMINATION_DTO_FIELDS:
                raise ContractConditionsValidationError({
                    "payload": "Complétez les conditions de fin anticipée transmises."
                })
            reasons = values["early_termination_reason_codes"]
            if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
                raise ContractConditionsValidationError({
                    "early_termination_reason_codes": "Les motifs de fin anticipée sont invalides."
                })
            if not isinstance(values["early_termination_custom_text"], str):
                raise ContractConditionsValidationError({
                    "early_termination_custom_text": "Le motif complémentaire doit être du texte."
                })
            cure_days = values["breach_cure_period_days"]
            if cure_days is not None and (
                isinstance(cure_days, bool) or not isinstance(cure_days, (int, float)) or int(cure_days) != cure_days
            ):
                raise ContractConditionsValidationError({
                    "breach_cure_period_days": "Saisissez un nombre entier valide."
                })
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(
                early_termination_reason_codes=tuple(reasons),
                early_termination_custom_text=values["early_termination_custom_text"],
                breach_cure_period_days=int(cure_days) if cure_days is not None else None,
            )
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "reason_count": len(saved.early_termination_reason_codes)}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractSpecialTerms(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_SPECIAL_TERMS_DTO_FIELDS)
            if set(values) != CONTRACT_SPECIAL_TERMS_DTO_FIELDS or not isinstance(values["special_terms"], str):
                raise ContractConditionsValidationError({
                    "special_terms": "Les conditions particulières doivent être du texte."
                })
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "special_terms": saved.special_terms, "message": "Conditions particulières enregistrées."}

    @Slot()
    def openContractModels(self) -> None:
        self.navigate_legacy("SETTINGS_MODELS")

    @Slot(str, str, result="QVariant")
    def selectContractClient(self, contract_id: str, client_id: str) -> dict:
        try:
            contract = self.context.contracts.select_client(contract_id, client_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "client_id": contract.client_source_id}

    @Slot(str, str, str, result="QVariant")
    def updateContractSignatory(self, contract_id: str, name: str, role: str) -> dict:
        try:
            contract = self.context.contracts.update_signatory(contract_id, name, role)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id}

    @Slot(str, str, result="QVariant")
    def selectContractSite(self, contract_id: str, site_id: str) -> dict:
        try:
            contract = self.context.contracts.select_site(contract_id, site_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "site_id": contract.site_source_id}

    @Slot(str, str, bool, result="QVariant")
    def setContractEquipment(self, contract_id: str, equipment_id: str, selected: bool) -> dict:
        try:
            operation = self.context.contracts.select_equipment if selected else self.context.contracts.deselect_equipment
            contract = operation(contract_id, equipment_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "equipment_id": equipment_id, "selected": selected}

    @Slot(str, str, int, result="QVariant")
    def moveContractEquipment(self, contract_id: str, item_id: str, delta: int) -> dict:
        try:
            contract = self.context.contracts.move_equipment(contract_id, item_id, delta)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "item_id": item_id}

    @Slot(str, str, str, result="QVariant")
    def updateContractEquipmentObservation(self, contract_id: str, item_id: str, observation: str) -> dict:
        try:
            contract = self.context.contracts.update_observation(contract_id, item_id, observation)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "item_id": item_id}

    @Slot(str, "QVariant", result="QVariant")
    def createContractClient(self, contract_id: str, payload: object) -> dict:
        try:
            contract = self.context.contracts.create_and_select_client(contract_id, _client_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "client_id": contract.client_source_id}

    @Slot(str, "QVariant", result="QVariant")
    def createContractSite(self, contract_id: str, payload: object) -> dict:
        try:
            contract = self.context.contracts.create_and_select_site(contract_id, _site_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "site_id": contract.site_source_id}

    @Slot(str, "QVariant", result="QVariant")
    def createContractEquipment(self, contract_id: str, payload: object) -> dict:
        try:
            contract = self.context.contracts.create_and_select_equipment(contract_id, _equipment_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        created = contract.equipment_items[-1]
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "equipment_id": created.source_equipment_id, "item_id": created.id}

    @Slot("QVariant", result="QVariant")
    def createClient(self, payload: object) -> dict:
        try:
            created = self.context.master_data.create_client(_client_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = created.id
        self.refresh()
        return {"ok": True, "id": created.id}

    @Slot(str, "QVariant", result="QVariant")
    def updateClient(self, client_id: str, payload: object) -> dict:
        try:
            current = self.context.master_data.get_client(client_id)
            updated = self.context.master_data.update_client(
                client_id, _client_draft(payload, current)
            )
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = updated.id
        self.refresh()
        return {"ok": True, "id": updated.id}

    @Slot(str, "QVariant", result="QVariant")
    def createSite(self, client_id: str, payload: object) -> dict:
        try:
            created = self.context.master_data.create_site(client_id, _site_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = created.client_id
        self.refresh()
        return {"ok": True, "id": created.id, "client_id": created.client_id}

    @Slot(str, "QVariant", result="QVariant")
    def updateSite(self, site_id: str, payload: object) -> dict:
        try:
            updated = self.context.master_data.update_site(site_id, _site_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = updated.client_id
        self.refresh()
        return {"ok": True, "id": updated.id, "client_id": updated.client_id}

    @Slot(str, "QVariant", result="QVariant")
    def createEquipment(self, site_id: str, payload: object) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            created = self.context.master_data.create_equipment(site_id, _equipment_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": created.id, "site_id": created.site_id}

    @Slot(str, "QVariant", result="QVariant")
    def updateEquipment(self, equipment_id: str, payload: object) -> dict:
        try:
            current = self.context.master_data.get_equipment(equipment_id)
            site = self.context.master_data.get_site(current.site_id)
            updated = self.context.master_data.update_equipment(
                equipment_id, _equipment_draft(payload)
            )
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": updated.id, "site_id": updated.site_id}

    @Slot(str, result="QVariant")
    def archiveClient(self, client_id: str) -> dict:
        try:
            client = self.context.master_data.get_client(client_id)
            self.context.master_data.archive_client(client.id)
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = True
        self.selected_client_id = client.id
        self.show_archived_master_data = False
        self.refresh()
        return {"ok": True, "id": client.id, "archived": True}

    @Slot(str, result="QVariant")
    def restoreClient(self, client_id: str) -> dict:
        try:
            client = self.context.master_data.get_client(client_id)
            self.context.master_data.restore_client(client.id)
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = client.id
        self.show_archived_master_data = False
        self.refresh()
        return {"ok": True, "id": client.id, "archived": False}

    @Slot(str, result="QVariant")
    def archiveSite(self, site_id: str) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            self.context.master_data.archive_site(site.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": site.id, "client_id": site.client_id, "archived": True}

    @Slot(str, result="QVariant")
    def restoreSite(self, site_id: str) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            self.context.master_data.restore_site(site.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": site.id, "client_id": site.client_id, "archived": False}

    @Slot(str, result="QVariant")
    def archiveEquipment(self, equipment_id: str) -> dict:
        try:
            equipment = self.context.master_data.get_equipment(equipment_id)
            site = self.context.master_data.get_site(equipment.site_id)
            self.context.master_data.archive_equipment(equipment.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": equipment.id, "site_id": equipment.site_id, "archived": True}

    @Slot(str, result="QVariant")
    def restoreEquipment(self, equipment_id: str) -> dict:
        try:
            equipment = self.context.master_data.get_equipment(equipment_id)
            site = self.context.master_data.get_site(equipment.site_id)
            self.context.master_data.restore_equipment(equipment.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": equipment.id, "site_id": equipment.site_id, "archived": False}

    @Slot(str)
    def openLinkedContract(self, contract_id: str) -> None:
        self.openContract(contract_id)

    @Slot(str)
    def editSite(self, site_id: str) -> None:
        self.navigate_legacy(f"EDIT_SITE:{site_id}")

    @Slot(str)
    def editEquipment(self, equipment_id: str) -> None:
        self.navigate_legacy(f"EDIT_EQUIPMENT:{equipment_id}")

    @Slot(str)
    def addEquipment(self, site_id: str) -> None:
        self.navigate_legacy(f"ADD_EQUIPMENT:{site_id}")

    @Slot(str, result="QVariant")
    def createContractForSite(self, site_id: str) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            client = self.context.master_data.get_client(site.client_id)
            if site.archived or client.archived:
                raise MasterDataValidationError({
                    "site_id": "Un contrat ne peut pas être créé pour un site ou un client archivé."
                })
            contract = self.context.contracts.create_draft()
            self.context.contracts.select_client(contract.id, client.id)
            contract = self.context.contracts.select_site(contract.id, site.id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.page_name = "CONTRACT_WORKSPACE"
        self.refresh()
        return {"ok": True, "id": contract.id, "site_id": site.id, "client_id": client.id}


class TrustedLocalPage(QWebEnginePage):
    """Allow only this packaged local web surface; popups and remote navigation are denied."""

    def __init__(self, assets_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.assets_root = assets_root.resolve()

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:
        if not url.isLocalFile():
            return False
        try:
            url.toLocalFile() and Path(url.toLocalFile()).resolve().relative_to(self.assets_root)
        except ValueError:
            return False
        return True

    def createWindow(self, window_type):  # noqa: N802 - Qt API spelling
        return None


class WebUiHost(QWebEngineView):
    def __init__(self, context, navigate: Callable[[str], None]) -> None:
        super().__init__()
        self.assets_root = web_ui_root()
        self._showing_load_error = False
        self.bridge = UiBridge(context, navigate)
        page = TrustedLocalPage(self.assets_root, self)
        self.setPage(page)
        self.channel = QWebChannel(page)
        self.channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self.channel)
        page.loadFinished.connect(self._load_finished)
        self.load(QUrl.fromLocalFile(str(self.assets_root / "index.html")))

    def _load_finished(self, success: bool) -> None:
        if success or self._showing_load_error:
            return
        self._showing_load_error = True
        self.setHtml(
            "<main style='font-family:Segoe UI,Arial;padding:32px'>"
            "<h1>Contrats indisponibles</h1>"
            "<p>La surface locale Contrats ne peut pas être chargée. Redémarrez l’application.</p>"
            "</main>"
        )
