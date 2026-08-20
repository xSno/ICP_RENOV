let contractWorkspaceState;
let contractDrawer;
let contractDrawerTrigger;
let contractSaveFailure = '';

function closeContractDrawer() {
  document.querySelector('.contract-selector-overlay')?.remove();
  contractDrawer = null;
  const trigger = contractDrawerTrigger;
  contractDrawerTrigger = null;
  if (trigger && document.contains(trigger)) trigger.focus();
}

function contractMutation(invoke, preserveValues = false) {
  const save = document.querySelector('.contract-save-state');
  if (save) { save.textContent = 'Enregistrement…'; save.classList.remove('failed'); }
  invoke(result => {
    if (result?.ok) { contractSaveFailure = ''; closeContractDrawer(); return; }
    contractSaveFailure = result?.message || 'Les dernières modifications ne sont pas encore enregistrées.';
    const current = document.querySelector('.contract-save-state');
    if (current) { current.textContent = 'Non enregistré'; current.classList.add('failed'); }
    let notice = document.querySelector('.contract-persistence-error');
    if (!notice) {
      notice = document.createElement('div'); notice.className = 'contract-persistence-error';
      document.querySelector('.contract-workspace-body')?.prepend(notice);
    }
    notice.textContent = 'Les dernières modifications ne sont pas encore enregistrées. ' + contractSaveFailure;
    if (!preserveValues) closeContractDrawer();
  });
}

function openContractSelector(kind, trigger) {
  if (!contractWorkspaceState?.contract.editable) return;
  contractDrawerTrigger = trigger || document.activeElement;
  contractDrawer = { kind, search: '' };
  renderContractSelector(true);
}

function renderContractSelector(focus = false) {
  if (!contractDrawer) return;
  document.querySelector('.contract-selector-overlay')?.remove();
  const clientMode = contractDrawer.kind === 'client';
  const source = clientMode ? contractWorkspaceState.clients : contractWorkspaceState.sites;
  const query = contractDrawer.search.trim().toLocaleLowerCase('fr-FR');
  const rows = source.filter(item => !query || `${item.name} ${item.secondary || item.address || ''}`.toLocaleLowerCase('fr-FR').includes(query));
  const title = clientMode ? 'Changer le client' : 'Changer le site';
  const subtitle = clientMode ? 'Sélectionnez une fiche client active.' : 'Sélectionnez un site actif pour ce client.';
  const warning = clientMode
    ? 'Le site, les équipements sélectionnés et leurs observations contractuelles seront retirés du brouillon. Les fiches maîtres resteront inchangées.'
    : 'Les équipements sélectionnés et leurs observations contractuelles seront retirés du brouillon.';
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay contract-selector-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDrawer()"></div><aside class="client-drawer contract-selector-drawer" role="dialog" aria-modal="true" aria-labelledby="contract-selector-title"><header class="drawer-header"><div><span class="eyebrow">Contrat</span><h2 id="contract-selector-title">${title}</h2><p>${subtitle}</p></div><button class="drawer-close" aria-label="Fermer" onclick="closeContractDrawer()">×</button></header><div class="drawer-body"><div class="drawer-notice">${icon('info')}<span><strong>Effet du changement</strong><small>${esc(warning)}</small></span></div>${clientMode ? `<label class="search contract-selector-search">${icon('search')}<input value="${esc(contractDrawer.search)}" placeholder="Rechercher un client"></label>` : ''}<div class="contract-selector-list">${rows.map(item => `<button class="contract-selector-row" data-id="${item.id}"><span class="client-avatar">${esc(item.name.slice(0,2).toUpperCase())}</span><span><strong>${esc(item.name)}</strong><small>${esc(item.type || item.address || item.secondary || '')}</small>${item.secondary && item.type ? `<small>${esc(item.secondary)}</small>` : ''}</span>${icon('chevron')}</button>`).join('') || '<div class="empty-list">Aucune fiche active disponible.</div>'}</div></div><footer class="drawer-footer">${clientMode ? '<button class="button button-secondary contract-create-client">Créer un nouveau client</button><span class="drawer-footer-spacer"></span>' : ''}<button class="button button-secondary" onclick="closeContractDrawer()">Annuler</button></footer></aside>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.contract-selector-search input')?.addEventListener('input', event => { contractDrawer.search = event.target.value; renderContractSelector(); });
  overlay.querySelectorAll('.contract-selector-row').forEach(button => button.addEventListener('click', () => {
    const id = button.dataset.id;
    const cid = contractWorkspaceState.contract.id;
    contractMutation(callback => clientMode ? bridge.selectContractClient(cid, id, callback) : bridge.selectContractSite(cid, id, callback));
  }));
  overlay.querySelector('.contract-create-client')?.addEventListener('click', event => { closeContractDrawer(); openClientDrawer('contract-create', event.currentTarget); });
  if (focus) requestAnimationFrame(() => overlay.querySelector('input, .contract-selector-row, button')?.focus());
}

