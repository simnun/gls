const state = {
  data: null,
  view: 'action',
  severity: '',
  workflow: '',
  glsState: '',
  glsStateMode: 'last',
  codOnly: false,
  search: '',
  dateFrom: '',
  dateTo: '',
  selectedTracking: null,
  syncTimer: null,
  stockData: null,
  stockSearch: '',
  stockOutcome: '',
  stockHandled: '',
  stockDateFrom: '',
  stockDateTo: '',
  sortKey: 'priority',
  sortDir: 'desc',
  stockSortKey: 'entered',
  stockSortDir: 'desc',
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const severityLabels = {
  CRITICAL: 'MASSIMA', WARNING: 'ALTA', WATCH: 'OSSERVAZIONE',
  INFO: 'INFORMATIVO', NORMAL: 'NORMALE'
};
const workflowLabels = {
  NEW: 'Da verificare', IN_PROGRESS: 'In lavorazione', WAITING_CUSTOMER: 'In lavorazione',
  WAITING_GLS: 'In lavorazione', RESOLVED: 'Chiusa', IGNORED: 'Chiusa'
};

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}

function formatMoney(amount, currency) {
  if (amount == null || amount === '') return '—';
  try { return new Intl.NumberFormat('it-IT', { style: 'currency', currency: currency || 'EUR' }).format(Number(amount)); }
  catch { return `${amount} ${currency || ''}`.trim(); }
}

function formatDate(value, withTime = true) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat('it-IT', withTime
    ? { day:'2-digit', month:'2-digit', year:'2-digit', hour:'2-digit', minute:'2-digit' }
    : { day:'2-digit', month:'2-digit', year:'numeric' }).format(d);
}

function localDateKey(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function dateKeyFromDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}


function prossimoGiornoLavorativo(da = new Date()) {
  // Prima data utile dopo oggi, escludendo sabato e domenica: una riconsegna
  // chiesta per il fine settimana non verrebbe comunque eseguita.
  const d = new Date(da.getFullYear(), da.getMonth(), da.getDate());
  do {
    d.setDate(d.getDate() + 1);
  } while (d.getDay() === 0 || d.getDay() === 6);
  return dateKeyFromDate(d);
}

function phoneDigits(value) {
  return String(value || '').replace(/\D+/g, '');
}
function localItalianPhone(value) {
  let digits = phoneDigits(value);
  if (digits.startsWith('0039')) digits = digits.slice(4);
  else if (digits.startsWith('39') && digits.length > 10) digits = digits.slice(2);
  return digits;
}
function orderNumeric(value) {
  const m = String(value || '').match(/\d+/g);
  return m ? Number(m.join('')) : 0;
}
// Anomalie che GLS risolve aprendo una giacenza il giorno dopo: sono i casi in
// cui l'operatore raccoglie le informazioni oggi e svincola domani.
// Il collo e' in viaggio verso il cliente: nessuna azione, solo da seguire.
const CATEGORIE_IN_CONSEGNA = ['OUT_FOR_DELIVERY', 'SCHEDULED', 'IN_TRANSIT', 'CORRESPONDENT'];
const CATEGORIE_VERSO_GIACENZA = ['ADDRESS_ERROR', 'ABSENT', 'REFUSED', 'ACTION_REQUIRED', 'DELIVERY_RETRY',
  'PICKUP_AT_DEPOT', 'RECIPIENT_CLOSED',
  'COD_ISSUE', 'DELIVERY_FAILURE', 'DAMAGE_OR_LOSS'];

function daVerificare(row) {
  // Una pratica entra in DA VERIFICARE solo finche' nessuno l'ha presa in mano:
  // se un operatore ci sta lavorando il compito e' gia' assegnato, e rimetterla
  // in coda non dice a nessuno cosa fare.
  if (!row || row.closed || row.workflow_status !== 'NEW') return false;
  const gravita = row.effective_severity || row.severity;
  return ['CRITICAL', 'WARNING'].includes(gravita);
}

function statoGestione(row) {
  // Dice a colpo d'occhio se una pratica e' gia' stata lavorata e cosa manca.
  // Funziona sia con i dati dell'elenco (contatori aggregati) sia con quelli
  // del dettaglio (elenchi completi di giacenze e svincoli).
  if (!row || row.closed) return null;
  const inLavorazione = ['IN_PROGRESS', 'WAITING_CUSTOMER', 'WAITING_GLS'].includes(row.workflow_status);
  const haLavoro = Boolean((row.operator_note || '').trim() || row.last_operator_action);
  if (!inLavorazione || !haLavoro) return null;

  // Nel dettaglio gli svincoli stanno dentro le giacenze, nell'elenco arrivano
  // come contatore aggregato: leggiamo entrambe le forme.
  const svincoli = (row.stock_cases || []).flatMap(sc => sc.release_requests || []);
  const svincolato = Number(row.release_count || 0) > 0
    || svincoli.some(r => Number(r.gls_success) === 1);
  const rifiutato = !svincolato
    && (svincoli.length > 0 || Number(row.release_failed_count || 0) > 0);
  const giacenzaAperta = Number(row.open_stock_cases || 0) > 0
    || (row.stock_cases || []).some(sc => sc.status === 'OPEN')
    || row.category === 'STORAGE';

  if (rifiutato) return {
    classe: 'rifiutato', icona: '✕', testo: 'Svincolo respinto',
    dettaglio: (svincoli[0] && svincoli[0].gls_result)
      ? `GLS non ha accettato l\u2019istruzione: ${svincoli[0].gls_result}`
      : 'GLS non ha accettato l\u2019istruzione di svincolo: va ripetuta o gestita fuori piattaforma.',
  };
  if (svincolato) return {
    classe: 'inviato', icona: '✓', testo: 'Svincolo inviato',
    dettaglio: 'Lo svincolo e\u2019 gia\u2019 stato inviato a GLS: si attende il riscontro.',
  };
  if (giacenzaAperta) return {
    classe: 'da-svincolare', icona: '⚠', testo: 'Da svincolare',
    dettaglio: 'Pratica gia\u2019 gestita e giacenza aperta: manca solo lo svincolo.',
  };
  if (CATEGORIE_VERSO_GIACENZA.includes(row.category)) return {
    classe: 'gestita', icona: '●', testo: 'Gestita · attende giacenza',
    dettaglio: 'Le informazioni sono gia\u2019 state raccolte: si attende che GLS apra la giacenza.',
  };
  return null;
}

function pastigliaNovita(row) {
  // GLS ha aggiornato una pratica gia' presa in carico: prima il sistema la
  // rimandava in DA VERIFICARE cancellando il lavoro fatto, ora la lascia dov'e'
  // e mette in evidenza la notizia finche' l'operatore non interviene.
  if (!row || !row.unread_event_at) return '';
  const quando = formatDate(row.unread_event_at);
  const testo = String(row.gls_status || '').trim();
  const titolo = testo ? `${quando} · ${testo}` : `Aggiornamento GLS del ${quando}`;
  return `<div class="news-chip" title="${esc(titolo)}"><span>!</span>Novit\u00e0 GLS</div>`;
}

function pastigliaGestione(row) {
  const s = statoGestione(row);
  if (!s) return '';
  return `<div class="handled-chip ${s.classe}" title="${esc(s.dettaglio)}"><span>${s.icona}</span>${esc(s.testo)}</div>`;
}

function priorityRank(row) {
  return {CRITICAL:5, WARNING:4, WATCH:3, INFO:2, NORMAL:1}[row.effective_severity || row.severity] || 0;
}
function cmpText(a,b) { return String(a ?? '').localeCompare(String(b ?? ''), 'it', {numeric:true, sensitivity:'base'}); }
function cmpDate(a,b) { return (new Date(a || 0).getTime() || 0) - (new Date(b || 0).getTime() || 0); }
function sortRows(rows, key, dir, getter) {
  const mult = dir === 'asc' ? 1 : -1;
  return rows.slice().sort((a,b) => {
    const [av,bv,type] = getter(key,a,b);
    const c = type === 'number' ? Number(av || 0) - Number(bv || 0) : type === 'date' ? cmpDate(av,bv) : cmpText(av,bv);
    if (c !== 0) return c * mult;
    return cmpText(a.tracking_number, b.tracking_number) * mult;
  });
}
function updateSortIndicators(selector, key, dir) {
  $$(selector).forEach(btn => {
    const span = btn.querySelector('span');
    if (!span) return;
    span.textContent = btn.dataset.sort === key ? (dir === 'asc' ? '↑' : '↓') : '↕';
    btn.classList.toggle('active', btn.dataset.sort === key);
  });
}

