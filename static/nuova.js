'use strict';
// GLS Monitor, nuova interfaccia. Vive accanto alla pagina di sempre e usa le
// stesse API: le azioni registrate qui finiscono nello stesso storico.

const S = {
  dati: null,
  tutteCaricate: false,
  coda: 'verificare',          // coda o vista d'archivio mostrata
  ricerca: '',
  filtri: { priorita: '', stato: '', periodo: '', cod: false },
  scelte: new Set(),
  limite: 200,
  novita: null,
  novitaSet: new Set(),
  giacenze: null,
  filtriGiacenze: { testo: '', esito: '', gestite: '', periodo: '30' },
  analisi: null,
  assistenteTracking: [],
  chatStoria: [],
  dettaglio: null,
  sezione: 'coda',
  sezioneBase: 'coda',
  opener: null,
  sync: null,
};

const CODE = [
  { id: 'verificare', nome: 'Da verificare' },
  { id: 'lavorazione', nome: 'In lavorazione' },
  { id: 'consegna', nome: 'In consegna' },
  { id: 'attive', nome: 'Tutte attive' },
];
const ARCHIVIO = {
  consegnate: 'Consegnate',
  rientrate: 'Rientrate al mittente',
  tutte: 'Tutte le spedizioni',
  sconosciuti: 'Stati GLS da classificare',
  assistente: 'Dalla risposta dell’assistente',
};
const GRAVITA = ['CRITICAL', 'WARNING', 'WATCH', 'INFO', 'NORMAL'];
const NOME_GRAVITA = { CRITICAL: 'Massima', WARNING: 'Alta', WATCH: 'Osservazione', INFO: 'Informativa', NORMAL: 'Normale' };
const IN_LAVORO = ['IN_PROGRESS', 'WAITING_CUSTOMER', 'WAITING_GLS'];
const IN_CONSEGNA = ['OUT_FOR_DELIVERY', 'SCHEDULED', 'IN_TRANSIT', 'CORRESPONDENT'];
const MESI = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];

const TITOLI = {
  STORAGE: 'In giacenza in sede GLS', ADDRESS_ERROR: 'Indirizzo errato', ABSENT: 'Destinatario assente',
  PICKUP_AT_DEPOT: 'Da ritirare in sede GLS', REFUSED: 'Rifiutata dal destinatario', COD_ISSUE: 'Problema col contrassegno',
  DAMAGE_OR_LOSS: 'Danneggiata o smarrita', DELIVERY_FAILURE: 'Consegna non riuscita', DELIVERY_RETRY: 'Nuovo tentativo di consegna',
  RECIPIENT_CLOSED: 'Destinatario chiuso', ACTION_REQUIRED: 'Serve un intervento', OUT_FOR_DELIVERY: 'In consegna oggi',
  IN_TRANSIT: 'In viaggio', SCHEDULED: 'Consegna programmata', LINEHAUL_DELAY: 'In ritardo', CORRESPONDENT: 'Presso un corrispondente',
  SERVICE_AREA: 'Zona disagiata', LABEL_CREATED: 'Affidata a GLS', DELIVERED: 'Consegnata', RETURN: 'Rientrata al mittente',
  UNCLASSIFIED: 'Stato GLS nuovo', NO_GLS_EVENTS: 'Nessun evento GLS', PENDING_PICKUP: 'In attesa di ritiro',
  PENDING_CHECK: 'Da controllare', ORDER_CANCELLED: 'Ordine annullato', REPLACED_CARRIER: 'Passata a un altro corriere',
};
const PASSI = {
  ADDRESS_ERROR: 'Chiedere al cliente l’indirizzo giusto',
  ABSENT: 'Avvisare il cliente: GLS ritenta il primo giorno lavorativo',
  DELIVERY_RETRY: 'Avvisare il cliente del nuovo tentativo',
  RECIPIENT_CLOSED: 'Chiedere al cliente quando può ricevere',
  PICKUP_AT_DEPOT: 'Dire al cliente dove ritirarla',
  REFUSED: 'Chiamare il cliente per capire il rifiuto',
  COD_ISSUE: 'Ricordare al cliente l’importo del contrassegno',
  DAMAGE_OR_LOSS: 'Aprire la pratica con GLS',
  DELIVERY_FAILURE: 'Capire con GLS perché non è stata consegnata',
  ACTION_REQUIRED: 'Contattare GLS o il cliente',
  LINEHAUL_DELAY: 'Tenerla d’occhio',
  NO_GLS_EVENTS: 'Verificare che il collo sia partito',
  PENDING_PICKUP: 'Verificare che GLS l’abbia ritirata',
  LABEL_CREATED: 'Verificare che il collo sia partito',
  UNCLASSIFIED: 'Leggere il nuovo stato GLS',
  DELIVERED: 'Niente: è stata consegnata',
  RETURN: 'Niente: è tornata al mittente',
  ORDER_CANCELLED: 'Niente: l’ordine è annullato',
};
const SCRIVI = ['ABSENT', 'PICKUP_AT_DEPOT', 'DELIVERY_RETRY', 'RECIPIENT_CLOSED', 'DELIVERY_FAILURE', 'ACTION_REQUIRED'];
const CHIAMA = ['ADDRESS_ERROR', 'REFUSED', 'COD_ISSUE'];

const TRATTI = {
  spunta: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  chiudi: '<path d="M6 6l12 12M18 6L6 18"/>',
  freccia: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  avanti: '<path d="M9 6l6 6-6 6"/>',
  indietro: '<path d="M15 6l-6 6 6 6"/>',
  telefono: '<path d="M7 3.5h3l1.5 4-2 1.5a12 12 0 0 0 5.5 5.5l1.5-2 4 1.5v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 5 7.7 2 2 0 0 1 7 3.5z"/>',
  busta: '<rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="M3.5 7l8.5 6 8.5-6"/>',
  esterno: '<path d="M14 5h5v5M19 5l-8 8M10 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4"/>',
  allarme: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5h.01"/>',
  novita: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/>',
  cerca: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
  scintilla: '<path d="M12 4l2.1 5.9L20 12l-5.9 2.1L12 20l-2.1-5.9L4 12l5.9-2.1L12 4z"/>',
  invia: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  su: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  giu: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  pari: '<path d="M5 12h14"/>',
  pacco: '<path d="M20.5 7.5l-8.5-4.5-8.5 4.5v9l8.5 4.5 8.5-4.5z"/><path d="M3.5 7.5l8.5 4.5 8.5-4.5M12 12v9"/>',
  vietato: '<circle cx="12" cy="12" r="9"/><path d="M8.5 8.5l7 7"/>',
  piu: '<path d="M12 5v14M5 12h14"/>',
};
const ic = (nome) => `<svg viewBox="0 0 24 24" aria-hidden="true">${TRATTI[nome] || ''}</svg>`;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function esc(v) {
  return String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}
function data(v) { const d = new Date(v); return Number.isNaN(d.getTime()) ? null : d; }
function due(n) { return String(n).padStart(2, '0'); }
function giornoOra(v) {
  const d = data(v); if (!d) return '';
  return `${due(d.getDate())}/${due(d.getMonth() + 1)}, ${due(d.getHours())}:${due(d.getMinutes())}`;
}
function giorno(v, anno = false) {
  const d = data(v); if (!d) return '';
  return `${due(d.getDate())}/${due(d.getMonth() + 1)}${anno ? '/' + d.getFullYear() : ''}`;
}
function giorniFa(v) {
  const d = data(v); if (!d) return null;
  const oggi = new Date(); oggi.setHours(0, 0, 0, 0);
  const quel = new Date(d); quel.setHours(0, 0, 0, 0);
  return Math.round((oggi - quel) / 86400000);
}
function daQuanto(v) {
  const n = giorniFa(v);
  if (n == null) return '';
  if (n <= 0) return 'da oggi';
  if (n === 1) return 'da ieri';
  return `da ${n} giorni`;
}
function quandoDetto(v) {
  const d = data(v); if (!d) return '';
  const n = giorniFa(v); const ora = `${due(d.getHours())}:${due(d.getMinutes())}`;
  if (n === 0) return `oggi alle ${ora}`;
  if (n === 1) return `ieri alle ${ora}`;
  return `il ${giorno(v)} alle ${ora}`;
}
function dalMomento(v) {
  // "Da ieri alle 18:30", "Da oggi alle 9:10", "Dal 31/10 alle 18:30".
  const t = quandoDetto(v);
  return t.startsWith('il ') ? `Dal ${t.slice(3)}` : `Da ${t}`;
}
function minutiFa(v) {
  const d = data(v); if (!d) return null;
  return Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
}
function soldi(v, valuta) {
  if (v == null || v === '') return '';
  try { return new Intl.NumberFormat('it-IT', { style: 'currency', currency: valuta || 'EUR' }).format(Number(v)); }
  catch { return `${v} ${valuta || ''}`; }
}
function chiave(d) { return `${d.getFullYear()}-${due(d.getMonth() + 1)}-${due(d.getDate())}`; }
function prossimoLavorativo() {
  const d = new Date(); d.setHours(0, 0, 0, 0);
  do { d.setDate(d.getDate() + 1); } while (d.getDay() === 0 || d.getDay() === 6);
  return chiave(d);
}
function cifre(v) { return String(v || '').replace(/\D+/g, ''); }
function telefonoLocale(v) {
  let c = cifre(v);
  if (c.startsWith('0039')) c = c.slice(4); else if (c.startsWith('39') && c.length > 10) c = c.slice(2);
  return c;
}
function plurale(n, uno, tanti) { return `${n} ${n === 1 ? uno : tanti}`; }

async function api(path, opzioni = {}) {
  const r = await fetch(path, { ...opzioni, headers: { 'Content-Type': 'application/json', ...(opzioni.headers || {}) }, cache: 'no-store' });
  let corpo = {};
  try { corpo = await r.json(); } catch {}
  if (r.status === 401 && corpo.login_required) { location.replace('/login'); throw new Error('Sessione scaduta'); }
  if (!r.ok) throw new Error(corpo.error || corpo.message || `Errore ${r.status}`);
  return corpo;
}

let timerAvviso = null;
function avvisa(testo, errore = false) {
  const el = $('#avviso');
  el.textContent = testo;
  el.classList.toggle('errore', errore);
  el.classList.remove('nascosto-del-tutto');
  clearTimeout(timerAvviso);
  timerAvviso = setTimeout(() => el.classList.add('nascosto-del-tutto'), errore ? 6000 : 3200);
}

function memoria(chiaveM, valore) {
  try {
    if (valore === undefined) return localStorage.getItem(chiaveM);
    if (valore === null) localStorage.removeItem(chiaveM); else localStorage.setItem(chiaveM, valore);
  } catch { return null; }
  return valore;
}

function operatore() {
  const dentro = (S.dati?.config?.current_user?.display_name || '').trim();
  if (dentro) return dentro;
  let nome = (memoria('glsMonitorOperator') || '').trim();
  if (!nome) {
    nome = (window.prompt('Come ti chiami? Il nome resta su ogni azione che registri.') || '').trim();
    if (nome) memoria('glsMonitorOperator', nome);
  }
  if (!nome) avvisa('Serve il tuo nome per registrare l’azione.', true);
  return nome || null;
}

// ---------------------------------------------------------------------------
// Regole sulle pratiche (le stesse della pagina di sempre)
// ---------------------------------------------------------------------------