function openContractSiteCreator(trigger) {
  const state = contractWorkspaceState;
  if (!state?.contract.editable || !state.contract.client_id) return;
  entityDrawerTrigger = trigger || document.activeElement;
  entityDrawer = {
    kind: 'site', mode: 'contract-create', siteId: null, clientId: state.contract.client_id,
    ownerLabel: state.contract.client, clientAddress: { ...state.contract.client_address },
    payload: emptySitePayload(), error: null, saving: false,
  };
  renderEntityDrawer(true);
}

function openContractEquipmentCreator(trigger) {
  const state = contractWorkspaceState;
  if (!state?.contract.editable || !state.contract.site_id) return;
  entityDrawerTrigger = trigger || document.activeElement;
  entityDrawer = {
    kind: 'equipment', mode: 'contract-create', siteId: state.contract.site_id,
    equipmentId: null, ownerLabel: state.contract.site, referenced: false,
    payload: emptyEquipmentPayload(), error: null, saving: false,
  };
  renderEntityDrawer(true);
}

function openObservationDrawer(itemId, trigger) {
  const item = contractWorkspaceState.equipment.find(value => value.item_id === itemId);
  if (!item || !contractWorkspaceState.contract.editable) return;
  contractDrawerTrigger = trigger || document.activeElement;
  contractDrawer = { kind: 'observation', itemId, value: item.observation };
  document.querySelector('.contract-selector-overlay')?.remove();
  const overlay = document.createElement('div'); overlay.className = 'drawer-overlay contract-selector-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDrawer()"></div><aside class="client-drawer observation-drawer" role="dialog" aria-modal="true"><header class="drawer-header"><div><span class="eyebrow">Équipement du contrat</span><h2>Observation contractuelle</h2><p>${esc(item.name)}</p></div><button class="drawer-close" aria-label="Fermer" onclick="closeContractDrawer()">×</button></header><div class="drawer-body"><div class="drawer-notice">${icon('info')}<span><strong>Cette valeur appartient uniquement au contrat</strong><small>Les notes internes de la fiche maître ne sont ni affichées ni recopiées.</small></span></div><label class="drawer-field"><span>Observation contractuelle</span><textarea rows="7">${esc(item.observation)}</textarea></label></div><footer class="drawer-footer"><button class="button button-secondary" onclick="closeContractDrawer()">Annuler</button><button class="primary observation-save">Enregistrer</button></footer></aside>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.observation-save').addEventListener('click', () => {
    const value = overlay.querySelector('textarea').value;
    contractMutation(callback => bridge.updateContractEquipmentObservation(contractWorkspaceState.contract.id, itemId, value, callback), true);
  });
  requestAnimationFrame(() => overlay.querySelector('textarea')?.focus());
}

function saveContractSignatory() {
  const name = document.querySelector('#contract-signatory-name')?.value || '';
  const role = document.querySelector('#contract-signatory-role')?.value || '';
  contractMutation(callback => bridge.updateContractSignatory(contractWorkspaceState.contract.id, name, role, callback), true);
}

function toggleContractEquipment(id, selected) {
  contractMutation(callback => bridge.setContractEquipment(contractWorkspaceState.contract.id, id, selected, callback));
}

function moveContractEquipment(itemId, delta) {
  contractMutation(callback => bridge.moveContractEquipment(contractWorkspaceState.contract.id, itemId, delta, callback));
}

function setContractStep(step) {
  bridge.setContractStep(contractWorkspaceState.contract.id, step, result => {
    if (!result?.ok) contractSaveFailure = result?.message || 'Cette étape n’est pas disponible.';
  });
}

function updateContractRegime(regime) {
  contractMutation(callback => bridge.updateContractFramework(
    contractWorkspaceState.contract.id, { regime }, callback
  ));
}

function selectContractTemplateVersion(versionId) {
  contractMutation(callback => bridge.selectContractTemplateVersion(
    contractWorkspaceState.contract.id, versionId, callback
  ));
}