function age(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const mins = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  if (mins < 2) return 'adesso';
  if (mins < 60) return `${mins} min fa`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours} h fa`;
  return `${Math.floor(hours / 24)} g fa`;
}

function formatEta(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return 'Calcolo tempo…';
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 3) return 'Quasi fatto';
  if (s < 60) return `~ ${s} sec rimanenti`;
  const m = Math.ceil(s / 60);
  return `~ ${m} min rimanenti`;
}

function getOperatorName(requireName = false) {
  // Con l'accesso per operatori il nome e' quello di chi ha fatto il login:
  // cosi ogni azione resta attribuita a una persona reale, non a un campo libero.
  const signedIn = (state.data?.config?.current_user?.display_name || '').trim();
  if (signedIn) return signedIn;

  const name = ($('#operatorInput')?.value || '').trim();
  if (requireName && !name) {
    showToast('Inserisci il tuo nome nel campo Operatore prima di registrare un’azione.', true);
    $('#operatorInput')?.focus();
    return null;
  }
  return name;
}

function renderSession() {
  const user = state.data?.config?.current_user;
  const box = $('#sessionBox');
  const field = $('#operatorField');
  if (!user) return;
  // Il campo libero non serve piu': l'identita' arriva dall'accesso.
  field?.classList.add('hidden');
  box?.classList.remove('hidden');
  const label = $('#sessionName');
  if (label) label.textContent = user.display_name || user.username;
}

async function logout() {
  try {
    await api('/api/logout', { method: 'POST', body: '{}' });
  } catch (err) {
    // Anche se la chiamata fallisce si torna comunque alla pagina di accesso.
  }
  window.location.replace('/login');
}

function showToast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast${error ? ' error' : ''}`;
  el.classList.remove('hidden');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => el.classList.add('hidden'), 3300);
}

function showNotice(text) {
  const n = $('#notice');
  n.textContent = text;
  n.classList.remove('hidden');
}
function hideNotice() { $('#notice').classList.add('hidden'); }

function showTechnicalNotice(lastSync) {
  const n = $('#notice');
  const total = Number(lastSync?.gls_errors || 0);
  const groups = Array.isArray(lastSync?.error_summary) ? lastSync.error_summary : [];
  const samples = Array.isArray(lastSync?.error_samples) ? lastSync.error_samples : [];
  const skipped = Number(lastSync?.skipped_closed || 0);
  const updated = Number(lastSync?.gls_success || 0);
  const groupHtml = groups.length ? groups.map(g => `
    <div class="tech-error-group">
      <div class="tech-error-head"><strong>${esc(g.count)} × ${esc(g.error_title || g.error_code)}</strong><code>${esc(g.error_code || '')}</code></div>
      <div class="tech-error-help">${esc(g.resolution_hint || '')}</div>
    </div>`).join('') : '<div class="tech-error-help">Il monitor ha registrato il messaggio tecnico ma non e ancora riuscito a classificarlo.</div>';
  const sampleHtml = samples.length ? `
    <div class="tech-error-samples">
      <strong>Esempi interessati</strong>
      ${samples.slice(0, 10).map(x => `<div><code>${esc(x.tracking_number || '—')}</code> · ${esc(x.error_title || x.error_code || 'Errore')}<br><span>${esc(x.error_message || '')}</span></div>`).join('')}
    </div>` : '';
  n.innerHTML = `
    <div class="notice-main"><strong>${total} tracking GLS non aggiornati</strong><span>Lo stato precedente e stato mantenuto: nessuna falsa anomalia viene creata.</span></div>
    <details class="tech-error-details">
      <summary>Capisci il problema e come risolverlo</summary>
      <div class="tech-sync-summary">${updated} aggiornati${skipped ? ` · ${skipped} spedizioni finali escluse automaticamente` : ''}</div>
      ${groupHtml}${sampleHtml}
    </details>`;
  n.classList.remove('hidden');
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    cache: 'no-store'
  });
  let payload = {};
  try { payload = await response.json(); } catch {}
  if (response.status === 401 && payload.login_required) {
    // Sessione scaduta o aperta da un'altra rete: si rientra dall'accesso.
    window.location.replace('/login');
    throw new Error('Sessione scaduta');
  }
  if (!response.ok) throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
  return payload;
}

function applyDateFilter(rows) {
  if (!state.dateFrom && !state.dateTo) return rows;
  return rows.filter(x => {
    const key = localDateKey(x.order_created_at);
    if (!key) return false;
    if (state.dateFrom && key < state.dateFrom) return false;
    if (state.dateTo && key > state.dateTo) return false;
    return true;
  });
}

function stockOutcomeKey(item) {
  if (item.status === 'OPEN') return 'OPEN';
  const c = String(item.outcome_category || '').toUpperCase();
  if (c === 'DELIVERED') return 'DELIVERED';
  if (c === 'RETURN') return 'RETURN';
  if (['OUT_FOR_DELIVERY','SCHEDULED','IN_TRANSIT','CORRESPONDENT','SERVICE_AREA','LINEHAUL_DELAY'].includes(c)) return 'MOVED';
  return 'OTHER';
}

function stockOutcomeBadge(item) {
  const key = stockOutcomeKey(item);
  const labels = { OPEN:'IN GIACENZA', DELIVERED:'CONSEGNATA', RETURN:'RIENTRO', MOVED:'RIPARTITA', OTHER:'DA VERIFICARE' };
  const cls = { OPEN:'danger', DELIVERED:'success', RETURN:'warning', MOVED:'worked', OTHER:'neutral' };
  return `<span class="stock-outcome ${cls[key] || 'neutral'}">${esc(labels[key] || key)}</span>`;
}

async function loadStockHistory({quiet = false} = {}) {
  try {
    state.stockData = await api('/api/stocks');
    renderStockHistory();
  } catch (err) {
    if (!quiet) showToast(`Storico giacenze non disponibile: ${err.message}`, true);
  }
}

function filteredStockCases() {
  let rows = [...(state.stockData?.cases || [])];
  if (state.stockSearch) {
    const q = state.stockSearch.toLowerCase();
    rows = rows.filter(x => {
      const instruction = x.latest_instruction?.release_label || x.latest_action?.action_label || '';
      return [x.order_name, x.customer_name, x.tracking_number, x.entry_state, x.entry_note, x.outcome_state, x.gls_status, instruction, x.last_operator_name]
        .some(v => String(v || '').toLowerCase().includes(q));
    });
  }
  if (state.stockOutcome) rows = rows.filter(x => stockOutcomeKey(x) === state.stockOutcome);
  if (state.stockHandled === 'yes') rows = rows.filter(x => x.handled);
  if (state.stockHandled === 'no') rows = rows.filter(x => !x.handled);
  if (state.stockDateFrom || state.stockDateTo) {
    rows = rows.filter(x => {
      const k = localDateKey(x.entered_at || x.created_at);
      if (!k) return false;
      if (state.stockDateFrom && k < state.stockDateFrom) return false;
      if (state.stockDateTo && k > state.stockDateTo) return false;
      return true;
    });
  }
  rows = sortRows(rows, state.stockSortKey, state.stockSortDir, (key,a,b) => {
    const instructionA = a.latest_instruction?.release_label || a.latest_action?.action_label || '';
    const instructionB = b.latest_instruction?.release_label || b.latest_action?.action_label || '';
    const map = {
      outcome:[stockOutcomeKey(a), stockOutcomeKey(b),'text'],
      order:[orderNumeric(a.order_name), orderNumeric(b.order_name),'number'],
      customer:[a.customer_name,b.customer_name,'text'],
      entered:[a.entered_at || a.created_at,b.entered_at || b.created_at,'date'],
      activity:[instructionA,instructionB,'text'],
      current:[a.outcome_state || a.gls_status,b.outcome_state || b.gls_status,'text'],
    };
    return map[key] || map.entered;
  });
  return rows;
}

