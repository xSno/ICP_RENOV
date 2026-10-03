let bridge;
let clientsState;
let clientDrawer;
let clientDrawerTrigger;
let entityDrawer;
let entityDrawerTrigger;
let siteMenu;
let archiveDialog;

const paths = {
  air: 'M3 12h18M6 8h12M6 16h12',
  laptop: 'M20 16V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v10m-2 4h20',
  backup: 'M3 5c0 1.1 4 2 9 2s9-.9 9-2-4-2-9-2-9 .9-9 2zm0 0v6c0 1.1 4 2 9 2 .8 0 1.6 0 2.3-.1M3 11v6c0 1.1 4 2 9 2 2.1 0 4-.2 5.5-.6M19 16v-5m0 0-2 2m2-2 2 2',
  file: 'M6 2h8l4 4v16H6zM14 2v5h5',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  settings: 'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2 2-.06-.06A1.7 1.7 0 0 0 15.86 19l-.86.5V22h-3v-2.5l-.86-.5a1.7 1.7 0 0 0-1.88-.12l-.06.06-2-2 .06-.06A1.7 1.7 0 0 0 7.4 15L7 14.14H4.5v-3H7l.4-.86a1.7 1.7 0 0 0-.14-1.88L7.2 8.34l2-2 .06.06a1.7 1.7 0 0 0 1.88.14L12 6V3.5h3V6l.86.54a1.7 1.7 0 0 0 1.88-.14l.06-.06 2 2-.06.06a1.7 1.7 0 0 0-.14 1.88l.4.86h2.5v3H20z',
  plus: 'M12 5v14M5 12h14', bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4',
  search: 'm21 21-4.35-4.35M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0', chevron: 'm9 18 6-6-6-6',
  pin: 'M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0zm-8 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6',
  info: 'M12 16v-4m0-4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  pencil: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  archive: 'M4 7h16v14H4zM2 3h20v4H2zM9 11h6',
  restore: 'M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5',
};

function icon(name) {
  return `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[name] || paths.file}"/></svg>`;
}
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); }
function updateLine(value) {
  const displayValue = String(value ?? '').trim();
  return displayValue && displayValue !== 'Dernière mise à jour' ? `<small>Mis à jour ${esc(displayValue)}</small>` : '';
}
function backupValue(summary) {
  const value = String(summary ?? '').replace(/^Sauvegarde\s*/i, '').trim();
  return !value || value.toLocaleLowerCase('fr-FR') === 'non configurée' ? 'Non configurée' : value;
}

function actionCountLabel(value) {
  const count = Number(value);
  return `${count} ${count === 1 ? 'action' : 'actions'} à traiter`;
}

const emptyClientPayload = () => ({
  party_type: 'PERSON', first_name: '', last_name: '', organization_name: '', legal_form: '',
  siret: '', address_line1: '', address_line2: '', postal_code: '', city: '', country: 'France',
  billing_address: '', phone: '', email: '', internal_reference: '', internal_notes: '',
});

function drawerField(name, label, options = {}) {
  const value = clientDrawer.payload[name] || '';
  const required = options.required ? ' required' : '';
  const wide = options.wide ? ' field-wide' : '';
  const control = options.multiline
    ? `<textarea name="${name}" rows="3"${required}>${esc(value)}</textarea>`
    : `<input name="${name}" value="${esc(value)}"${required}>`;
  return `<label class="drawer-field${wide}"><span>${esc(label)}${options.required ? ' <b>*</b>' : ''}</span>${control}</label>`;
}

function collectClientPayload() {
  const form = document.querySelector('#client-drawer-form');
  if (!form) return clientDrawer.payload;
  const payload = { ...clientDrawer.payload };
  new FormData(form).forEach((value, key) => { payload[key] = String(value); });
  return payload;
}

function openClientDrawer(mode, trigger) {
  if (!clientsState && mode !== 'contract-create') return;
  clientDrawerTrigger = trigger || document.activeElement;
  clientDrawer = {
    mode,
    clientId: mode === 'edit' ? clientsState.selected.id : null,
    payload: mode === 'edit' ? { ...clientsState.selected.editor } : emptyClientPayload(),
    error: null,
    saving: false,
  };
  renderClientDrawer(true);
}

function closeClientDrawer() {
  const overlay = document.querySelector('.drawer-overlay');
  if (overlay) overlay.remove();
  clientDrawer = null;
  const trigger = clientDrawerTrigger;
  clientDrawerTrigger = null;
  if (trigger && document.contains(trigger)) trigger.focus();
}

function setClientType(partyType) {
  if (!clientDrawer || clientDrawer.mode === 'edit') return;
  clientDrawer.payload = collectClientPayload();
  clientDrawer.payload.party_type = partyType;
  clientDrawer.error = null;
  renderClientDrawer();
}