function updateContractContext() {
  const conclusion = document.querySelector('#contract-conclusion-mode')?.value || null;
  const early = document.querySelector('input[name="early-performance"]:checked');
  contractMutation(callback => bridge.updateContractFramework(
    contractWorkspaceState.contract.id,
    { conclusion_mode: conclusion, early_performance_requested: early ? early.value === 'true' : null },
    callback
  ), true);
}

function priorityDelayMarkup(value = '') {
  return `<label class="condition-field priority-delay-field"><span>Délai d’intervention</span><input id="priority-breakdown-delay" value="${esc(value)}" placeholder="Ex. Sous 48 heures"></label>`;
}

function syncPriorityDelay() {
  const host = document.querySelector('#priority-delay-host');
  if (!host) return;
  const included = document.querySelector('input[name="priority-breakdown"]:checked')?.value === 'true';
  const previous = document.querySelector('#priority-breakdown-delay')?.value || '';
  host.innerHTML = included ? priorityDelayMarkup(previous) : '';
}

function saveContractServiceOffer() {
  const visits = Number(document.querySelector('#contract-visits')?.value || 0);
  const refrigerant = document.querySelector('#contract-refrigerant')?.value || '';
  const priorityNode = document.querySelector('input[name="priority-breakdown"]:checked');
  const payload = {
    visits_per_year: visits,
    refrigerant_handling_mode: refrigerant,
    included_options: [...document.querySelectorAll('input[name="included-option"]:checked')].map(node => node.value),
    priority_breakdown: priorityNode ? priorityNode.value === 'true' : null,
    priority_breakdown_delay: document.querySelector('#priority-breakdown-delay')?.value || null,
  };
  contractMutation(callback => bridge.updateContractServiceOffer(
    contractWorkspaceState.contract.id, payload, callback
  ), true);
}

function periodDependentMarkup(mode, months, endDate) {
  if (mode === 'STANDARD') return `<label class="condition-field"><span>Durée standard (mois)</span><input id="contract-duration-months" type="number" min="1" step="1" value="${esc(months ?? '')}"></label><label class="condition-field calculated-field"><span>Date de fin</span><input id="contract-end-date" type="date" value="${esc(endDate || '')}" readonly><small>Calculée automatiquement</small></label>`;
  if (mode === 'CUSTOM') return `<label class="condition-field"><span>Date de fin</span><input id="contract-end-date" type="date" value="${esc(endDate || '')}"></label>`;
  return '';
}

function syncPeriodMode() {
  const host = document.querySelector('#period-dependent-fields');
  if (!host) return;
  const b2 = contractWorkspaceState.conditions_b2;
  const mode = document.querySelector('#contract-duration-mode')?.value || '';
  const months = document.querySelector('#contract-duration-months')?.value || b2.initial_duration_months || '';
  const endDate = document.querySelector('#contract-end-date')?.value || b2.initial_end_date || b2.resolved_end_date || '';
  host.innerHTML = periodDependentMarkup(mode, months, endDate);
}

function saveContractPeriod() {
  const mode = document.querySelector('#contract-duration-mode')?.value || null;
  const months = document.querySelector('#contract-duration-months')?.value;
  const endDate = document.querySelector('#contract-end-date')?.value || null;
  contractMutation(callback => bridge.updateContractPeriod(contractWorkspaceState.contract.id, {
    issue_date: document.querySelector('#contract-issue-date')?.value || null,
    start_date: document.querySelector('#contract-start-date')?.value || null,
    initial_duration_mode: mode,
    initial_duration_months: mode === 'STANDARD' && months ? Number(months) : null,
    initial_end_date: mode === 'CUSTOM' ? endDate : null,
    signature_city: document.querySelector('#contract-signature-city')?.value || '',
  }, callback), true);
}

function missedAppointmentMarkup(value = '') {
  return `<label class="condition-field"><span>Montant</span><input id="missed-appointment-fee" inputmode="decimal" value="${esc(value)}" placeholder="Ex. 45,00"></label>`;
}

function syncMissedAppointmentFee() {
  const host = document.querySelector('#missed-appointment-host');
  if (!host) return;
  const amount = document.querySelector('input[name="missed-appointment-mode"]:checked')?.value === 'AMOUNT';
  const previous = document.querySelector('#missed-appointment-fee')?.value || contractWorkspaceState.conditions_b2.missed_appointment_fee || '';
  host.innerHTML = amount ? missedAppointmentMarkup(previous) : '';
}

