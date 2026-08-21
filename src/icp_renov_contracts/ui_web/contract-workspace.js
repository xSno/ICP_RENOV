let contractWorkspaceState;
let contractDrawer;
let contractDrawerTrigger;
let contractSaveFailure = '';
let contractGenerationRunning = false;
let contractDocumentsError = '';

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

function closeContractDocumentsModal() {
  document.querySelector('.documents-modal-overlay')?.remove();
}

function openContractFile(revision, kind) {
  contractDocumentsError = '';
  bridge.openContractDocument(contractWorkspaceState.contract.id, revision, kind, result => {
    if (!result?.ok) {
      contractDocumentsError = result?.message || 'Le fichier est introuvable dans le dossier de travail.';
      renderContractWorkspace(contractWorkspaceState);
    }
  });
}

function openCorrectionConfirmation() {
  if (!contractWorkspaceState?.documents_d1?.correction_allowed) return;
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay documents-modal-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDocumentsModal()"></div><section class="documents-modal" role="dialog" aria-modal="true" aria-labelledby="correction-modal-title"><header><span class="eyebrow">Correction du contrat</span><h2 id="correction-modal-title">Corriger le contrat</h2><p>Confirmez le retour du contrat à l’état Brouillon.</p></header><div class="documents-modal-body"><ul><li>Le contrat retournera à l’état Brouillon.</li><li>Chaque révision existante restera préservée.</li><li>Cette action ne crée aucune nouvelle révision.</li><li>Une nouvelle révision existera uniquement après une future génération réussie depuis la Revue.</li></ul><div class="documents-modal-error" hidden></div></div><footer><button class="button button-secondary" onclick="closeContractDocumentsModal()">Annuler</button><button class="primary correction-confirm-action">Corriger le contrat</button></footer></section>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.correction-confirm-action').addEventListener('click', () => {
    bridge.reopenContractForCorrection(contractWorkspaceState.contract.id, result => {
      if (result?.ok) closeContractDocumentsModal();
      else { const error = overlay.querySelector('.documents-modal-error'); error.hidden = false; error.textContent = result?.message || 'Le contrat ne peut pas être rouvert pour correction.'; }
    });
  });
  overlay.querySelector('.button-secondary')?.focus();
}

function openRecordSentModal() {
  const data = contractWorkspaceState?.documents_d1;
  if (!data?.send_allowed || !data?.revisions?.length) return;
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay documents-modal-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDocumentsModal()"></div><section class="documents-modal" role="dialog" aria-modal="true" aria-labelledby="sent-modal-title"><header><span class="eyebrow">Suivi du contrat</span><h2 id="sent-modal-title">Enregistrer l’envoi</h2><p>L’événement sera attaché uniquement à la révision choisie.</p></header><div class="documents-modal-body"><label><span>Révision envoyée</span><select id="sent-revision">${data.revisions.map(item => `<option value="${esc(item.revision)}">${esc(item.revision)} · ${esc(item.template_name)} · ${esc(item.template_version)}</option>`).join('')}</select></label><label><span>Date d’envoi</span><input id="sent-date" type="date" value="${esc(data.today)}" required></label><label><span>Note <small>Facultatif</small></span><textarea id="sent-note" rows="3" placeholder="Ex. transmise à la direction"></textarea></label><div class="documents-info-notice"><strong>Aucun message ne sera envoyé</strong><span>L’application enregistre une action réalisée hors du logiciel.</span></div><div class="documents-modal-error" hidden></div></div><footer><button class="button button-secondary" onclick="closeContractDocumentsModal()">Annuler</button><button class="primary sent-confirm-action">Enregistrer l’envoi</button></footer></section>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.sent-confirm-action').addEventListener('click', () => {
    const revision = overlay.querySelector('#sent-revision').value;
    const effectiveDate = overlay.querySelector('#sent-date').value;
    const note = overlay.querySelector('#sent-note').value;
    bridge.recordContractSent(contractWorkspaceState.contract.id, revision, effectiveDate, note, result => {
      if (result?.ok) closeContractDocumentsModal();
      else { const error = overlay.querySelector('.documents-modal-error'); error.hidden = false; error.textContent = result?.message || 'L’envoi ne peut pas être enregistré.'; }
    });
  });
  overlay.querySelector('.button-secondary')?.focus();
}

function selectSignedPdf(intent, host) {
  const state = contractWorkspaceState;
  if (!state) return;
  bridge.selectContractSignedPdf(state.contract.id, intent, result => {
    if (!result?.ok) {
      contractDocumentsError = result?.message || 'Le PDF signé ne peut pas être sélectionné.';
      renderContractWorkspace(contractWorkspaceState);
      return;
    }
    if (result.selected && host) {
      const target = host.querySelector?.('.signed-pdf-selection') || host.closest?.('.documents-modal')?.querySelector('.signed-pdf-selection');
      if (target) target.textContent = result.name || 'PDF sélectionné';
    }
  });
}