function renderStockHistory() {
  const summary = state.stockData?.summary || {};
  const rows = filteredStockCases();
  $('#stockTotal').textContent = summary.total || 0;
  $('#stockOpen').textContent = summary.open || 0;
  $('#stockHandled').textContent = summary.handled || 0;
  $('#stockDelivered').textContent = summary.delivered || 0;
  $('#stockReturned').textContent = summary.returned || 0;
  $('#tabStockCount').textContent = summary.total || 0;
  $('#stockMeta').textContent = `${rows.length} casi nella vista · ${summary.total || 0} giacenze storiche memorizzate`;
  updateSortIndicators('.stock-sort', state.stockSortKey, state.stockSortDir);
  const body = $('#stockBody');
  const empty = $('#stockEmpty');
  body.innerHTML = rows.map(item => {
    const instruction = item.latest_instruction;
    const action = item.latest_action;
    const actionText = instruction?.release_label || action?.action_label || 'Nessuna istruzione registrata';
    const actionBy = instruction?.operator_name || action?.operator_name || '';
    const actionAt = instruction?.created_at || action?.created_at || '';
    const glsResult = instruction ? (instruction.gls_success ? `GLS: ${instruction.gls_result || 'OK'}` : `Errore GLS: ${instruction.gls_result || 'non confermato'}`) : '';
    return `<tr class="shipment-row ${item.status === 'OPEN' ? 'CRITICAL' : ''}">
      <td>${stockOutcomeBadge(item)}</td>
      <td><div class="order-main">${esc(item.order_name || '—')}</div><div class="tracking-code">${esc(item.tracking_number)}</div>${item.is_cod ? '<div class="cod-badge">COD</div>' : ''}</td>
      <td><div class="customer-main">${esc(item.customer_name || '—')}</div><div class="cell-sub">${esc([item.city,item.province].filter(Boolean).join(' · '))}</div></td>
      <td><strong>${esc(formatDate(item.entered_at))}</strong><div class="cell-sub">${esc(item.entry_state || item.entry_note || 'Giacenza GLS')}</div></td>
      <td><div class="activity-box ${item.handled ? '' : 'muted-box'}"><div class="activity-label">${esc(actionText)}</div>${actionBy ? `<div class="activity-meta">${esc(actionBy)} · ${esc(formatDate(actionAt))}</div>` : ''}${glsResult ? `<div class="activity-meta">${esc(glsResult)}</div>` : ''}</div></td>
      <td><strong>${esc(item.outcome_label || '—')}</strong><div class="cell-sub">${esc(item.outcome_state || item.gls_status || '')}</div>${item.outcome_event_at ? `<div class="cell-sub">${esc(formatDate(item.outcome_event_at))}</div>` : item.exited_at ? `<div class="cell-sub">uscita ${esc(formatDate(item.exited_at))}</div>` : ''}</td>
      <td><button class="btn small secondary open-stock" data-tracking="${esc(item.tracking_number)}">Apri</button></td>
    </tr>`;
  }).join('');
  empty.classList.toggle('hidden', rows.length > 0);
  $$('.open-stock').forEach(btn => btn.addEventListener('click', () => openDrawer(btn.dataset.tracking)));
}

async function loadDashboard({quiet = false} = {}) {
  try {
    const data = await api('/api/dashboard?include_closed=true');
    state.data = data;
    renderSession();
    aggiornaTendinaStati();
    renderKpis();
    renderConnection();
    renderTable();
    // Una sincronizzazione avviata dallo scheduler deve comparire comunque:
    // fra un sondaggio e l'altro il pulsante resterebbe altrimenti cliccabile.
    if (data.sync_running) {
      setSyncBusy(true);
      startSyncPolling();
    }
    if (data.sync_running) startSyncPolling();
  } catch (err) {
    if (!quiet) showNotice(`Errore caricamento dashboard: ${err.message}`);
  }
}

function renderConnection() {
  const cfg = state.data?.config || {};
  const badge = $('#connectionBadge');
  if (cfg.mock_mode) {
    badge.innerHTML = '<span class="status-dot"></span> Modalità demo';
    badge.className = 'status-chip neutral';
  } else if (cfg.shopify_configured && cfg.gls_tracking_configured) {
    if (state.data?.sync_running) {
      badge.innerHTML = '<span class="status-dot"></span> Sincronizzazione';
      badge.className = 'status-chip running';
    } else {
      badge.innerHTML = '<span class="status-dot"></span> Shopify + GLS online';
      badge.className = 'status-chip ok';
    }
  } else {
    badge.innerHTML = '<span class="status-dot"></span> Configurazione incompleta';
    badge.className = 'status-chip neutral';
  }

  const missing = [];
  if (!cfg.mock_mode && !cfg.shopify_configured) missing.push('Shopify');
  if (!cfg.mock_mode && !cfg.gls_tracking_configured) missing.push('GLS');
  const technicalErrors = Number(state.data?.last_sync?.gls_errors || 0);
  if (missing.length) {
    showNotice(`Configurazione mancante: ${missing.join(' e ')}.`);
  } else if (technicalErrors > 0 && !state.data?.sync_running) {
    showTechnicalNotice(state.data?.last_sync || {});
  } else {
    hideNotice();
  }
}

function segnalaStatiDaClassificare(quanti) {
  // "Stati nuovi" vive nell'ingranaggio: se GLS inventa un testo che nessuna
  // regola riconosce, senza un segno nessuno andrebbe mai a guardare.
  const bottone = $('#settingsBtn');
  const voce = $$('.settings-item').find(v => v.dataset.view === 'unknown');
  if (bottone) bottone.classList.toggle('ha-novita', quanti > 0);
  if (voce) {
    voce.textContent = quanti > 0
      ? `Stati nuovi da classificare · ${quanti}`
      : 'Stati nuovi da classificare';
    voce.classList.toggle('con-avviso', quanti > 0);
  }
}

function aggiornaTendinaStati() {
  // L'elenco viene dagli stati realmente incontrati da GLS, non da una lista
  // fissa: i testi del corriere cambiano nel tempo e una lista scritta a mano
  // invecchierebbe in silenzio.
  const menu = $('#glsStateFilter');
  if (!menu) return;
  const stati = [...(state.data?.stati_gls || [])].sort((a, b) => a.localeCompare(b, 'it'));
  const scelto = state.glsState;
  menu.innerHTML = `<option value="">Tutti gli stati GLS</option>` +
    stati.map(t => `<option value="${esc(t)}" ${t === scelto ? 'selected' : ''}>${esc(t.length > 70 ? t.slice(0, 68) + '…' : t)}</option>`).join('');
  if (scelto && !stati.includes(scelto)) {
    state.glsState = '';
    menu.value = '';
  }
}

function renderKpis() {
  const rows = applyDateFilter([...(state.data?.shipments || [])]);
  const actionable = rows.filter(daVerificare);
  const watch = rows.filter(x => !x.closed && (x.effective_severity || x.severity) === 'WATCH').length;
  const working = rows.filter(x => !x.closed && ['IN_PROGRESS','WAITING_CUSTOMER','WAITING_GLS'].includes(x.workflow_status)).length;
  const delivered = rows.filter(x => x.category === 'DELIVERED').length;
  const returned = rows.filter(x => x.category === 'RETURN').length;
  const inConsegna = rows.filter(x => !x.closed && CATEGORIE_IN_CONSEGNA.includes(x.category)).length;
  const attive = rows.filter(x => !x.closed).length;
  const unknown = rows.filter(x => !x.closed && x.category === 'UNCLASSIFIED').length;
  $('#kpiVerify').textContent = actionable.length;
  $('#kpiWatch').textContent = watch;
  $('#kpiWorking').textContent = working;
  $('#tabActionCount').textContent = actionable.length;
  $('#tabWorkingCount').textContent = working;
  $('#tabDeliveredCount').textContent = delivered;
  $('#tabReturnedCount').textContent = returned;
  $('#tabDeliveringCount').textContent = inConsegna;
  $('#tabActiveCount').textContent = attive;
  segnalaStatiDaClassificare(unknown);
  $('#tabAllCount').textContent = rows.length;
}

function filteredShipments() {
  let rows = applyDateFilter([...(state.data?.shipments || [])]);
  if (state.view === 'action') rows = rows.filter(x => daVerificare(x));
  if (state.view === 'working') rows = rows.filter(x => !x.closed && ['IN_PROGRESS','WAITING_CUSTOMER','WAITING_GLS'].includes(x.workflow_status));
  if (state.view === 'active') rows = rows.filter(x => !x.closed);
  // 'all' non filtra: ogni spedizione affidata a GLS, esito definitivo compreso.
  if (state.view === 'delivered') rows = rows.filter(x => x.category === 'DELIVERED');
  if (state.view === 'returned') rows = rows.filter(x => x.category === 'RETURN');
  if (state.view === 'delivering') rows = rows.filter(x => !x.closed && CATEGORIE_IN_CONSEGNA.includes(x.category));
  if (state.view === 'unknown') rows = rows.filter(x => !x.closed && x.category === 'UNCLASSIFIED');
  if (state.severity) rows = rows.filter(x => (x.effective_severity || x.severity) === state.severity);
  if (state.workflow) rows = rows.filter(x => x.workflow_status === state.workflow);
  if (state.glsState) {
    const vocabolario = state.data?.stati_gls || [];
    const indice = vocabolario.indexOf(state.glsState);
    rows = rows.filter(x => state.glsStateMode === 'any'
      ? (x.stati_storico || []).includes(indice)
      : String(x.gls_status || '').trim() === state.glsState);
  }
  if (state.codOnly) rows = rows.filter(x => x.is_cod);
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter(x => [
      x.order_name, x.customer_name, x.customer_email, x.customer_phone, x.tracking_number,
      x.gls_status, x.gls_note, x.gls_code, x.city, x.gls_destination_city,
      x.last_operator_action, x.last_operator_name
    ].some(v => String(v || '').toLowerCase().includes(q)));
  }
  rows = sortRows(rows, state.sortKey, state.sortDir, (key,a,b) => {
    const map = {
      priority:[priorityRank(a),priorityRank(b),'number'],
      order:[orderNumeric(a.order_name),orderNumeric(b.order_name),'number'],
      customer:[a.customer_name,b.customer_name,'text'],
      status:[a.gls_status || a.gls_note,b.gls_status || b.gls_note,'text'],
      updated:[a.gls_event_at,b.gls_event_at,'date'],
      workflow:[workflowLabels[a.workflow_status] || a.workflow_status,workflowLabels[b.workflow_status] || b.workflow_status,'text'],
    };
    return map[key] || map.priority;
  });
  return rows;
}