function saveContractInterventionConditions() {
  const travel = document.querySelector('input[name="travel-included"]:checked');
  const amountMode = document.querySelector('input[name="missed-appointment-mode"]:checked')?.value === 'AMOUNT';
  contractMutation(callback => bridge.updateContractInterventionConditions(contractWorkspaceState.contract.id, {
    included_area: document.querySelector('#contract-included-area')?.value || '',
    business_hours: document.querySelector('#contract-business-hours')?.value || '',
    travel_included: travel ? travel.value === 'true' : null,
    missed_appointment_fee: amountMode ? document.querySelector('#missed-appointment-fee')?.value || '' : null,
    additional_exclusions: document.querySelector('#contract-additional-exclusions')?.value || '',
  }, callback), true);
}

function paymentConditionalMarkup(code, dueDays, customText) {
  const term = contractWorkspaceState.conditions_b2.payment_terms.find(item => item.id === code);
  if (!term) return '';
  const due = term.requires_day_count ? `<label class="condition-field"><span>Échéance (jours)</span><input id="contract-payment-due-days" type="number" min="0" step="1" value="${esc(dueDays ?? '')}"></label>` : '';
  const custom = term.allows_custom_text ? `<label class="condition-field field-wide"><span>Précision de paiement</span><input id="contract-payment-custom" value="${esc(customText || '')}" maxlength="240"></label>` : '';
  return due + custom;
}

function syncPaymentTermFields() {
  const host = document.querySelector('#payment-conditional-fields');
  if (!host) return;
  const b2 = contractWorkspaceState.conditions_b2;
  const code = document.querySelector('#contract-payment-term')?.value || '';
  const due = document.querySelector('#contract-payment-due-days')?.value || b2.payment_due_days || '';
  const custom = document.querySelector('#contract-payment-custom')?.value || b2.payment_terms_custom_text || '';
  host.innerHTML = paymentConditionalMarkup(code, due, custom);
}

function saveContractPricing() {
  const due = document.querySelector('#contract-payment-due-days')?.value;
  contractMutation(callback => bridge.updateContractPricing(contractWorkspaceState.contract.id, {
    annual_ht: document.querySelector('#contract-annual-ht')?.value || null,
    vat_rate: document.querySelector('#contract-vat-rate')?.value || null,
    payment_terms_code: document.querySelector('#contract-payment-term')?.value || null,
    payment_due_days: due ? Number(due) : null,
    payment_terms_custom_text: document.querySelector('#contract-payment-custom')?.value || '',
    payment_methods: [...document.querySelectorAll('input[name="payment-method"]:checked')].map(node => node.value),
  }, callback), true);
}