const gravita = (r) => r.effective_severity || r.severity || 'NORMAL';
function daVerificare(r) {
  return Boolean(r && !r.closed && r.workflow_status === 'NEW' && ['CRITICAL', 'WARNING'].includes(gravita(r)));
}
function inLavorazione(r) { return !r.closed && IN_LAVORO.includes(r.workflow_status); }
function giacenzaAperta(r) {
  return Number(r.open_stock_cases || 0) > 0 || r.category === 'STORAGE'
    || (r.stock_cases || []).some(sc => sc.status === 'OPEN');
}
function svincoli(r) {
  const elenco = (r.stock_cases || []).flatMap(sc => sc.release_requests || []);
  const riuscito = Number(r.release_count || 0) > 0 || elenco.some(x => Number(x.gls_success) === 1);
  const respinto = !riuscito && (elenco.length > 0 || Number(r.release_failed_count || 0) > 0);
  return { riuscito, respinto, ultimo: elenco[0] || null };
}
function titolo(r) { return TITOLI[r.category] || r.gls_status || 'Stato GLS non disponibile'; }
function passo(r) {
  if (r.closed) return r.category === 'DELIVERED' ? PASSI.DELIVERED : r.category === 'RETURN' ? PASSI.RETURN : 'Pratica chiusa';
  if (giacenzaAperta(r)) {
    const sv = svincoli(r);
    if (sv.riuscito) return 'Attendere che GLS esegua lo svincolo';
    if (sv.respinto) return 'Rifare lo svincolo: GLS l’ha respinto';
    return 'Mandare l’istruzione di svincolo';
  }
  if (inLavorazione(r) && (r.last_operator_action || '').trim() && ['ADDRESS_ERROR', 'ABSENT', 'REFUSED', 'DELIVERY_RETRY', 'RECIPIENT_CLOSED'].includes(r.category)) {
    return 'Attendere che GLS apra la giacenza';
  }
  if (IN_CONSEGNA.includes(r.category)) return 'Nessuna azione: è in viaggio';
  return PASSI[r.category] || r.recommended_action || 'Verificare la pratica';
}
function azioneRiga(r) {
  if (r.closed) return { tipo: 'apri', testo: 'Apri' };
  if (giacenzaAperta(r) && !svincoli(r).riuscito) return { tipo: 'svincola', testo: 'Svincola', primario: true };
  if (CHIAMA.includes(r.category) && r.customer_phone) return { tipo: 'chiama', testo: 'Chiama' };
  if (SCRIVI.includes(r.category) && r.customer_phone) return { tipo: 'scrivi', testo: 'Scrivi' };
  return { tipo: 'apri', testo: 'Apri' };
}
function statoPratica(r) {
  if (r.closed || ['RESOLVED', 'IGNORED'].includes(r.workflow_status)) return { testo: 'Chiusa', classe: 'e-neutro' };
  if (IN_LAVORO.includes(r.workflow_status)) {
    const chi = (r.last_operator_name || '').trim();
    return { testo: chi && chi.toLowerCase() !== 'sistema' ? `In lavorazione · ${chi}` : 'In lavorazione', classe: 'e-lavoro' };
  }
  return { testo: 'Nuova', classe: 'e-neutro' };
}
function haNovita(r) { return Boolean(r.unread_event_at) || S.novitaSet.has(r.tracking_number); }

// ---------------------------------------------------------------------------
// Dati
// ---------------------------------------------------------------------------

async function caricaDati({ tutte = S.tutteCaricate, quieto = false } = {}) {
  try {
    S.dati = await api(tutte ? '/api/dashboard?include_closed=true' : '/api/dashboard');
    S.tutteCaricate = tutte;
    disegna();
    disegnaTestata();
  } catch (err) {
    if (!quieto) avvisa(`Dati non disponibili: ${err.message}`, true);
  }
}
async function servonoTutte() { if (!S.tutteCaricate) await caricaDati({ tutte: true }); }

const spedizioni = () => S.dati?.shipments || [];

function righeDellaCoda(coda = S.coda) {
  const tutte = spedizioni();
  switch (coda) {
    case 'verificare': return tutte.filter(daVerificare);
    case 'lavorazione': return tutte.filter(inLavorazione);
    case 'consegna': return tutte.filter(r => !r.closed && IN_CONSEGNA.includes(r.category));
    case 'attive': return tutte.filter(r => !r.closed);
    case 'consegnate': return tutte.filter(r => r.category === 'DELIVERED');
    case 'rientrate': return tutte.filter(r => r.category === 'RETURN');
    case 'tutte': return tutte.slice();
    case 'sconosciuti': return tutte.filter(r => !r.closed && r.category === 'UNCLASSIFIED');
    case 'assistente': { const v = new Set(S.assistenteTracking); return tutte.filter(r => v.has(r.tracking_number)); }
    default: return [];
  }
}

function filtra(righe) {
  const f = S.filtri;
  let out = righe;
  if (f.priorita) out = out.filter(r => gravita(r) === f.priorita);
  if (f.stato) out = out.filter(r => String(r.gls_status || '').trim() === f.stato);
  if (f.cod) out = out.filter(r => r.is_cod);
  if (f.periodo) {
    const da = new Date(); da.setHours(0, 0, 0, 0); da.setDate(da.getDate() - (Number(f.periodo) - 1));
    out = out.filter(r => { const d = data(r.order_created_at); return d && d >= da; });
  }
  return out;
}
function cercaTra(righe) {
  const q = S.ricerca.trim().toLowerCase();
  if (!q) return righe;
  return righe.filter(r => [r.order_name, r.customer_name, r.customer_email, r.customer_phone, r.tracking_number,
    r.gls_status, r.gls_note, r.city, r.last_operator_name].some(v => String(v || '').toLowerCase().includes(q)));
}
const filtriAttivi = () => Boolean(S.filtri.priorita || S.filtri.stato || S.filtri.cod || S.filtri.periodo);

function ordina(righe) {
  const archivio = ['consegnate', 'rientrate', 'tutte'].includes(S.coda);
  return righe.slice().sort((a, b) => {
    if (!archivio) {
      const g = GRAVITA.indexOf(gravita(a)) - GRAVITA.indexOf(gravita(b));
      if (g) return g;
      // La pratica ferma da piu' tempo sta in cima al suo gruppo.
      return (data(a.gls_event_at)?.getTime() || 0) - (data(b.gls_event_at)?.getTime() || 0);
    }
    return (data(b.gls_event_at)?.getTime() || 0) - (data(a.gls_event_at)?.getTime() || 0);
  });
}

// ---------------------------------------------------------------------------
// Testata, code, filtri
// ---------------------------------------------------------------------------

function disegnaTestata() {
  const u = S.dati?.config?.current_user;
  const nome = (u?.display_name || memoria('glsMonitorOperator') || '').trim();
  $('#utente').innerHTML = nome ? `<span class="iniziale" aria-hidden="true">${esc(nome[0].toUpperCase())}</span><span>${esc(nome)}</span>` : '';
  aggiornaOra();
}
function aggiornaOra() {
  const fine = S.dati?.last_sync?.finished_at;
  const m = minutiFa(fine);
  let t = 'Mai aggiornato';
  if (m != null) t = m < 1 ? 'Aggiornato adesso' : m < 60 ? `Aggiornato ${m} min fa` : m < 1440 ? `Aggiornato ${Math.floor(m / 60)} h fa` : `Aggiornato il ${giorno(fine)}`;
  if (!S.sync) $('#aggiornato').textContent = t;
}

function disegnaCode() {
  const nav = $('#code');
  nav.innerHTML = CODE.map(c => {
    const n = righeDellaCoda(c.id).length;
    const urgente = c.id === 'verificare' && n > 0;
    return `<button type="button" class="coda" data-coda="${c.id}" ${S.coda === c.id && !S.ricerca ? 'aria-current="page"' : ''}>${esc(c.nome)} <span class="numero ${urgente ? 'urgente' : ''}">${n}</span></button>`;
  }).join('');
  $$('.coda', nav).forEach(b => b.addEventListener('click', () => vaiA(b.dataset.coda)));
  const inArchivio = ARCHIVIO[S.coda] && !S.ricerca;
  $('#archivioEtichetta').textContent = inArchivio ? ARCHIVIO[S.coda] : 'Archivio';
  $('#apriArchivio').setAttribute('aria-current', inArchivio ? 'page' : 'false');
}

function disegnaMenuArchivio() {
  const chiuse = S.dati?.chiuse || {};
  const conta = (id) => {
    if (S.tutteCaricate) return righeDellaCoda(id).length;
    if (id === 'consegnate') return Number(chiuse.consegnate || 0);
    if (id === 'rientrate') return Number(chiuse.rientrate || 0);
    if (id === 'tutte') return spedizioni().length + Number(chiuse.totale || 0);
    return righeDellaCoda(id).length;
  };
  const voci = ['consegnate', 'rientrate', 'tutte'].map(id =>
    `<button type="button" role="menuitem" data-archivio="${id}">${esc(ARCHIVIO[id])} <span class="quante">${conta(id)}</span></button>`);
  const ignoti = righeDellaCoda('sconosciuti').length;
  if (ignoti) voci.push(`<button type="button" role="menuitem" data-archivio="sconosciuti">${esc(ARCHIVIO.sconosciuti)} <span class="quante">${ignoti}</span></button>`);
  voci.push('<hr>');
  voci.push(`<button type="button" role="menuitem" data-archivio="giacenze">Storico giacenze <span class="quante">${Number(S.dati?.giacenze_totali || 0)}</span></button>`);
  $('#menuArchivio').innerHTML = voci.join('');
  $$('[data-archivio]', $('#menuArchivio')).forEach(b => b.addEventListener('click', () => {
    chiudiMenu();
    if (b.dataset.archivio === 'giacenze') return apriGiacenze();
    vaiA(b.dataset.archivio);
  }));
}

function disegnaMenuImpostazioni() {
  const multi = S.dati?.config?.multi_user || S.me?.multi_user;
  $('#menuImpostazioni').innerHTML = `
    <a role="menuitem" href="/">Pagina classica ${ic('esterno')}</a>
    <button type="button" role="menuitem" id="voceAnalisi">Analisi del mese</button>
    ${multi ? '<hr><button type="button" role="menuitem" id="voceEsci">Esci</button>' : ''}`;
  $('#voceAnalisi')?.addEventListener('click', () => { chiudiMenu(); apriAnalisi(); });
  $('#voceEsci')?.addEventListener('click', async () => {
    try { await api('/api/logout', { method: 'POST', body: '{}' }); } catch {}
    location.replace('/login');
  });
}

function apriMenu(tasto, menu) {
  const aperto = !menu.classList.contains('nascosto-del-tutto');
  chiudiMenu();
  if (aperto) return;
  if (menu.id === 'menuArchivio') disegnaMenuArchivio(); else disegnaMenuImpostazioni();
  menu.classList.remove('nascosto-del-tutto');
  tasto.setAttribute('aria-expanded', 'true');
  menu.querySelector('button, a')?.focus();
}
function chiudiMenu() {
  $$('.menu').forEach(m => m.classList.add('nascosto-del-tutto'));
  $$('[aria-haspopup]').forEach(b => b.setAttribute('aria-expanded', 'false'));
}

async function vaiA(coda) {
  S.coda = coda;
  S.limite = 200;
  S.scelte.clear();
  if (S.ricerca) { S.ricerca = ''; $('#cerca').value = ''; }
  mostraSezione('coda');
  if (['consegnate', 'rientrate', 'tutte', 'assistente'].includes(coda)) await servonoTutte();
  disegna();
  window.scrollTo({ top: 0 });
}

