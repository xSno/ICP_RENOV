from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from ..domain import (
    GenerationCheck, GenerationReadiness, ReviewBlockId, ReviewBlockResult, ReviewIssue,
    ReviewResult, ReviewState, TemplateVersionStatus,
)
from ..errors import ContractNotFoundError
from ..storage import Workspace, WorkspaceService
from .capabilities import DocumentCapabilities, DocumentCapabilityProbe
from .contracts import ContractService


BLOCK_TITLES = (
    (ReviewBlockId.CLIENT_SIGNATORY, "Client & signataire", 0),
    (ReviewBlockId.SITE_EQUIPMENT, "Site & équipements", 0),
    (ReviewBlockId.CONTRACT_CONTEXT, "Cadre du contrat", 1),
    (ReviewBlockId.MODEL_SERVICES, "Modèle & prestations", 1),
    (ReviewBlockId.PERIOD, "Période", 1),
    (ReviewBlockId.INTERVENTION, "Conditions d’intervention", 1),
    (ReviewBlockId.PRICE_PAYMENT, "Prix & paiement", 1),
    (ReviewBlockId.RENEWAL_END, "Renouvellement & fin du contrat", 1),
    (ReviewBlockId.SPECIAL_TERMS, "Conditions particulières", 1),
)


def _format_date_fr(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _format_money_fr(value: Decimal) -> str:
    return format(value, ".2f").replace(".", ",")


class ReviewService:
    def __init__(self, contracts: ContractService, workspace_service: WorkspaceService,
                 workspace: Workspace, capability_probe: DocumentCapabilityProbe | None = None,
                 generation_ready: Callable[[str], bool] | None = None) -> None:
        self.contracts = contracts
        self.workspace_service = workspace_service
        self.workspace = workspace
        self.capability_probe = capability_probe or DocumentCapabilityProbe()
        self.generation_ready = generation_ready

    def review(self, contract_id: str) -> ReviewResult:
        contract, version, blocks = self._business_blocks(contract_id)
        workspace = self.workspace_service.inspect(self.workspace.root)
        capabilities = self.capability_probe.probe()
        docx_available = self.generation_ready(contract_id) if self.generation_ready else capabilities.docx_available
        model_ok = self._model_compatible(contract, version)
        checks = (
            GenerationCheck("MODEL", "Modèle disponible", model_ok, "Disponible" if model_ok else "Indisponible"),
            GenerationCheck("WORKSPACE", "Dossier de travail accessible", workspace.available and workspace.writable,
                            "Disponible" if workspace.available and workspace.writable else "Indisponible"),
            GenerationCheck("DOCX", "Génération DOCX disponible", docx_available,
                            "Disponible" if docx_available else "Indisponible"),
            GenerationCheck("PDF", "Conversion PDF disponible", capabilities.pdf_available,
                            "Disponible" if capabilities.pdf_available else capabilities.pdf_detail.replace("Conversion PDF ", "").capitalize()),
        )
        return ReviewResult(blocks, GenerationReadiness(checks))

    def business_data_complete(self, contract_id: str) -> bool:
        """Validate the nine business blocks without probing workspace or LibreOffice."""
        _, _, blocks = self._business_blocks(contract_id)
        return all(block.state is ReviewState.VALID for block in blocks)

    def _business_blocks(self, contract_id: str):
        contract = self.contracts.get(contract_id)
        conditions = self.contracts.get_conditions(contract_id)
        version = None
        if contract.template_version_id:
            try: version = self.contracts.selected_template_version(contract_id)
            except ContractNotFoundError: version = None
        blocks = (
            self._client(contract), self._site_equipment(contract), self._context(contract, conditions, version),
            self._model_services(contract, conditions, version), self._period(conditions),
            self._intervention(conditions), self._price_payment(conditions, version),
            self._renewal(conditions, version), self._special(conditions),
        )
        return contract, version, blocks

    @staticmethod
    def _block(block_id: ReviewBlockId, summary: str, issues: list[str]) -> ReviewBlockResult:
        item = next(value for value in BLOCK_TITLES if value[0] is block_id)
        return ReviewBlockResult(block_id, item[1], ReviewState.ERROR if issues else ReviewState.VALID,
                                 summary, tuple(ReviewIssue(value) for value in issues), item[2])

    def _client(self, contract) -> ReviewBlockResult:
        issues = []
        if contract.client_snapshot is None: issues.append("Sélectionnez un client.")
        if not contract.signatory_name.strip(): issues.append("Renseignez le nom du signataire.")
        if not contract.signatory_role.strip(): issues.append("Renseignez la qualité du signataire.")
        if contract.client_snapshot:
            identity = "Personne" if contract.client_snapshot.party_type == "PERSON" else "Organisation"
            summary = f"{contract.client_snapshot.display_name} · {identity}"
            if contract.signatory_name.strip():
                summary += f" · signataire {contract.signatory_name.strip()}"
                if contract.signatory_role.strip(): summary += f", {contract.signatory_role.strip()}"
        else: summary = "Client à sélectionner"
        return self._block(ReviewBlockId.CLIENT_SIGNATORY, summary, issues)

    def _site_equipment(self, contract) -> ReviewBlockResult:
        issues = []
        if contract.site_snapshot is None: issues.append("Sélectionnez un site.")
        if not contract.equipment_items: issues.append("Aucun équipement sélectionné.")
        observed = sum(bool(item.observation.strip()) for item in contract.equipment_items)
        count = len(contract.equipment_items)
        equipment_label = f"{count} équipement" if count == 1 else f"{count} équipements"
        observation_label = (
            "1 avec observation contractuelle" if observed == 1
            else f"{observed} avec observations contractuelles"
        )
        summary = f"{equipment_label} · {observation_label}"
        if contract.site_snapshot: summary = f"{contract.site_snapshot.label} · {summary}"
        return self._block(ReviewBlockId.SITE_EQUIPMENT, summary, issues)

    def _context(self, contract, conditions, version) -> ReviewBlockResult:
        issues = []
        regime = contract.regime.value if contract.regime else None
        if regime is None: issues.append("Choisissez le régime du contrat.")
        if version and version.validation.requires_conclusion(regime):
            if not conditions.conclusion_mode: issues.append("Renseignez le mode de conclusion.")
            else:
                known = any(context.regime == regime and context.conclusion_mode == conditions.conclusion_mode
                            for context in version.validation.context_authorizations)
                if not known: issues.append("Le mode de conclusion ne correspond pas au contexte configuré.")
                blocks = version.validation.blocks_for(regime, conditions.conclusion_mode)
                early_authorized = {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE"}.issubset(blocks)
                if early_authorized and conditions.early_performance_requested is None:
                    issues.append("Indiquez si un démarrage anticipé est demandé.")
                elif conditions.early_performance_requested and not early_authorized:
                    issues.append("La demande de démarrage anticipé ne correspond pas au contexte configuré.")
        summary_parts = [{"CONSUMER": "Consommateur", "NON_PROFESSIONAL": "Non-professionnel",
                          "PROFESSIONAL": "Professionnel"}.get(regime, "Régime à compléter")]
        if version and version.validation.requires_conclusion(regime):
            summary_parts.append({
                "IN_PREMISES": "Conclusion dans les locaux du professionnel",
                "OFF_PREMISES": "Conclusion hors établissement",
                "DISTANCE_EMAIL": "Conclusion à distance par e-mail",
                "ONLINE_INTERFACE": "Conclusion via une interface en ligne",
                "OTHER_DISTANCE": "Autre conclusion à distance",
            }.get(conditions.conclusion_mode, "Mode de conclusion à compléter"))
            blocks = version.validation.blocks_for(regime, conditions.conclusion_mode)
            if {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE"}.issubset(blocks):
                summary_parts.append(
                    "Démarrage anticipé demandé" if conditions.early_performance_requested is True
                    else "Aucun démarrage anticipé demandé" if conditions.early_performance_requested is False
                    else "Démarrage anticipé à confirmer"
                )
        summary = " · ".join(summary_parts)
        return self._block(ReviewBlockId.CONTRACT_CONTEXT, summary, issues)

    def _model_services(self, contract, conditions, version) -> ReviewBlockResult:
        issues = []
        if contract.template_version_id is None:
            issues.append("Aucun modèle sélectionné.")
        elif version is None:
            issues.append("Le modèle sélectionné est introuvable.")
        elif not self._model_compatible(contract, version):
            issues.append("Le modèle sélectionné n’est plus disponible pour ce contrat.")
        if conditions.visits_per_year is None or conditions.visits_per_year < 1:
            issues.append("Renseignez un nombre de visites valide.")
        if not conditions.refrigerant_handling_mode: issues.append("Choisissez la gestion des fluides.")
        if conditions.priority_breakdown is None: issues.append("Indiquez si le dépannage prioritaire est inclus.")
        elif conditions.priority_breakdown and not (conditions.priority_breakdown_delay or "").strip():
            issues.append("Renseignez le délai d’intervention prioritaire.")
        model = version.display_name if version else "Modèle à sélectionner"
        visits = (
            "1 visite par an" if conditions.visits_per_year == 1
            else f"{conditions.visits_per_year} visites par an" if conditions.visits_per_year
            else "Visites à compléter"
        )
        fluid = {
            "IN_HOUSE_AUTHORIZED": "Fluides gérés en interne",
            "PARTNER": "Fluides gérés par un partenaire habilité",
            "EXCLUDED": "Gestion des fluides exclue",
        }.get(conditions.refrigerant_handling_mode, "Gestion des fluides à compléter")
        option_labels = {
            "DEEP_CLEANING": "Nettoyage approfondi",
            "DISINFECTION": "Désinfection",
        }
        services = [model, "Entretien préventif inclus", visits, fluid]
        if conditions.included_options:
            services.append("Options : " + ", ".join(option_labels.get(code, "Option non reconnue") for code in conditions.included_options))
        if conditions.priority_breakdown is True:
            priority = "Dépannage prioritaire inclus"
            if (conditions.priority_breakdown_delay or "").strip():
                priority += f" · délai {conditions.priority_breakdown_delay.strip()}"
            services.append(priority)
        elif conditions.priority_breakdown is False:
            services.append("Dépannage prioritaire non inclus")
        return self._block(ReviewBlockId.MODEL_SERVICES, " · ".join(services), issues)

    def _period(self, conditions) -> ReviewBlockResult:
        issues = []
        if not conditions.issue_date: issues.append("Renseignez la date d’émission.")
        if not conditions.start_date: issues.append("Renseignez la date de prise d’effet.")
        if not conditions.initial_duration_mode: issues.append("Choisissez la durée initiale.")
        if conditions.initial_duration_mode == "STANDARD" and not conditions.initial_duration_months:
            issues.append("Renseignez la durée standard en mois.")
        if conditions.initial_duration_mode == "CUSTOM" and not conditions.initial_end_date:
            issues.append("Renseignez la date de fin.")
        end = None
        try: end = conditions.resolved_end_date
        except (TypeError, ValueError): issues.append("La période ne peut pas être calculée.")
        if conditions.start_date and end:
            try:
                if date.fromisoformat(end) < date.fromisoformat(conditions.start_date):
                    issues.append("La date de fin doit être postérieure à la date de prise d’effet.")
            except ValueError: issues.append("La période contient une date invalide.")
        if not end and conditions.initial_duration_mode: issues.append("La date de fin ne peut pas être déterminée.")
        summary_parts = []
        if conditions.issue_date: summary_parts.append(f"Émise le {_format_date_fr(conditions.issue_date)}")
        if conditions.start_date and end:
            summary_parts.append(f"Du {_format_date_fr(conditions.start_date)} au {_format_date_fr(end)}")
        if conditions.initial_duration_mode == "STANDARD" and conditions.initial_duration_months:
            summary_parts.append(f"Durée standard · {conditions.initial_duration_months} mois")
        elif conditions.initial_duration_mode == "CUSTOM" and end:
            summary_parts.append("Date de fin personnalisée")
        if conditions.signature_city.strip(): summary_parts.append(f"Signature à {conditions.signature_city.strip()}")
        summary = " · ".join(summary_parts) or "Période à compléter"
        return self._block(ReviewBlockId.PERIOD, summary, list(dict.fromkeys(issues)))

    def _intervention(self, conditions) -> ReviewBlockResult:
        issues = []
        if not conditions.included_area.strip(): issues.append("Renseignez la zone géographique incluse.")
        if not conditions.business_hours.strip(): issues.append("Renseignez les horaires habituels.")
        if conditions.travel_included is None: issues.append("Indiquez si les déplacements sont inclus.")
        travel = "déplacements inclus" if conditions.travel_included is True else (
            "déplacements non inclus" if conditions.travel_included is False else "déplacements à compléter")
        values = [conditions.included_area, conditions.business_hours, travel]
        if conditions.missed_appointment_fee is not None:
            values.append(f"Rendez-vous manqué : {_format_money_fr(Decimal(conditions.missed_appointment_fee))} €")
        if conditions.additional_exclusions.strip(): values.append(f"Exclusions : {conditions.additional_exclusions.strip()}")
        summary = " · ".join(value for value in values if value)
        return self._block(ReviewBlockId.INTERVENTION, summary or "Conditions à compléter", issues)

    def _price_payment(self, conditions, version) -> ReviewBlockResult:
        issues = []
        if conditions.annual_ht is None: issues.append("Renseignez le prix annuel HT.")
        catalogs = version.catalogs if version else None
        allowed_rates = {Decimal(value) for value in catalogs.vat_rates} if catalogs else set()
        try: rate_ok = conditions.vat_rate is not None and Decimal(conditions.vat_rate) in allowed_rates
        except (InvalidOperation, ValueError): rate_ok = False
        if not rate_ok: issues.append("Choisissez un taux de TVA configuré pour ce modèle.")
        try: vat, total = conditions.vat_amount, conditions.annual_ttc
        except (InvalidOperation, ValueError): vat = total = None
        if conditions.annual_ht is not None and (vat is None or total is None): issues.append("Le prix TTC ne peut pas être calculé.")
        terms = {item.code: item for item in catalogs.payment_terms} if catalogs else {}
        term = terms.get(conditions.payment_terms_code)
        if term is None: issues.append("Choisissez une modalité de paiement configurée.")
        else:
            if term.requires_day_count and conditions.payment_due_days is None: issues.append("Renseignez le délai de paiement.")
            if term.allows_custom_text and not conditions.payment_terms_custom_text.strip():
                issues.append("Précisez la modalité de paiement.")
        methods = {item.code for item in catalogs.payment_methods} if catalogs else set()
        if not conditions.payment_methods: issues.append("Sélectionnez au moins un moyen de paiement.")
        elif any(code not in methods for code in conditions.payment_methods): issues.append("Un moyen de paiement n’est plus configuré.")
        summary_parts = []
        if conditions.annual_ht is not None and vat is not None and total is not None:
            summary_parts.append(
                f"{_format_money_fr(Decimal(conditions.annual_ht))} € HT · TVA {conditions.vat_rate} % "
                f"({_format_money_fr(vat)} €) · {_format_money_fr(total)} € TTC"
            )
        if term:
            payment = term.label
            if term.requires_day_count and conditions.payment_due_days is not None:
                payment += f" · {conditions.payment_due_days} jours"
            if term.allows_custom_text and conditions.payment_terms_custom_text.strip():
                payment += f" · {conditions.payment_terms_custom_text.strip()}"
            summary_parts.append(payment)
        method_labels = {item.code: item.label for item in catalogs.payment_methods} if catalogs else {}
        if conditions.payment_methods:
            summary_parts.append("Moyens : " + ", ".join(method_labels.get(code, "Moyen non reconnu") for code in conditions.payment_methods))
        summary = " · ".join(summary_parts) or "Prix et paiement à compléter"
        return self._block(ReviewBlockId.PRICE_PAYMENT, summary, issues)

    def _renewal(self, conditions, version) -> ReviewBlockResult:
        issues = []
        mode = conditions.renewal_mode
        validation = version.validation if version and version.status is TemplateVersionStatus.AVAILABLE else None
        if mode not in {"NONE", "MANUAL", "TACIT"}: issues.append("Choisissez le mode de renouvellement.")
        if mode in {"MANUAL", "TACIT"}:
            if not conditions.renewal_period_months: issues.append("Renseignez la durée de renouvellement.")
            if conditions.renewal_price_rule not in {"FIXED", "NEW_PRICE_ON_RENEWAL"}:
                issues.append("Choisissez la règle de prix au renouvellement.")
            if conditions.internal_alert_days is None: issues.append("Renseignez l’alerte interne.")
        if mode == "TACIT":
            if validation and validation.requires_non_renewal_notice_days and conditions.non_renewal_notice_days is None:
                issues.append("Renseignez le préavis de non-renouvellement.")
            allowed = {item.code for item in version.catalogs.non_renewal_channels} if version else set()
            if validation and validation.requires_non_renewal_notice_channels and not conditions.non_renewal_notice_channels:
                issues.append("Sélectionnez un canal de non-renouvellement.")
            elif any(code not in allowed for code in conditions.non_renewal_notice_channels):
                issues.append("Un canal de non-renouvellement n’est plus configuré.")
        if validation and validation.requires_breach_cure_period_days and conditions.breach_cure_period_days is None:
            issues.append("Renseignez le délai de régularisation.")
        if conditions.renewal_price_rule == "INDEXED": issues.append("L’indexation n’est pas disponible en V1.")
        parts = [{"NONE": "Aucun renouvellement", "MANUAL": "Renouvellement manuel",
                  "TACIT": "Reconduction tacite"}.get(mode, "Renouvellement à compléter")]
        if mode in {"MANUAL", "TACIT"}:
            if conditions.renewal_period_months: parts.append(f"Période de {conditions.renewal_period_months} mois")
            price = {"FIXED": "Même prix", "NEW_PRICE_ON_RENEWAL": "Nouveau prix défini au renouvellement"}.get(
                conditions.renewal_price_rule
            )
            if price: parts.append(price)
            if conditions.internal_alert_days is not None:
                parts.append(f"Alerte interne {conditions.internal_alert_days} jours avant")
        if mode == "TACIT" and validation:
            if validation.requires_non_renewal_notice_days and conditions.non_renewal_notice_days is not None:
                parts.append(f"Préavis de non-renouvellement : {conditions.non_renewal_notice_days} jours")
            if validation.requires_non_renewal_notice_channels and conditions.non_renewal_notice_channels:
                labels = {item.code: item.label for item in version.catalogs.non_renewal_channels}
                parts.append("Canaux : " + ", ".join(labels.get(code, "Canal non reconnu") for code in conditions.non_renewal_notice_channels))
        ending = []
        if conditions.early_termination_reason_codes:
            labels = {item.code: item.label for item in version.catalogs.early_termination_reasons} if version else {}
            ending.append(", ".join(labels.get(code, "Motif non reconnu") for code in conditions.early_termination_reason_codes))
        if conditions.early_termination_custom_text.strip(): ending.append("précision complémentaire renseignée")
        if validation and validation.requires_breach_cure_period_days and conditions.breach_cure_period_days is not None:
            ending.append(f"délai de régularisation {conditions.breach_cure_period_days} jours")
        if ending: parts.append("Fin anticipée : " + " · ".join(ending))
        return self._block(ReviewBlockId.RENEWAL_END, " · ".join(parts), issues)

    def _special(self, conditions) -> ReviewBlockResult:
        summary = "Conditions particulières renseignées" if conditions.special_terms else "Aucune condition particulière"
        return self._block(ReviewBlockId.SPECIAL_TERMS, summary, [])

    @staticmethod
    def _model_compatible(contract, version) -> bool:
        return bool(version and version.status is TemplateVersionStatus.AVAILABLE and
                    version.contract_type_code == contract.type_code.value and contract.regime and
                    contract.regime.value in version.allowed_client_regimes)