function renderContractB2(state) {
  const b2 = state.conditions_b2;
  const editable = state.contract.editable;
  const durationMode = b2.initial_duration_mode || '';
  const missedMode = b2.missed_appointment_fee === null ? 'NONE' : 'AMOUNT';
  const paymentMethods = new Set(b2.payment_methods);
  const period = `<article class="contract-section conditions-b2-section period-section"><div class="contract-section-head"><div><span class="eyebrow">Période</span><h3>Dates et durée initiale</h3></div></div><div class="b2-form-grid"><label class="condition-field"><span>Date d’émission</span><input id="contract-issue-date" type="date" value="${esc(b2.issue_date || '')}" ${editable ? '' : 'disabled'}></label><label class="condition-field"><span>Date de prise d’effet</span><input id="contract-start-date" type="date" value="${esc(b2.start_date || '')}" ${editable ? '' : 'disabled'}></label><label class="condition-field"><span>Durée</span><select id="contract-duration-mode" ${editable ? '' : 'disabled'} onchange="syncPeriodMode()"><option value="">À confirmer</option><option value="STANDARD" ${durationMode === 'STANDARD' ? 'selected' : ''}>Durée standard</option><option value="CUSTOM" ${durationMode === 'CUSTOM' ? 'selected' : ''}>Durée personnalisée</option></select></label><div id="period-dependent-fields" class="dependent-grid">${periodDependentMarkup(durationMode, b2.initial_duration_months, durationMode === 'STANDARD' ? b2.resolved_end_date : b2.initial_end_date)}</div><label class="condition-field"><span>Ville de signature</span><input id="contract-signature-city" value="${esc(b2.signature_city || '')}" ${editable ? '' : 'disabled'}></label></div>${editable ? '<div class="conditions-actions"><button class="primary" onclick="saveContractPeriod()">Enregistrer la période</button></div>' : ''}</article>`;
  const intervention = `<article class="contract-section conditions-b2-section intervention-section"><div class="contract-section-head"><div><span class="eyebrow">Conditions d’intervention</span><h3>Périmètre d’intervention</h3></div></div><div class="b2-form-grid"><label class="condition-field"><span>Zone géographique incluse</span><input id="contract-included-area" value="${esc(b2.included_area || '')}" ${editable ? '' : 'disabled'}></label><label class="condition-field"><span>Horaires habituels</span><input id="contract-business-hours" value="${esc(b2.business_hours || '')}" ${editable ? '' : 'disabled'}></label><fieldset class="condition-choice"><legend>Conditions de déplacement</legend><label><input type="radio" name="travel-included" value="true" ${b2.travel_included === true ? 'checked' : ''} ${editable ? '' : 'disabled'}> Inclus</label><label><input type="radio" name="travel-included" value="false" ${b2.travel_included === false ? 'checked' : ''} ${editable ? '' : 'disabled'}> Facturés séparément</label></fieldset><fieldset class="condition-choice"><legend>Frais de rendez-vous non réalisable</legend><label><input type="radio" name="missed-appointment-mode" value="NONE" ${missedMode === 'NONE' ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="syncMissedAppointmentFee()"> Aucun</label><label><input type="radio" name="missed-appointment-mode" value="AMOUNT" ${missedMode === 'AMOUNT' ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="syncMissedAppointmentFee()"> Montant</label></fieldset><div id="missed-appointment-host">${missedMode === 'AMOUNT' ? missedAppointmentMarkup(b2.missed_appointment_fee) : ''}</div><label class="condition-field field-wide"><span>Exclusions complémentaires</span><textarea id="contract-additional-exclusions" rows="3" ${editable ? '' : 'disabled'}>${esc(b2.additional_exclusions || '')}</textarea></label></div>${editable ? '<div class="conditions-actions"><button class="primary" onclick="saveContractInterventionConditions()">Enregistrer les conditions d’intervention</button></div>' : ''}</article>`;
  let pricingBody = '<div class="conditions-state">Sélectionnez un modèle compatible pour configurer le prix et le paiement.</div>';
  if (b2.pricing_catalog_available) pricingBody = `<div class="b2-form-grid pricing-grid"><label class="condition-field"><span>Prix annuel HT</span><input id="contract-annual-ht" inputmode="decimal" value="${esc(b2.annual_ht || '')}" ${editable ? '' : 'disabled'}></label><label class="condition-field"><span>Taux de TVA</span><select id="contract-vat-rate" ${editable ? '' : 'disabled'}><option value="">À confirmer</option>${b2.vat_rates.map(rate => `<option value="${esc(rate)}" ${b2.vat_rate === rate ? 'selected' : ''}>${esc(rate.replace('.', ','))} %</option>`).join('')}</select></label><div class="calculated-money"><span>Montant TVA</span><strong>${b2.vat_amount ? `${esc(b2.vat_amount)} €` : 'À calculer'}</strong><small>Calculé automatiquement</small></div><div class="calculated-money"><span>Total TTC</span><strong>${b2.annual_ttc ? `${esc(b2.annual_ttc)} €` : 'À calculer'}</strong><small>Calculé automatiquement</small></div><label class="condition-field"><span>Modalité de paiement</span><select id="contract-payment-term" ${editable ? '' : 'disabled'} onchange="syncPaymentTermFields()"><option value="">À confirmer</option>${b2.payment_terms.map(item => `<option value="${item.id}" ${b2.payment_terms_code === item.id ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select></label><div id="payment-conditional-fields" class="dependent-grid">${paymentConditionalMarkup(b2.payment_terms_code, b2.payment_due_days, b2.payment_terms_custom_text)}</div><fieldset class="condition-choice field-wide payment-methods"><legend>Moyens de paiement</legend>${b2.payment_method_options.map(item => `<label><input type="checkbox" name="payment-method" value="${item.id}" ${paymentMethods.has(item.id) ? 'checked' : ''} ${editable ? '' : 'disabled'}> ${esc(item.label)}</label>`).join('') || '<span>Aucun moyen configuré</span>'}</fieldset></div>${editable ? '<div class="conditions-actions"><button class="primary" onclick="saveContractPricing()">Enregistrer le prix et le paiement</button></div>' : ''}`;
  const pricing = `<article class="contract-section conditions-b2-section pricing-section"><div class="contract-section-head"><div><span class="eyebrow">Prix & paiement</span><h3>Prix annuel et règlement</h3></div></div>${pricingBody}</article>`;
  return period + intervention + pricing;
}

function renderContractConditions(state) {
  const b1 = state.conditions_b1;
  const editable = state.contract.editable;
  const regimeCards = b1.regime_options.map(option => `<label class="regime-card ${b1.regime === option.id ? 'selected' : ''}"><input type="radio" name="contract-regime" value="${option.id}" ${b1.regime === option.id ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="updateContractRegime(this.value)"><span><strong>${esc(option.label)}</strong><small>Choix propre à ce contrat</small></span></label>`).join('');
  let modelContent = '';
  if (b1.model_state === 'REGIME_REQUIRED') {
    modelContent = '<div class="conditions-state">Confirmez d’abord le régime du contrat.</div>';
  } else if (b1.model_state === 'NO_MODEL') {
    modelContent = `<div class="conditions-state no-model-state"><strong>Aucun modèle disponible pour ce régime</strong><button class="button button-secondary" onclick="bridge.openContractModels()">Ouvrir Paramètres &gt; Modèles</button></div>`;
  } else {
    modelContent = `<div class="template-choice-list">${b1.templates.map(item => `<label class="template-choice ${b1.selected_template_version_id === item.id ? 'selected' : ''}"><input type="radio" name="contract-template" value="${item.id}" ${b1.selected_template_version_id === item.id ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="selectContractTemplateVersion(this.value)"><span><strong>${esc(item.name)}</strong><small>Version ${esc(item.version)}</small></span></label>`).join('')}</div>`;
  }
  const conclusion = b1.conclusion_required ? `<div class="framework-conditional"><label class="condition-field"><span>Mode de conclusion</span><select id="contract-conclusion-mode" ${editable ? '' : 'disabled'} onchange="updateContractContext()"><option value="">À confirmer</option>${b1.conclusion_options.map(item => `<option value="${item.id}" ${b1.conclusion_mode === item.id ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select></label>${b1.early_performance_visible ? `<fieldset class="condition-choice"><legend>Démarrage anticipé demandé</legend><label><input type="radio" name="early-performance" value="true" ${b1.early_performance_requested === true ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="updateContractContext()"> Oui</label><label><input type="radio" name="early-performance" value="false" ${b1.early_performance_requested === false ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="updateContractContext()"> Non</label></fieldset>` : ''}</div>` : '';
  const included = new Set(b1.included_options);
  const priority = b1.priority_breakdown;
  return `<section class="contract-step-panel conditions-step-panel"><header><span class="eyebrow">Étape 2 sur 4</span><h2>Conditions du contrat</h2><p>Configurez le cadre exact et l’offre de services du brouillon.</p></header><article class="contract-section framework-section"><div class="contract-section-head"><div><span class="eyebrow">Cadre du contrat</span><h3>Régime client</h3></div></div><div class="regime-grid">${regimeCards}</div>${conclusion}</article><article class="contract-section service-offer-section"><div class="contract-section-head"><div><span class="eyebrow">Modèle & prestations</span><h3>Modèle de contrat</h3></div></div>${modelContent}${b1.selected_template_compatible ? `<div class="base-service"><span>${icon('settings')}</span><span><strong>Entretien préventif</strong><small>Prestation de base incluse</small></span><b>Inclus</b></div><div class="service-grid"><label class="condition-field"><span>Visites par an</span><input id="contract-visits" type="number" min="1" step="1" value="${esc(b1.visits_per_year ?? '')}" ${editable ? '' : 'disabled'}></label><label class="condition-field"><span>Gestion des fluides frigorigènes</span><select id="contract-refrigerant" ${editable ? '' : 'disabled'}><option value="">À confirmer</option><option value="IN_HOUSE_AUTHORIZED" ${b1.refrigerant_handling_mode === 'IN_HOUSE_AUTHORIZED' ? 'selected' : ''}>Gestion en interne autorisée</option><option value="PARTNER" ${b1.refrigerant_handling_mode === 'PARTNER' ? 'selected' : ''}>Partenaire habilité</option><option value="EXCLUDED" ${b1.refrigerant_handling_mode === 'EXCLUDED' ? 'selected' : ''}>Exclue</option></select></label><fieldset class="condition-choice included-options"><legend>Options incluses</legend><label><input type="checkbox" name="included-option" value="DEEP_CLEANING" ${included.has('DEEP_CLEANING') ? 'checked' : ''} ${editable ? '' : 'disabled'}> Nettoyage approfondi</label><label><input type="checkbox" name="included-option" value="DISINFECTION" ${included.has('DISINFECTION') ? 'checked' : ''} ${editable ? '' : 'disabled'}> Désinfection</label></fieldset><fieldset class="condition-choice"><legend>Dépannage prioritaire</legend><label><input type="radio" name="priority-breakdown" value="false" ${priority === false ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="syncPriorityDelay()"> Non inclus</label><label><input type="radio" name="priority-breakdown" value="true" ${priority === true ? 'checked' : ''} ${editable ? '' : 'disabled'} onchange="syncPriorityDelay()"> Inclus</label></fieldset><div id="priority-delay-host">${priority === true ? priorityDelayMarkup(b1.priority_breakdown_delay) : ''}</div></div>${editable ? '<div class="conditions-actions"><button class="primary" onclick="saveContractServiceOffer()">Enregistrer les prestations</button></div>' : ''}` : ''}</article></section>`;
}

function workspaceSummaryRow(label, value, tone = '') {
  return `<div class="workspace-summary-row ${tone}"><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
}

function renderContractWorkspace(state) {
  contractWorkspaceState = state;
  clientsState = null;
  const c = state.contract;
  const backup = backupValue(state.backup);
  const selected = state.equipment.filter(item => item.selected);
  const equipmentRows = state.equipment.map(item => {
    const order = item.selected ? `<span class="equipment-position">N° ${item.position + 1}</span><button class="table-action" ${item.position === 0 ? 'disabled' : ''} onclick="moveContractEquipment('${item.item_id}',-1)">Monter</button><button class="table-action" ${item.position === selected.length - 1 ? 'disabled' : ''} onclick="moveContractEquipment('${item.item_id}',1)">Descendre</button><button class="button button-secondary observation-action" onclick="openObservationDrawer('${item.item_id}',this)">${item.observation ? 'Modifier l’observation' : 'Observation'}</button>` : '';
    return `<div class="contract-equipment-row ${item.selected ? 'selected' : ''}"><label><input type="checkbox" ${item.selected ? 'checked' : ''} ${!c.editable || (!item.available && !item.selected) ? 'disabled' : ''} onchange="toggleContractEquipment('${item.id}',this.checked)"><span>${icon('settings')}<span><strong>${esc(item.name)}</strong><small>${esc(item.location || 'Localisation non renseignée')}</small></span></span></label><div class="contract-equipment-actions">${order}</div></div>`;
  }).join('');
  const steps = ['Client, site & équipements', 'Conditions du contrat', 'Revue', 'Documents & suivi'];
  const summaryEquipment = state.summary.equipment.length ? state.summary.equipment.join(' · ') : 'Aucun sélectionné';
  const editClient = c.client_id ? 'Changer le client' : 'Choisir un client';
  const editSite = c.site_id ? 'Changer le site' : 'Choisir un site';
  const failure = contractSaveFailure ? `<div class="contract-persistence-error">Les dernières modifications ne sont pas encore enregistrées. ${esc(contractSaveFailure)}</div>` : '';
  document.querySelector('#app').innerHTML = `<div class="app"><aside class="side"><div class="brand"><span class="brand-icon">${icon('air')}</span><span>ICP Renov<br><small>Contrats d’entretien</small></span></div><div class="nav active" onclick="bridge.returnToContracts()">${icon('file')}Contrats</div><div class="nav" onclick="bridge.navigate('CLIENTS')">${icon('users')}Clients & installations</div><div class="nav" onclick="bridge.navigate('SETTINGS')">${icon('settings')}Paramètres</div><div class="side-bottom"><div class="local-state">${icon('laptop')}<b>Mode local</b><small>Données conservées uniquement sur ce poste.</small></div><div class="backup-state">${icon('backup')}<small>Sauvegarde</small><b>${esc(backup)}</b></div></div></aside><main class="main contract-main"><header class="contract-header"><button class="ghost contract-back" onclick="bridge.returnToContracts()">← Retour Contrats</button><div class="contract-heading"><span class="pill ${esc(c.status)}">${esc(c.status_label)}</span><h1>${esc(c.number)}</h1><p>${esc(c.client || 'Client à sélectionner')} · ${esc(c.site || 'Site à sélectionner')}</p></div><div class="contract-header-state"><span class="contract-save-state">${esc(c.saved_label)}</span></div></header><div class="contract-shell"><nav class="contract-step-rail" aria-label="Étapes du contrat">${steps.map((label,index) => `<button class="contract-step ${index === (state.active_step || 1) - 1 ? 'active' : ''}" ${index > 1 ? 'disabled' : `onclick="setContractStep(${index + 1})"`}><span>${index + 1}</span><span><strong>${label}</strong>${index === (state.active_step || 1) - 1 ? '<small>Étape en cours</small>' : ''}</span></button>`).join('')}</nav><div class="contract-workspace-body">${failure}<section class="contract-step-panel"><header><span class="eyebrow">Étape 1 sur 4</span><h2>Client, site & équipements</h2><p>Définissez le contexte exact repris dans ce contrat.</p></header><article class="contract-section"><div class="contract-section-head"><div><span class="eyebrow">Client du contrat</span><h3>${esc(c.client || 'Aucun client sélectionné')}</h3></div>${c.editable ? `<div><button class="button button-secondary" onclick="openContractSelector('client',this)">${editClient}</button><button class="ghost" onclick="openClientDrawer('contract-create',this)">Créer un nouveau client</button></div>` : ''}</div>${c.client_id ? `<div class="contract-signatory"><div><span class="eyebrow">Signataire pour ce contrat</span><p>Ces informations ne modifient pas la fiche maître.</p></div><label><span>Nom</span><input id="contract-signatory-name" value="${esc(c.signatory_name)}" ${c.editable ? '' : 'disabled'}></label><label><span>Fonction ou qualité</span><input id="contract-signatory-role" value="${esc(c.signatory_role)}" ${c.editable ? '' : 'disabled'}></label>${c.editable ? '<button class="button button-secondary" onclick="saveContractSignatory()">Enregistrer</button>' : ''}</div>` : '<div class="contract-empty">Choisissez ou créez un client pour continuer.</div>'}</article><article class="contract-section ${!c.client_id ? 'disabled-section' : ''}"><div class="contract-section-head"><div><span class="eyebrow">Site unique du contrat</span><h3>${esc(c.site || 'Aucun site sélectionné')}</h3></div>${c.editable && c.client_id ? `<div><button class="button button-secondary" onclick="openContractSelector('site',this)">${editSite}</button><button class="ghost" onclick="openContractSiteCreator(this)">Ajouter un site</button></div>` : ''}</div>${!c.client_id ? '<div class="contract-empty">Sélectionnez d’abord un client.</div>' : !c.site_id ? '<div class="contract-empty">Choisissez ou ajoutez un site.</div>' : ''}</article><article class="contract-section equipment-contract-section ${!c.site_id ? 'disabled-section' : ''}"><div class="contract-section-head"><div><span class="eyebrow">Équipements du site</span><h3>${selected.length} équipement(s) sélectionné(s)</h3></div>${c.editable && c.site_id ? `<button class="button button-secondary" onclick="openContractEquipmentCreator(this)">${icon('plus')}Ajouter un équipement</button>` : ''}</div>${c.site_id ? `<p class="order-copy">Ordre repris dans l’annexe du contrat</p>${equipmentRows || '<div class="contract-empty">Aucun équipement actif sur ce site.</div>'}${selected.length ? '' : '<div class="step-anomaly">Aucun équipement sélectionné. Le brouillon reste enregistré, mais la génération future sera bloquée.</div>'}` : '<div class="contract-empty">Sélectionnez d’abord un site.</div>'}</article></section></div><aside class="contract-summary"><span class="eyebrow">Résumé du contrat</span><h2>État actuel</h2><dl>${workspaceSummaryRow('Client',state.summary.client)}${workspaceSummaryRow('Signataire',state.summary.signatory)}${workspaceSummaryRow('Site',state.summary.site)}${workspaceSummaryRow('Équipements',summaryEquipment)}${workspaceSummaryRow('Régime',state.summary.regime)}${workspaceSummaryRow('Mode de conclusion',state.summary.conclusion)}${workspaceSummaryRow('Période',state.summary.period)}${workspaceSummaryRow('Prix',state.summary.price)}${workspaceSummaryRow('Renouvellement',state.summary.renewal)}${workspaceSummaryRow('Modèle / version',state.summary.template)}${workspaceSummaryRow('Complétude',state.summary.completion,state.summary.completion.includes('complète') ? 'complete' : 'warning')}</dl></aside></div></main></div>`;
  if ((state.active_step || 1) === 2) {
    const body = document.querySelector('.contract-workspace-body');
    body.innerHTML = failure + renderContractConditions(state);
    body.querySelector('.conditions-step-panel').insertAdjacentHTML('beforeend', renderContractB2(state));
  }
}

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && contractDrawer) closeContractDrawer();
});