function renderTable() {
  if (['rules','stocks'].includes(state.view)) return;
  const rows = filteredShipments();
  const body = $('#shipmentsBody');
  const empty = $('#emptyState');
  const titles = {
    action: 'Spedizioni da verificare', working: 'Pratiche in lavorazione',
    active: 'Tutte le spedizioni GLS ancora in corso', all: 'Tutte le spedizioni affidate a GLS',
    delivered: 'Consegnati al cliente · chiusi', returned: 'Rientrati al mittente · chiusi',
    delivering: 'In viaggio verso il cliente', unknown: 'Nuovi stati GLS'
  };
  $('#tableTitle').textContent = titles[state.view] || 'Spedizioni GLS';
  const total = (state.data?.shipments || []).filter(x => !x.closed).length;
  const last = state.data?.last_sync;
  if (last?.finished_at) {
    const skipped = Number(last.skipped_closed || 0);
    $('#lastSync').textContent = `Ultimo sync ${formatDate(last.finished_at)}${skipped ? ` · ${skipped} finali escluse` : ''}`;
  } else { $('#lastSync').textContent = 'Mai aggiornato'; }
  updateSortIndicators('.sort-head:not(.stock-sort)', state.sortKey, state.sortDir);

  body.innerHTML = rows.map(row => {
    const current = row.gls_status || 'Stato GLS non disponibile';
    const note = row.gls_note || '';
    const hasAction = Boolean(row.last_operator_action);
    const sev = row.effective_severity || row.severity;
    const activity = hasAction
      ? `<div class="activity-box"><div class="activity-label">${esc(row.last_operator_action)}</div><div class="activity-meta">${esc(row.last_operator_name || 'Operatore')} · ${esc(age(row.last_operator_action_at))}</div></div>`
      : `<div class="no-activity">Nessuna azione registrata</div>`;
    const location = [row.city, row.province].filter(Boolean).join(' · ') || '—';
    return `
      <tr class="shipment-row ${esc(sev)} ${hasAction ? 'has-action' : ''}">
        <td><span class="priority-pill ${esc(sev)}">${esc(severityLabels[sev] || sev)}</span></td>
        <td><div class="order-main">${esc(row.order_name || '—')}</div><div class="cell-sub">${esc(formatDate(row.order_created_at, false))} · ${esc(formatMoney(row.total_amount, row.currency))}${row.is_cod ? ' · COD' : ''}</div><div class="tracking-code">${esc(row.tracking_number)}</div></td>
        <td><div class="customer-main">${esc(row.customer_name || 'Cliente non disponibile')}</div><div class="cell-sub">${esc(location)}</div></td>
        <td><div class="event-title">${esc(current)}</div>${note ? `<div class="event-note" title="${esc(note)}">${esc(note)}</div>` : ''}</td>
        <td><div>${esc(age(row.gls_event_at))}</div><div class="cell-sub">${esc(formatDate(row.gls_event_at))}</div></td>
        <td>${pastigliaNovita(row)}${pastigliaGestione(row)}<span class="workflow-pill ${esc(row.workflow_status)}">${esc(workflowLabels[row.workflow_status] || row.workflow_status)}</span>${activity}</td>
        <td><button class="row-btn" data-open="${esc(row.tracking_number)}">Gestisci →</button></td>
      </tr>`;
  }).join('');
  empty.classList.toggle('hidden', rows.length !== 0);
  if (rows.length === 0) renderEmptyState(empty, total);
  // Nell'archivio completo il metro di paragone e' il totale affidato a GLS,
  // non le sole spedizioni ancora in corso.
  const complessive = (state.data?.shipments || []).length;
  const riferimento = state.view === 'all'
    ? `${complessive} affidate a GLS`
    : `${total} GLS attive monitorate`;
  $('#tableMeta').textContent = filtriAttivi()
    ? `${rows.length} con i filtri attivi · ${riferimento}`
    : `${rows.length} nella vista corrente · ${riferimento}`;
  $$('[data-open]').forEach(btn => btn.addEventListener('click', () => openDrawer(btn.dataset.open)));
}

function filtriAttivi() {
  return Boolean(state.severity || state.workflow || state.codOnly || state.search
    || state.dateFrom || state.dateTo);
}

function renderEmptyState(empty, monitored) {
  // Distinzione essenziale: la coda e' davvero vuota, oppure ci sono righe che
  // i filtri stanno nascondendo? Senza dirlo, sembra che i dati siano spariti.
  if (filtriAttivi()) {
    empty.innerHTML = `<div class="empty-icon">⌕</div>
      <h3>Nessun risultato con i filtri attivi</h3>
      <p>Ci sono <b>${monitored}</b> spedizioni monitorate, ma nessuna corrisponde ai filtri impostati.</p>
      <div class="empty-actions"><button class="btn secondary" type="button" data-clear-filters>Azzera i filtri</button></div>`;
    empty.querySelector('[data-clear-filters]')?.addEventListener('click', resetFilters);
    return;
  }
  return renderEmptyStateVuota(empty, monitored);
}

function renderEmptyStateVuota(empty, monitored) {
  // Una vista vuota non significa "nessuna spedizione": le code operative
  // mostrano solo cio' che richiede attenzione. Senza distinguere i due casi
  // sembra che la sincronizzazione non abbia caricato nulla.
  const nessunDato = monitored === 0;
  const plurale = monitored === 1 ? 'spedizione GLS attiva' : 'spedizioni GLS attive';
  const vaiATutte = `<button class="btn secondary" type="button" data-goto-view="active">Vedi tutte le attive</button>`;

  const messaggi = {
    action: nessunDato
      ? ['Nessuna spedizione caricata', 'Premi <b>Aggiorna</b> per la prima sincronizzazione con Shopify e GLS.']
      : ['Nessuna spedizione da verificare',
         `Le <b>${monitored}</b> ${plurale} stanno viaggiando senza anomalie. Qui compaiono solo i casi che richiedono un intervento.`],
    working: ['Nessuna pratica in lavorazione',
              'Compaiono qui le spedizioni che un operatore ha preso in carico dal pannello <b>Gestisci</b>.'],
    active: nessunDato
      ? ['Nessuna spedizione caricata', 'Premi <b>Aggiorna</b> per la prima sincronizzazione con Shopify e GLS.']
      : ['Nessuna spedizione attiva', 'Tutte le spedizioni monitorate hanno raggiunto un esito finale.'],
    delivered: ['Nessuna consegna registrata', 'Qui finiscono le spedizioni consegnate al cliente.'],
    returned: ['Nessun rientro registrato', 'Qui finiscono le spedizioni tornate al mittente.'],
    delivering: ['Nessuna spedizione in viaggio', 'Qui compaiono le spedizioni in transito o in consegna oggi: da seguire, non da gestire.'],
    all: nessunDato
      ? ['Nessuna spedizione caricata', 'Premi <b>Aggiorna</b> per la prima sincronizzazione con Shopify e GLS.']
      : ['Nessuna spedizione', 'Nessuna spedizione affidata a GLS nel periodo selezionato.'],
    unknown: ['Nessuno stato GLS sconosciuto', 'Tutti gli stati ricevuti sono gia' + String.fromCharCode(39) + ' classificati dalle regole.'],
  };

  const [titolo, dettaglio] = messaggi[state.view] || ['Niente da mostrare', ''];
  const mostraPulsante = !nessunDato && !['active', 'all'].includes(state.view);
  empty.innerHTML = `<div class="empty-icon">✓</div><h3>${titolo}</h3><p>${dettaglio}</p>${mostraPulsante ? `<div class="empty-actions">${vaiATutte}</div>` : ''}`;
  empty.querySelector('[data-goto-view]')?.addEventListener('click', () => switchView('active'));
}

async function openDrawer(tracking) {
  state.selectedTracking = tracking;
  try {
    const item = await api(`/api/shipment/${encodeURIComponent(tracking)}`);
    $('#drawerOrder').textContent = item.order_name || 'SPEDIZIONE GLS';
    $('#drawerTitle').textContent = item.customer_name || item.tracking_number;
    $('#drawerContent').innerHTML = drawerHtml(item);
    $('#drawerBackdrop').classList.remove('hidden');
    $('#drawer').classList.add('open');
    $('#drawer').setAttribute('aria-hidden', 'false');
    bindDrawer(item);
  } catch (err) { showToast(err.message, true); }
}