function disegnaFiltroStati(base) {
  const sel = $('#filtroStato');
  const conti = new Map();
  base.forEach(r => { const s = String(r.gls_status || '').trim(); if (s) conti.set(s, (conti.get(s) || 0) + 1); });
  const voci = [...conti.entries()].sort((a, b) => b[1] - a[1]);
  if (S.filtri.stato && !conti.has(S.filtri.stato)) voci.unshift([S.filtri.stato, 0]);
  sel.innerHTML = '<option value="">Stato GLS: tutti</option>' + voci.map(([s, n]) =>
    `<option value="${esc(s)}" ${s === S.filtri.stato ? 'selected' : ''}>${esc(s.length > 60 ? s.slice(0, 58) + '…' : s)} (${n})</option>`).join('');
}
function segnaFiltri() {
  $('#filtroPriorita').classList.toggle('attivo', Boolean(S.filtri.priorita));
  $('#filtroStato').classList.toggle('attivo', Boolean(S.filtri.stato));
  $('#filtroPeriodo').classList.toggle('attivo', Boolean(S.filtri.periodo));
  $('#azzeraFiltri').classList.toggle('nascosto-del-tutto', !filtriAttivi());
}

// ---------------------------------------------------------------------------
// Elenco pratiche
// ---------------------------------------------------------------------------

function disegna() {
  if (!S.dati) return;
  disegnaCode();
  disegnaNovita();
  if (S.sezioneBase === 'giacenze') { disegnaGiacenze(); return; }
  const base = S.ricerca ? spedizioni() : righeDellaCoda();
  disegnaFiltroStati(base);
  segnaFiltri();
  const righe = ordina(filtra(cercaTra(base)));
  const vista = $('#vistaCoda');
  const raggruppa = !['consegnate', 'rientrate', 'tutte'].includes(S.coda) && !S.ricerca;

  const titoloRicerca = S.ricerca ? `per “${S.ricerca}”` : '';
  $('#conteggio').textContent = S.ricerca
    ? `${plurale(righe.length, 'pratica trovata', 'pratiche trovate')} ${titoloRicerca}`
    : `${plurale(righe.length, 'pratica', 'pratiche')}${raggruppa ? ' · le più urgenti in alto' : ' · le più recenti in alto'}`;

  if (!righe.length) { vista.innerHTML = vuoto(); collegaVuoto(); disegnaSelezione(); return; }

  const mostrate = righe.slice(0, S.limite);
  const intestazione = `<div class="griglia intestazione">
      <span><input type="checkbox" class="spunta" id="sceltaTutte" aria-label="Seleziona tutte le pratiche mostrate"></span>
      <span>Urgenza</span><span>Ordine e cliente</span><span>Cosa è successo</span><span>Prossimo passo</span><span>Pratica</span><span></span>
    </div>`;
  let corpo = '';
  if (raggruppa) {
    for (const g of GRAVITA) {
      const qui = mostrate.filter(r => gravita(r) === g);
      if (!qui.length) continue;
      const tot = righe.filter(r => gravita(r) === g).length;
      corpo += `<div class="gruppo"><span class="etichetta e-${g}">${NOME_GRAVITA[g]}</span><span class="quante">${plurale(tot, 'pratica', 'pratiche')}</span></div>`;
      corpo += qui.map(rigaHtml).join('');
    }
  } else {
    corpo = mostrate.map(rigaHtml).join('');
  }
  const altre = righe.length - mostrate.length;
  const piede = altre > 0
    ? `<div class="piede-elenco"><button type="button" class="tasto" id="mostraAltre">Mostra altre ${Math.min(altre, 200)} di ${altre}</button></div>`
    : `<div class="piede-elenco">Clicca una riga per aprire la pratica. Il pulsante a destra fa subito il passo suggerito.</div>`;
  vista.innerHTML = intestazione + corpo + piede;

  $('#mostraAltre')?.addEventListener('click', () => { S.limite += 200; disegna(); });
  const tutte = $('#sceltaTutte');
  tutte.checked = mostrate.length > 0 && mostrate.every(r => S.scelte.has(r.tracking_number));
  tutte.addEventListener('change', () => {
    mostrate.forEach(r => tutte.checked ? S.scelte.add(r.tracking_number) : S.scelte.delete(r.tracking_number));
    disegna();
  });
  $$('.riga', vista).forEach(el => {
    const t = el.dataset.tracking;
    el.addEventListener('click', (e) => {
      if (e.target.closest('.c-spunta, .c-cta')) return;
      apriDettaglio(t, el);
    });
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === el) apriDettaglio(t, el); });
    $('.spunta', el)?.addEventListener('change', (e) => {
      e.target.checked ? S.scelte.add(t) : S.scelte.delete(t);
      el.classList.toggle('selezionata', e.target.checked);
      disegnaSelezione();
      $('#sceltaTutte').checked = mostrate.every(r => S.scelte.has(r.tracking_number));
    });
    $('.c-cta button', el)?.addEventListener('click', (e) => { e.stopPropagation(); faiPasso(t, e.currentTarget); });
  });
  disegnaSelezione();
}

function rigaHtml(r) {
  const g = gravita(r);
  const t = esc(r.tracking_number);
  const luogo = [r.city, r.province].filter(Boolean).join(' · ');
  const pr = statoPratica(r);
  const az = azioneRiga(r);
  const sv = svincoli(r);
  const chip = [];
  if (haNovita(r)) chip.push('<span class="etichetta mini e-novita">Novità</span>');
  if (sv.riuscito && giacenzaAperta(r)) chip.push('<span class="etichetta mini e-NORMAL">Svincolo inviato</span>');
  if (sv.respinto) chip.push('<span class="etichetta mini e-CRITICAL">Svincolo respinto</span>');
  return `<div class="griglia riga ${S.scelte.has(r.tracking_number) ? 'selezionata' : ''}" data-tracking="${t}" tabindex="0" aria-label="Apri la pratica ${esc(r.order_name || r.tracking_number)}">
    <span class="c-spunta"><input type="checkbox" class="spunta" aria-label="Seleziona ${esc(r.order_name || r.tracking_number)}" ${S.scelte.has(r.tracking_number) ? 'checked' : ''}></span>
    <span class="colonna c-urgenza"><span class="forte t-${g}">${esc(daQuanto(r.gls_event_at) || '—')}</span><span class="piccolo">${esc(giornoOra(r.gls_event_at))}</span></span>
    <span class="colonna c-ordine"><span class="forte taglia">${esc(r.order_name || '—')} · ${esc(r.customer_name || 'Cliente')}</span>
      <span class="piccolo in-linea">${esc(luogo)}${r.is_cod ? '<span class="etichetta mini e-cod">Contrassegno</span>' : ''}</span></span>
    <span class="colonna c-successo"><span class="forte in-linea">${esc(titolo(r))}${chip.join('')}</span><span class="piccolo taglia" title="${esc(r.gls_status || '')}">${esc(r.gls_status || '')}</span></span>
    <span class="c-passo">${esc(passo(r))}</span>
    <span class="c-pratica"><span class="etichetta ${pr.classe}">${esc(pr.testo)}</span></span>
    <span class="c-cta"><button type="button" class="tasto cta ${az.primario ? 'primario' : ''}" data-tipo="${az.tipo}">${esc(az.testo)}</button></span>
    <span class="c-freccia" aria-hidden="true">${ic('avanti')}</span>
  </div>`;
}

function vuoto() {
  if (S.ricerca) {
    return `<div class="vuoto"><span class="vuoto-segno neutro">${ic('cerca')}</span><div><h2>Nessuna pratica per “${esc(S.ricerca)}”</h2><p>Prova con il numero d’ordine, il cognome o il tracking GLS.</p></div></div>`;
  }
  if (filtriAttivi()) {
    return `<div class="vuoto"><span class="vuoto-segno neutro">${ic('cerca')}</span><div><h2>Nessuna pratica con questi filtri</h2><p>Ci sono pratiche in questa coda, ma i filtri le nascondono tutte.</p></div><div class="azioni-centro"><button type="button" class="tasto" data-vuoto="filtri">Togli i filtri</button></div></div>`;
  }
  if (S.coda === 'verificare') {
    const lav = righeDellaCoda('lavorazione').length;
    return `<div class="vuoto"><span class="vuoto-segno">${ic('spunta')}</span>
      <div><h2>Niente da verificare</h2><p>Il monitor continua a controllare GLS. Se qualcosa si blocca, la pratica ricompare qui da sola.</p></div>
      <div class="schede" id="schedeOggi"><div class="scheda"><span class="voce">Gestite oggi dal team</span><span class="cifra">…</span></div><div class="scheda"><span class="voce">Svincoli inviati oggi</span><span class="cifra">…</span></div><div class="scheda"><span class="voce">Consegnate oggi</span><span class="cifra">…</span></div></div>
      <div class="azioni-centro">${lav ? `<button type="button" class="tasto grande" data-vuoto="lavorazione">Guarda ${lav === 1 ? 'quella' : `le ${lav}`} in lavorazione</button>` : ''}<button type="button" class="tasto grande" data-vuoto="giacenze">Apri lo storico giacenze</button></div></div>`;
  }
  return `<div class="vuoto"><span class="vuoto-segno neutro">${ic('pacco')}</span><div><h2>Qui non c’è niente</h2><p>Nessuna pratica in questa vista.</p></div></div>`;
}
function collegaVuoto() {
  $$('[data-vuoto]').forEach(b => b.addEventListener('click', () => {
    const v = b.dataset.vuoto;
    if (v === 'filtri') return azzeraFiltri();
    if (v === 'giacenze') return apriGiacenze();
    vaiA(v);
  }));
  if ($('#schedeOggi')) caricaOggi();
}
async function caricaOggi() {
  try {
    const o = await api('/api/oggi');
    const box = $('#schedeOggi'); if (!box) return;
    const chi = (o.per_operatore || []).slice(0, 4).map(p => `${esc(p.nome)} ${p.pratiche}`).join(' · ');
    const accettati = o.svincoli === 0 ? 'nessuno, per ora' : o.svincoli_accettati === o.svincoli ? (o.svincoli === 1 ? 'accettato da GLS' : 'tutti accettati da GLS') : `${o.svincoli_accettati} accettati da GLS`;
    box.innerHTML = `
      <div class="scheda"><span class="voce">Gestite oggi dal team</span><span class="cifra">${o.gestite}</span><span class="nota">${chi || 'nessuna azione registrata oggi'}</span></div>
      <div class="scheda"><span class="voce">Svincoli inviati oggi</span><span class="cifra">${o.svincoli}</span><span class="nota">${accettati}</span></div>
      <div class="scheda"><span class="voce">Consegnate oggi</span><span class="cifra">${o.consegnate}</span><span class="nota">${o.consegnate_dopo_giacenza ? `di cui ${o.consegnate_dopo_giacenza} dopo una giacenza` : 'secondo l’ultimo evento GLS'}</span></div>`;
  } catch { $('#schedeOggi')?.remove(); }
}

function azzeraFiltri() {
  S.filtri = { priorita: '', stato: '', periodo: '', cod: false };
  $('#filtroPriorita').value = ''; $('#filtroPeriodo').value = ''; $('#filtroContrassegno').checked = false;
  disegna();
}