function renderClientDrawer(focus = false) {
  if (!clientDrawer) return;
  document.querySelector('.drawer-overlay')?.remove();
  const person = clientDrawer.payload.party_type === 'PERSON';
  const title = clientDrawer.mode === 'edit' ? 'Modifier le client' : 'Nouveau client';
  const errors = clientDrawer.error?.field_errors || {};
  const errorCopy = clientDrawer.error
    ? `<div class="drawer-error" role="alert"><strong>${esc(clientDrawer.error.message)}</strong>${Object.values(errors).map(message => `<span>${esc(message)}</span>`).join('')}</div>`
    : '';
  const typeControl = clientDrawer.mode !== 'edit'
    ? `<div class="type-choice" role="group" aria-label="Type de fiche"><button type="button" class="${person ? 'active' : ''}" onclick="setClientType('PERSON')">Particulier</button><button type="button" class="${person ? '' : 'active'}" onclick="setClientType('ORGANIZATION')">Entreprise</button></div>`
    : `<div class="drawer-readonly"><span>Type de fiche</span><strong>${person ? 'Particulier' : 'Entreprise'}</strong></div>`;
  const identity = person
    ? `${drawerField('first_name', 'Prénom', { required: true })}${drawerField('last_name', 'Nom', { required: true })}`
    : `${drawerField('organization_name', 'Raison sociale', { required: true, wide: true })}${drawerField('legal_form', 'Forme juridique')}${drawerField('siret', 'SIRET')}`;
  const billing = person ? '' : drawerField('billing_address', 'Adresse de facturation si différente', { wide: true });
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeClientDrawer()"></div><aside class="client-drawer" role="dialog" aria-modal="true" aria-labelledby="client-drawer-title"><header class="drawer-header"><div><span class="eyebrow">Fiche maître</span><h2 id="client-drawer-title">${title}</h2><p>Informations réutilisables pour les futurs contrats.</p></div><button type="button" class="drawer-close" aria-label="Fermer" onclick="closeClientDrawer()">×</button></header><form id="client-drawer-form" onsubmit="saveClient(event)"><div class="drawer-body">${errorCopy}<section class="drawer-section"><h3>Type de fiche</h3>${typeControl}</section><section class="drawer-section"><h3>Identité</h3><div class="drawer-form-grid">${identity}</div></section><section class="drawer-section"><h3>Adresse</h3><div class="drawer-form-grid">${drawerField('address_line1', 'Adresse', { required: true, wide: true })}${drawerField('address_line2', 'Complément d’adresse', { wide: true })}${drawerField('postal_code', 'Code postal', { required: true })}${drawerField('city', 'Ville', { required: true })}${drawerField('country', 'Pays', { required: true })}</div></section><section class="drawer-section"><h3>Contact & repères</h3><div class="drawer-form-grid">${billing}${drawerField('email', 'E-mail')}${drawerField('phone', 'Téléphone')}${drawerField('internal_reference', 'Référence interne', { wide: true })}${drawerField('internal_notes', 'Notes internes', { multiline: true, wide: true })}</div></section><div class="drawer-notice">${icon('info')}<span><strong>Aucun régime dans la fiche maître</strong><small>Le régime applicable et le signataire sont confirmés dans chaque contrat.</small></span></div></div><footer class="drawer-footer"><button type="button" class="button button-secondary" onclick="closeClientDrawer()">Annuler</button><button type="submit" class="primary" ${clientDrawer.saving ? 'disabled' : ''}>${clientDrawer.saving ? 'Enregistrement…' : clientDrawer.mode === 'edit' ? 'Enregistrer les modifications' : 'Créer le client'}</button></footer></form></aside>`;
  document.body.appendChild(overlay);
  if (focus) requestAnimationFrame(() => overlay.querySelector('input, textarea, button')?.focus());
}

function saveClient(event) {
  event.preventDefault();
  if (!clientDrawer || clientDrawer.saving) return;
  clientDrawer.payload = collectClientPayload();
  clientDrawer.error = null;
  clientDrawer.saving = true;
  renderClientDrawer();
  const callback = result => {
    if (result?.ok) { closeClientDrawer(); return; }
    if (!clientDrawer) return;
    clientDrawer.saving = false;
    clientDrawer.error = result || { message: 'Les données ne peuvent pas être enregistrées.', field_errors: {} };
    renderClientDrawer(true);
  };
  if (clientDrawer.mode === 'contract-create') bridge.createContractClient(contractWorkspaceState.contract.id, clientDrawer.payload, callback);
  else if (clientDrawer.mode === 'create') bridge.createClient(clientDrawer.payload, callback);
  else bridge.updateClient(clientDrawer.clientId, clientDrawer.payload, callback);
}

function closeArchiveDialog(restoreFocus = true) {
  document.querySelector('.archive-dialog-overlay')?.remove();
  const trigger = archiveDialog?.trigger;
  archiveDialog = null;
  if (restoreFocus && trigger && document.contains(trigger)) trigger.focus();
}

function archiveTitle(kind) {
  if (kind === 'client') return 'Archiver cette fiche ?';
  if (kind === 'site') return 'Archiver le site ?';
  return 'Archiver l’équipement ?';
}

function callArchiveBridge(kind, id, callback) {
  if (kind === 'client') bridge.archiveClient(id, callback);
  else if (kind === 'site') bridge.archiveSite(id, callback);
  else bridge.archiveEquipment(id, callback);
}

function callRestoreBridge(kind, id, callback) {
  if (kind === 'client') bridge.restoreClient(id, callback);
  else if (kind === 'site') bridge.restoreSite(id, callback);
  else bridge.restoreEquipment(id, callback);
}

function openArchiveConfirmation(kind, entity, trigger) {
  archiveDialog = { kind, entity, trigger: trigger || document.activeElement, mode: 'archive', saving: false, error: null };
  renderArchiveDialog(true);
}