function closeDrawer() {
  $('#drawerBackdrop').classList.add('hidden');
  $('#drawer').classList.remove('open');
  $('#drawer').setAttribute('aria-hidden', 'true');
  state.selectedTracking = null;
}

function drawerHtml(item) {
  const events = item.events || [];
  const actions = item.operator_actions || [];
  const links = item.links || {};
  const eventCode = item.gls_code || '';
  const sev = item.effective_severity || item.severity;
  const contactPhone = item.customer_phone ? `<a href="tel:${esc(item.customer_phone)}">${esc(item.customer_phone)}</a>` : '—';
  const contactEmail = item.customer_email ? `<a href="mailto:${esc(item.customer_email)}">${esc(item.customer_email)}</a>` : '—';
  const glsPhone = item.gls_destination_phone ? `<a href="tel:${esc(item.gls_destination_phone)}">${esc(item.gls_destination_phone)}</a>` : '—';
  const spokiPhone = phoneDigits(item.customer_phone || '');
  const spokiUrl = spokiPhone ? `https://app.spoki.com/contacts?search=${encodeURIComponent(spokiPhone)}` : '';

  // Se la pratica e' gia' stata lavorata, la prima cosa che si deve vedere e'
  // cosa era stato raccolto: serve per compilare lo svincolo senza cercarlo
  // nella cronologia.
  const gestione = statoGestione(item);
  // La nota puo' stare nel campo della pratica oppure in un'azione registrata:
  // all'operatore serve vederla comunque, da qualunque strada sia arrivata.
  const notaAzione = (actions.find(a => (a.note || '').trim()) || {}).note || '';
  const notaOperatore = (item.operator_note || '').trim() || notaAzione.trim();
  const internalBanner = gestione ? `
    <div class="internal-banner handled ${gestione.classe}">
      <div class="icon">${gestione.icona}</div>
      <div>
        <strong>${esc(gestione.testo)} · ${esc(item.last_operator_name || 'Operatore')} il ${esc(formatDate(item.last_operator_action_at))}</strong>
        ${notaOperatore ? `<em class="handled-note">${esc(notaOperatore)}</em>` : ''}
        <span>${esc(gestione.dettaglio)}</span>
      </div>
    </div>`
    : item.last_operator_action ? `
    <div class="internal-banner"><div class="icon">✓</div><div><strong>Intervento già registrato: ${esc(item.last_operator_action)}</strong><span>${esc(item.last_operator_name || 'Operatore')} · ${esc(formatDate(item.last_operator_action_at))}. Lo stato GLS può restare invariato finché la sede non recepisce l'operazione.</span></div></div>` : '';

  // La novita' arrivata da GLS su una pratica gia' in lavorazione: va vista
  // subito, prima di ogni altra cosa nel pannello.
  const newsBanner = item.unread_event_at ? `
    <div class="news-banner">
      <div class="icon">!</div>
      <div>
        <strong>Novit\u00e0 da GLS dopo la tua ultima nota</strong>
        <span>${esc(formatDate(item.unread_event_at))} · ${esc(item.gls_status || 'Nuovo evento GLS')}${item.gls_note ? ` · ${esc(item.gls_note)}` : ''}</span>
        <em>La pratica \u00e8 rimasta in lavorazione: l'avviso sparisce quando salvi una nota o registri un'azione.</em>
      </div>
    </div>` : '';


  const stockCases = item.stock_cases || [];
  const latestStock = stockCases[0] || null;
  const hasOpenStock = Boolean(latestStock && latestStock.status === 'OPEN') || item.category === 'STORAGE';
  const stockHistoryBox = stockCases.length ? `
    <div class="section-box stock-history-box"><div class="section-title-row"><h3>Storico giacenze</h3><span>${stockCases.length} ${stockCases.length===1?'episodio':'episodi'}</span></div><div class="stock-case-list">
      ${stockCases.map(sc => {
        const releases = sc.release_requests || [];
        const lastRelease = releases[0];
        const outcome = sc.status === 'OPEN' ? 'Ancora in giacenza' : (sc.outcome_state || sc.outcome_category || 'Uscita dalla giacenza');
        return `<div class="stock-case-card ${sc.status === 'OPEN' ? 'open' : 'closed'}"><div class="stock-case-head"><strong>Entrata ${esc(formatDate(sc.entered_at))}</strong><span>${sc.status === 'OPEN' ? 'APERTA' : 'CHIUSA'}</span></div><div class="stock-case-state">${esc(sc.entry_state || sc.entry_note || 'Spedizione in giacenza')}</div>${lastRelease ? `<div class="stock-instruction"><b>Istruzione inviata:</b> ${esc(lastRelease.release_label)} · ${esc(lastRelease.operator_name || 'Operatore')} · ${esc(formatDate(lastRelease.created_at))}<br><span>${lastRelease.gls_success ? 'Ricevuta da GLS' : 'Non accettata da GLS'}${lastRelease.gls_result ? ` · ${esc(lastRelease.gls_result)}` : ''}</span>${lastRelease.note ? `<br><em>${esc(lastRelease.note)}</em>` : ''}</div>` : '<div class="stock-instruction muted">Nessuna istruzione API registrata per questa giacenza.</div>'}<div class="stock-outcome-line"><b>Esito:</b> ${esc(outcome)}${sc.outcome_event_at ? ` · ${esc(formatDate(sc.outcome_event_at))}` : sc.exited_at ? ` · uscita ${esc(formatDate(sc.exited_at))}` : ''}</div></div>`;
      }).join('')}
    </div></div>` : '';

  const releaseBox = hasOpenStock ? `
    <div class="section-box release-box">
      <div class="section-title-row"><h3>Gestisci giacenza via API GLS</h3><span>istruzione + risposta GLS restano nello storico</span></div>
      <div class="release-warning">La richiesta viene registrata subito. Se il tracking resta fermo, il monitor confronterà automaticamente istruzione e movimenti GLS e segnalerà eventuali incongruenze.</div>
      <div class="release-grid">
        <label class="field-span-2">Istruzione<select id="releaseType"><option value="1">Ritenta consegna allo stesso indirizzo</option><option value="2">Consegna a un indirizzo diverso</option><option value="3">Ritorno al mittente</option><option value="7">Ritiro del destinatario presso la sede GLS</option><option value="8">Consegna parziale e rientro</option><option value="4">Distruzione</option><option value="9">Consegna parziale e distruzione</option></select></label>
        <label>Data riconsegna <input id="releaseDate" type="date" value="${prossimoGiornoLavorativo()}" min="${dateKeyFromDate(new Date())}"></label>
        <label id="expensePayerField">Spese riconsegna<select id="releaseExpensePayer"><option value="sender" selected>A carico mittente</option><option value="recipient">A carico destinatario</option><option value="">Non specificato</option></select></label>
        <label>Telefono destinatario <input id="releasePhone" type="tel" value="${esc(localItalianPhone(item.customer_phone || ''))}" maxlength="15"></label>
        <label class="check-field"><input id="releasePhoneNotice" type="checkbox" checked> Preavviso telefonico</label>
        ${item.is_cod ? `<label class="check-field"><input id="releaseCancelCod" type="checkbox"> Annulla contrassegno</label>` : ''}
        <label class="field-span-2">Note GLS <textarea id="releaseNote" maxlength="65" placeholder="Massimo 65 caratteri"></textarea></label>
        <div id="newAddressFields" class="new-address-fields hidden field-span-2"><label>Destinatario <input id="releaseNewName" maxlength="20"></label><label>Nuovo indirizzo <input id="releaseNewAddress" maxlength="30"></label><label>Località <input id="releaseNewCity" maxlength="30"></label><label>CAP <input id="releaseNewZip" maxlength="5"></label><label>Provincia <input id="releaseNewProvince" maxlength="3"></label></div>
      </div>
      <div class="release-actions"><button id="sendReleaseBtn" class="btn primary">Invia istruzione a GLS</button><button class="btn secondary quick-action-inline" data-action="STOCK_MANUAL_HANDLED">Registra gestione fatta fuori dal tool</button></div>
    </div>` : '';

  // Lo storico racconta cosa ha fatto GLS e cosa hanno fatto le persone. I
  // passaggi di stato decisi dal sistema sono meccanica interna: restano
  // registrati, ma non affollano la lettura.
  const azioniUmane = actions.filter(a => (a.operator_name || '').trim().toLowerCase() !== 'sistema');
  // La croce compare solo sull'ultima operazione registrata: si torna indietro
  // un passo per volta, senza poter riscrivere lo storico a meta'.
  const ultimaAzioneId = azioniUmane.length ? azioniUmane[0].id : null;

  const combined = [
    ...events.map(ev => ({when: ev.event_at || ev.created_at, source:'GLS', severity:ev.severity || 'WATCH', title:ev.state || 'Evento GLS', note:ev.note || '', meta:[ev.location, ev.code ? `codice ${ev.code}` : ''].filter(Boolean).join(' · ')})),
    ...azioniUmane.map(a => ({when:a.created_at, source:'TEAM', severity:'TEAM', title:a.action_label || a.action_type, note:a.note || '', meta:a.operator_name || 'Operatore', actionId:a.id})),
  ].sort((a,b) => (new Date(b.when || 0).getTime()||0) - (new Date(a.when || 0).getTime()||0));

  return `
    <div class="status-card ${esc(sev)}"><span class="priority-pill ${esc(sev)}">${esc(severityLabels[sev] || sev)}</span><h3>${esc(item.gls_status || 'Stato GLS non disponibile')}</h3>${item.gls_note ? `<p>${esc(item.gls_note)}</p>` : ''}${item.reason ? `<p class="muted" style="margin-top:7px">${esc(item.reason)}</p>` : ''}</div>
    ${newsBanner}${internalBanner}

    <div class="detail-grid">
      <div class="detail-item"><label>Ordine Shopify</label><strong>${esc(item.order_name || '—')}</strong><div class="muted">${esc(formatMoney(item.total_amount, item.currency))} · ${esc(formatDate(item.order_created_at, false))}</div></div>
      <div class="detail-item"><label>Pagamento</label><strong>${item.is_cod ? 'Contrassegno' : esc((item.payment_gateways || []).join(', ') || '—')}</strong><div class="muted">${esc(item.shopify_financial_status || '')}</div></div>
      <div class="detail-item tracking-detail"><label>Tracking GLS</label><strong class="tracking-code tracking-code-large">${esc(item.tracking_number)}</strong><div class="muted">${eventCode ? `Codice ${esc(eventCode)}` : 'Codice evento non disponibile'}</div></div>
      <div class="detail-item"><label>Ultimo evento GLS</label><strong>${esc(formatDate(item.gls_event_at))}</strong><div class="muted">${esc(item.gls_location || '')}</div></div>
      <div class="detail-item"><label>Cliente</label><strong>${esc(item.customer_name || '—')}</strong><div>${contactPhone}</div><div>${contactEmail}</div></div>
      <div class="detail-item"><label>Sede GLS destinataria</label><strong>${esc([item.gls_destination_depot, item.gls_destination_city].filter(Boolean).join(' · ') || '—')}</strong><div>${glsPhone}</div></div>
    </div>

    <div class="detail-item"><label>Azione consigliata</label><strong>${esc(item.recommended_action || 'Verificare lo stato e decidere l’intervento operativo.')}</strong></div>
    <div class="link-row action-links">
      ${spokiUrl ? `<a class="btn primary" target="_blank" rel="noopener" href="${esc(spokiUrl)}">Contatta cliente</a>` : ''}
      ${item.gls_destination_phone ? `<a class="btn secondary" href="tel:${esc(item.gls_destination_phone)}">Chiama GLS</a>` : ''}
      ${links.gls ? `<button id="openGlsIncognitoBtn" class="btn secondary" type="button">${state.data?.config?.native_incognito === false ? 'Tracking GLS' : 'Tracking GLS · Incognito'}</button>` : ''}
      ${links.shopify ? `<a class="btn secondary" target="_blank" rel="noopener" href="${esc(links.shopify)}">Ordine Shopify</a>` : ''}
      ${links.shopify_profile ? `<a class="btn secondary" target="_blank" rel="noopener" href="${esc(links.shopify_profile)}">Profilo Shopify</a>` : ''}
    </div>

    <div class="section-box"><div class="section-title-row"><h3>Registra intervento</h3><span>ogni azione entra nello storico unico</span></div><div class="quick-actions">
      <button class="quick-action" data-action="CUSTOMER_MESSAGE"><span class="qa-icon">✉</span><span>Messaggio cliente inviato</span></button>
      <button class="quick-action" data-action="CUSTOMER_CALLED"><span class="qa-icon">☏</span><span>Cliente chiamato</span></button>
      <button class="quick-action" data-action="GLS_CONTACTED"><span class="qa-icon">G</span><span>GLS contattato</span></button>
      <button class="quick-action" data-action="NOTE"><span class="qa-icon">＋</span><span>Registra nota</span></button>
    </div><textarea id="quickActionNote" class="action-note" placeholder="Nota opzionale: es. cliente conferma indirizzo, contattata sede GLS…"></textarea></div>

    ${releaseBox}${stockHistoryBox}

    <div class="section-box"><div class="section-title-row"><h3>Stato pratica interno</h3><span>puoi lasciarla aperta oppure chiuderla manualmente</span></div><div class="workflow-buttons"><button class="workflow-btn ${item.workflow_status==='IN_PROGRESS'?'active':''}" data-workflow="IN_PROGRESS">IN LAVORAZIONE</button><button class="workflow-btn ${['RESOLVED','IGNORED'].includes(item.workflow_status)?'active':''}" data-workflow="RESOLVED">CHIUSA</button></div><textarea id="operatorNote" class="operator-note" placeholder="Nota pratica generale…">${esc(item.operator_note || '')}</textarea><div class="link-row"><button id="saveWorkflowBtn" class="btn primary">Salva pratica</button></div></div>

    <div class="section-box"><div class="section-title-row"><h3>Storico completo</h3><span>GLS + attività operatori · ${combined.length} eventi</span></div><div class="timeline combined-timeline">
      ${combined.length ? combined.map(e => `<div class="timeline-item ${e.source==='TEAM'?'team':esc(e.severity)}">${e.actionId && e.actionId === ultimaAzioneId ? `<button class="undo-action" type="button" data-undo-action="${e.actionId}" title="Cancella questa operazione: \u00e8 l\u2019ultima registrata da un operatore">\u00d7</button>` : ''}<div class="timeline-time"><span class="source-badge ${e.source==='TEAM'?'team':'gls'}">${e.source}</span> ${esc(formatDate(e.when))}${e.meta ? ` · ${esc(e.meta)}` : ''}</div><div class="timeline-state">${esc(e.title)}</div>${e.note ? `<div class="timeline-note">${esc(e.note)}</div>` : ''}</div>`).join('') : '<div class="muted">Nessuno storico disponibile.</div>'}
    </div></div>`;
}