function faiPasso(tracking, tasto) {
  const r = spedizioni().find(x => x.tracking_number === tracking);
  if (!r) return;
  const tipo = azioneRiga(r).tipo;
  // Spoki si apre in un'altra scheda; qui resta la pratica pronta per
  // registrare il messaggio quando si torna.
  if (tipo === 'scrivi') { apriSpoki(r); return apriDettaglio(tracking, tasto, { contatto: 'CUSTOMER_MESSAGE' }); }
  if (tipo === 'chiama') {
    if (matchMedia('(max-width: 760px)').matches && r.customer_phone) location.href = `tel:${r.customer_phone}`;
    return apriDettaglio(tracking, tasto, { contatto: 'CUSTOMER_CALLED' });
  }
  if (tipo === 'svincola') return apriDettaglio(tracking, tasto, { vaiA: 'svincolo' });
  apriDettaglio(tracking, tasto);
}

// ---------------------------------------------------------------------------
// Selezione multipla
// ---------------------------------------------------------------------------

function disegnaSelezione() {
  const barra = $('#barraSelezione');
  const scelte = spedizioni().filter(r => S.scelte.has(r.tracking_number));
  if (!scelte.length) { barra.classList.add('nascosto-del-tutto'); return; }
  barra.innerHTML = `<span class="quante">${plurale(scelte.length, 'pratica scelta', 'pratiche scelte')}</span>
    <button type="button" class="tasto primo" id="multiCarico">Prendile in carico</button>
    <button type="button" class="tasto" id="multiChiudi">Chiudile</button>
    <button type="button" class="tasto-icona" id="multiAnnulla" aria-label="Annulla la selezione">${ic('chiudi')}</button>`;
  barra.classList.remove('nascosto-del-tutto');
  $('#multiCarico').addEventListener('click', () => cambiaStatoMolte(scelte, 'IN_PROGRESS'));
  $('#multiChiudi').addEventListener('click', () => {
    if (!confirm(`Chiudere ${plurale(scelte.length, 'pratica', 'pratiche')}? Escono dalle code finché GLS non scrive qualcosa di nuovo.`)) return;
    cambiaStatoMolte(scelte, 'RESOLVED');
  });
  $('#multiAnnulla').addEventListener('click', () => { S.scelte.clear(); disegna(); });
}

async function cambiaStatoMolte(righe, stato) {
  const nome = operatore(); if (!nome) return;
  let fatte = 0;
  for (const r of righe) {
    try {
      await api(`/api/shipment/${encodeURIComponent(r.tracking_number)}/workflow`, {
        method: 'POST', body: JSON.stringify({ workflow_status: stato, operator_name: nome }),
      });
      fatte += 1;
    } catch (err) { avvisa(`${r.order_name || r.tracking_number}: ${err.message}`, true); }
  }
  S.scelte.clear();
  avvisa(stato === 'RESOLVED' ? `${plurale(fatte, 'pratica chiusa', 'pratiche chiuse')}` : `${plurale(fatte, 'pratica presa', 'pratiche prese')} in carico`);
  await caricaDati({ quieto: true });
}

// ---------------------------------------------------------------------------
// Novita' da GLS dall'ultima volta
// ---------------------------------------------------------------------------

async function caricaNovita() {
  const visto = memoria('glsNuovaVisto');
  if (!visto) { memoria('glsNuovaVisto', new Date().toISOString()); return; }
  try {
    S.novita = await api(`/api/novita?dal=${encodeURIComponent(visto)}`);
    S.novitaSet = new Set((S.novita.pratiche || []).map(p => p.tracking_number));
    disegna();
  } catch { S.novita = null; }
}
function disegnaNovita() {
  const box = $('#novita');
  const n = S.novita;
  if (!n || !n.totale || S.sezioneBase !== 'coda') { box.classList.add('nascosto-del-tutto'); return; }
  const perTracking = new Map(spedizioni().map(r => [r.tracking_number, r]));
  const voci = (n.pratiche || []).slice(0, 8);
  const daGestire = (n.pratiche || []).filter(p => daVerificare(perTracking.get(p.tracking_number) || p)).length;
  box.innerHTML = `<div class="novita-testa"><div>
      <h2>${esc(dalMomento(n.dal))}, GLS ha aggiornato ${plurale(n.totale, 'pratica', 'pratiche')}</h2>
      <p>È l’ultima volta che hai segnato le novità come viste. ${daGestire === 0 ? 'Nessuna chiede di intervenire.' : daGestire === 1 ? 'Una sola chiede di intervenire.' : `${daGestire} chiedono di intervenire.`}</p>
    </div><button type="button" class="tasto" id="novitaViste">Segna tutte come viste</button></div>
    <ul>${voci.map(p => {
      const r = perTracking.get(p.tracking_number) || p;
      const g = gravita(r);
      const prima = p.prima ? (TITOLI[p.prima.category] || p.prima.state || 'Prima') : 'Prima notizia';
      const dopo = p.dopo ? (TITOLI[p.dopo.category] || p.dopo.state || 'Aggiornata') : titolo(r);
      const serve = daVerificare(r);
      const esito = serve ? 'Da gestire' : r.closed ? 'Chiusa' : inLavorazione(r) ? 'Già in lavorazione' : 'Nessuna azione';
      return `<li data-tracking="${esc(p.tracking_number)}" tabindex="0">
        <span class="forte taglia">${esc(r.order_name || p.tracking_number)} · ${esc(r.customer_name || 'Cliente')}</span>
        <span class="in-linea"><span class="etichetta e-neutro">${esc(prima)}</span><svg class="verso" viewBox="0 0 24 24" role="img" aria-label="diventa">${TRATTI.freccia}</svg><span class="etichetta e-${g}">${esc(dopo)}</span><span class="piccolo">${esc(giornoOra(p.dopo?.event_at || p.arrivato))}</span></span>
        <span class="esito-azione ${serve ? 'serve' : 'niente'}">${esito}</span></li>`;
    }).join('')}</ul>
    ${n.totale > voci.length ? `<p class="piccolo">E altre ${n.totale - voci.length}: hanno l’etichetta Novità nell’elenco.</p>` : ''}`;
  box.classList.remove('nascosto-del-tutto');
  $('#novitaViste').addEventListener('click', () => {
    memoria('glsNuovaVisto', new Date().toISOString());
    S.novita = null; S.novitaSet = new Set();
    disegna();
  });
  $$('li[data-tracking]', box).forEach(li => {
    li.addEventListener('click', () => apriDettaglio(li.dataset.tracking, li));
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter') apriDettaglio(li.dataset.tracking, li); });
  });
}

// ---------------------------------------------------------------------------
// Pannelli
// ---------------------------------------------------------------------------

function apriPannello(id, opener) {
  ['dettaglio', 'chat', 'analisi'].forEach(p => { if (p !== id) $('#' + p).classList.add('nascosto-del-tutto'); });
  S.opener = opener || document.activeElement;
  $('#velo').classList.remove('nascosto-del-tutto');
  const p = $('#' + id);
  p.classList.remove('nascosto-del-tutto');
  document.body.style.overflow = 'hidden';
  setTimeout(() => p.focus({ preventScroll: true }), 0);
}
function chiudiPannelli() {
  ['dettaglio', 'chat', 'analisi'].forEach(p => $('#' + p).classList.add('nascosto-del-tutto'));
  $('#velo').classList.add('nascosto-del-tutto');
  document.body.style.overflow = '';
  S.dettaglio = null;
  if (S.sezione === 'analisi' || S.sezione === 'chiedi') mostraSezione(S.sezioneBase || 'coda');
  if (S.opener && document.contains(S.opener)) S.opener.focus();
}

// ---------------------------------------------------------------------------
// Dettaglio pratica
// ---------------------------------------------------------------------------

async function apriDettaglio(tracking, opener, opzioni = {}) {
  const pannello = $('#dettaglio');
  const riga = spedizioni().find(x => x.tracking_number === tracking) || {};
  pannello.innerHTML = `<div class="pannello-testa"><div class="pannello-titolo"><div><span class="meta">${esc(riga.order_name || tracking)}</span><h2>${esc(riga.customer_name || 'Caricamento…')}</h2></div>${tastoChiudi()}</div></div><div class="pannello-corpo"><p class="meta">Carico la pratica…</p></div>`;
  $('.chiudi-pannello', pannello).addEventListener('click', chiudiPannelli);
  apriPannello('dettaglio', opener);
  try {
    const item = await api(`/api/shipment/${encodeURIComponent(tracking)}`);
    S.dettaglio = { ...riga, ...item };
    disegnaDettaglio(S.dettaglio, opzioni);
  } catch (err) {
    $('.pannello-corpo', pannello).innerHTML = `<p class="meta">Non riesco ad aprire la pratica: ${esc(err.message)}</p>`;
  }
}
const tastoChiudi = () => `<button type="button" class="tasto-icona chiudi-pannello" aria-label="Chiudi">${ic('chiudi')}</button>`;