function restoreEntity(kind, entity, trigger) {
  const callback = result => {
    if (result?.ok) { closeArchiveDialog(false); return; }
    archiveDialog = {
      kind, entity, trigger: trigger || document.activeElement, mode: 'restore-error',
      saving: false, error: result || { message: 'Les données ne peuvent pas être enregistrées.' },
    };
    renderArchiveDialog(true);
  };
  callRestoreBridge(kind, entity.id, callback);
}

function confirmArchive() {
  if (!archiveDialog || archiveDialog.saving) return;
  archiveDialog.saving = true;
  archiveDialog.error = null;
  renderArchiveDialog();
  const { kind, entity } = archiveDialog;
  callArchiveBridge(kind, entity.id, result => {
    if (result?.ok) {
      closeArchiveDialog(false);
      if (kind === 'equipment' && entityDrawer?.equipmentId === entity.id) closeEntityDrawer();
      return;
    }
    if (!archiveDialog) return;
    archiveDialog.saving = false;
    archiveDialog.error = result || { message: 'Les données ne peuvent pas être enregistrées.' };
    renderArchiveDialog(true);
  });
}

function retryRestore() {
  if (!archiveDialog) return;
  const { kind, entity, trigger } = archiveDialog;
  closeArchiveDialog(false);
  restoreEntity(kind, entity, trigger);
}

function renderArchiveDialog(focus = false) {
  if (!archiveDialog) return;
  document.querySelector('.archive-dialog-overlay')?.remove();
  const failure = archiveDialog.mode === 'restore-error';
  const referenced = archiveDialog.entity.referenced
    ? '<p><strong>Cette fiche est utilisée par un ou plusieurs contrats.</strong></p>'
    : '';
  const error = archiveDialog.error
    ? `<div class="drawer-error" role="alert"><strong>${esc(archiveDialog.error.message)}</strong></div>`
    : '';
  const overlay = document.createElement('div');
  overlay.className = 'archive-dialog-overlay';
  overlay.innerHTML = `<div class="archive-dialog-backdrop" onclick="closeArchiveDialog()"></div><section class="archive-dialog" role="dialog" aria-modal="true" aria-labelledby="archive-dialog-title"><header><span class="eyebrow">Fiche maître</span><h2 id="archive-dialog-title">${failure ? 'Restauration impossible' : archiveTitle(archiveDialog.kind)}</h2></header><div class="archive-dialog-body">${error}${failure ? '<p>L’état enregistré reste inchangé. Vous pouvez réessayer.</p>' : `${referenced}<p>Les contrats existants restent inchangés et accessibles.</p><p>Cette fiche ne sera plus proposée dans les nouveaux choix. Elle pourra être restaurée.</p>`}</div><footer><button type="button" class="button button-secondary" onclick="closeArchiveDialog()">Annuler</button><button type="button" class="primary" ${archiveDialog.saving ? 'disabled' : ''} onclick="${failure ? 'retryRestore()' : 'confirmArchive()'}">${archiveDialog.saving ? 'Enregistrement…' : failure ? 'Réessayer' : 'Archiver'}</button></footer></section>`;
  document.body.appendChild(overlay);
  if (focus) requestAnimationFrame(() => overlay.querySelector('button')?.focus());
}

const emptySitePayload = () => ({
  label: '', address_line1: '', address_line2: '', postal_code: '', city: '', country: 'France',
  contact_name: '', contact_phone: '', internal_notes: '',
});
const emptyEquipmentPayload = () => ({
  equipment_type: 'Unité murale', brand: '', model: '', serial_number: '', power_kw: '',
  location: '', installation_date: '', internal_reference: '', internal_notes: '',
});

function entityField(name, label, options = {}) {
  const value = entityDrawer.payload[name] || '';
  const required = options.required ? ' required' : '';
  const wide = options.wide ? ' field-wide' : '';
  let control;
  if (options.multiline) control = `<textarea name="${name}" rows="3"${required}>${esc(value)}</textarea>`;
  else if (options.choices) {
    const choices = options.choices.includes(value) || !value ? options.choices : [value, ...options.choices];
    control = `<select name="${name}"${required}>${choices.map(choice => `<option value="${esc(choice)}" ${choice === value ? 'selected' : ''}>${esc(choice)}</option>`).join('')}</select>`;
  } else control = `<input name="${name}" value="${esc(value)}"${required}>`;
  const hint = options.hint ? `<small class="field-hint">${esc(options.hint)}</small>` : '';
  return `<label class="drawer-field${wide}"><span>${esc(label)}${options.required ? ' <b>*</b>' : ''}</span>${control}${hint}</label>`;
}

function collectEntityPayload() {
  const form = document.querySelector('#entity-drawer-form');
  if (!form) return entityDrawer.payload;
  const payload = { ...entityDrawer.payload };
  new FormData(form).forEach((value, key) => { payload[key] = String(value); });
  return payload;
}

function openSiteDrawer(trigger, mode = 'create', siteId = null) {
  if (!clientsState?.selected) return;
  const client = clientsState.selected;
  const site = mode === 'edit' ? client.sites.find(item => item.id === siteId) : null;
  if (mode === 'edit' && !site) return;
  entityDrawerTrigger = trigger || document.activeElement;
  entityDrawer = {
    kind: 'site', mode, siteId: site?.id || null, clientId: client.id, ownerLabel: client.name,
    clientAddress: {
      address_line1: client.editor.address_line1, address_line2: client.editor.address_line2,
      postal_code: client.editor.postal_code, city: client.editor.city, country: client.editor.country,
    },
    payload: site ? { ...site.editor } : emptySitePayload(), error: null, saving: false,
  };
  renderEntityDrawer(true);
}