function bindDrawer(item) {
  let workflow = item.workflow_status || 'NEW';
  $$('[data-workflow]').forEach(btn => btn.addEventListener('click', () => {
    workflow = btn.dataset.workflow;
    $$('[data-workflow]').forEach(x => x.classList.toggle('active', x === btn));
  }));

  $('#openGlsIncognitoBtn')?.addEventListener('click', async () => {
    // L'apertura in incognito la pilota il backend, possibile solo sul Mac locale.
    // Online si apre una scheda normale sulla pagina ufficiale GLS.
    if (state.data?.config?.native_incognito === false) {
      const url = item.links?.gls;
      if (url) window.open(url, '_blank', 'noopener');
      return;
    }
    try {
      await api('/api/open-incognito', {method:'POST', body:JSON.stringify({tracking:item.tracking_number})});
      showToast('Tracking GLS aperto in Chrome Incognito');
    } catch (err) { showToast(err.message, true); }
  });

  $$('[data-undo-action]').forEach(btn => btn.addEventListener('click', async () => {
    if (!confirm('Cancellare questa operazione dallo storico?\n\nLa pratica torner\u00e0 a valere per quello che dice lo storico rimasto.')) return;
    btn.disabled = true;
    try {
      const esito = await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/annulla-azione`, {
        method: 'POST',
        body: JSON.stringify({action_id: Number(btn.dataset.undoAction)}),
      });
      showToast(`Operazione cancellata · pratica ${workflowLabels[esito.workflow_status] || esito.workflow_status}`);
      await loadDashboard({quiet:true});
      await openDrawer(item.tracking_number);
    } catch (err) {
      showToast(err.message, true);
      btn.disabled = false;
    }
  }));

  $$('[data-action]').forEach(btn => btn.addEventListener('click', async () => {
    const operatorName = getOperatorName(true);
    if (!operatorName) return;
    btn.disabled = true;
    try {
      await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/action`, {
        method: 'POST',
        body: JSON.stringify({
          action_type: btn.dataset.action,
          note: $('#quickActionNote')?.value || '',
          operator_name: operatorName,
        })
      });
      showToast('Intervento registrato');
      await loadDashboard({quiet:true});
      await openDrawer(item.tracking_number);
    } catch (err) {
      showToast(err.message, true);
      btn.disabled = false;
    }
  }));

  const releaseType = $('#releaseType');
  if (releaseType) {
    const toggleReleaseFields = () => {
      $('#newAddressFields')?.classList.toggle('hidden', releaseType.value !== '2');
      $('#expensePayerField')?.classList.toggle('hidden', !['1','2'].includes(releaseType.value));
    };
    releaseType.addEventListener('change', toggleReleaseFields);
    toggleReleaseFields();
  }

  $('#sendReleaseBtn')?.addEventListener('click', async () => {
    const operatorName = getOperatorName(true);
    if (!operatorName) return;
    const type = $('#releaseType')?.value || '';
    const label = $('#releaseType')?.selectedOptions?.[0]?.textContent || 'istruzione GLS';
    const destructive = ['4','9'].includes(type);
    const question = destructive
      ? `ATTENZIONE: stai per inviare a GLS “${label}”. Confermi?`
      : `Inviare a GLS l'istruzione “${label}” per ${item.tracking_number}?`;
    if (!window.confirm(question)) return;
    const btn = $('#sendReleaseBtn');
    btn.disabled = true;
    btn.textContent = 'Invio a GLS…';
    try {
      const payload = {
        operator_name: operatorName,
        release_type: type,
        delivery_date: $('#releaseDate')?.value || '',
        expense_payer: $('#releaseExpensePayer')?.value || '',
        recipient_phone: $('#releasePhone')?.value || '',
        customer_phone: item.customer_phone || '',
        phone_notice: Boolean($('#releasePhoneNotice')?.checked),
        cancel_cod: Boolean($('#releaseCancelCod')?.checked),
        note: $('#releaseNote')?.value || '',
        new_name: $('#releaseNewName')?.value || '',
        new_address: $('#releaseNewAddress')?.value || '',
        new_city: $('#releaseNewCity')?.value || '',
        new_zip: $('#releaseNewZip')?.value || '',
        new_province: $('#releaseNewProvince')?.value || '',
      };
      const res = await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/release-stock`, {
        method:'POST', body:JSON.stringify(payload)
      });
      showToast(`GLS: ${res.message || 'istruzione accettata'}`);
      await loadDashboard({quiet:true});
      await loadStockHistory({quiet:true});
      await openDrawer(item.tracking_number);
    } catch (err) {
      showToast(`Svincolo non confermato: ${err.message}`, true);
      btn.disabled = false;
      btn.textContent = 'Invia istruzione a GLS';
    }
  });

  $('#saveWorkflowBtn')?.addEventListener('click', async () => {
    const operatorName = getOperatorName(true);
    if (!operatorName) return;
    try {
      await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/workflow`, {
        method:'POST',
        body:JSON.stringify({
          workflow_status: workflow,
          operator_note: $('#operatorNote').value,
          operator_name: operatorName,
        })
      });
      showToast('Pratica aggiornata');
      await loadDashboard({quiet:true});
      await openDrawer(item.tracking_number);
    } catch (err) { showToast(err.message, true); }
  });

}