function disegnaDettaglio(item, opzioni = {}) {
  const p = $('#dettaglio');
  const g = gravita(item);
  const links = item.links || {};
  const aperta = giacenzaAperta(item);
  const sv = svincoli(item);
  const luogo = [item.city, item.province].filter(Boolean).join(' · ');
  const sede = [item.gls_destination_depot, item.gls_destination_city].filter(Boolean).join(' · ');
  const quando = item.gls_event_at;
  const giorniFermo = giorniFa(quando);
  const titoloSuccesso = aperta && giorniFermo != null
    ? (giorniFermo <= 0 ? 'In giacenza da oggi' : giorniFermo === 1 ? 'In giacenza da ieri' : `In giacenza da ${giorniFermo} giorni`)
    : titolo(item);
  const workflow = ['RESOLVED', 'IGNORED'].includes(item.workflow_status) || item.closed ? 'RESOLVED' : IN_LAVORO.includes(item.workflow_status) ? 'IN_PROGRESS' : 'NEW';

  const azioni = (item.operator_actions || []).filter(a => (a.operator_name || '').trim().toLowerCase() !== 'sistema');
  const notaAzione = (azioni.find(a => (a.note || '').trim()) || {}).note || '';
  const nota = (item.operator_note || '').trim() || notaAzione.trim();
  const ultima = azioni[0];

  const fasce = [];
  if (item.unread_event_at) {
    fasce.push(`<div class="fascia novita-gls">${ic('novita')}<div><strong>Novità da GLS dopo l’ultima nota</strong><span>${esc(giornoOra(item.unread_event_at))} · ${esc(item.gls_status || 'nuovo evento')}</span><em>Sparisce quando registri un’azione.</em></div></div>`);
  }
  if (sv.respinto) {
    fasce.push(`<div class="fascia rifiutato">${ic('vietato')}<div><strong>GLS ha respinto lo svincolo</strong><span>${esc(sv.ultimo?.gls_result || 'L’istruzione va ripetuta o gestita fuori dal monitor.')}</span></div></div>`);
  } else if (ultima) {
    fasce.push(`<div class="fascia lavoro">${ic('spunta')}<div><strong>${esc(ultima.action_label || 'Azione registrata')} · ${esc(ultima.operator_name || 'Operatore')}, ${esc(quandoDetto(ultima.created_at))}</strong>${nota ? `<em>${esc(nota)}</em>` : ''}${sv.riuscito && aperta ? '<span>Lo svincolo è già stato inviato: si attende che GLS lo esegua.</span>' : ''}</div></div>`);
  }

  const contatti = [];
  if (item.customer_phone) contatti.push(`<a class="tasto" href="tel:${esc(item.customer_phone)}">${ic('telefono')}${esc(item.customer_phone)}</a>`);
  if (item.customer_phone) contatti.push(`<button type="button" class="tasto" id="dScrivi">${ic('busta')}Scrivi su Spoki</button>`);
  if (links.gls) contatti.push(`<a class="tasto mono" href="${esc(links.gls)}" target="_blank" rel="noopener">${esc(item.tracking_number)}${ic('esterno')}</a>`);
  if (links.shopify) contatti.push(`<a class="tasto" href="${esc(links.shopify)}" target="_blank" rel="noopener">Ordine Shopify${ic('esterno')}</a>`);

  p.innerHTML = `
    <div class="pannello-testa">
      <div class="pannello-titolo"><div>
        <span class="meta">${esc(item.order_name || '—')}${item.order_created_at ? ` · ordinato il ${esc(giorno(item.order_created_at, true))}` : ''}${item.total_amount != null ? ` · ${esc(soldi(item.total_amount, item.currency))}` : ''}${item.is_cod ? ' · <span class="cod">contrassegno</span>' : ''}</span>
        <h2>${esc(item.customer_name || 'Cliente')}</h2>
        <span class="meta">${esc(luogo)}</span>
      </div>${tastoChiudi()}</div>
      <div class="contatti">${contatti.join('')}</div>
    </div>
    <div class="pannello-corpo">
      <section class="successo s-${g}" aria-label="Cosa è successo">
        <div class="in-linea"><span class="etichetta e-${g}">${NOME_GRAVITA[g]}</span><span class="titolo">${esc(titoloSuccesso)}</span></div>
        <p>${esc(item.gls_status || 'Stato GLS non disponibile')}${item.gls_note && item.gls_note !== item.gls_status ? ` · ${esc(item.gls_note)}` : ''}</p>
        <span class="dove">${quando ? `Ultimo evento GLS ${esc(quandoDetto(quando))}` : 'Nessun evento GLS'}${item.gls_location ? ` · ${esc(item.gls_location)}` : ''}</span>
      </section>
      ${fasce.join('')}
      ${aperta ? formSvincolo(item, sv) : passoSemplice(item)}
      <section class="sezione" id="dRegistra" aria-label="Registra cosa hai fatto">
        <h3>Cosa hai fatto</h3>
        <div class="scelte" role="group" aria-label="Tipo di contatto">
          ${[['CUSTOMER_MESSAGE', 'Messaggio inviato'], ['CUSTOMER_CALLED', 'Cliente chiamato'], ['GLS_CONTACTED', 'GLS contattato']]
            .map(([k, t]) => `<button type="button" class="scelta" data-contatto="${k}" aria-pressed="${opzioni.contatto === k}">${t}</button>`).join('')}
        </div>
        <label class="nascosto" for="dNota">Nota</label>
        <textarea id="dNota" class="area" rows="2" placeholder="Nota facoltativa: cosa ti ha detto il cliente"></textarea>
        <div class="tra">
          <div class="segmenti" role="group" aria-label="Stato della pratica">
            ${[['NEW', 'Nuova'], ['IN_PROGRESS', 'In lavorazione'], ['RESOLVED', 'Chiusa']]
              .map(([k, t]) => `<button type="button" data-stato="${k}" aria-pressed="${(opzioni.contatto && workflow === 'NEW' ? 'IN_PROGRESS' : workflow) === k}">${t}</button>`).join('')}
          </div>
          <button type="button" class="tasto" id="dRegistraTasto"><b>Registra</b></button>
        </div>
      </section>
      ${storico(item)}
      <section class="sezione con-linea" aria-label="Dati della spedizione">
        <h3>Dati</h3>
        <div class="dati">
          <div class="dato"><span class="voce">Tracking GLS</span><span class="valore mono">${esc(item.tracking_number)}</span></div>
          <div class="dato"><span class="voce">Pagamento</span><span class="valore">${item.is_cod ? 'Contrassegno' : esc((item.payment_gateways || []).join(', ') || '—')}</span></div>
          <div class="dato"><span class="voce">Email</span><span class="valore">${item.customer_email ? `<a href="mailto:${esc(item.customer_email)}">${esc(item.customer_email)}</a>` : '—'}</span></div>
          <div class="dato"><span class="voce">Sede GLS</span><span class="valore">${esc(sede || '—')}${item.gls_destination_phone ? ` · <a href="tel:${esc(item.gls_destination_phone)}">${esc(item.gls_destination_phone)}</a>` : ''}</span></div>
          ${links.shopify_profile ? `<div class="dato"><span class="voce">Cliente su Shopify</span><span class="valore"><a href="${esc(links.shopify_profile)}" target="_blank" rel="noopener">Apri il profilo</a></span></div>` : ''}
        </div>
      </section>
    </div>
    <div class="pannello-piede">
      ${item.customer_phone ? `<a class="tasto" href="tel:${esc(item.customer_phone)}">${ic('telefono')}Chiama</a>` : ''}
      ${item.customer_phone ? `<button type="button" class="tasto" id="pScrivi">${ic('busta')}Scrivi</button>` : ''}
      ${aperta && !sv.riuscito ? '<button type="button" class="tasto primario" id="pSvincola">Svincola</button>' : '<button type="button" class="tasto primario" id="pRegistra">Registra</button>'}
    </div>`;
  collegaDettaglio(item, opzioni);
}

function passoSemplice(item) {
  const consigliato = (item.recommended_action || '').trim();
  const breve = passo(item);
  return `<section class="passo" aria-label="Prossimo passo"><div class="passo-testa"><span class="sopra">Prossimo passo</span><h3>${esc(breve)}</h3>${consigliato && consigliato !== breve ? `<p>${esc(consigliato)}</p>` : ''}</div></section>`;
}

function formSvincolo(item, sv) {
  const titoloPasso = sv.riuscito ? 'Svincolo già inviato: puoi mandarne un altro se serve' : sv.respinto ? 'Rifare l’istruzione di svincolo' : 'Mandare l’istruzione di svincolo a GLS';
  return `<section class="passo" id="dSvincolo" aria-label="Prossimo passo">
    <div class="passo-testa"><span class="sopra">Prossimo passo</span><h3>${esc(titoloPasso)}</h3></div>
    <div class="campi">
      <label class="campo largo">Istruzione
        <select id="sTipo">
          <option value="1">Riconsegna allo stesso indirizzo</option>
          <option value="2">Riconsegna a un nuovo indirizzo</option>
          <option value="7">Ritiro in sede da parte del cliente</option>
          <option value="3">Rientro al mittente</option>
          <optgroup label="Altre istruzioni">
            <option value="8">Consegna parziale e rientro</option>
            <option value="4">Distruzione</option>
            <option value="9">Consegna parziale e distruzione</option>
          </optgroup>
        </select>
      </label>
      <label class="campo">Data riconsegna<input id="sData" type="date" value="${prossimoLavorativo()}" min="${chiave(new Date())}"></label>
      <label class="campo" id="sSpeseCampo">Spese
        <select id="sSpese"><option value="sender">A carico del mittente</option><option value="recipient">A carico del destinatario</option><option value="">Non specificato</option></select>
      </label>
      <label class="campo">Telefono destinatario<input id="sTelefono" type="tel" maxlength="15" value="${esc(telefonoLocale(item.customer_phone || ''))}"></label>
      <label class="spuntina"><input id="sPreavviso" type="checkbox" checked> Avvisa il cliente per telefono</label>
      ${item.is_cod ? '<label class="spuntina"><input id="sAnnullaCod" type="checkbox"> Annulla il contrassegno</label>' : ''}
      <div class="campi campo largo nascosto-del-tutto" id="sIndirizzo">
        <label class="campo">Destinatario<input id="sNome" maxlength="20" value="${esc((item.customer_name || '').slice(0, 20))}"></label>
        <label class="campo">Indirizzo<input id="sVia" maxlength="30"></label>
        <label class="campo">Località<input id="sCitta" maxlength="30" value="${esc(item.city || '')}"></label>
        <label class="campo">CAP<input id="sCap" maxlength="5" inputmode="numeric"></label>
        <label class="campo">Provincia<input id="sProvincia" maxlength="3" value="${esc(item.province || '')}"></label>
      </div>
      <label class="campo largo">Note per GLS
        <textarea id="sNote" rows="2" maxlength="65" placeholder="Per esempio: citofono al secondo piano, scala B"></textarea>
        <span class="aiuto destra" id="sNoteConta">0 di 65 caratteri</span>
      </label>
    </div>
    <div id="sConferma"></div>
    <div class="riga-tasti" id="sTasti">
      <button type="button" class="tasto primario grande" id="sInvia">Invia a GLS</button>
      <button type="button" class="collegamento" id="sFuori">L’ho gestita fuori dal monitor</button>
    </div>
  </section>`;
}

function storico(item) {
  const eventi = (item.events || []).map(ev => ({
    quando: ev.event_at || ev.created_at, team: false, chi: 'GLS',
    testo: ev.state || 'Evento GLS', nota: [ev.note && ev.note !== ev.state ? ev.note : '', ev.location].filter(Boolean).join(' · '),
  }));
  const umane = (item.operator_actions || []).filter(a => (a.operator_name || '').trim().toLowerCase() !== 'sistema');
  const ultimaId = umane.length ? umane[0].id : null;
  const azioni = umane.map(a => ({ quando: a.created_at, team: true, chi: a.operator_name || 'Operatore', testo: a.action_label || a.action_type, nota: a.note || '', id: a.id }));
  const svincoliStorico = (item.stock_cases || []).flatMap(sc => sc.release_requests || []).map(r => ({
    quando: r.created_at, team: true, chi: r.operator_name || 'Operatore',
    testo: `Svincolo: ${r.release_label}`, nota: Number(r.gls_success) === 1 ? 'Ricevuto da GLS' : `Non accettato da GLS${r.gls_result ? ': ' + r.gls_result : ''}`,
  }));
  const tutti = [...eventi, ...azioni, ...svincoliStorico].sort((a, b) => (data(b.quando)?.getTime() || 0) - (data(a.quando)?.getTime() || 0));
  if (!tutti.length) return '<section class="sezione con-linea"><h3>Storico</h3><p class="meta">Ancora niente da raccontare.</p></section>';
  const voce = (e, i) => `<li class="${i >= 6 ? 'nascosto-del-tutto in-piu' : ''}">
    <span class="pallino ${e.team ? 'team' : ''}" aria-hidden="true"></span>
    <span class="quando">${esc(giornoOra(e.quando))}</span>
    <span class="colonna"><span class="chi ${e.team ? 'team' : ''}">${esc(e.chi)}${e.id && e.id === ultimaId ? ` <button type="button" class="annulla" data-annulla="${e.id}">annulla</button>` : ''}</span>
      <span class="cosa">${esc(e.testo)}</span>${e.nota ? `<span class="nota">${esc(e.nota)}</span>` : ''}</span></li>`;
  return `<section class="sezione con-linea" aria-label="Storico"><h3>Storico</h3>
    <ol class="storico">${tutti.map(voce).join('')}</ol>
    ${tutti.length > 6 ? `<button type="button" class="collegamento" id="dTuttoStorico">Mostra tutti gli eventi (${tutti.length})</button>` : ''}</section>`;
}