function closeSiteMenu(restoreFocus = true) {
  document.querySelector('.site-action-popover')?.remove();
  const trigger = siteMenu?.trigger;
  siteMenu = null;
  if (restoreFocus && trigger && document.contains(trigger)) trigger.focus();
}

function openSiteMenu(site, trigger) {
  closeSiteMenu(false);
  const popover = document.createElement('div');
  popover.className = 'site-action-popover';
  popover.setAttribute('role', 'menu');
  popover.setAttribute('aria-label', `Actions du site ${site.label}`);
  const item = (label, action, danger = false) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.textContent = label;
    if (danger) button.className = 'archive-menu-item';
    button.addEventListener('click', action);
    return button;
  };
  if (site.archived) {
    popover.append(item('Restaurer le site', () => {
      closeSiteMenu(false);
      restoreEntity('site', site, trigger);
    }));
  } else {
    popover.append(item('Modifier le site', () => {
      closeSiteMenu(false);
      openSiteDrawer(trigger, 'edit', site.id);
    }));
    if (!clientsState.selected.archived && site.active_equipment_count > 0) {
      popover.append(item('Ajouter un équipement', () => {
        closeSiteMenu(false);
        openEquipmentDrawer('create', site.id, trigger);
      }));
    }
    const separator = document.createElement('div');
    separator.className = 'site-menu-separator';
    separator.setAttribute('role', 'separator');
    popover.append(separator, item('Archiver le site', () => {
      closeSiteMenu(false);
      openArchiveConfirmation('site', site, trigger);
    }, true));
  }
  document.body.appendChild(popover);
  const anchor = trigger.getBoundingClientRect();
  const width = popover.offsetWidth;
  popover.style.left = `${Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8))}px`;
  popover.style.top = `${Math.min(anchor.bottom + 6, window.innerHeight - popover.offsetHeight - 8)}px`;
  siteMenu = { siteId: site.id, trigger, popover };
  requestAnimationFrame(() => popover.querySelector('button')?.focus());
}

function findEquipmentContext(equipmentId) {
  for (const site of clientsState?.selected?.sites || []) {
    const equipment = site.equipment.find(item => item.id === equipmentId);
    if (equipment) return { site, equipment };
  }
  return null;
}

function openEquipmentDrawer(mode, exactId, trigger) {
  if (!clientsState?.selected) return;
  let site;
  let equipment;
  if (mode === 'create') site = clientsState.selected.sites.find(item => item.id === exactId);
  else ({ site, equipment } = findEquipmentContext(exactId) || {});
  if (!site || (mode === 'edit' && !equipment)) return;
  entityDrawerTrigger = trigger || document.activeElement;
  entityDrawer = {
    kind: 'equipment', mode, siteId: site.id, equipmentId: equipment?.id || null,
    ownerLabel: site.label, referenced: Boolean(equipment?.referenced),
    payload: equipment ? { ...equipment.editor } : emptyEquipmentPayload(),
    error: null, saving: false,
  };
  renderEntityDrawer(true);
}

function copyClientAddress() {
  if (entityDrawer?.kind !== 'site') return;
  entityDrawer.payload = collectEntityPayload();
  Object.assign(entityDrawer.payload, entityDrawer.clientAddress);
  renderEntityDrawer(true);
}

function closeEntityDrawer() {
  document.querySelector('.drawer-overlay')?.remove();
  entityDrawer = null;
  const trigger = entityDrawerTrigger;
  entityDrawerTrigger = null;
  if (trigger && document.contains(trigger)) trigger.focus();
}