function setSyncBusy(busy) {
  // Un'unica funzione decide l'aspetto del pulsante: finche' una
  // sincronizzazione e' in corso non dev'essere cliccabile, altrimenti si
  // ottiene soltanto un errore "gia' in corso".
  const btn = $('#syncBtn');
  if (!btn) return;
  btn.disabled = Boolean(busy);
  btn.classList.toggle('busy', Boolean(busy));
  btn.title = busy ? 'Sincronizzazione già in corso' : 'Aggiorna i dati da Shopify e GLS';
  btn.innerHTML = busy
    ? '<span class="btn-icon spin">↻</span> Aggiornamento…'
    : '<span class="btn-icon">↻</span> Aggiorna';
}

function renderSyncProgress(p) {
  const panel = $('#syncPanel');
  const bar = $('#syncProgressBar');
  const spinner = $('#syncSpinner');
  if (!p || (!p.running && !['DONE','ERROR'].includes(p.phase))) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  $('#syncLabel').textContent = p.label || 'Sincronizzazione…';
  const total = Number(p.total || 0);
  const processed = Number(p.processed || 0);
  const percent = Number(p.percent || 0);
  $('#syncProcessed').textContent = total ? `${processed} / ${total} spedizioni GLS` : 'Preparazione dati…';
  const syncBits = [`${Number(p.success || 0)} aggiornate`];
  if (Number(p.pending_pickup || 0)) syncBits.push(`${Number(p.pending_pickup || 0)} in attesa ritiro`);
  if (Number(p.no_event_attention || 0)) syncBits.push(`${Number(p.no_event_attention || 0)} da verificare senza eventi`);
  if (Number(p.errors || 0)) syncBits.push(`${Number(p.errors || 0)} errori tecnici`);
  $('#syncResults').textContent = syncBits.join(' · ');
  $('#syncDetail').textContent = p.phase === 'SHOPIFY' ? 'Connessione Shopify' : p.phase === 'FILTERING' ? 'Filtro solo corriere GLS' : p.current_tracking ? `Tracking ${p.current_tracking}` : p.phase === 'DONE' ? 'Dati aggiornati' : p.phase === 'ERROR' ? 'Controlla diagnostica' : 'Interrogazione GLS';
  $('#syncPercent').textContent = total ? `${Math.min(100, percent)}%` : '…';
  $('#syncEta').textContent = p.running ? formatEta(p.eta_seconds) : (p.phase === 'DONE' ? 'Completato' : 'Interrotto');
  bar.classList.toggle('indeterminate', p.running && !total);
  bar.style.width = total ? `${Math.min(100, percent)}%` : '';
  spinner.classList.toggle('done', !p.running && p.phase === 'DONE');

  setSyncBusy(p.running);
}

async function pollSyncProgress() {
  try {
    const p = await api('/api/sync/progress');
    renderSyncProgress(p);
    if (p.running) return true;
    await loadDashboard({quiet:true});
    await loadStockHistory({quiet:true});
    if (p.phase === 'DONE') setTimeout(() => $('#syncPanel').classList.add('hidden'), 4500);
    return false;
  } catch (err) {
    showToast(`Stato sincronizzazione non disponibile: ${err.message}`, true);
    return false;
  }
}

function startSyncPolling() {
  if (state.syncTimer) return;
  const tick = async () => {
    const keepGoing = await pollSyncProgress();
    if (!keepGoing) {
      clearInterval(state.syncTimer);
      state.syncTimer = null;
    }
  };
  tick();
  state.syncTimer = setInterval(tick, 800);
}

async function syncNow() {
  const btn = $('#syncBtn');
  if (btn.disabled || state.syncTimer) return;
  setSyncBusy(true);
  $('#syncPanel')?.classList.remove('hidden');
  try {
    const res = await api('/api/sync', { method:'POST', body:'{}' });
    if (res && res.done) {
      // Deploy serverless: la sincronizzazione e' gia' conclusa quando risponde.
      showToast('Sincronizzazione completata');
      await loadDashboard({quiet:true});
      await loadStockHistory({quiet:true});
      setSyncBusy(false);
      return;
    }
    showToast('Sincronizzazione avviata');
    startSyncPolling();
  } catch (err) {
    setSyncBusy(false);
    showToast(err.message, true);
  }
}

async function diagnostics() {
  const btn = $('#diagnosticsBtn');
  btn.disabled = true; btn.textContent = 'Test…';
  try {
    const d = await api('/api/diagnostics');
    const lines = [
      `Shopify: ${d.shopify.auth_ok === true ? 'OK' : d.shopify.configured ? 'configurato, test non riuscito' : 'non configurato'}`,
      `GLS: ${d.gls.list_sped_ok === true ? 'OK' : d.gls.list_configured ? 'configurato, test non riuscito' : 'non configurato'}`,
    ];
    if (d.shopify.error) lines.push(`Shopify: ${d.shopify.error}`);
    if (d.gls.list_sped_error || d.gls.error) lines.push(`GLS: ${d.gls.list_sped_error || d.gls.error}`);
    const last = state.data?.last_sync;
    if (Number(last?.pending_pickup || 0) > 0) lines.push('', `${last.pending_pickup} tracking in attesa fisiologica di ritiro/presa in carico GLS.`);
    if (Number(last?.no_event_attention || 0) > 0) lines.push(`${last.no_event_attention} tracking senza eventi oltre la finestra prevista: sono in DA VERIFICARE.`);
    if (Number(last?.gls_errors || 0) > 0) {
      lines.push('', `Ultima sincronizzazione: ${last.gls_errors} errori tecnici reali`);
      (last.error_summary || []).forEach(g => lines.push(`- ${g.count} × ${g.error_title}: ${g.resolution_hint}`));
    }
    alert(lines.join('\n'));
  } catch (err) { showToast(err.message, true); }
  finally { btn.disabled = false; btn.textContent = 'Diagnostica'; }
}

