let bridge;

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
};

function icon(name) {
  return `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[name] || paths.file}"/></svg>`;
}
function esc(value) { return String(value ?? '').replace(/[&<>]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[char])); }
function updateLine(value) {
  const displayValue = String(value ?? '').trim();
  return displayValue && displayValue !== 'Dernière mise à jour' ? `<small>Mis à jour ${esc(displayValue)}</small>` : '';
}
function backupValue(summary) {
  const value = String(summary ?? '').replace(/^Sauvegarde\s*/i, '').trim();
  return !value || value.toLocaleLowerCase('fr-FR') === 'non configurée' ? 'Non configurée' : value;
}

function render(state) {
  const rows = state.rows.map(row => `<tr onclick="bridge.openContract('${row.id}')">
    <td><strong>${esc(row.number)}</strong>${updateLine(row.updated)}</td>
    <td><strong>${esc(row.client)}</strong>${row.site ? `<small>${icon('pin')}${esc(row.site)}</small>` : ''}</td>
    <td><span class="pill ${row.status_code}">${esc(row.status)}</span></td><td>${esc(row.deadline)}</td>
    <td>${row.needs_action ? '<span class="dot"></span>' : icon('info')} ${esc(row.signal)}</td>
    <td class="docs">${icon('file')} ${esc(row.document)}</td><td>${icon('chevron')}</td>
  </tr>`).join('');
  const backup = backupValue(state.backup);
  document.querySelector('#app').innerHTML = `<div class="app"><aside class="side">
    <div class="brand"><span class="brand-icon">${icon('air')}</span><span>ICP Renov<br><small>Contrats d’entretien</small></span></div>
    <div class="nav active">${icon('file')}Contrats</div>
    <div class="nav" onclick="bridge.navigate('CLIENTS')">${icon('users')}Clients & installations</div>
    <div class="nav" onclick="bridge.navigate('SETTINGS')">${icon('settings')}Paramètres</div>
    <div class="side-bottom"><div class="local-state">${icon('laptop')}<b>Mode local</b><small>Données conservées uniquement sur ce poste.</small></div>
    <div class="backup-state">${icon('backup')}<small>Sauvegarde</small><b>${esc(backup)}</b></div></div>
  </aside><main class="main"><header class="head"><div><div class="eyebrow">ACCUEIL OPÉRATIONNEL</div><h1>Contrats</h1><p>Retrouvez vos contrats et les actions à traiter.</p></div>
    <button class="primary" onclick="bridge.createContract()">${icon('plus')}Nouveau contrat</button></header>
    <section class="attention">${icon('bell')}<span><b>${state.action_count} actions à traiter</b><br><small>maintenant ou prochainement</small></span><button class="ghost" onclick="bridge.setFilter('ACTIONS')">Afficher les actions ${icon('chevron')}</button><span class="divider"></span><span>${icon('backup')} ${esc(backup)}</span><button class="ghost" onclick="bridge.saveBackup()">Sauvegarder maintenant</button></section>
    <section class="toolbar"><label class="search">${icon('search')}<input value="${esc(state.search)}" placeholder="Rechercher par numéro, client ou site" oninput="bridge.setSearch(this.value)"></label><span class="tabs">${state.filters.map(filter => `<button class="${filter.active ? 'active' : ''}" onclick="bridge.setFilter('${filter.id}')">${filter.label}</button>`).join('')}</span></section>
    <div class="shell"><table><thead><tr><th>Contrat</th><th>Client & site</th><th>Statut</th><th>Échéance</th><th>Action / information</th><th class="docs">Documents</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
  </main></div>`;
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