function renderEntityDrawer(focus = false) {
  if (!entityDrawer) return;
  document.querySelector('.drawer-overlay')?.remove();
  const siteDrawer = entityDrawer.kind === 'site';
  const title = siteDrawer ? entityDrawer.mode === 'edit' ? 'Modifier le site' : 'Ajouter un site' : entityDrawer.mode === 'edit' ? 'Modifier l’équipement' : 'Ajouter un équipement';
  const errors = entityDrawer.error?.field_errors || {};
  const errorCopy = entityDrawer.error
    ? `<div class="drawer-error" role="alert"><strong>${esc(entityDrawer.error.message)}</strong>${Object.values(errors).map(message => `<span>${esc(message)}</span>`).join('')}</div>`
    : '';
  const owner = `<div class="drawer-readonly"><span>${siteDrawer ? 'Client' : 'Site'}</span><strong>${esc(entityDrawer.ownerLabel)}</strong></div>`;
  const siteFields = `<section class="drawer-section"><h3>Identification</h3><div class="drawer-form-grid">${entityField('label', 'Nom du site', { required: true, wide: true })}${owner}</div></section><section class="drawer-section"><div class="drawer-section-heading"><h3>Adresse</h3><button type="button" class="button button-secondary address-copy" onclick="copyClientAddress()">Reprendre l’adresse du client</button></div><div class="drawer-form-grid">${entityField('address_line1', 'Adresse', { required: true, wide: true })}${entityField('address_line2', 'Complément d’adresse', { wide: true })}${entityField('postal_code', 'Code postal', { required: true })}${entityField('city', 'Ville', { required: true })}${entityField('country', 'Pays', { required: true })}</div></section><section class="drawer-section"><h3>Contact & accès</h3><div class="drawer-form-grid">${entityField('contact_name', 'Contact sur site')}${entityField('contact_phone', 'Téléphone du contact')}${entityField('internal_notes', 'Consignes internes', { multiline: true, wide: true })}</div></section>`;
  const equipmentTypes = ['Unité murale', 'Groupe extérieur', 'Cassette', 'Gainable', 'Autre'];
  const equipmentFields = `<section class="drawer-section"><h3>Rattachement</h3>${owner}</section><section class="drawer-section"><h3>Équipement</h3><div class="drawer-form-grid">${entityField('equipment_type', 'Type', { required: true, choices: equipmentTypes })}${entityField('location', 'Localisation', { required: true })}${entityField('brand', 'Marque')}${entityField('model', 'Modèle')}${entityField('serial_number', 'N° de série')}${entityField('power_kw', 'Puissance kW')}${entityField('installation_date', 'Date d’installation')}${entityField('internal_reference', 'Référence interne')}</div></section><section class="drawer-section"><h3>Informations internes</h3><div class="drawer-form-grid">${entityField('internal_notes', 'Notes internes', { multiline: true, wide: true, hint: 'Interne — jamais reprise automatiquement dans un contrat.' })}</div></section>`;
  const archiveEquipmentAction = !siteDrawer && entityDrawer.mode === 'edit'
    ? `<button type="button" class="archive-drawer-action" onclick="openArchiveConfirmation('equipment', {id: '${entityDrawer.equipmentId}', referenced: ${entityDrawer.referenced}}, this)">${icon('archive')}Archiver l’équipement</button><span class="drawer-footer-spacer"></span>`
    : '';
  const overlay = document.createElement('div');
  overlay.className = 'drawer-overlay';
  overlay.innerHTML = `<div class="drawer-backdrop" onclick="closeEntityDrawer()"></div><aside class="client-drawer" role="dialog" aria-modal="true" aria-labelledby="entity-drawer-title"><header class="drawer-header"><div><span class="eyebrow">Fiche maître</span><h2 id="entity-drawer-title">${title}</h2><p>${siteDrawer ? 'Site rattaché au client sélectionné.' : 'Équipement rattaché au site sélectionné.'}</p></div><button type="button" class="drawer-close" aria-label="Fermer" onclick="closeEntityDrawer()">×</button></header><form id="entity-drawer-form" onsubmit="saveEntity(event)"><div class="drawer-body">${errorCopy}${siteDrawer ? siteFields : equipmentFields}</div><footer class="drawer-footer">${archiveEquipmentAction}<button type="button" class="button button-secondary" onclick="closeEntityDrawer()">Annuler</button><button type="submit" class="primary" ${entityDrawer.saving ? 'disabled' : ''}>${entityDrawer.saving ? 'Enregistrement…' : entityDrawer.mode === 'edit' ? 'Enregistrer les modifications' : siteDrawer ? 'Créer le site' : 'Créer l’équipement'}</button></footer></form></aside>`;
  document.body.appendChild(overlay);
  if (focus) requestAnimationFrame(() => overlay.querySelector('input, select, textarea, button')?.focus());
}

function saveEntity(event) {
  event.preventDefault();
  if (!entityDrawer || entityDrawer.saving) return;
  entityDrawer.payload = collectEntityPayload();
  entityDrawer.error = null;
  entityDrawer.saving = true;
  renderEntityDrawer();
  const callback = result => {
    if (result?.ok) { closeEntityDrawer(); return; }
    if (!entityDrawer) return;
    entityDrawer.saving = false;
    entityDrawer.error = result || { message: 'Les données ne peuvent pas être enregistrées.', field_errors: {} };
    renderEntityDrawer(true);
  };
  if (entityDrawer.kind === 'site' && entityDrawer.mode === 'contract-create') bridge.createContractSite(contractWorkspaceState.contract.id, entityDrawer.payload, callback);
  else if (entityDrawer.kind === 'equipment' && entityDrawer.mode === 'contract-create') bridge.createContractEquipment(contractWorkspaceState.contract.id, entityDrawer.payload, callback);
  else if (entityDrawer.kind === 'site' && entityDrawer.mode === 'create') bridge.createSite(entityDrawer.clientId, entityDrawer.payload, callback);
  else if (entityDrawer.kind === 'site') bridge.updateSite(entityDrawer.siteId, entityDrawer.payload, callback);
  else if (entityDrawer.mode === 'create') bridge.createEquipment(entityDrawer.siteId, entityDrawer.payload, callback);
  else bridge.updateEquipment(entityDrawer.equipmentId, entityDrawer.payload, callback);
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if (archiveDialog) closeArchiveDialog();
  else if (siteMenu) closeSiteMenu();
  else if (clientDrawer) closeClientDrawer();
  else if (entityDrawer) closeEntityDrawer();
});
document.addEventListener('mousedown', event => {
  if (siteMenu && !siteMenu.popover.contains(event.target) && !siteMenu.trigger.contains(event.target)) closeSiteMenu();
});

const SEARCH_INPUT_IDS = new Set(['contracts-search', 'clients-search']);