function openSignatureRecordingModal() {
  const state = contractWorkspaceState;
  const data = state?.documents_d2;
  const revisions = state?.documents_d1?.revisions || [];
  if (!data?.signature_allowed || !revisions.length) return;
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay documents-modal-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDocumentsModal()"></div><section class="documents-modal signature-modal" role="dialog" aria-modal="true" aria-labelledby="signature-modal-title"><header><span class="eyebrow">Signature hors application</span><h2 id="signature-modal-title">Enregistrer la signature</h2><p>Confirmez la révision réellement signée et la date effective. ICP Renov ne réalise pas de signature électronique.</p></header><div class="documents-modal-body"><label><span>Révision réellement signée</span><select id="signature-revision">${revisions.map(item => `<option value="${esc(item.revision)}">${esc(item.revision)} · ${esc(item.template_name)} · ${esc(item.template_version)}</option>`).join('')}</select></label><label><span>Date de signature</span><input id="signature-date" type="date" value="${esc(state.documents_d1.today)}" required></label><div class="signature-start-date"></div><div class="signed-pdf-picker"><span>PDF signé <small>Facultatif</small></span><div><button class="button button-secondary signature-pdf-select">Choisir le PDF signé</button><small class="signed-pdf-selection">Aucun PDF sélectionné</small></div></div><div class="documents-info-notice"><strong>Signature réalisée hors de l’application</strong><span>L’enregistrement verrouille les données structurées. La copie PDF signée reste facultative.</span></div><div class="documents-modal-error" hidden></div></div><footer><button class="button button-secondary" onclick="closeContractDocumentsModal()">Annuler</button><button class="primary signature-confirm-action">Enregistrer la signature</button></footer></section>`;
  document.body.appendChild(overlay);
  const refreshStart = () => {
    const revision = overlay.querySelector('#signature-revision').value;
    const selected = revisions.find(item => item.revision === revision);
    const target = overlay.querySelector('.signature-start-date');
    target.textContent = selected?.start_display ? `Prise d’effet prévue le ${selected.start_display}` : '';
  };
  overlay.querySelector('#signature-revision').addEventListener('change', refreshStart);
  overlay.querySelector('.signature-pdf-select').addEventListener('click', () => selectSignedPdf('SIGNATURE', overlay));
  overlay.querySelector('.signature-confirm-action').addEventListener('click', () => {
    const revision = overlay.querySelector('#signature-revision').value;
    const effectiveDate = overlay.querySelector('#signature-date').value;
    if (!effectiveDate) {
      const error = overlay.querySelector('.documents-modal-error'); error.hidden = false; error.textContent = 'Renseignez une date de signature valide.'; return;
    }
    bridge.recordContractSignature(state.contract.id, revision, effectiveDate, result => {
      if (result?.ok) closeContractDocumentsModal();
      else { const error = overlay.querySelector('.documents-modal-error'); error.hidden = false; error.textContent = result?.message || 'La signature ne peut pas être enregistrée.'; }
    });
  });
  refreshStart();
  overlay.querySelector('.button-secondary')?.focus();
}

function renewalRequestId() {
  return globalThis.crypto?.randomUUID?.() || `renewal-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function openLinkedRenewalModal() {
  const state = contractWorkspaceState;
  const data = state?.documents_d3;
  if (!data?.linked_draft_allowed) return;
  const manual = data.mode === 'MANUAL';
  const title = manual ? 'Préparer un nouveau contrat lié' : 'Créer un nouveau contrat lié';
  const action = manual ? 'Préparer le renouvellement' : 'Créer un nouveau contrat lié';
  const requestId = renewalRequestId();
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay documents-modal-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDocumentsModal()"></div><section class="documents-modal" role="dialog" aria-modal="true" aria-labelledby="linked-renewal-title"><header><span class="eyebrow">Contrat lié</span><h2 id="linked-renewal-title">${title}</h2><p>Un nouveau Brouillon sera ouvert pour préparer la prochaine période.</p></header><div class="documents-modal-body"><ul><li>Le contrat actuel reste inchangé, avec ses documents, signature et historique.</li><li>Le nouveau brouillon ne reçoit ni numéro officiel ni révision.</li><li>Les dates, équipements, régime et conditions doivent être revus avant toute génération.</li><li>Aucun document officiel n’est créé maintenant.</li></ul><div class="documents-modal-error" hidden></div></div><footer><button class="button button-secondary" onclick="closeContractDocumentsModal()">Annuler</button><button class="primary linked-renewal-confirm">${action}</button></footer></section>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.linked-renewal-confirm').addEventListener('click', event => {
    const button = event.currentTarget; button.disabled = true;
    bridge.prepareLinkedRenewal(state.contract.id, requestId, result => {
      if (result?.ok) closeContractDocumentsModal();
      else { button.disabled = false; const error = overlay.querySelector('.documents-modal-error'); error.hidden = false; error.textContent = result?.message || 'Le nouveau brouillon lié n’a pas été créé. Le contrat actuel reste inchangé.'; }
    });
  });
  overlay.querySelector('.button-secondary')?.focus();
}

function renewalPriceMarkup(price) {
  if (!price) return '<div class="documents-info-notice"><strong>Prix de la prochaine période</strong><span>Renseignez le prix HT et le taux de TVA pour obtenir le calcul contrôlé.</span></div>';
  return `<div class="documents-info-notice"><strong>Prix de la prochaine période</strong><span>${esc(price.annual_ht)} € HT · TVA ${esc(price.vat_rate)} % (${esc(price.vat_amount)} €) · ${esc(price.annual_ttc)} € TTC</span></div>`;
}

function openTacitRenewalModal() {
  const state = contractWorkspaceState;
  const data = state?.documents_d3?.tacit;
  if (!data?.confirmation_allowed) return;
  const isFixed = data.price_rule === 'FIXED';
  const pricing = isFixed
    ? renewalPriceMarkup({ annual_ht: data.current_annual_ht, vat_rate: data.current_vat_rate, vat_amount: data.current_vat_amount, annual_ttc: data.current_annual_ttc })
    : `<div class="renewal-price-inputs"><label><span>Nouveau prix annuel HT</span><input id="renewal-annual-ht" inputmode="decimal" placeholder="Ex. 1200,00"></label><label><span>Taux de TVA</span><select id="renewal-vat-rate"><option value="">Choisir</option>${data.vat_rates.map(rate => `<option value="${esc(rate)}">${esc(rate)} %</option>`).join('')}</select></label></div><div class="renewal-price-preview"></div>`;
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay documents-modal-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDocumentsModal()"></div><section class="documents-modal" role="dialog" aria-modal="true" aria-labelledby="tacit-renewal-title"><header><span class="eyebrow">Reconduction tacite</span><h2 id="tacit-renewal-title">Confirmer la reconduction</h2><p>La reconduction est enregistrée uniquement après votre confirmation explicite.</p></header><div class="documents-modal-body"><div class="renewal-period-summary"><span>Période en cours</span><strong>${esc(data.current_period_display)}</strong><span>Prochaine période · ${esc(String(data.renewal_months))} mois</span><strong>${esc(data.next_period_display)}</strong><span>Règle de prix</span><strong>${esc(data.price_rule_label)}</strong></div>${pricing}<div class="documents-info-notice"><strong>Aucun document n’est généré</strong><span>La confirmation enrichit uniquement l’historique du contrat existant.</span></div><div class="documents-modal-error" hidden></div></div><footer><button class="button button-secondary" onclick="closeContractDocumentsModal()">Annuler</button><button class="primary tacit-renewal-confirm">Confirmer la reconduction</button></footer></section>`;
  document.body.appendChild(overlay);
  const error = overlay.querySelector('.documents-modal-error');
  const refreshPrice = () => {
    if (isFixed) return;
    const annualHt = overlay.querySelector('#renewal-annual-ht').value;
    const vatRate = overlay.querySelector('#renewal-vat-rate').value;
    bridge.previewTacitRenewal(state.contract.id, annualHt, vatRate, result => {
      const target = overlay.querySelector('.renewal-price-preview');
      if (!result?.ok) { target.innerHTML = ''; return; }
      target.innerHTML = renewalPriceMarkup(result.price);
    });
  };
  overlay.querySelector('#renewal-annual-ht')?.addEventListener('input', refreshPrice);
  overlay.querySelector('#renewal-vat-rate')?.addEventListener('change', refreshPrice);
  overlay.querySelector('.tacit-renewal-confirm').addEventListener('click', () => {
    const annualHt = isFixed ? '' : overlay.querySelector('#renewal-annual-ht').value;
    const vatRate = isFixed ? '' : overlay.querySelector('#renewal-vat-rate').value;
    bridge.confirmTacitRenewal(state.contract.id, annualHt, vatRate, result => {
      if (result?.ok) closeContractDocumentsModal();
      else { error.hidden = false; error.textContent = result?.message || 'La reconduction n’a pas été enregistrée. Les données existantes sont conservées.'; }
    });
  });
  overlay.querySelector('.button-secondary')?.focus();
}