// Porta una sezione in vista scorrendo solo il corpo del pannello.
// scrollIntoView scorrerebbe anche il pannello stesso (che ha overflow
// nascosto): la testata sparirebbe in alto lasciando una fascia bianca.
function mostraNelPannello(sel, morbido = false) {
  const corpo = $('#dettaglio .pannello-corpo'); const el = $(sel);
  if (!corpo || !el) return;
  const top = el.getBoundingClientRect().top - corpo.getBoundingClientRect().top + corpo.scrollTop - 16;
  corpo.scrollTo({ top: Math.max(0, top), behavior: morbido ? 'smooth' : 'auto' });
}

function collegaDettaglio(item, opzioni) {
  const p = $('#dettaglio');
  $('.chiudi-pannello', p).addEventListener('click', chiudiPannelli);
  // Dopo Spoki la pratica e' gia' pronta per registrare "Messaggio inviato".
  const scriviSpoki = () => {
    if (!apriSpoki(item)) return;
    const chip = $('[data-contatto="CUSTOMER_MESSAGE"]', p);
    if (chip && chip.getAttribute('aria-pressed') !== 'true') chip.click();
  };
  $('#dScrivi')?.addEventListener('click', scriviSpoki);
  $('#pScrivi')?.addEventListener('click', scriviSpoki);
  $('#pSvincola')?.addEventListener('click', () => mostraNelPannello('#dSvincolo', true));
  $('#pRegistra')?.addEventListener('click', () => { mostraNelPannello('#dRegistra', true); $('#dNota')?.focus({ preventScroll: true }); });
  $('#dTuttoStorico')?.addEventListener('click', (e) => { $$('.in-piu', p).forEach(x => x.classList.remove('nascosto-del-tutto')); e.currentTarget.remove(); });

  $$('[data-contatto]', p).forEach(b => b.addEventListener('click', () => {
    const era = b.getAttribute('aria-pressed') === 'true';
    $$('[data-contatto]', p).forEach(x => x.setAttribute('aria-pressed', 'false'));
    b.setAttribute('aria-pressed', String(!era));
    if (!era && $('[data-stato="NEW"]', p).getAttribute('aria-pressed') === 'true') segnaStato('IN_PROGRESS');
  }));
  const segnaStato = (k) => $$('[data-stato]', p).forEach(x => x.setAttribute('aria-pressed', String(x.dataset.stato === k)));
  $$('[data-stato]', p).forEach(b => b.addEventListener('click', () => segnaStato(b.dataset.stato)));
  $('#dRegistraTasto').addEventListener('click', () => registra(item));

  $$('[data-annulla]', p).forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Cancellare questa operazione dallo storico?')) return;
    b.disabled = true;
    try {
      await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/annulla-azione`, { method: 'POST', body: JSON.stringify({ action_id: Number(b.dataset.annulla) }) });
      avvisa('Operazione cancellata');
      await dopoUnAzione(item.tracking_number);
    } catch (err) { avvisa(err.message, true); b.disabled = false; }
  }));

  const tipo = $('#sTipo');
  if (tipo) {
    const aggiorna = () => {
      $('#sIndirizzo').classList.toggle('nascosto-del-tutto', tipo.value !== '2');
      $('#sSpeseCampo').classList.toggle('nascosto-del-tutto', !['1', '2'].includes(tipo.value));
      $('#sConferma').innerHTML = '';
      $('#sTasti').classList.remove('nascosto-del-tutto');
    };
    tipo.addEventListener('change', aggiorna); aggiorna();
    $('#sNote').addEventListener('input', (e) => { $('#sNoteConta').textContent = `${e.target.value.length} di 65 caratteri`; });
    $('#sInvia').addEventListener('click', () => chiediConferma(item));
    $('#sFuori').addEventListener('click', () => registraAzione(item, 'STOCK_MANUAL_HANDLED', $('#dNota').value || ''));
  }
  if (opzioni.vaiA === 'svincolo') setTimeout(() => { mostraNelPannello('#dSvincolo'); $('#sTipo')?.focus({ preventScroll: true }); }, 60);
  if (opzioni.contatto) setTimeout(() => { mostraNelPannello('#dRegistra'); $('#dNota')?.focus({ preventScroll: true }); }, 60);
}

function chiediConferma(item) {
  const tipo = $('#sTipo');
  const etichetta = tipo.selectedOptions[0]?.textContent || 'istruzione';
  const grave = ['4', '9'].includes(tipo.value);
  if (tipo.value === '2' && !($('#sVia').value.trim() && $('#sCap').value.trim() && $('#sCitta').value.trim())) {
    avvisa('Per un nuovo indirizzo servono via, CAP e località.', true); $('#sVia').focus(); return;
  }
  const quando = ['1', '2'].includes(tipo.value) && $('#sData').value ? ` per il ${giorno($('#sData').value, true)}` : '';
  $('#sTasti').classList.add('nascosto-del-tutto');
  $('#sConferma').innerHTML = `<div class="conferma ${grave ? 'grave' : ''}" role="alert">
    <div><strong>${grave ? 'Attenzione: il collo verrà distrutto e non si torna indietro.' : 'GLS esegue davvero questa istruzione.'}</strong><br>
    “${esc(etichetta)}”${esc(quando)} per ${esc(item.order_name || '')} · ${esc(item.tracking_number)}.</div>
    <div class="riga-tasti"><button type="button" class="tasto ${grave ? 'pericolo' : 'primario'}" id="sSi">Sì, invia a GLS</button><button type="button" class="tasto" id="sNo">Annulla</button></div></div>`;
  $('#sNo').addEventListener('click', () => { $('#sConferma').innerHTML = ''; $('#sTasti').classList.remove('nascosto-del-tutto'); });
  $('#sSi').addEventListener('click', () => inviaSvincolo(item));
  $('#sSi').focus({ preventScroll: true });
}

async function inviaSvincolo(item) {
  const nome = operatore(); if (!nome) return;
  const b = $('#sSi'); b.disabled = true; b.textContent = 'Invio a GLS…';
  const v = (id) => ($(id)?.value || '').trim();
  try {
    const esito = await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/release-stock`, {
      method: 'POST', body: JSON.stringify({
        operator_name: nome, release_type: v('#sTipo'), delivery_date: v('#sData'), expense_payer: $('#sSpese')?.value || '',
        recipient_phone: v('#sTelefono'), customer_phone: item.customer_phone || '',
        phone_notice: Boolean($('#sPreavviso')?.checked), cancel_cod: Boolean($('#sAnnullaCod')?.checked), note: v('#sNote'),
        new_name: v('#sNome'), new_address: v('#sVia'), new_city: v('#sCitta'), new_zip: v('#sCap'), new_province: v('#sProvincia'),
      }),
    });
    avvisa(`GLS: ${esito.message || 'istruzione accettata'}`);
    S.giacenze = null;
    await dopoUnAzione(item.tracking_number);
  } catch (err) {
    avvisa(`Svincolo non confermato: ${err.message}`, true);
    b.disabled = false; b.textContent = 'Riprova';
  }
}