function captureSearchFocus() {
  const input = document.activeElement;
  if (!(input instanceof HTMLInputElement) || !SEARCH_INPUT_IDS.has(input.id)) return null;
  return {
    id: input.id,
    start: input.selectionStart ?? input.value.length,
    end: input.selectionEnd ?? input.value.length,
    direction: input.selectionDirection || 'none',
  };
}

function restoreSearchFocus(focus) {
  if (!focus) return;
  const input = document.getElementById(focus.id);
  if (!(input instanceof HTMLInputElement)) return;
  input.focus();
  const length = input.value.length;
  const start = Math.min(Math.max(0, focus.start), length);
  const end = Math.min(Math.max(start, focus.end), length);
  input.setSelectionRange(start, end, focus.direction);
}

function render(state) {
  const searchFocus = captureSearchFocus();
  if (state.page === 'CONTRACT_WORKSPACE') { renderContractWorkspace(state); restoreSearchFocus(searchFocus); return; }
  if (state.page === 'CLIENTS') { renderClients(state); restoreSearchFocus(searchFocus); return; }
  const rows = state.rows.map(row => `<tr class="contract-row" onclick="bridge.openContract('${row.id}')">
    <td><strong>${esc(row.number)}</strong>${updateLine(row.updated)}</td>
    <td><strong>${esc(row.client)}</strong>${row.site ? `<small>${icon('pin')}${esc(row.site)}</small>` : ''}</td>
    <td><span class="pill ${row.status_code}">${esc(row.status)}</span></td><td>${esc(row.deadline)}</td>
    <td>${row.needs_action ? '<span class="dot"></span>' : icon('info')} ${esc(row.signal)}</td>
    <td class="docs">${icon('file')} ${esc(row.document)}</td><td><button type="button" class="row-open-action" onclick="event.stopPropagation();bridge.openContract('${row.id}')">Ouvrir ${icon('chevron')}</button></td>
  </tr>`).join('');
  const backup = backupValue(state.backup);
  const actionLabel = actionCountLabel(state.action_count);
  document.querySelector('#app').innerHTML = `<div class="app"><aside class="side">
    <div class="brand"><span class="brand-icon">${icon('air')}</span><span>ICP Renov<br><small>Contrats d’entretien</small></span></div>
    <button type="button" class="nav active" aria-current="page">${icon('file')}Contrats</button>
    <button type="button" class="nav" onclick="bridge.navigate('CLIENTS')">${icon('users')}Clients & installations</button>
    <button type="button" class="nav" onclick="bridge.navigate('SETTINGS')">${icon('settings')}Paramètres</button>
    <div class="side-bottom"><div class="local-state">${icon('laptop')}<b>Mode local</b><small>Données conservées uniquement sur ce poste.</small></div>
    <div class="backup-state">${icon('backup')}<small>Sauvegarde</small><b>${esc(backup)}</b></div></div>
  </aside><main class="main"><header class="head"><div><div class="eyebrow">ACCUEIL OPÉRATIONNEL</div><h1>Contrats</h1><p>Retrouvez vos contrats et les actions à traiter.</p></div>
    <button class="primary" onclick="bridge.createContract()">${icon('plus')}Nouveau contrat</button></header>
    <section class="attention">${icon('bell')}<span><b>${actionLabel}</b><br><small>maintenant ou prochainement</small></span><span class="divider"></span><span>${icon('backup')} ${esc(backup)}</span><button class="ghost" onclick="bridge.saveBackup()">Sauvegarder maintenant</button></section>
    <section class="toolbar"><label class="search">${icon('search')}<input id="contracts-search" value="${esc(state.search)}" placeholder="Rechercher par numéro, client ou site" oninput="bridge.setSearch(this.value)"></label><span class="tabs">${state.filters.map(filter => `<button class="${filter.active ? 'active' : ''}" onclick="bridge.setFilter('${filter.id}')">${filter.label}</button>`).join('')}</span></section>
    <div class="shell"><table><thead><tr><th>Contrat</th><th>Client & site</th><th>Statut</th><th>Échéance</th><th>Action / information</th><th class="docs">Documents</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
  </main></div>`;
  restoreSearchFocus(searchFocus);
}