function publishSignedPdf(intent) {
  const state = contractWorkspaceState;
  if (!state) return;
  if (!['ADD', 'LOCATE', 'REPLACE'].includes(intent)) return;
  bridge.selectContractSignedPdf(state.contract.id, intent, result => {
    if (!result?.ok || !result.selected) {
      if (!result?.ok) { contractDocumentsError = result?.message || 'Le PDF signé ne peut pas être sélectionné.'; renderContractWorkspace(contractWorkspaceState); }
      return;
    }
    const savedHandler = saved => {
      if (!saved?.ok) { contractDocumentsError = saved?.message || 'Le PDF signé ne peut pas être archivé.'; renderContractWorkspace(contractWorkspaceState); }
    };
    if (intent === 'ADD') bridge.addContractSignedPdf(state.contract.id, savedHandler);
    else if (intent === 'LOCATE') bridge.locateContractSignedPdf(state.contract.id, savedHandler);
    else bridge.replaceContractSignedPdf(state.contract.id, savedHandler);
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

function renewalDependentMarkup(mode, values) {
  if (!['MANUAL', 'TACIT'].includes(mode)) return '';
  const b3 = contractWorkspaceState.conditions_b3;
  const selectedChannels = new Set(values.channels || []);
  const noticeDays = mode === 'TACIT' && b3.requires_non_renewal_notice_days ? `<label class="condition-field"><span>Préavis de non-renouvellement (jours)</span><input id="contract-non-renewal-notice-days" type="number" min="0" step="1" value="${esc(values.noticeDays ?? '')}"></label>` : '';
  const noticeChannels = mode === 'TACIT' && b3.requires_non_renewal_notice_channels ? `<fieldset class="condition-choice field-wide renewal-channels"><legend>Canaux de non-renouvellement</legend>${b3.non_renewal_channel_options.map(item => `<label><input type="checkbox" name="non-renewal-channel" value="${esc(item.id)}" ${selectedChannels.has(item.id) ? 'checked' : ''}> ${esc(item.label)}</label>`).join('') || '<span>Aucun canal contrôlé configuré</span>'}</fieldset>` : '';
  return `<div class="b3-form-grid"><label class="condition-field"><span>Durée de renouvellement (mois)</span><input id="contract-renewal-period" type="number" min="1" step="1" value="${esc(values.period ?? '')}"></label><label class="condition-field"><span>Prix au renouvellement</span><select id="contract-renewal-price-rule"><option value="">À confirmer</option>${b3.renewal_price_rules.map(item => `<option value="${item.id}" ${values.priceRule === item.id ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select></label><label class="condition-field internal-alert-field"><span>Alerte interne (jours)</span><input id="contract-internal-alert-days" type="number" min="0" step="1" value="${esc(values.alertDays ?? '')}"><small>Usage interne uniquement · distinct du préavis contractuel</small></label>${noticeDays}${noticeChannels}</div>`;
}

function syncRenewalFields() {
  const host = document.querySelector('#renewal-dependent-fields');
  if (!host) return;
  const b3 = contractWorkspaceState.conditions_b3;
  const mode = document.querySelector('#contract-renewal-mode')?.value || '';
  host.innerHTML = renewalDependentMarkup(mode, {
    period: document.querySelector('#contract-renewal-period')?.value || b3.renewal_period_months || '',
    noticeDays: document.querySelector('#contract-non-renewal-notice-days')?.value || b3.non_renewal_notice_days || '',
    channels: [...document.querySelectorAll('input[name="non-renewal-channel"]:checked')].map(node => node.value).length ? [...document.querySelectorAll('input[name="non-renewal-channel"]:checked')].map(node => node.value) : b3.non_renewal_notice_channels,
    alertDays: document.querySelector('#contract-internal-alert-days')?.value || b3.internal_alert_days || '',
    priceRule: document.querySelector('#contract-renewal-price-rule')?.value || b3.renewal_price_rule || '',
  });
}

function saveContractRenewal() {
  const mode = document.querySelector('#contract-renewal-mode')?.value || null;
  const integerValue = selector => {
    const value = document.querySelector(selector)?.value;
    return value === undefined || value === '' ? null : Number(value);
  };
  contractMutation(callback => bridge.updateContractRenewal(contractWorkspaceState.contract.id, {
    renewal_mode: mode,
    renewal_period_months: mode === 'MANUAL' || mode === 'TACIT' ? integerValue('#contract-renewal-period') : null,
    non_renewal_notice_days: integerValue('#contract-non-renewal-notice-days'),
    non_renewal_notice_channels: [...document.querySelectorAll('input[name="non-renewal-channel"]:checked')].map(node => node.value),
    internal_alert_days: mode === 'MANUAL' || mode === 'TACIT' ? integerValue('#contract-internal-alert-days') : null,
    renewal_price_rule: mode === 'MANUAL' || mode === 'TACIT' ? document.querySelector('#contract-renewal-price-rule')?.value || null : null,
  }, callback), true);
}

function openEarlyTerminationDrawer(trigger) {
  const state = contractWorkspaceState;
  if (!state?.contract.editable) return;
  contractDrawerTrigger = trigger || document.activeElement;
  contractDrawer = { kind: 'early-termination' };
  const b3 = state.conditions_b3;
  const selected = new Set(b3.early_termination_reason_codes);
  const cure = b3.breach_cure_period_required ? `<label class="drawer-field"><span>Délai de régularisation (jours)</span><input id="contract-breach-cure" type="number" min="0" step="1" value="${esc(b3.breach_cure_period_days ?? '')}"></label>` : '';
  document.querySelector('.contract-selector-overlay')?.remove();
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay contract-selector-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeContractDrawer()"></div><aside class="client-drawer early-termination-drawer" role="dialog" aria-modal="true" aria-labelledby="early-termination-title"><header class="drawer-header"><div><span class="eyebrow">Fin du contrat</span><h2 id="early-termination-title">Conditions de fin anticipée</h2><p>Ces informations modifient uniquement les conditions du brouillon.</p></div><button class="drawer-close" aria-label="Fermer" onclick="closeContractDrawer()">×</button></header><div class="drawer-body"><fieldset class="condition-choice early-reasons"><legend>Motifs prévus</legend>${b3.early_termination_reason_options.map(item => `<label><input type="checkbox" name="early-termination-reason" value="${esc(item.id)}" ${selected.has(item.id) ? 'checked' : ''}> ${esc(item.label)}</label>`).join('') || '<span>Aucun motif contrôlé configuré par ce modèle.</span>'}</fieldset><label class="drawer-field"><span>Motifs supplémentaires</span><textarea id="early-termination-custom" rows="6">${esc(b3.early_termination_custom_text || '')}</textarea><small>Texte brut limité aux précisions propres à ce contrat.</small></label>${cure}</div><footer class="drawer-footer"><button class="button button-secondary" onclick="closeContractDrawer()">Annuler</button><button class="primary" onclick="saveEarlyTermination()">Enregistrer</button></footer></aside>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.querySelector('input, textarea, button')?.focus());
}

function saveEarlyTermination() {
  const cure = document.querySelector('#contract-breach-cure')?.value;
  contractMutation(callback => bridge.updateContractEarlyTermination(contractWorkspaceState.contract.id, {
    early_termination_reason_codes: [...document.querySelectorAll('input[name="early-termination-reason"]:checked')].map(node => node.value),
    early_termination_custom_text: document.querySelector('#early-termination-custom')?.value || '',
    breach_cure_period_days: cure === undefined || cure === '' ? null : Number(cure),
  }, callback), true);
}

function saveContractSpecialTerms() {
  contractMutation(callback => bridge.updateContractSpecialTerms(contractWorkspaceState.contract.id, {
    special_terms: document.querySelector('#contract-special-terms')?.value || '',
  }, callback), true);
}

function renderContractB3(state) {
  const b3 = state.conditions_b3;
  const editable = state.contract.editable;
  const renewalValues = {
    period: b3.renewal_period_months, noticeDays: b3.non_renewal_notice_days,
    channels: b3.non_renewal_notice_channels, alertDays: b3.internal_alert_days,
    priceRule: b3.renewal_price_rule,
  };
  const renewal = `<article class="contract-section conditions-b3-section renewal-section"><div class="contract-section-head"><div><span class="eyebrow">Renouvellement</span><h3>Mode et conditions de renouvellement</h3></div></div><label class="condition-field renewal-mode-field"><span>Mode de renouvellement</span><select id="contract-renewal-mode" ${editable ? '' : 'disabled'} onchange="syncRenewalFields()"><option value="">À confirmer</option>${b3.renewal_modes.map(item => `<option value="${item.id}" ${b3.renewal_mode === item.id ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select></label><div id="renewal-dependent-fields">${renewalDependentMarkup(b3.renewal_mode, renewalValues)}</div>${editable ? '<div class="conditions-actions"><button class="primary" onclick="saveContractRenewal()">Enregistrer le renouvellement</button></div>' : ''}</article>`;
  const reasonCount = b3.early_termination_reason_codes.length;
  const reasons = reasonCount === 0 ? 'Aucun motif sélectionné' : reasonCount === 1 ? '1 motif sélectionné' : `${reasonCount} motifs sélectionnés`;
  const ending = `<article class="contract-section conditions-b3-section ending-section"><div class="contract-section-head"><div><span class="eyebrow">Fin du contrat / fin anticipée</span><h3>Conditions de fin anticipée</h3></div>${editable ? '<button class="button button-secondary" onclick="openEarlyTerminationDrawer(this)">Modifier les conditions de fin anticipée</button>' : ''}</div><div class="ending-summary"><strong>${esc(reasons)}</strong><small>${b3.early_termination_custom_text ? 'Une précision contractuelle est renseignée.' : 'Aucune précision contractuelle renseignée.'}</small></div></article>`;
  const special = `<article class="contract-section conditions-b3-section special-terms-section"><div class="contract-section-head"><div><span class="eyebrow">Conditions particulières</span><h3>Précisions propres au contrat</h3></div></div><label class="condition-field field-wide"><span>Conditions particulières</span><textarea id="contract-special-terms" rows="5" ${editable ? '' : 'disabled'}>${esc(b3.special_terms || '')}</textarea><small>Texte brut uniquement · les retours à la ligne sont conservés.</small></label>${editable ? '<div class="conditions-actions"><button class="primary" onclick="saveContractSpecialTerms()">Enregistrer les conditions particulières</button></div>' : ''}</article>`;
  return renewal + ending + special;
}

function openReviewBlock(blockId) {
  bridge.openContractReviewBlock(contractWorkspaceState.contract.id, blockId, result => {
    if (!result?.ok) contractSaveFailure = result?.message || 'Cette section n’est pas disponible.';
  });
}

function focusContractReviewTarget(state) {
  const target = state.contract_focus_target;
  if (!target) return;
  requestAnimationFrame(() => {
    const selectors = {
      'client-signatory': '[data-review-target="client-signatory"]',
      'site-equipment': '[data-review-target="site-equipment"]',
      'contract-context': '.framework-section',
      'model-services': '.service-offer-section',
      'period': '.period-section',
      'intervention': '.intervention-section',
      'price-payment': '.pricing-section',
      'renewal-end': '.renewal-section',
      'special-terms': '.special-terms-section',
    };
    const section = document.querySelector(selectors[target]);
    if (section) section.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
}

function closeGenerationConfirmation() {
  if (contractGenerationRunning) return;
  document.querySelector('.generation-modal-overlay')?.remove();
}

function confirmOfficialGeneration() {
  if (contractGenerationRunning || !contractWorkspaceState?.official_generation?.allowed) return;
  contractGenerationRunning = true;
  const confirm = document.querySelector('.generation-confirm-action');
  const cancel = document.querySelector('.generation-cancel-action');
  if (confirm) { confirm.disabled = true; confirm.textContent = 'Génération en cours…'; }
  if (cancel) cancel.disabled = true;
  bridge.generateOfficialContract(contractWorkspaceState.contract.id, result => {
    contractGenerationRunning = false;
    document.querySelector('.generation-modal-overlay')?.remove();
    if (!result?.ok && !contractWorkspaceState?.official_generation?.feedback) {
      contractSaveFailure = result?.message || 'La génération a été interrompue.';
    }
  });
}

function openGenerationConfirmation() {
  const generation = contractWorkspaceState?.official_generation;
  if (!generation?.allowed || contractGenerationRunning) return;
  document.querySelector('.generation-modal-overlay')?.remove();
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay generation-modal-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeGenerationConfirmation()"></div><section class="generation-modal" role="dialog" aria-modal="true" aria-labelledby="generation-modal-title"><header><span class="eyebrow">Génération officielle</span><h2 id="generation-modal-title">Créer ${esc(generation.next_revision)} ?</h2><p>Confirmez la création complète des documents officiels.</p></header><div class="generation-modal-body"><div class="generation-preview"><span>Numéro prévu</span><strong>${esc(generation.preview_number || 'Attribué après succès complet')}</strong><span>Nouvelle révision</span><strong>${esc(generation.next_revision)}</strong></div><ul><li>Les données actuelles seront figées dans une nouvelle révision.</li><li>Le DOCX et le PDF seront générés.</li><li>Le numéro officiel et ${esc(generation.next_revision)} seront attribués uniquement après le succès complet.</li><li>Le contrat passera à « À signer » uniquement après le succès complet.</li></ul><div class="generation-failure-guarantee"><strong>Si la génération échoue</strong><span>Aucune nouvelle révision ne sera créée, aucun premier numéro officiel ne sera consommé, les révisions existantes resteront inchangées et les données du brouillon seront conservées.</span></div></div><footer><button class="button button-secondary generation-cancel-action" onclick="closeGenerationConfirmation()">Annuler</button><button class="primary generation-confirm-action" onclick="confirmOfficialGeneration()">Générer le DOCX et le PDF</button></footer></section>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.generation-cancel-action')?.focus();
}

function renderContractDocuments(state) {
  const data = state.documents_d1;
  const signature = state.documents_d2 || {};
  const feedback = data.feedback ? `<div class="documents-feedback ${esc(data.feedback.kind)}">${esc(data.feedback.message)}</div>` : '';
  const error = contractDocumentsError ? `<div class="documents-feedback error">${esc(contractDocumentsError)}</div>` : '';
  const revisions = data.revisions.map(item => {
    const statePill = item.latest ? '<span class="revision-pill latest">Dernière révision</span>' : '<span class="revision-pill replaced">Révision précédente</span>';
    const sent = item.sent_display ? `<span class="revision-sent">Envoyée le ${esc(item.sent_display)}</span>` : '<span class="revision-unsent">Non envoyée</span>';
    const docx = item.docx_available ? `<button class="button button-secondary" onclick="openContractFile('${esc(item.revision)}','docx')">Ouvrir le DOCX</button>` : '<span class="revision-file-missing">DOCX introuvable</span>';
    const pdf = item.pdf_available ? `<button class="button button-secondary" onclick="openContractFile('${esc(item.revision)}','pdf')">Ouvrir le PDF</button>` : '<span class="revision-file-missing">PDF introuvable</span>';
    const signedMarker = item.signed ? '<span class="revision-pill signed">Révision signée</span>' : '';
    const signedDate = item.signed_display ? `<span class="revision-signed-date">Signée le ${esc(item.signed_display)}</span>` : '';
    let signedCopy = '';
    if (item.signed_copy_state === 'NONE') signedCopy = '<div class="signed-copy-notice"><strong>Signature enregistrée — copie signée non archivée</strong><button class="button button-secondary" onclick="publishSignedPdf(\'ADD\')">Ajouter le PDF signé</button></div>';
    else if (item.signed_copy_state === 'VALID') signedCopy = `<div class="signed-copy-valid"><span>Copie signée archivée${item.signed_copy_attached_display ? ` le ${esc(item.signed_copy_attached_display)}` : ''}</span><button class="button button-secondary" onclick="openContractFile('${esc(item.revision)}','signed')">Ouvrir le PDF signé</button></div>`;
    else if (item.signed_copy_state === 'MISSING' || item.signed_copy_state === 'HASH_MISMATCH') {
      const message = item.signed_copy_state === 'MISSING' ? 'Copie signée introuvable dans le dossier de travail' : 'La copie signée ne correspond plus au fichier archivé.';
      signedCopy = `<div class="signed-copy-missing"><strong>${message}</strong><div><button class="button button-secondary" onclick="publishSignedPdf('LOCATE')">Localiser le fichier</button><button class="button button-secondary" onclick="publishSignedPdf('REPLACE')">Ajouter une nouvelle copie</button></div></div>`;
    }
    const anomaly = item.docx_available && item.pdf_available ? '' : '<p class="revision-anomaly">La révision reste conservée dans l’historique. Le fichier manquant n’a pas été recréé.</p>';
    return `<article class="revision-card"><header><div><span class="revision-number">${esc(item.revision)}</span>${statePill}${signedMarker}</div>${sent}</header><div class="revision-metadata"><span>Générée le <strong>${esc(item.generated_display)}</strong></span><span>Modèle <strong>${esc(item.template_name)} · ${esc(item.template_version)}</strong></span>${signedDate}</div><div class="revision-actions">${docx}${pdf}</div>${signedCopy}${anomaly}</article>`;
  }).join('');
  const timeline = data.timeline.map(item => `<li><span class="timeline-dot"></span><div><strong>${esc(item.label)}${item.revision ? ` · ${esc(item.revision)}` : ''}</strong><small>${esc(item.effective_display || item.occurred_display)}</small>${item.note ? `<p>${esc(item.note)}</p>` : ''}</div></li>`).join('');
  const correction = data.correction_allowed ? `<section class="correction-zone"><div><span class="eyebrow">Contrat à signer</span><h3>Une correction est nécessaire ?</h3><p>Les données structurées restent verrouillées tant que le contrat n’est pas explicitement rouvert.</p></div><button class="button button-secondary" onclick="openCorrectionConfirmation()">Corriger le contrat</button></section>` : '';
  const renewal = state.documents_d3 || {};
  const linkedRenewal = renewal.linked_draft_allowed ? `<section class="correction-zone renewal-zone"><div><span class="eyebrow">${renewal.mode === 'MANUAL' ? 'Renouvellement manuel' : 'Contrat lié'}</span><h3>${esc(renewal.linked_draft_label)}</h3><p>Le contrat actuel reste inchangé. Le nouveau brouillon devra être revu avant toute génération.</p></div><button class="button button-secondary" onclick="openLinkedRenewalModal()">${esc(renewal.linked_draft_label)}</button></section>` : '';
  const tacitRenewal = renewal.tacit?.confirmation_allowed ? `<section class="correction-zone renewal-zone"><div><span class="eyebrow">Reconduction tacite</span><h3>Reconduction à confirmer</h3><p>${esc(renewal.tacit.current_period_display)} · prochaine période ${esc(renewal.tacit.next_period_display)}</p></div><button class="primary" onclick="openTacitRenewalModal()">Confirmer la reconduction</button></section>` : '';
  const sendAction = data.send_allowed ? '<button class="primary" onclick="openRecordSentModal()">Enregistrer un envoi</button>' : '';
  const signatureAction = signature.signature_allowed ? '<button class="primary" onclick="openSignatureRecordingModal()">Enregistrer la signature</button>' : '';
  return `<section class="contract-step-panel contract-documents-panel"><header><span class="eyebrow">Étape 4 sur 4</span><h2>Documents & suivi</h2><p>Consultez les révisions officielles et enregistrez les actions réalisées hors du logiciel.</p></header>${feedback}${error}<section class="documents-section"><div class="documents-section-head"><div><span class="eyebrow">Révisions contractuelles</span><h3>Documents officiels</h3></div><div class="documents-primary-actions">${sendAction}${signatureAction}</div></div><div class="revision-list">${revisions || '<div class="contract-empty">Aucune révision contractuelle disponible.</div>'}</div></section>${correction}${linkedRenewal}${tacitRenewal}<section class="documents-section other-documents"><div><span class="eyebrow">Autres documents</span><h3>Documents distincts du contrat</h3></div><p>Aucun autre document disponible.</p></section><section class="documents-section history-section"><div><span class="eyebrow">Historique</span><h3>Événements du contrat</h3></div><ol class="contract-timeline">${timeline || '<li class="timeline-empty">Aucun événement disponible.</li>'}</ol></section></section>`;
}

function renderContractReview(state) {
  const review = state.review;
  const generation = state.official_generation || {};
  const completeWithoutPendingFailure = review.data_complete && !contractSaveFailure;
  const overall = !state.contract.editable
    ? '<div class="review-business-state valid"><strong>Révision officielle créée</strong><span>La Revue est en consultation. Toute correction doit être ouverte depuis Documents & suivi.</span></div>'
    : completeWithoutPendingFailure
    ? '<div class="review-business-state valid"><strong>Toutes les informations nécessaires sont complètes</strong><span>Les données métier du contrat peuvent être préparées pour une future génération.</span></div>'
    : `<div class="review-business-state error"><strong>${contractSaveFailure ? 'Des modifications ne sont pas encore enregistrées' : 'Informations à compléter avant génération'}</strong><span>${contractSaveFailure ? 'Enregistrez les dernières modifications avant de considérer la revue comme complète.' : 'Corrigez les éléments signalés dans les blocs ci-dessous.'}</span></div>`;
  const blocks = review.blocks.map(block => {
    const valid = block.state === 'VALID';
    const issues = block.issues.map(issue => `<li>${esc(issue)}</li>`).join('');
    const action = state.contract.editable ? `<button class="button button-secondary" onclick="openReviewBlock('${esc(block.id)}')">Modifier</button>` : '';
    return `<article class="review-block ${valid ? 'valid' : 'error'}"><header><span class="review-state">${valid ? 'Valide' : 'À corriger'}</span><h3>${esc(block.title)}</h3>${action}</header><p>${esc(block.summary)}</p>${issues ? `<ul>${issues}</ul>` : ''}</article>`;
  }).join('');
  const failedChecks = review.generation_checks.filter(check => !check.available);
  let distinction = 'Les informations du contrat et les capacités de génération sont disponibles.';
  if (!review.data_complete) distinction = 'La disponibilité documentaire reste distincte des informations métier à compléter.';
  else if (failedChecks.length) {
    const onlyPdf = failedChecks.length === 1 && failedChecks[0].key === 'PDF';
    distinction = onlyPdf
      ? 'Le contrat est complet, mais la génération PDF est indisponible.'
      : 'Le contrat est complet, mais la génération est indisponible sur ce poste.';
  }
  const checks = review.generation_checks.map(check => `<div class="review-generation-check ${check.available ? 'available' : 'unavailable'}"><span>${esc(check.label)}</span><strong>${esc(check.detail)}</strong></div>`).join('');
  const feedback = generation.feedback ? `<div class="generation-result ${generation.feedback.kind}"><strong>${esc(generation.feedback.title || (generation.feedback.kind === 'success' ? 'Génération terminée' : 'Génération interrompue'))}</strong><span>${esc(generation.feedback.message)}</span>${generation.feedback.guarantee ? `<small>${esc(generation.feedback.guarantee)}</small>` : ''}${generation.feedback.kind === 'success' ? `<small>${esc(generation.feedback.contract_number)} · ${esc(generation.feedback.revision)} · À signer</small>` : ''}</div>` : '';
  const numberContext = generation.allowed ? `<div class="generation-number-context"><span>Numéro prévu</span><strong>${esc(generation.preview_number || 'Attribué après succès complet')}</strong><span>Prochaine révision</span><strong>${esc(generation.next_revision)}</strong></div>` : '';
  const disabled = !generation.allowed || contractGenerationRunning || Boolean(contractSaveFailure);
  const generationPanel = state.contract.editable ? `<section class="review-generation"><div><span class="eyebrow">Disponibilité de la génération</span><h3>Préparation documentaire</h3><p>${esc(distinction)}</p></div><div class="review-generation-checks">${checks}</div>${numberContext}<button class="primary review-generation-action" ${disabled ? 'disabled' : ''} onclick="openGenerationConfirmation()">${contractGenerationRunning ? 'Génération en cours…' : 'Générer le DOCX et le PDF'}</button></section>` : `<section class="review-generation review-consultation"><div><span class="eyebrow">Consultation</span><h3>Révision officielle créée</h3><p>Les données sont en lecture seule. Utilisez Documents & suivi pour consulter les fichiers ou corriger le contrat.</p></div><button class="button button-secondary" onclick="setContractStep(4)">Ouvrir Documents & suivi</button></section>`;
  return `<section class="contract-step-panel contract-review-panel"><header><span class="eyebrow">Étape 3 sur 4</span><h2>Revue</h2><p>Vérifiez les informations qui seront figées et la disponibilité de la génération.</p></header>${feedback}<section class="review-level"><span class="eyebrow">Données du contrat</span>${overall}</section><div class="review-block-grid">${blocks}</div>${generationPanel}</section>`;
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
  if (label === 'Mode de conclusion' && !contractWorkspaceState?.conditions_b1?.conclusion_required) return '';
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
  document.querySelector('#app').innerHTML = `<div class="app"><aside class="side"><div class="brand"><span class="brand-icon">${icon('air')}</span><span>ICP Renov<br><small>Contrats d’entretien</small></span></div><div class="nav active" onclick="bridge.returnToContracts()">${icon('file')}Contrats</div><div class="nav" onclick="bridge.navigate('CLIENTS')">${icon('users')}Clients & installations</div><div class="nav" onclick="bridge.navigate('SETTINGS')">${icon('settings')}Paramètres</div><div class="side-bottom"><div class="local-state">${icon('laptop')}<b>Mode local</b><small>Données conservées uniquement sur ce poste.</small></div><div class="backup-state">${icon('backup')}<small>Sauvegarde</small><b>${esc(backup)}</b></div></div></aside><main class="main contract-main"><header class="contract-header"><button class="ghost contract-back" onclick="bridge.returnToContracts()">← Retour Contrats</button><div class="contract-heading"><span class="pill ${esc(c.status)}">${esc(c.status_label)}</span><h1>${esc(c.number)}</h1><p>${esc(c.client || 'Client à sélectionner')} · ${esc(c.site || 'Site à sélectionner')}</p></div><div class="contract-header-state"><span class="contract-save-state">${esc(c.saved_label)}</span></div></header><div class="contract-shell"><nav class="contract-step-rail" aria-label="Étapes du contrat">${steps.map((label,index) => `<button class="contract-step ${index === (state.active_step || 1) - 1 ? 'active' : ''}" ${index > 2 ? 'disabled' : `onclick="setContractStep(${index + 1})"`}><span>${index + 1}</span><span><strong>${label}</strong>${index === (state.active_step || 1) - 1 ? '<small>Étape en cours</small>' : ''}</span></button>`).join('')}</nav><div class="contract-workspace-body">${failure}<section class="contract-step-panel"><header><span class="eyebrow">Étape 1 sur 4</span><h2>Client, site & équipements</h2><p>Définissez le contexte exact repris dans ce contrat.</p></header><article class="contract-section" data-review-target="client-signatory"><div class="contract-section-head"><div><span class="eyebrow">Client du contrat</span><h3>${esc(c.client || 'Aucun client sélectionné')}</h3></div>${c.editable ? `<div><button class="button button-secondary" onclick="openContractSelector('client',this)">${editClient}</button><button class="ghost" onclick="openClientDrawer('contract-create',this)">Créer un nouveau client</button></div>` : ''}</div>${c.client_id ? `<div class="contract-signatory"><div><span class="eyebrow">Signataire pour ce contrat</span><p>Ces informations ne modifient pas la fiche maître.</p></div><label><span>Nom</span><input id="contract-signatory-name" value="${esc(c.signatory_name)}" ${c.editable ? '' : 'disabled'}></label><label><span>Fonction ou qualité</span><input id="contract-signatory-role" value="${esc(c.signatory_role)}" ${c.editable ? '' : 'disabled'}></label>${c.editable ? '<button class="button button-secondary" onclick="saveContractSignatory()">Enregistrer</button>' : ''}</div>` : '<div class="contract-empty">Choisissez ou créez un client pour continuer.</div>'}</article><article class="contract-section ${!c.client_id ? 'disabled-section' : ''}" data-review-target="site-equipment"><div class="contract-section-head"><div><span class="eyebrow">Site unique du contrat</span><h3>${esc(c.site || 'Aucun site sélectionné')}</h3></div>${c.editable && c.client_id ? `<div><button class="button button-secondary" onclick="openContractSelector('site',this)">${editSite}</button><button class="ghost" onclick="openContractSiteCreator(this)">Ajouter un site</button></div>` : ''}</div>${!c.client_id ? '<div class="contract-empty">Sélectionnez d’abord un client.</div>' : !c.site_id ? '<div class="contract-empty">Choisissez ou ajoutez un site.</div>' : ''}</article><article class="contract-section equipment-contract-section ${!c.site_id ? 'disabled-section' : ''}"><div class="contract-section-head"><div><span class="eyebrow">Équipements du site</span><h3>${selected.length} équipement(s) sélectionné(s)</h3></div>${c.editable && c.site_id ? `<button class="button button-secondary" onclick="openContractEquipmentCreator(this)">${icon('plus')}Ajouter un équipement</button>` : ''}</div>${c.site_id ? `<p class="order-copy">Ordre repris dans l’annexe du contrat</p>${equipmentRows || '<div class="contract-empty">Aucun équipement actif sur ce site.</div>'}${selected.length ? '' : '<div class="step-anomaly">Aucun équipement sélectionné. Le brouillon reste enregistré, mais la génération future sera bloquée.</div>'}` : '<div class="contract-empty">Sélectionnez d’abord un site.</div>'}</article></section></div><aside class="contract-summary"><span class="eyebrow">Résumé du contrat</span><h2>État actuel</h2><dl>${workspaceSummaryRow('Client',state.summary.client)}${workspaceSummaryRow('Signataire',state.summary.signatory)}${workspaceSummaryRow('Site',state.summary.site)}${workspaceSummaryRow('Équipements',summaryEquipment)}${workspaceSummaryRow('Régime',state.summary.regime)}${workspaceSummaryRow('Mode de conclusion',state.summary.conclusion)}${workspaceSummaryRow('Période',state.summary.period)}${workspaceSummaryRow('Prix',state.summary.price)}${workspaceSummaryRow('Renouvellement',state.summary.renewal)}${workspaceSummaryRow('Modèle / version',state.summary.template)}${workspaceSummaryRow('Complétude',state.summary.completion,state.summary.completion.includes('complète') ? 'complete' : 'warning')}</dl></aside></div></main></div>`;
  const documentsStep = document.querySelectorAll('.contract-step')[3];
  if (documentsStep && state.documents_d1.available) {
    documentsStep.disabled = false;
    documentsStep.onclick = () => setContractStep(4);
  }
  if ((state.active_step || 1) === 2) {
    const body = document.querySelector('.contract-workspace-body');
    body.innerHTML = failure + renderContractConditions(state);
    body.querySelector('.conditions-step-panel').insertAdjacentHTML('beforeend', renderContractB2(state));
    body.querySelector('.conditions-step-panel').insertAdjacentHTML('beforeend', renderContractB3(state));
  } else if ((state.active_step || 1) === 3) {
    document.querySelector('.contract-workspace-body').innerHTML = failure + renderContractReview(state);
  } else if ((state.active_step || 1) === 4) {
    document.querySelector('.contract-workspace-body').innerHTML = failure + renderContractDocuments(state);
  }
  focusContractReviewTarget(state);
}

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && contractDrawer) closeContractDrawer();
  if (event.key === 'Escape') closeGenerationConfirmation();
  if (event.key === 'Escape') closeContractDocumentsModal();
});