async function renderRules() {
  $('#shipmentsPanel').classList.add('hidden');
  $('#stockPanel').classList.add('hidden');
  $('#rulesPanel').classList.remove('hidden');
  $('#filters').classList.add('hidden');
  try {
    const data = await api('/api/rules');
    $('#rulesContent').innerHTML = (data.rules || []).map(rule => `
      <div class="rule-row">
        <div><span class="priority-pill ${esc(rule.severity)}">${esc(severityLabels[rule.severity] || rule.severity)}</span></div>
        <div><strong>${esc(rule.category)}</strong><br><code>${esc(rule.id)}</code></div>
        <div><strong>Riconosce</strong><br><span class="muted">${esc((rule.patterns || []).join(' · '))}</span></div>
        <div><strong>Azione</strong><br><span class="muted">${esc(rule.action || '')}</span></div>
      </div>`).join('') + (data.overrides?.length ? `
        <div class="rule-row"><div><strong>Override codici</strong></div><div></div><div>${data.overrides.map(x => `<code>${esc(x.event_code)} → ${esc(x.severity)} / ${esc(x.category)}</code>`).join('<br>')}</div><div>Creati dalla dashboard.</div></div>` : '');
  } catch (err) { showToast(err.message, true); }
}

function clearQueueFilters() {
  // Gravita' e lavorazione sono affinamenti dentro una coda, non fra code.
  // Portandoli da una scheda all'altra si ottengono combinazioni impossibili:
  // "Da verificare" contiene solo massima e alta, quindi con il filtro
  // "Osservazione" attivo resterebbe sempre vuota senza spiegazione.
  state.severity = '';
  state.workflow = '';
  state.glsState = '';
  const sev = $('#severityFilter'); if (sev) sev.value = '';
  const wf = $('#workflowFilter'); if (wf) wf.value = '';
  const gls = $('#glsStateFilter'); if (gls) gls.value = '';
}

function impostaMenuImpostazioni() {
  // Stati nuovi e Regole servono di rado: stanno nell'ingranaggio, non fra le
  // code di lavoro quotidiane.
  const bottone = $('#settingsBtn');
  const pannello = $('#settingsPanel');
  if (!bottone || !pannello) return;

  const chiudi = () => {
    pannello.classList.add('hidden');
    bottone.setAttribute('aria-expanded', 'false');
  };
  bottone.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const aperto = !pannello.classList.contains('hidden');
    pannello.classList.toggle('hidden', aperto);
    bottone.setAttribute('aria-expanded', String(!aperto));
  });
  pannello.addEventListener('click', (ev) => ev.stopPropagation());
  $$('.settings-item').forEach(voce => voce.addEventListener('click', () => {
    chiudi();
    switchView(voce.dataset.view);
  }));
  document.addEventListener('click', chiudi);
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') chiudi(); });
}

function switchView(view) {
  if (state.view !== view) clearQueueFilters();
  state.view = view;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  $$('.settings-item').forEach(v => v.classList.toggle('active', v.dataset.view === view));
  $('#rulesPanel').classList.add('hidden');
  $('#stockPanel').classList.add('hidden');
  $('#shipmentsPanel').classList.add('hidden');
  $('#filters').classList.add('hidden');
  if (view === 'rules') return renderRules();
  if (view === 'stocks') { $('#stockPanel').classList.remove('hidden'); loadStockHistory({quiet:true}); return; }
  $('#shipmentsPanel').classList.remove('hidden');
  $('#filters').classList.remove('hidden');
  renderTable();
}

function setQuickDate(days) {
  const today = new Date();
  state.dateTo = dateKeyFromDate(today);
  if (days === 0) state.dateFrom = state.dateTo;
  else {
    const from = new Date(today);
    from.setDate(today.getDate() - (days - 1));
    state.dateFrom = dateKeyFromDate(from);
  }
  $('#dateFrom').value = state.dateFrom;
  $('#dateTo').value = state.dateTo;
  $$('.quick-dates button').forEach(b => b.classList.toggle('active', Number(b.dataset.days) === days));
  renderKpis(); renderTable();
}

function resetFilters() {
  state.severity = ''; state.workflow = ''; state.codOnly = false; state.search = ''; state.dateFrom = ''; state.dateTo = '';
  state.glsState = ''; state.glsStateMode = 'last';
  $('#searchInput').value = ''; $('#severityFilter').value = ''; $('#workflowFilter').value = ''; $('#codOnly').checked = false;
  $('#glsStateFilter').value = ''; $('#glsStateMode').value = 'last';
  $('#dateFrom').value = ''; $('#dateTo').value = '';
  $$('.quick-dates button').forEach(b => b.classList.remove('active'));
  renderKpis(); renderTable();
}

function bind() {
  const savedOperator = localStorage.getItem('glsMonitorOperator') || '';
  $('#operatorInput').value = savedOperator;
  $('#operatorInput').addEventListener('input', e => localStorage.setItem('glsMonitorOperator', e.target.value.trim()));
  $('#syncBtn').addEventListener('click', syncNow);
  $('#diagnosticsBtn').addEventListener('click', diagnostics);
  $('#logoutBtn')?.addEventListener('click', logout);
  $('#drawerClose').addEventListener('click', closeDrawer);
  $('#drawerBackdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
  $$('.tab').forEach(t => t.addEventListener('click', () => switchView(t.dataset.view)));
  impostaMenuImpostazioni();
  $$('.metric-card').forEach(k => k.addEventListener('click', () => {
    if (k.dataset.viewTarget) switchView(k.dataset.viewTarget);
    else switchView(k.dataset.category ? 'unknown' : 'active');
    if (k.dataset.severity) { state.severity = k.dataset.severity; $('#severityFilter').value = state.severity; }
    renderTable();
  }));
  $('#searchInput').addEventListener('input', e => { state.search = e.target.value.trim(); renderTable(); });
  $('#severityFilter').addEventListener('change', e => { state.severity = e.target.value; renderTable(); });
  $('#workflowFilter').addEventListener('change', e => { state.workflow = e.target.value; renderTable(); });
  $('#glsStateFilter').addEventListener('change', e => { state.glsState = e.target.value; renderTable(); });
  $('#glsStateMode').addEventListener('change', e => { state.glsStateMode = e.target.value; renderTable(); });
  $('#codOnly').addEventListener('change', e => { state.codOnly = e.target.checked; renderTable(); });
  $('#dateFrom').addEventListener('change', e => {
    state.dateFrom = e.target.value || '';
    if (state.dateTo && state.dateFrom > state.dateTo) { state.dateTo = state.dateFrom; $('#dateTo').value = state.dateTo; }
    $$('.quick-dates button').forEach(b => b.classList.remove('active'));
    renderKpis(); renderTable();
  });
  $('#dateTo').addEventListener('change', e => {
    state.dateTo = e.target.value || '';
    if (state.dateFrom && state.dateTo < state.dateFrom) { state.dateFrom = state.dateTo; $('#dateFrom').value = state.dateFrom; }
    $$('.quick-dates button').forEach(b => b.classList.remove('active'));
    renderKpis(); renderTable();
  });
  $$('.quick-dates button').forEach(btn => btn.addEventListener('click', () => setQuickDate(Number(btn.dataset.days))));
  $('#resetFilters').addEventListener('click', resetFilters);
  $$('.sort-head:not(.stock-sort)').forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.sort;
    if (state.sortKey === key) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    else { state.sortKey = key; state.sortDir = key === 'customer' || key === 'status' || key === 'workflow' ? 'asc' : 'desc'; }
    renderTable();
  }));
  $$('.stock-sort').forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.sort;
    if (state.stockSortKey === key) state.stockSortDir = state.stockSortDir === 'asc' ? 'desc' : 'asc';
    else { state.stockSortKey = key; state.stockSortDir = key === 'customer' || key === 'activity' || key === 'current' ? 'asc' : 'desc'; }
    renderStockHistory();
  }));
  $('#stockSearch').addEventListener('input', e => { state.stockSearch = e.target.value.trim(); renderStockHistory(); });
  $('#stockOutcomeFilter').addEventListener('change', e => { state.stockOutcome = e.target.value; renderStockHistory(); });
  $('#stockHandledFilter').addEventListener('change', e => { state.stockHandled = e.target.value; renderStockHistory(); });
  $('#stockDateFrom').addEventListener('change', e => { state.stockDateFrom = e.target.value || ''; renderStockHistory(); });
  $('#stockDateTo').addEventListener('change', e => { state.stockDateTo = e.target.value || ''; renderStockHistory(); });
  $('#stockReset').addEventListener('click', () => {
    state.stockSearch=''; state.stockOutcome=''; state.stockHandled=''; state.stockDateFrom=''; state.stockDateTo='';
    $('#stockSearch').value=''; $('#stockOutcomeFilter').value=''; $('#stockHandledFilter').value=''; $('#stockDateFrom').value=''; $('#stockDateTo').value='';
    renderStockHistory();
  });
}

bind();
loadDashboard();
loadStockHistory({quiet:true});
// Se all'apertura una sincronizzazione e' gia' in corso, la barra deve
// aggiornarsi da sola invece di restare ferma sul primo valore letto.
pollSyncProgress().then(inCorso => { if (inCorso) startSyncPolling(); });
setInterval(() => { loadDashboard({quiet:true}); loadStockHistory({quiet:true}); }, 30000);