function renderClients(state) {
  closeSiteMenu(false);
  clientsState = state;
  const selected = state.selected;
  const identityLabel = selected?.type === 'Particulier' ? 'Nom complet' : 'Raison sociale';
  const backup = backupValue(state.backup);
  const archiveBadge = '<span class="archive-badge">Archivé</span>';
  const list = state.clients.map(client => `<button class="client-list-item ${client.selected ? 'selected' : ''}" onclick="bridge.selectClient('${client.id}')"><span class="client-avatar">${esc(client.name.slice(0, 2).toUpperCase())}</span><span><strong>${esc(client.name)}${client.archived ? archiveBadge : ''}</strong><small>${esc(client.type)} · ${esc(client.summary)}</small></span>${icon('chevron')}</button>`).join('') || '<div class="empty-list">Aucun client à afficher.<br><small>Créez une première fiche client.</small></div>';
  const sites = !selected ? '' : selected.sites.map(site => {
    const equipmentRows = site.equipment.map(eq => `<div class="equipment-row ${eq.archived ? 'archived-equipment' : ''}">${icon('air')}<span><strong>${esc(eq.name)}${eq.archived ? archiveBadge : ''}</strong><small>${esc(eq.location)}</small></span>${eq.archived ? `<button class="equipment-restore-action" type="button">${icon('restore')}Restaurer</button>` : !site.archived && !selected.archived ? `<button class="table-action equipment-edit-action" type="button" aria-label="Modifier ${esc(eq.name)}">${icon('pencil')}</button>` : ''}</div>`).join('');
    const mayCreate = !selected.archived && !site.archived;
    const hasActiveEquipment = site.active_equipment_count > 0;
    const contractAction = !mayCreate ? '' : hasActiveEquipment
      ? `<button class="button button-secondary" onclick="bridge.createContractForSite('${site.id}')">Créer un contrat pour ce site</button>`
      : '<button type="button" class="button button-secondary" disabled aria-disabled="true">Créer un contrat pour ce site</button>';
    const zeroActiveEquipment = mayCreate && !hasActiveEquipment
      ? `<div class="empty-list zero-active-equipment"><div class="zero-active-equipment-message">Ajoutez au moins un équipement actif pour créer un contrat depuis ce site.</div><div class="zero-active-equipment-action"><button type="button" class="button button-secondary zero-active-add-equipment">${icon('plus')}Ajouter un équipement</button></div></div>`
      : '';
    const emptyEquipment = '<div class="empty-list">Aucun équipement.</div>';
    return `<article class="site-card ${site.archived ? 'archived-site' : ''}"><header class="site-card-header"><span>${icon('pin')}<span><strong>${esc(site.label)}${site.archived ? archiveBadge : ''}</strong><small>${esc(site.address)}</small></span></span><div><span class="count-label">${site.active_equipment_count} ${site.active_equipment_count === 1 ? 'équipement' : 'équipements'}</span>${contractAction}</div></header><div class="equipment-list">${equipmentRows || (zeroActiveEquipment ? '' : emptyEquipment)}${zeroActiveEquipment}</div></article>`;
  }).join('') || '<div class="empty-list">Aucun site.</div>';
  const linked = selected?.linked?.length ? selected.linked.map(contract => `<button class="linked-contract-row" onclick="bridge.openLinkedContract('${contract.id}')">${icon('file')}<span><strong>${esc(contract.number)}</strong><small>${esc(contract.secondary_display)}</small></span><span class="pill ${esc(contract.status_tone)}">${esc(contract.status_label)}</span>${icon('chevron')}</button>`).join('') : '<div class="empty-list">Aucun contrat lié</div>';
  const clientHeaderAction = selected && !selected.archived ? '<button class="ghost">Modifier</button>' : '';
  const clientArchiveZone = !selected ? '' : `<div class="archive-zone"><button type="button" class="archive-zone-action">${icon(selected.archived ? 'restore' : 'archive')}<span><b>${selected.archived ? 'Restaurer cette fiche' : 'Archiver cette fiche'}</b><small>${selected.archived ? 'La fiche redeviendra disponible dans les nouvelles sélections.' : 'La fiche sera retirée des nouvelles sélections ; les contrats historiques resteront accessibles.'}</small></span></button></div>`;
  document.querySelector('#app').innerHTML = `<div class="app"><aside class="side"><div class="brand"><span class="brand-icon">${icon('air')}</span><span>ICP Renov<br><small>Contrats d’entretien</small></span></div><button type="button" class="nav" onclick="bridge.navigate('CONTRACTS')">${icon('file')}Contrats</button><button type="button" class="nav active" aria-current="page">${icon('users')}Clients & installations</button><button type="button" class="nav" onclick="bridge.navigate('SETTINGS')">${icon('settings')}Paramètres</button><div class="side-bottom"><div class="local-state">${icon('laptop')}<b>Mode local</b><small>Données conservées uniquement sur ce poste.</small></div><div class="backup-state">${icon('backup')}<small>Sauvegarde</small><b>${esc(backup)}</b></div></div></aside><main class="main"><header class="head"><div><div class="eyebrow">Données réutilisables</div><h1>Clients & installations</h1><p>Gérez les fiches maîtres sans modifier les snapshots des contrats.</p></div><button class="primary">${icon('plus')}Nouveau client</button></header><section class="client-workspace"><aside class="client-list-panel"><label class="search">${icon('search')}<input id="clients-search" value="${esc(state.search)}" placeholder="Rechercher un client" oninput="bridge.setClientSearch(this.value)"></label><div class="archive-filter"><button class="${!state.archived ? 'active' : ''}" onclick="bridge.setClientArchived(false)">Actifs</button><button class="${state.archived ? 'active' : ''}" onclick="bridge.setClientArchived(true)">Archivés</button></div><div class="client-list">${list}</div></aside><section class="client-detail">${selected ? `<header class="client-detail-header"><span class="large-avatar">${esc(selected.name.slice(0,2).toUpperCase())}</span><div><div class="eyebrow">${esc(selected.type)}${selected.archived ? archiveBadge : ''}</div><h2>${esc(selected.name)}</h2><small>${esc(selected.email || selected.phone || '')}</small></div>${clientHeaderAction}</header><div class="notice-info"><b>Effet des modifications maître</b><br><small>Les modifications seront proposées pour les futurs contrats. Les brouillons déjà créés, contrats et révisions existants restent inchangés.</small></div><div class="detail-grid"><section class="detail-block"><h3>Identité & coordonnées</h3><dl>${selected.type ? `<div><dt>Type de fiche</dt><dd>${esc(selected.type === 'ORGANIZATION' ? 'Entreprise' : selected.type === 'PERSON' ? 'Particulier' : selected.type)}</dd></div>` : ''}${selected.name ? `<div><dt>${identityLabel}</dt><dd>${esc(selected.name)}</dd></div>` : ''}${selected.legal_form ? `<div><dt>Forme juridique</dt><dd>${esc(selected.legal_form)}</dd></div>` : ''}${selected.siret ? `<div><dt>SIRET</dt><dd>${esc(selected.siret)}</dd></div>` : ''}${selected.address ? `<div><dt>Adresse</dt><dd>${esc(selected.address)}</dd></div>` : ''}</dl></section><section class="detail-block"><h3>Contact / repères</h3><dl>${selected.phone ? `<div><dt>Téléphone</dt><dd>${esc(selected.phone)}</dd></div>` : ''}${selected.email ? `<div><dt>E-mail</dt><dd>${esc(selected.email)}</dd></div>` : ''}${selected.billing_address ? `<div><dt>Adresse de facturation</dt><dd>${esc(selected.billing_address)}</dd></div>` : ''}${selected.internal_reference ? `<div><dt>Référence interne</dt><dd>${esc(selected.internal_reference)}</dd></div>` : ''}</dl></section></div><section class="installation-section"><h2>Sites & équipements</h2>${sites}</section><section class="linked-contracts"><h2>Contrats liés</h2>${linked}</section>${clientArchiveZone}` : '<div class="empty-list">Sélectionnez un client pour consulter sa fiche.</div>'}</section></section></main></div>`;
  document.querySelector('.head .primary').addEventListener('click', event => openClientDrawer('create', event.currentTarget));
  if (!selected?.archived) document.querySelector('.client-detail-header .ghost')?.addEventListener('click', event => openClientDrawer('edit', event.currentTarget));
  if (selected) {
    const installationSection = document.querySelector('.installation-section');
    const heading = installationSection.querySelector('h2');
    const headingRow = document.createElement('div');
    headingRow.className = 'section-title-row';
    heading.before(headingRow);
    headingRow.appendChild(heading);
    const sectionActions = document.createElement('div');
    sectionActions.className = 'section-title-actions';
    if (selected.has_archived_children) {
      const archivedToggle = document.createElement('button');
      archivedToggle.type = 'button';
      archivedToggle.className = `button button-secondary archived-visibility-toggle ${state.show_archived_master_data ? 'active' : ''}`;
      archivedToggle.textContent = 'Afficher les archivés';
      archivedToggle.setAttribute('aria-pressed', String(state.show_archived_master_data));
      archivedToggle.addEventListener('click', () => bridge.setShowArchivedMasterData(!state.show_archived_master_data));
      sectionActions.appendChild(archivedToggle);
    }
    if (!selected.archived) {
      const addSite = document.createElement('button');
      addSite.className = 'button button-secondary add-site-button';
      addSite.innerHTML = `${icon('plus')}Ajouter un site`;
      addSite.addEventListener('click', event => openSiteDrawer(event.currentTarget));
      sectionActions.appendChild(addSite);
    }
    headingRow.appendChild(sectionActions);
    document.querySelectorAll('.site-card').forEach((card, siteIndex) => {
      const site = selected.sites[siteIndex];
      const headerActions = card.querySelector('.site-card-header > div');
      const contractAction = headerActions.querySelector('.button-secondary');
      const siteActions = document.createElement('button');
      siteActions.type = 'button';
      siteActions.className = 'table-action site-action-button';
      siteActions.setAttribute('aria-label', `Actions du site ${site.label}`);
      siteActions.innerHTML = icon('more');
      siteActions.addEventListener('click', event => {
        event.stopPropagation();
        openSiteMenu(site, event.currentTarget);
      });
      headerActions.insertBefore(siteActions, contractAction);
      const addEquipment = card.querySelector('.zero-active-add-equipment');
      if (addEquipment) addEquipment.onclick = event => openEquipmentDrawer('create', site.id, event.currentTarget);
      card.querySelectorAll('.equipment-row').forEach((row, equipmentIndex) => {
        const equipment = site.equipment[equipmentIndex];
        row.querySelector('.equipment-edit-action')?.addEventListener('click', event => openEquipmentDrawer('edit', equipment.id, event.currentTarget));
        row.querySelector('.equipment-restore-action')?.addEventListener('click', event => restoreEntity('equipment', equipment, event.currentTarget));
      });
    });
    document.querySelector('.archive-zone-action')?.addEventListener('click', event => {
      if (selected.archived) restoreEntity('client', selected, event.currentTarget);
      else openArchiveConfirmation('client', selected, event.currentTarget);
    });
  }
  if (clientDrawer) renderClientDrawer();
  if (entityDrawer) renderEntityDrawer();
  if (archiveDialog) renderArchiveDialog();
}

function showBridgeFailure() {
  document.querySelector('#app').innerHTML = '<main style="padding:32px"><h1>Contrats indisponibles</h1><p>La liaison locale de la surface Contrats ne peut pas être initialisée. Redémarrez l’application.</p></main>';
}

try {
  if (typeof qt === 'undefined' || typeof QWebChannel === 'undefined') throw new Error('WebChannel unavailable');
  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.bridge;
    if (!bridge) { showBridgeFailure(); return; }
    bridge.stateChanged.connect(render);
    bridge.refresh();
  });
} catch (error) {
  showBridgeFailure();
}