async function registra(item) {
  const p = $('#dettaglio');
  const contatto = $('[data-contatto][aria-pressed="true"]', p)?.dataset.contatto;
  const stato = $('[data-stato][aria-pressed="true"]', p)?.dataset.stato || 'IN_PROGRESS';
  const nota = $('#dNota').value.trim();
  if (contatto || nota) return registraAzione(item, contatto || 'NOTE', nota, stato);
  const nome = operatore(); if (!nome) return;
  try {
    await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/workflow`, {
      method: 'POST', body: JSON.stringify({ workflow_status: stato, operator_name: nome }),
    });
    avvisa(stato === 'RESOLVED' ? 'Pratica chiusa' : stato === 'NEW' ? 'Pratica rimessa fra le nuove' : 'Pratica in lavorazione');
    await dopoUnAzione(item.tracking_number);
  } catch (err) { avvisa(err.message, true); }
}

async function registraAzione(item, tipo, nota = '', stato) {
  const nome = operatore(); if (!nome) return false;
  try {
    await api(`/api/shipment/${encodeURIComponent(item.tracking_number)}/action`, {
      method: 'POST', body: JSON.stringify({ action_type: tipo, note: nota, operator_name: nome, ...(stato ? { workflow_status: stato } : {}) }),
    });
    avvisa('Registrato nella pratica');
    if (S.dettaglio?.tracking_number === item.tracking_number) await dopoUnAzione(item.tracking_number);
    return true;
  } catch (err) { avvisa(err.message, true); return false; }
}

async function dopoUnAzione(tracking) {
  await caricaDati({ quieto: true });
  if (S.dettaglio?.tracking_number === tracking) {
    try {
      const item = await api(`/api/shipment/${encodeURIComponent(tracking)}`);
      const riga = spedizioni().find(x => x.tracking_number === tracking) || {};
      S.dettaglio = { ...riga, ...item };
      disegnaDettaglio(S.dettaglio);
    } catch {}
  }
}

// ---------------------------------------------------------------------------
// Scrivere al cliente: si passa da Spoki, come nella pagina classica
// ---------------------------------------------------------------------------

function urlSpoki(r) {
  const c = cifre(r.customer_phone);
  return c ? `https://app.spoki.com/contacts?search=${encodeURIComponent(c)}` : '';
}
function apriSpoki(r) {
  const url = urlSpoki(r);
  if (!url) { avvisa('Il cliente non ha un numero di telefono: scrivi dall’email.', true); return false; }
  window.open(url, '_blank', 'noopener');
  return true;
}

// ---------------------------------------------------------------------------
// Storico giacenze
// ---------------------------------------------------------------------------

function mostraSezione(sezione) {
  if (['coda', 'giacenze'].includes(sezione)) S.sezioneBase = sezione;
  S.sezione = sezione;
  $$('.navigazione-basso button').forEach(b => b.toggleAttribute('aria-current', b.dataset.sezione === sezione));
  $$('.navigazione-basso button[aria-current]').forEach(b => b.setAttribute('aria-current', 'page'));
  const giacenze = S.sezioneBase === 'giacenze';
  $('#vistaGiacenze').classList.toggle('nascosto-del-tutto', !giacenze);
  $('#vistaCoda').classList.toggle('nascosto-del-tutto', giacenze);
  $('#filtri').classList.toggle('nascosto-del-tutto', giacenze);
  if (giacenze) $('#novita').classList.add('nascosto-del-tutto');
}

async function apriGiacenze() {
  S.scelte.clear(); disegnaSelezione();
  mostraSezione('giacenze');
  window.scrollTo({ top: 0 });
  $('#vistaGiacenze').innerHTML = '<p class="meta">Carico lo storico giacenze…</p>';
  if (!S.giacenze) {
    try { S.giacenze = await api('/api/stocks'); }
    catch (err) { $('#vistaGiacenze').innerHTML = `<p class="meta">Storico non disponibile: ${esc(err.message)}</p>`; return; }
  }
  disegnaGiacenze();
}

function esitoGiacenza(c) {
  if (c.status === 'OPEN') return 'aperta';
  const k = String(c.outcome_category || '').toUpperCase();
  if (k === 'DELIVERED') return 'consegnata';
  if (k === 'RETURN') return 'rientrata';
  if (['OUT_FOR_DELIVERY', 'SCHEDULED', 'IN_TRANSIT', 'CORRESPONDENT', 'SERVICE_AREA', 'LINEHAUL_DELAY'].includes(k)) return 'ripartita';
  return 'altro';
}
const ESITI = {
  aperta: { nome: 'Ancora aperta', plurale: 'ancora aperte', classe: 'e-CRITICAL', colore: 'var(--massima)' },
  consegnata: { nome: 'Consegnata', plurale: 'consegnate', classe: 'e-NORMAL', colore: 'var(--normale)' },
  rientrata: { nome: 'Rientrata', plurale: 'rientrate al mittente', classe: 'e-WARNING', colore: 'var(--alta)' },
  ripartita: { nome: 'Ripartita', plurale: 'ripartite', classe: 'e-INFO', colore: 'var(--info)' },
  altro: { nome: 'Da verificare', plurale: 'da verificare', classe: 'e-neutro', colore: '#98a2b3' },
};

function disegnaGiacenze() {
  const vista = $('#vistaGiacenze');
  if (!S.giacenze) return;
  const f = S.filtriGiacenze;
  let casi = S.giacenze.cases || [];
  if (f.periodo) {
    const da = new Date(); da.setHours(0, 0, 0, 0); da.setDate(da.getDate() - Number(f.periodo));
    casi = casi.filter(c => { const d = data(c.entered_at || c.created_at); return d && d >= da; });
  }
  const nelPeriodo = casi;
  const conti = { aperta: 0, consegnata: 0, rientrata: 0, ripartita: 0, altro: 0 };
  nelPeriodo.forEach(c => { conti[esitoGiacenza(c)] += 1; });
  const gestite = nelPeriodo.filter(c => c.handled).length;
  if (f.esito) casi = casi.filter(c => esitoGiacenza(c) === f.esito);
  if (f.gestite) casi = casi.filter(c => (f.gestite === 'si') === Boolean(c.handled));
  if (f.testo) {
    const q = f.testo.toLowerCase();
    casi = casi.filter(c => [c.order_name, c.customer_name, c.tracking_number, c.city].some(v => String(v || '').toLowerCase().includes(q)));
  }
  casi = casi.slice().sort((a, b) => (esitoGiacenza(a) === 'aperta' ? 0 : 1) - (esitoGiacenza(b) === 'aperta' ? 0 : 1)
    || (data(b.entered_at || b.created_at)?.getTime() || 0) - (data(a.entered_at || a.created_at)?.getTime() || 0));
  const periodoTesto = f.periodo ? `Ultimi ${f.periodo} giorni` : 'Da sempre';
  const ordineEsiti = ['consegnata', 'rientrata', 'ripartita', 'aperta', 'altro'];
  const opz = (val, attuale, testo) => `<option value="${val}" ${val === attuale ? 'selected' : ''}>${testo}</option>`;

  vista.innerHTML = `
    <div class="titolo-vista">
      <button type="button" class="indietro" id="gIndietro">${ic('indietro')}Torna alla coda</button>
      <h1>Storico giacenze</h1>
      <p>Ogni volta che un collo si è fermato in sede GLS, e com’è finita. ${esc(periodoTesto)}.</p>
    </div>
    <section class="riquadro esiti" aria-label="Com’è finita">
      <div class="in-linea"><span class="grande">${nelPeriodo.length}</span><span class="meta">${nelPeriodo.length === 1 ? 'giacenza' : 'giacenze'} · ${gestite} gestite dal team</span></div>
      <div class="barra-esiti" role="img" aria-label="${ordineEsiti.filter(k => conti[k]).map(k => `${conti[k]} ${ESITI[k].plurale}`).join(', ')}">
        ${ordineEsiti.filter(k => conti[k]).map(k => `<span style="flex:${conti[k]};background:${ESITI[k].colore}"></span>`).join('')}
      </div>
      <div class="legenda">${ordineEsiti.filter(k => conti[k] || k !== 'altro').map(k => `<span><i style="background:${ESITI[k].colore}"></i><b>${conti[k]}</b> ${ESITI[k].plurale}</span>`).join('')}</div>
    </section>
    <div class="filtri">
      <div class="cerca-chiara"><label for="gCerca" class="nascosto">Cerca fra le giacenze</label>${ic('cerca')}<input id="gCerca" type="search" placeholder="Ordine, cliente o tracking" value="${esc(f.testo)}"></div>
      <label class="pillola-scelta"><span class="nascosto">Esito</span><select id="gEsito" class="${f.esito ? 'attivo' : ''}">${opz('', f.esito, 'Esito: tutti')}${ordineEsiti.map(k => opz(k, f.esito, ESITI[k].nome)).join('')}</select></label>
      <label class="pillola-scelta"><span class="nascosto">Gestite</span><select id="gGestite" class="${f.gestite ? 'attivo' : ''}">${opz('', f.gestite, 'Gestite: tutte')}${opz('si', f.gestite, 'Gestite dal team')}${opz('no', f.gestite, 'Senza azioni')}</select></label>
      <label class="pillola-scelta"><span class="nascosto">Periodo</span><select id="gPeriodo">${opz('7', f.periodo, 'Ultimi 7 giorni')}${opz('30', f.periodo, 'Ultimi 30 giorni')}${opz('90', f.periodo, 'Ultimi 90 giorni')}${opz('', f.periodo, 'Da sempre')}</select></label>
      <span class="conteggio">${plurale(casi.length, 'giacenza', 'giacenze')} nella vista</span>
    </div>
    <section class="riquadro elenco elenco-giacenze" aria-label="Elenco giacenze">
      <div class="griglia-giacenze intestazione"><span>Esito</span><span>Ordine e cliente</span><span>In giacenza</span><span>Cosa abbiamo fatto</span><span>Com’è finita</span><span></span></div>
      ${casi.slice(0, 300).map(rigaGiacenza).join('') || '<div class="piede-elenco">Nessuna giacenza con questi criteri.</div>'}
    </section>`;

  $('#gIndietro').addEventListener('click', () => vaiA(CODE.some(c => c.id === S.coda) ? S.coda : 'verificare'));
  let attesa;
  $('#gCerca').addEventListener('input', (e) => {
    clearTimeout(attesa);
    attesa = setTimeout(() => { f.testo = e.target.value; disegnaGiacenze(); const c = $('#gCerca'); c.focus(); c.setSelectionRange(c.value.length, c.value.length); }, 250);
  });
  $('#gEsito').addEventListener('change', (e) => { f.esito = e.target.value; disegnaGiacenze(); });
  $('#gGestite').addEventListener('change', (e) => { f.gestite = e.target.value; disegnaGiacenze(); });
  $('#gPeriodo').addEventListener('change', (e) => { f.periodo = e.target.value; disegnaGiacenze(); });
  $$('.riga[data-tracking]', vista).forEach(el => {
    el.addEventListener('click', () => apriDettaglio(el.dataset.tracking, el));
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter') apriDettaglio(el.dataset.tracking, el); });
  });
}

function rigaGiacenza(c) {
  const k = esitoGiacenza(c); const e = ESITI[k];
  const inizio = c.entered_at || c.created_at;
  const fine = c.exited_at || c.outcome_event_at;
  let durata = '';
  if (k === 'aperta') durata = daQuanto(inizio);
  else if (fine && data(inizio)) {
    const n = Math.max(0, Math.round((data(fine) - data(inizio)) / 86400000));
    durata = n === 0 ? 'meno di un giorno' : plurale(n, 'giorno', 'giorni');
  }
  const istr = c.latest_instruction; const az = c.latest_action;
  let fatto = 'Nessuna azione';
  if (istr) fatto = `Svincolo inviato da ${istr.operator_name || 'operatore'}: ${String(istr.release_label || '').toLowerCase()}${Number(istr.gls_success) === 1 ? '' : ' · non accettato da GLS'}`;
  else if (az) fatto = `${az.action_label || 'Azione'} · ${az.operator_name || 'operatore'}`;
  const finita = k === 'aperta' ? 'Ancora ferma in sede' : `${c.outcome_label || e.nome}${fine ? ` il ${giorno(fine)}` : ''}`;
  return `<div class="griglia-giacenze riga" data-tracking="${esc(c.tracking_number)}" tabindex="0">
    <span class="etichetta ${e.classe}">${e.nome}</span>
    <span class="colonna"><span class="forte taglia">${esc(c.order_name || c.tracking_number)} · ${esc(c.customer_name || 'Cliente')}</span><span class="piccolo">${esc([c.city, c.province].filter(Boolean).join(' · '))}</span></span>
    <span class="colonna"><span>dal ${esc(giorno(inizio))}</span><span class="piccolo">${esc(durata)}</span></span>
    <span>${esc(fatto)}</span>
    <span class="meta">${esc(finita)}</span>
    <button type="button" class="freccia-apri" aria-label="Apri la pratica" tabindex="-1">${ic('avanti')}</button>
  </div>`;
}

// ---------------------------------------------------------------------------
// Analisi del mese
// ---------------------------------------------------------------------------

async function apriAnalisi(opener) {
  const p = $('#analisi');
  p.innerHTML = `<div class="pannello-testa"><div class="pannello-titolo"><div><h2>Analisi del mese</h2><span class="meta">Confronto con lo stesso tratto del mese scorso</span></div>${tastoChiudi()}</div></div>
    <div class="pannello-corpo" id="analisiCorpo"><p class="meta">Carico i numeri…</p></div>`;
  $('.chiudi-pannello', p).addEventListener('click', chiudiPannelli);
  apriPannello('analisi', opener);
  if (!S.analisi) {
    try { S.analisi = await api('/api/analisi'); }
    catch (err) { $('#analisiCorpo').innerHTML = `<p class="meta">Analisi non disponibili: ${esc(err.message)}</p>`; return; }
  }
  disegnaAnalisi();
}
function disegnaAnalisi() {
  const corpo = $('#analisiCorpo'); if (!corpo) return;
  const m = S.analisi?.mese || {}; const serie = S.analisi?.serie || {};
  const meseScorso = MESI[Number(String(m.prima_da || '').slice(5, 7)) - 1] || 'il mese scorso';
  const voci = [['consegnate', 'Consegnate questo mese'], ['giacenze', 'Entrate in giacenza'], ['rientri', 'Rientrate al mittente']];
  corpo.innerHTML = `<div class="mese">${voci.map(([k, t]) => {
      const v = (m.voci || {})[k] || { ora: 0, prima: 0 };
      const ora = Number(v.ora || 0); const prima = Number(v.prima || 0);
      let confronto = `nessun dato su ${esc(meseScorso)}`;
      if (prima) {
        const s = Math.round(((ora - prima) / prima) * 100);
        confronto = `${ic(s > 0 ? 'su' : s < 0 ? 'giu' : 'pari')}<b>${s > 0 ? '+' : ''}${s}%</b> sui primi ${m.giorni} giorni di ${esc(meseScorso)} (${prima})`;
      }
      return `<div class="scheda"><span class="voce">${t}</span><span class="cifra">${ora}</span><span class="confronto">${confronto}</span></div>`;
    }).join('')}</div>
    <section class="sezione"><h3>Ultimi 30 giorni</h3><div class="grafico" id="grafico"></div></section>`;
  disegnaGrafico($('#grafico'), serie);
}
function disegnaGrafico(box, serie) {
  const giorni = serie.giorni || []; const linee = (serie.serie || []).slice(0, 2);
  if (!giorni.length || !linee.length) { box.innerHTML = '<p class="meta">Nessun dato.</p>'; return; }
  const W = Math.max(280, box.clientWidth || 520); const H = 200; const sx = 8; const dx = 96; const sopra = 8; const sotto = 24;
  const massimo = Math.max(1, ...linee.flatMap(l => l.valori));
  const x = (i) => sx + (i / Math.max(1, giorni.length - 1)) * (W - sx - dx);
  const y = (v) => sopra + (1 - v / massimo) * (H - sopra - sotto);
  const colori = ['var(--accento)', '#7d8794'];
  const etichette = linee.map((l, i) => ({ y: y(l.valori[l.valori.length - 1] || 0), i }));
  etichette.sort((a, b) => a.y - b.y);
  for (let k = 1; k < etichette.length; k++) if (etichette[k].y - etichette[k - 1].y < 16) etichette[k].y = etichette[k - 1].y + 16;
  const posEtichetta = new Map(etichette.map(e => [e.i, e.y]));
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${linee.map(l => `${l.nome}: ${l.valori.reduce((a, b) => a + b, 0)} in 30 giorni`).join('; ')}">
    <line x1="${sx}" x2="${W - dx}" y1="${H - sotto}" y2="${H - sotto}" stroke="#e7eaf0"/>
    <line x1="${sx}" x2="${W - dx}" y1="${y(massimo)}" y2="${y(massimo)}" stroke="#eef1f5" stroke-dasharray="2 4"/>
    <text x="${sx}" y="${y(massimo) - 4}">${massimo}</text>
    ${linee.map((l, i) => `<polyline fill="none" stroke="${colori[i]}" stroke-width="1.5" stroke-linejoin="round" points="${l.valori.map((v, j) => `${x(j).toFixed(1)},${y(v).toFixed(1)}`).join(' ')}"/>
      <text x="${W - dx + 8}" y="${posEtichetta.get(i) + 4}" style="fill:${colori[i]}">${esc(l.nome)}</text>`).join('')}
    <text x="${sx}" y="${H - 6}">${esc(giorno(giorni[0]))}</text>
    <text x="${W - dx}" y="${H - 6}" text-anchor="end">${esc(giorno(giorni[giorni.length - 1]))}</text>
  </svg>`;
}

// ---------------------------------------------------------------------------
// Chiedi al monitor
// ---------------------------------------------------------------------------

function apriChat(opener) {
  const p = $('#chat');
  if (!p.dataset.pronta) {
    p.innerHTML = `<div class="pannello-testa"><div class="pannello-titolo"><div><h2>Chiedi al monitor</h2><span class="meta">Risponde leggendo i dati delle spedizioni</span></div>
      <button type="button" class="tasto-icona" id="chatNuova" aria-label="Nuova conversazione" title="Nuova conversazione">${ic('piu')}</button>${tastoChiudi()}</div></div>
      <div class="chat-registro" id="chatRegistro" aria-live="polite"></div>
      <form class="chat-scrivi" id="chatForm"><label for="chatDomanda" class="nascosto">La tua domanda</label>
        <textarea id="chatDomanda" rows="1" placeholder="Scrivi una domanda"></textarea>
        <button type="submit" class="tasto-icona" aria-label="Invia">${ic('invia')}</button></form>`;
    p.dataset.pronta = '1';
    $('.chiudi-pannello', p).addEventListener('click', chiudiPannelli);
    $('#chatNuova').addEventListener('click', () => { S.chatStoria = []; disegnaChatVuota(); $('#chatDomanda').focus(); });
    const area = $('#chatDomanda');
    area.addEventListener('input', () => { area.style.height = 'auto'; area.style.height = `${Math.min(160, area.scrollHeight)}px`; });
    area.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chatForm').requestSubmit(); } });
    $('#chatForm').addEventListener('submit', (e) => { e.preventDefault(); chiedi(); });
    disegnaChatVuota();
  }
  apriPannello('chat', opener);
  setTimeout(() => $('#chatDomanda').focus(), 0);
}
function disegnaChatVuota() {
  $('#chatRegistro').innerHTML = `<div class="chat-vuota">${ic('scintilla')}<span>Chiedi quello che ti serve sulle spedizioni, come lo chiederesti a un collega.</span></div>`;
}
async function chiedi() {
  const area = $('#chatDomanda');
  const domanda = area.value.trim(); if (!domanda) return;
  area.value = ''; area.style.height = 'auto';
  const reg = $('#chatRegistro');
  $('.chat-vuota', reg)?.remove();
  reg.insertAdjacentHTML('beforeend', `<div class="bolla mia">${esc(domanda)}</div><div class="bolla sua attesa">Ci penso…</div>`);
  const bolla = reg.lastElementChild;
  reg.scrollTop = reg.scrollHeight;
  try {
    const esito = await api('/api/assistente', { method: 'POST', body: JSON.stringify({ domanda, storia: S.chatStoria }) });
    S.chatStoria = [...S.chatStoria, { ruolo: 'operatore', testo: domanda }, { ruolo: 'assistente', testo: esito.risposta || '' }].slice(-8);
    const pezzi = [`<div>${esc(esito.risposta || '').replace(/\n/g, '<br>')}</div>`];
    if (esito.ripiego) pezzi.push('<div class="avvertenza">Risposta essenziale, calcolata dalle sole regole del monitor.</div>');
    const t = esito.tabella;
    if (t && (t.righe || []).length) {
      pezzi.push(`<table><thead><tr>${(t.colonne || []).map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${t.righe.map(r => `<tr>${r.map(v => `<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
    }
    const righe = esito.righe || [];
    if (righe.length) {
      const quante = esito.totale_righe > righe.length ? `${righe.length} di ${esito.totale_righe}` : `${righe.length}`;
      pezzi.push(`<button type="button" class="collegamento" data-mostra>Mostrami quali (${quante})</button>`);
    }
    bolla.classList.remove('attesa');
    bolla.innerHTML = pezzi.join('');
    $('[data-mostra]', bolla)?.addEventListener('click', async () => {
      S.assistenteTracking = righe.map(r => r.tracking_number).filter(Boolean);
      chiudiPannelli();
      await vaiA('assistente');
    });
  } catch (err) {
    bolla.classList.remove('attesa');
    bolla.innerHTML = `<div>Non riesco a rispondere adesso: ${esc(err.message)}</div>`;
  }
  reg.scrollTop = reg.scrollHeight;
}

// ---------------------------------------------------------------------------
// Sincronizzazione
// ---------------------------------------------------------------------------

async function sincronizza() {
  if (S.sync) return;
  const tasto = $('#sincronizza');
  tasto.classList.add('gira'); tasto.disabled = true;
  S.sync = true;
  $('#avanzamento').classList.remove('nascosto-del-tutto');
  $('#aggiornato').textContent = 'Aggiornamento in corso';
  try {
    const r = await api('/api/sync', { method: 'POST', body: '{}' });
    if (r && r.done) return fineSync('Aggiornamento completato');
    seguiSync();
  } catch (err) {
    // Una sincronizzazione gia' in corso, magari partita dal cron, si segue lo stesso.
    if (/corso|running|already/i.test(err.message)) return seguiSync();
    fineSync(null); avvisa(err.message, true);
  }
}
function seguiSync() {
  const giro = async () => {
    try {
      const p = await api('/api/sync/progress');
      const tot = Number(p.total || 0);
      const barra = $('#avanzamentoBarra');
      barra.parentElement.classList.toggle('indefinita', !tot);
      barra.style.width = tot ? `${Math.min(100, Number(p.percent || 0))}%` : '';
      $('#avanzamentoTesto').textContent = tot ? `${p.label || 'Controllo GLS'} · ${p.processed || 0} di ${tot}` : (p.label || 'Preparazione…');
      if (p.running) { setTimeout(giro, 1000); return; }
      fineSync(p.phase === 'ERROR' ? null : 'Aggiornamento completato', p.phase === 'ERROR' ? (p.label || 'Aggiornamento interrotto') : '');
    } catch (err) { fineSync(null); avvisa(err.message, true); }
  };
  giro();
}
async function fineSync(messaggio, errore = '') {
  S.sync = null;
  const tasto = $('#sincronizza'); tasto.classList.remove('gira'); tasto.disabled = false;
  $('#avanzamento').classList.add('nascosto-del-tutto');
  if (messaggio) avvisa(messaggio);
  if (errore) avvisa(errore, true);
  S.giacenze = null; S.analisi = null;
  await caricaDati({ quieto: true });
  await caricaNovita();
  if (S.sezioneBase === 'giacenze') apriGiacenze();
}

// ---------------------------------------------------------------------------
// Avvio
// ---------------------------------------------------------------------------

function collega() {
  let attesa;
  $('#cerca').addEventListener('input', (e) => {
    clearTimeout(attesa);
    attesa = setTimeout(async () => {
      S.ricerca = e.target.value.trim();
      S.limite = 200;
      if (S.ricerca) { mostraSezione('coda'); await servonoTutte(); }
      disegna();
    }, 200);
  });
  $('#apriRicerca').addEventListener('click', () => { const b = $('#ricercaBox'); b.classList.toggle('aperta'); if (b.classList.contains('aperta')) $('#cerca').focus(); });
  $('#sincronizza').addEventListener('click', sincronizza);
  $('#apriChat').addEventListener('click', (e) => apriChat(e.currentTarget));
  $('#apriAnalisi').addEventListener('click', (e) => apriAnalisi(e.currentTarget));
  $('#apriArchivio').addEventListener('click', (e) => { e.stopPropagation(); apriMenu(e.currentTarget, $('#menuArchivio')); });
  $('#apriImpostazioni').addEventListener('click', (e) => { e.stopPropagation(); apriMenu(e.currentTarget, $('#menuImpostazioni')); });
  document.addEventListener('click', (e) => { if (!e.target.closest('.menu-ancora')) chiudiMenu(); });
  $('#velo').addEventListener('click', chiudiPannelli);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$$('.menu').every(m => m.classList.contains('nascosto-del-tutto'))) return chiudiMenu();
    if (!$('#velo').classList.contains('nascosto-del-tutto')) return chiudiPannelli();
  });
  $('#filtroPriorita').addEventListener('change', (e) => { S.filtri.priorita = e.target.value; disegna(); });
  $('#filtroStato').addEventListener('change', (e) => { S.filtri.stato = e.target.value; disegna(); });
  $('#filtroPeriodo').addEventListener('change', (e) => { S.filtri.periodo = e.target.value; disegna(); });
  $('#filtroContrassegno').addEventListener('change', (e) => { S.filtri.cod = e.target.checked; disegna(); });
  $('#azzeraFiltri').addEventListener('click', azzeraFiltri);
  $$('.navigazione-basso button').forEach(b => b.addEventListener('click', () => {
    const s = b.dataset.sezione;
    if (s === 'coda') { chiudiPannelli(); return vaiA(CODE.some(c => c.id === S.coda) ? S.coda : 'verificare'); }
    if (s === 'giacenze') { chiudiPannelli(); return apriGiacenze(); }
    mostraSezione(s);
    if (s === 'analisi') apriAnalisi(b); else apriChat(b);
  }));
  window.addEventListener('resize', () => { if (S.analisi && !$('#analisi').classList.contains('nascosto-del-tutto')) disegnaGrafico($('#grafico'), S.analisi.serie || {}); });
  // Una sincronizzazione partita altrove (cron o un collega) si vede anche qui.
  setInterval(() => { if (!document.hidden && !S.sync) caricaDati({ quieto: true }); }, 120000);
  setInterval(aggiornaOra, 30000);
}

async function avvia() {
  collega();
  try { S.me = await api('/api/me'); } catch {}
  await caricaDati();
  await caricaNovita();
  try {
    const p = await api('/api/sync/progress');
    if (p.running) { S.sync = true; $('#sincronizza').classList.add('gira'); $('#avanzamento').classList.remove('nascosto-del-tutto'); seguiSync(); }
  } catch {}
}
avvia();
