/* ==========================================================================
   Gestionale CRM — interactions (Alpine + HTMX glue)
   ========================================================================== */

// ---- Theme / density --------------------------------------------------------
function applyUI(theme, density) {
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.setAttribute('data-density', density);
}

window.UI = {
  setTheme(theme) {
    applyUI(theme, document.documentElement.getAttribute('data-density') || 'normal');
    fetch('/ui/theme/', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'theme=' + encodeURIComponent(theme),
    });
  },
  setDensity(density) {
    applyUI(document.documentElement.getAttribute('data-theme') || 'light', density);
    fetch('/ui/density/', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'density=' + encodeURIComponent(density),
    });
  },
};

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : '';
}

// Nome del cookie CSRF = settings.CSRF_COOKIE_NAME
function csrfToken() {
  return getCookie('aigiscrm_csrftoken');
}

// ---- Toast stack ------------------------------------------------------------
function ensureToastLayer() {
  let layer = document.querySelector('.toast-layer');
  if (!layer) {
    layer = document.createElement('div');
    layer.className = 'toast-layer';
    layer.setAttribute('role', 'status');
    layer.setAttribute('aria-live', 'polite');
    document.body.appendChild(layer);
  }
  return layer;
}

window.toast = function (message, { level = 'info', timeout = 4000 } = {}) {
  const layer = ensureToastLayer();
  const el = document.createElement('div');
  el.className = 'toast ' + level;
  if (level === 'error') el.setAttribute('role', 'alert');
  el.textContent = message;
  layer.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .2s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 220);
  }, timeout);
};

// ---- Pipeline drag & drop ---------------------------------------------------
// Supporta spostamento tra colonne e riordino interno (invia `posizione`).
// Inserisce un placeholder dinamico per visualizzare il drop target.

function pipePlaceholder() {
  let el = document.getElementById('pipe-placeholder');
  if (!el) {
    el = document.createElement('div');
    el.id = 'pipe-placeholder';
    el.className = 'pipe-placeholder';
  }
  return el;
}

function nextCardBelow(col, y) {
  // Trova la card la cui metà superiore è sotto il cursore, escluse quelle in drag.
  const cards = [...col.querySelectorAll('.pipe-card:not(.dragging)')];
  for (const c of cards) {
    const rect = c.getBoundingClientRect();
    if (y < rect.top + rect.height / 2) return c;
  }
  return null;
}

document.addEventListener('dragstart', (e) => {
  const card = e.target.closest('.pipe-card');
  if (!card || card.getAttribute('draggable') !== 'true' || !card.dataset.oppId || pipelineSaving) return;
  card.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/opp-id', card.dataset.oppId);
  e.dataTransfer.setData('text/plain', card.dataset.oppId);
});

document.addEventListener('dragend', (e) => {
  const card = e.target.closest('.pipe-card');
  if (card) card.classList.remove('dragging');
  document.querySelectorAll('.pipe-col.drop-target').forEach(c => c.classList.remove('drop-target'));
  const ph = document.getElementById('pipe-placeholder');
  if (ph) ph.remove();
});

document.addEventListener('dragover', (e) => {
  const col = e.target.closest('.pipe-col');
  if (!col || !document.querySelector('.pipe-card.dragging') || pipelineSaving) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';

  document.querySelectorAll('.pipe-col.drop-target').forEach(c => {
    if (c !== col) c.classList.remove('drop-target');
  });
  col.classList.add('drop-target');

  // Posiziona il placeholder in base alla Y del cursore
  const ph = pipePlaceholder();
  const nextCard = nextCardBelow(col, e.clientY);
  if (nextCard) {
    col.insertBefore(ph, nextCard);
  } else {
    // inserisci prima del bottone "Nuova" (ultimo figlio <a class="btn btn-subtle">)
    const btn = col.querySelector(':scope > .btn');
    if (btn) col.insertBefore(ph, btn);
    else col.appendChild(ph);
  }
});

document.addEventListener('dragleave', (e) => {
  const col = e.target.closest('.pipe-col');
  if (!col) return;
  if (!col.contains(e.relatedTarget)) col.classList.remove('drop-target');
});

let pipelineSaving = false;
document.addEventListener('drop', async (e) => {
  const col = e.target.closest('.pipe-col');
  const dragged = document.querySelector('.pipe-card.dragging');
  if (!col || !dragged || pipelineSaving) return;
  e.preventDefault();
  col.classList.remove('drop-target');

  const oppId = e.dataTransfer.getData('text/opp-id');
  const stadio = col.dataset.stadio;
  if (!oppId || oppId !== dragged.dataset.oppId || !stadio) return;

  // Calcola la posizione del placeholder tra le sole pipe-card (escluso dragging)
  const ph = document.getElementById('pipe-placeholder');
  let posizione = 0;
  if (ph) {
    const cards = [...col.querySelectorAll('.pipe-card:not(.dragging)')];
    for (const c of cards) {
      if (c.compareDocumentPosition(ph) & Node.DOCUMENT_POSITION_FOLLOWING) {
        posizione++;
      }
    }
    ph.remove();
  }

  const form = new FormData();
  form.set('stadio', stadio);
  form.set('posizione', String(posizione));

  const target = document.getElementById('pipeline-results') || document.getElementById('pipeline-board');
  if (!target) return;
  const url = new URL(dragged.dataset.moveUrl || `/opportunita/${oppId}/sposta/`, location.origin);
  url.search = location.search;
  pipelineSaving = true;
  target.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken(), 'HX-Request': 'true' },
      body: form,
    });
    if (!response.ok || response.redirected) {
      throw new Error(response.status === 403 ? 'Non hai i permessi per spostare questa opportunità.' :
        response.redirected ? 'Sessione scaduta: accedi di nuovo per continuare.' : 'Spostamento non riuscito. Riprova.');
    }
    const html = await response.text();
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const replacement = parsed.getElementById(target.id);
    if (!replacement) throw new Error('Risposta non valida. Ricarica la pagina e riprova.');
    // Sostituisci il wrapper: innerHTML annidava board con lo stesso id.
    target.replaceWith(replacement);
    if (window.htmx) window.htmx.process(replacement);
    toast('Opportunità aggiornata', { level: 'success' });
    document.body.dispatchEvent(new CustomEvent('crm:changed', { bubbles: true }));
  } catch (error) {
    toast(error instanceof TypeError ? 'Connessione non disponibile. Riprova.' : error.message, { level: 'error' });
  } finally {
    pipelineSaving = false;
    target.removeAttribute('aria-busy');
  }
});

// ---- Filter chip dropdown: close on outside click --------------------------
document.addEventListener('click', (e) => {
  document.querySelectorAll('[data-dropdown].open').forEach(drop => {
    if (!drop.contains(e.target)) drop.classList.remove('open');
  });
});

// ---- Menu laterale su tablet e telefono -----------------------------------
window.toggleSidebar = (aperto) => {
  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;
  const apri = typeof aperto === 'boolean' ? aperto : !sidebar.classList.contains('open');
  sidebar.classList.toggle('open', apri);
  document.querySelector('.sfondo-menu')?.classList.toggle('open', apri);
  document.querySelector('.apri-menu')?.setAttribute('aria-expanded', String(apri));
  document.body.classList.toggle('menu-open', apri);
  if (apri) {
    sidebar.setAttribute('role', 'dialog');
    sidebar.setAttribute('aria-modal', 'true');
    sidebar.querySelector('button, a[href], summary')?.focus();
  } else {
    sidebar.removeAttribute('role');
    sidebar.removeAttribute('aria-modal');
    document.querySelector('.apri-menu')?.focus();
  }
};

document.addEventListener('keydown', (event) => {
  const sidebar = document.querySelector('.sidebar.open');
  if (!sidebar) return;
  if (event.key === 'Escape') { event.preventDefault(); toggleSidebar(false); return; }
  if (event.key !== 'Tab') return;
  const focusable = [...sidebar.querySelectorAll('a[href], button, summary')].filter(el => el.getClientRects().length && !el.disabled);
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});

// ---- Gruppi della sidebar: aperti/chiusi come li ha lasciati l'utente -------
// Lo stato salvato si applica già con lo script in linea subito dopo la
// sidebar (base.html), prima del primo disegno: qui si registra solo il
// cambiamento. Il gruppo della pagina corrente resta sempre aperto.
document.querySelectorAll('.sidebar-group[data-group]').forEach(group => {
  group.addEventListener('toggle', () => {
    try { localStorage.setItem('sidebar.v2.' + group.dataset.group, group.open ? 'open' : 'closed'); } catch (e) {}
  });
});

// ---- htmx ------------------------------------------------------------------
document.body.addEventListener('htmx:responseError', (event) => {
  const status = event.detail.xhr.status;
  toast(status === 403 ? 'Azione non autorizzata o sessione scaduta. Ricarica la pagina.' :
    'Operazione non riuscita. Riprova.', { level: 'error' });
});
document.body.addEventListener('htmx:sendError', () => toast('Connessione non disponibile. Riprova.', { level: 'error' }));

// ============================================================================
// Velocità e usabilità
// ============================================================================

const _norm = (t) => (t || '').toString().normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const _debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const _esc = (t) => (t || '').toString().replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const _mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

// ---- Barra di caricamento ---------------------------------------------------
const caricamento = (() => {
  let el, timer;
  const barra = () => el || (el = Object.assign(document.body.appendChild(document.createElement('div')), { className: 'caricamento' }));
  return {
    // compare solo se l'attesa supera un attimo: niente lampi sulle pagine veloci
    avvia(ritardo = 180) { clearTimeout(timer); timer = setTimeout(() => barra().classList.add('attivo'), ritardo); },
    ferma() { clearTimeout(timer); el?.classList.remove('attivo'); },
  };
})();
window.addEventListener('pageshow', () => { caricamento.ferma(); sbloccaForm(document); });
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href]');
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  if (a.target && a.target !== '_self') return;
  if (a.hasAttribute('download') || a.getAttribute('href').startsWith('#')) return;
  if (a.origin !== location.origin) return;
  if (/\/(export|zip|xml)|pdf/.test(a.pathname)) return;   // download: la pagina non cambia
  caricamento.avvia();
});
document.body.addEventListener('htmx:beforeRequest', () => caricamento.avvia(400));
document.body.addEventListener('htmx:afterRequest', () => caricamento.ferma());

// ---- Niente doppio invio dei form ------------------------------------------
// Un form POST inviato disabilita i suoi pulsanti finché la pagina non cambia
// (o per qualche secondo, se la risposta è un download): un doppio clic su
// «Salva» o «Invia al SDI» non crea due documenti o due invii.
function sbloccaForm(root) {
  root.querySelectorAll('[data-submit-disabled]').forEach(b => {
    b.removeAttribute('aria-busy');
    b.disabled = false;
    delete b.dataset.submitDisabled;
  });
  root.querySelectorAll('form[data-inviato]').forEach(form => { delete form.dataset.inviato; });
  if (root instanceof HTMLFormElement) delete root.dataset.inviato;
}
document.addEventListener('submit', (e) => {
  const form = e.target;
  if (e.defaultPrevented || !(form instanceof HTMLFormElement)) return;
  if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
  if ([...form.attributes].some(a => a.name.startsWith('hx-'))) return;
  if (form.dataset.inviato === '1') { e.preventDefault(); return; }
  form.dataset.inviato = '1';
  const premuto = e.submitter;
  // dopo il submit: il valore del pulsante premuto deve arrivare al server
  setTimeout(() => {
    form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]').forEach(b => {
      if (b.disabled) return;
      b.dataset.submitDisabled = '1';
      b.disabled = true;
      if (b === premuto || form.querySelectorAll('button[type="submit"], button:not([type])').length === 1) b.setAttribute('aria-busy', 'true');
    });
  }, 0);
  if (!form.target || form.target === '_self') caricamento.avvia();
  setTimeout(() => { delete form.dataset.inviato; sbloccaForm(form); }, 15000);
});   // in bubbling: dopo gli onsubmit="return confirm(…)" del form

// ---- Rotellina del mouse sui campi numerici: scorre la pagina, non cambia il
// numero (un importo modificato per sbaglio passando sopra col mouse)
document.addEventListener('wheel', (e) => {
  if (e.target instanceof HTMLInputElement && e.target.type === 'number' && document.activeElement === e.target) {
    e.target.blur();
  }
}, { passive: true });

// ---- Avviso modifiche non salvate ------------------------------------------
document.querySelectorAll('form[data-avvisa-modifiche]').forEach(form => {
  let sporco = false;
  form.addEventListener('input', () => { sporco = true; });
  form.addEventListener('change', () => { sporco = true; });
  form.addEventListener('submit', () => { sporco = false; });
  window.addEventListener('beforeunload', (e) => {
    if (sporco) { e.preventDefault(); e.returnValue = ''; }
  });
});

// ---- Select con ricerca -----------------------------------------------------
// <select data-cerca> (e ogni select con molte voci) diventa un campo in cui
// si scrive per filtrare. La select originale resta nel form (nascosta) e
// riceve value + evento change come se l'utente l'avesse usata.
let _comboN = 0;
function miglioraSelect(select) {
  if (select.dataset.combo || select.multiple || select.size > 1) return;
  select.dataset.combo = '1';
  const id = 'combo-' + (++_comboN);
  const wrap = document.createElement('div');
  wrap.className = 'combo';
  if (select.style.width) wrap.style.width = select.style.width;
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  select.classList.add('combo-select');
  select.tabIndex = -1;
  select.setAttribute('aria-hidden', 'true');
  select.required = false;                 // la validazione la fa il server
  const input = document.createElement('input');
  input.type = 'text';
  input.className = (select.className.replace('combo-select', '').replace('select', '') + ' input combo-input').trim();
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-controls', id);
  if (select.id) {
    const label = document.querySelector(`label[for="${select.id}"]`);
    if (label) { input.id = select.id + '_cerca'; label.htmlFor = input.id; }
  }
  // la voce vuota dei filtri («Tutti i tipi…») resta leggibile come segnaposto
  const vuota = [...select.options].find(o => !o.value);
  const testoVuota = vuota ? vuota.text.trim() : '';
  input.placeholder = testoVuota && !/^-+$/.test(testoVuota) ? testoVuota : 'Scrivi per cercare…';
  wrap.insertBefore(input, select);
  wrap.insertAdjacentHTML('beforeend', '<svg class="combo-freccia" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 9 6 6 6-6"/></svg>');
  const lista = document.createElement('ul');
  lista.className = 'combo-lista';
  lista.id = id;
  lista.setAttribute('role', 'listbox');
  lista.hidden = true;
  wrap.appendChild(lista);

  const voci = () => [...select.options].map((o, i) => ({ i, value: o.value, label: o.text.trim(), vuota: !o.value }));
  const etichetta = () => {
    const o = select.options[select.selectedIndex];
    return o && o.value ? o.text.trim() : '';
  };
  let attiva = -1, trovate = [];
  const sincronizza = () => { input.value = etichetta(); };
  const chiudi = () => { lista.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
  const evidenzia = (n) => {
    attiva = Math.max(0, Math.min(n, trovate.length - 1));
    [...lista.children].forEach((li, k) => li.setAttribute('aria-selected', k === attiva ? 'true' : 'false'));
    const li = lista.children[attiva];
    if (li) { input.setAttribute('aria-activedescendant', li.id); li.scrollIntoView({ block: 'nearest' }); }
  };
  const mostra = (filtro) => {
    const parole = _norm(filtro).split(/\s+/).filter(Boolean);
    trovate = voci().filter(v => !parole.length ? true : (!v.vuota && parole.every(p => _norm(v.label).includes(p))));
    const tot = trovate.length;
    trovate = trovate.slice(0, 80);
    lista.innerHTML = trovate.map((v, k) =>
      `<li role="option" id="${id}-${k}" data-k="${k}" class="${v.value === select.value && v.value ? 'scelta' : ''}">${_esc(v.vuota ? (/^-+$/.test(v.label) || !v.label ? '— nessuno —' : v.label) : v.label)}</li>`).join('')
      + (tot > trovate.length ? `<li class="vuoto">… altre ${tot - trovate.length}: scrivi per restringere</li>` : '')
      + (!tot ? '<li class="vuoto">Nessun risultato</li>' : '');
    lista.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    const corrente = trovate.findIndex(v => v.value === select.value);
    evidenzia(parole.length ? 0 : Math.max(corrente, 0));
  };
  const scegli = (k) => {
    const v = trovate[k];
    if (!v) return;
    if (select.value !== v.value) {
      select.value = v.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    sincronizza();
    chiudi();
  };
  sincronizza();
  input.addEventListener('focus', () => { input.select(); mostra(''); });
  input.addEventListener('click', () => { if (lista.hidden) mostra(''); });
  input.addEventListener('input', () => mostra(input.value));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); lista.hidden ? mostra('') : evidenzia(attiva + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); evidenzia(attiva - 1); }
    else if (e.key === 'Enter') { if (!lista.hidden) { e.preventDefault(); scegli(attiva); } }
    else if (e.key === 'Escape') { if (!lista.hidden) { e.stopPropagation(); sincronizza(); chiudi(); } }
    else if (e.key === 'Tab') { if (!lista.hidden && input.value !== etichetta() && trovate.length) scegli(attiva); else chiudi(); }
  });
  input.addEventListener('blur', () => setTimeout(() => {
    if (wrap.contains(document.activeElement)) return;
    if (!input.value.trim() && select.value && [...select.options].some(o => !o.value)) {
      select.value = '';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    sincronizza();
    chiudi();
  }, 120));
  lista.addEventListener('mousedown', (e) => {
    const li = e.target.closest('li[data-k]');
    e.preventDefault();
    if (li) scegli(+li.dataset.k);
  });
  select.addEventListener('change', sincronizza);
}
function miglioraSelectIn(root) {
  root.querySelectorAll('select[data-cerca], select.select, select.input').forEach(sel => {
    if (sel.hasAttribute('data-no-cerca') || sel.closest('template')) return;
    if (sel.hasAttribute('data-cerca') || sel.options.length >= 15) miglioraSelect(sel);
  });
}
window.UI.miglioraSelectIn = miglioraSelectIn;
miglioraSelectIn(document);

// ---- Tabelle su schermi stretti: scorrono di lato invece di essere tagliate --
const _stretto = window.matchMedia('(max-width: 900px)');
function tabelleScorrevoli(root) {
  if (!_stretto.matches) return;
  root.querySelectorAll('table.tbl').forEach(t => {
    if (t.closest('.tbl-scroll')) return;
    const w = document.createElement('div');
    w.className = 'tbl-scroll';
    t.parentNode.insertBefore(w, t);
    w.appendChild(t);
  });
}
tabelleScorrevoli(document);
document.body.addEventListener('htmx:afterSwap', (e) => tabelleScorrevoli(e.target));

// ---- Righe dei documenti (formset) ------------------------------------------
// <div data-formset="righe"> con management form, <tbody data-formset-righe>,
// <template data-formset-modello> e i pulsanti [data-formset-aggiungi] /
// [data-formset-rimuovi]. Le righe tolte si nascondono e si segnano DELETE;
// la numerazione delle righe si rifà a ogni invio. Vale per le righe dei
// documenti (importo_unitario, iva) e per quelle delle fatture di acquisto
// (prezzo_unitario, sconto_percentuale, aliquota_iva, categoria_costo).
const _SEL_PREZZO = 'input[name$="-importo_unitario"], input[name$="-prezzo_unitario"]';
const _SEL_IVA = 'select[name$="-iva"], select[name$="-aliquota_iva"]';
function _numero(v) {
  const n = parseFloat((v || '').toString().replace(',', '.'));
  return isFinite(n) ? n : 0;
}
const _cifre = new Intl.NumberFormat('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const _euro = { format: (n) => '€ ' + _cifre.format(n) };   // come il filtro «euro» dei template

function inizializzaFormset(box) {
  const prefisso = box.dataset.formset;
  const tbody = box.querySelector('[data-formset-righe]');
  const modello = box.querySelector('template[data-formset-modello]');
  const totale = box.querySelector(`input[name="${prefisso}-TOTAL_FORMS"]`);
  const form = box.closest('form');
  if (!tbody || !modello || !totale) return;

  const rinumera = () => {
    let n = 0;
    tbody.querySelectorAll('tr[data-form-row]').forEach(tr => {
      if (tr.hidden) return;
      n += 1;
      const num = tr.querySelector('[data-numero]');
      if (num) num.textContent = n;
      const campo = tr.querySelector(`input[name$="-numero_riga"]`);
      if (campo) campo.value = n;
    });
  };
  const importoRiga = (tr) => {
    const cella = tr.querySelector('[data-importo-riga]');
    if (!cella) return;
    const q = tr.querySelector('input[name$="-quantita"]');
    const p = tr.querySelector(_SEL_PREZZO);
    const sc = tr.querySelector('input[name$="-sconto_percentuale"]');
    const fattore = sc ? 1 - _numero(sc.value) / 100 : 1;
    cella.textContent = q && p && (q.value || p.value) ? _euro.format(_numero(q.value) * _numero(p.value) * fattore) : '';
  };
  const ivaPredefinita = () => {
    // l'IVA del cliente scelto, altrimenti quella dell'ultima riga compilata
    const cli = form?.querySelector('select[name="cliente"]');
    const opt = cli?.options[cli.selectedIndex];
    if (opt?.dataset.iva) return opt.dataset.iva;
    return ultimoValore(_SEL_IVA);
  };
  // il valore dell'ultima riga compilata (IVA, categoria di costo)
  const ultimoValore = (sel) => [...tbody.querySelectorAll('tr[data-form-row]:not([hidden])')]
    .map(tr => tr.querySelector(sel)?.value).filter(Boolean).pop() || '';
  const proponi = (tr) => {
    const iva = tr.querySelector(_SEL_IVA);
    const predef = ivaPredefinita();
    if (iva && !iva.value && predef) iva.value = predef;
    const cat = tr.querySelector('select[name$="-categoria_costo"]');
    const catPrima = cat && !cat.value && ultimoValore('select[name$="-categoria_costo"]');
    if (catPrima) cat.value = catPrima;
  };
  const aggiungi = () => {
    const i = parseInt(totale.value, 10) || 0;
    const tmp = document.createElement('tbody');
    tmp.innerHTML = modello.innerHTML.replaceAll('__prefix__', i);
    const tr = tmp.querySelector('tr');
    tbody.appendChild(tr);
    totale.value = i + 1;
    const q = tr.querySelector('input[name$="-quantita"]');
    if (q && !q.value) q.value = '1';
    proponi(tr);
    miglioraSelectIn(tr);
    rinumera();
    importoRiga(tr);
    [...tr.querySelectorAll('.combo-input, input:not([type="hidden"]), select')]
      .find(el => !el.hidden && el.offsetParent !== null)?.focus();
    form?.dispatchEvent(new Event('change', { bubbles: true }));
    return tr;
  };
  const rimuovi = (tr) => {
    const del = tr.querySelector('input[name$="-DELETE"]');
    if (del) del.checked = true;
    tr.hidden = true;
    rinumera();
    form?.dispatchEvent(new Event('change', { bubbles: true }));
    if (!tbody.querySelector('tr[data-form-row]:not([hidden])')) aggiungi();
  };

  box.addEventListener('click', (e) => {
    if (e.target.closest('[data-formset-aggiungi]')) { e.preventDefault(); aggiungi(); }
    const btn = e.target.closest('[data-formset-rimuovi]');
    if (btn) { e.preventDefault(); rimuovi(btn.closest('tr')); }
  });
  box.addEventListener('input', (e) => {
    const tr = e.target.closest('tr[data-form-row]');
    if (tr) importoRiga(tr);
  });
  // Invio sull'ultimo campo dell'ultima riga: nuova riga invece di salvare
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.target.matches('textarea, .combo-input')) return;
    const tr = e.target.closest('tr[data-form-row]');
    if (!tr) return;
    const visibili = [...tbody.querySelectorAll('tr[data-form-row]:not([hidden])')];
    if (tr === visibili[visibili.length - 1]) { e.preventDefault(); aggiungi(); }
  });
  // l'articolo scelto riempie descrizione, prezzo, UM e IVA ancora vuoti
  box.addEventListener('change', async (e) => {
    const sel = e.target;
    if (!sel.matches('select[name$="-articolo"]') || !sel.value) return;
    const url = box.dataset.articoloUrl?.replace('/0/', `/${encodeURIComponent(sel.value)}/`);
    if (!url) return;
    try {
      const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!r.ok) return;
      const d = await r.json();
      const tr = sel.closest('tr');
      const imposta = (suffisso, valore) => {
        const el = tr.querySelector(`[name$="-${suffisso}"]`);
        if (el && valore !== null && valore !== undefined && valore !== '' && (!el.value || (suffisso === 'importo_unitario' && _numero(el.value) === 0))) {
          el.value = valore;
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
      };
      imposta('descrizione_libera', d.descrizione);
      imposta('importo_unitario', d.prezzo);
      imposta('um', d.um);
      imposta('iva', d.iva);
      importoRiga(tr);
    } catch (err) { /* resta com'è */ }
  });
  form?.addEventListener('submit', rinumera);
  // senza articoli in anagrafica la select dell'articolo non serve
  const modelloArticolo = modello.content.querySelector('select[name$="-articolo"]');
  if (modelloArticolo && modelloArticolo.options.length <= 1) {
    box.querySelectorAll('select[name$="-articolo"]').forEach(s => { s.hidden = true; });
    modelloArticolo.hidden = true;
    modelloArticolo.setAttribute('hidden', '');
  }
  tbody.querySelectorAll('tr[data-form-row]').forEach(tr => {
    const del = tr.querySelector('input[name$="-DELETE"]');
    if (del?.checked) tr.hidden = true;
    // le righe nuove partono come quelle aggiunte: quantità 1 e IVA proposta
    // (una riga lasciata così non si salva, vedi RigaDocumentoForm.has_changed)
    if (!tr.querySelector('input[name$="-id"]')?.value) {
      const q = tr.querySelector('input[name$="-quantita"]');
      if (q && !q.value) q.value = '1';
      proponi(tr);
    }
    importoRiga(tr);
  });
  rinumera();
}
document.querySelectorAll('[data-formset]').forEach(inizializzaFormset);

// ---- Form documento: cliente (o fornitore) → pagamento e IVA, riepilogo live
document.querySelectorAll('form[data-documento]').forEach(form => {
  const cliente = form.querySelector('select[name="cliente"], select[name="fornitore"]');
  const forma = form.querySelector('select[name="forme_pagamento"]');
  let predefinitoPrima = cliente?.options[cliente.selectedIndex]?.dataset.forma || '';
  cliente?.addEventListener('change', () => {
    const opt = cliente.options[cliente.selectedIndex];
    // la forma di pagamento del cliente, se il campo è vuoto o aveva quella
    // del cliente scelto prima
    if (forma && opt?.dataset.forma && (!forma.value || forma.value === predefinitoPrima)) {
      forma.value = opt.dataset.forma;
      forma.dispatchEvent(new Event('change', { bubbles: true }));
    }
    predefinitoPrima = opt?.dataset.forma || '';
    if (opt?.dataset.iva) {
      form.querySelectorAll('tr[data-form-row]:not([hidden]) select[name$="-iva"]').forEach(s => {
        if (!s.value) { s.value = opt.dataset.iva; s.dispatchEvent(new Event('change', { bubbles: true })); }
      });
    }
  });
  const aggiorna = _debounce(() => document.body.dispatchEvent(new Event('riepilogo')), 350);
  form.addEventListener('input', aggiorna);
  form.addEventListener('change', aggiorna);
});

// ---- Palette comandi (⌘K / Ctrl+K, oppure /) --------------------------------
const palette = (() => {
  let box, input, lista, voci = [], attiva = 0, ultimaRicerca = '', controller;
  const icone = {
    pagina: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    azione: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    risultato: '<path d="M9 18l6-6-6-6"/>',
    cerca: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  };
  const svg = (k) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${icone[k]}</svg>`;
  const pagine = () => [...document.querySelectorAll('.sidebar a.nav-item[href]')].map(a => ({
    tipo: 'pagina', titolo: a.querySelector('span')?.textContent.trim() || a.textContent.trim(),
    sotto: a.dataset.gruppo || '',
    url: a.getAttribute('href'),
  }));
  const azioni = () => {
    try { return JSON.parse(document.getElementById('palette-azioni')?.textContent || '[]').map(a => ({ tipo: 'azione', ...a })); }
    catch (e) { return []; }
  };
  const crea = () => {
    box = document.createElement('div');
    box.className = 'palette';
    box.hidden = true;
    box.innerHTML = `<div class="palette-box" role="dialog" aria-modal="true" aria-label="Cerca e vai a">
      <div class="palette-campo">${svg('cerca')}<input class="palette-input" type="text" placeholder="Cerca clienti, fatture, fornitori… o scrivi una pagina" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="palette-lista"></div>
      <div class="palette-lista" id="palette-lista" role="listbox"></div>
      <div class="palette-piede"><span><kbd>↑</kbd> <kbd>↓</kbd> per muoverti</span><span><kbd>Invio</kbd> per aprire</span><span><kbd>Esc</kbd> per chiudere</span></div>
    </div>`;
    document.body.appendChild(box);
    input = box.querySelector('.palette-input');
    lista = box.querySelector('.palette-lista');
    box.addEventListener('mousedown', (e) => { if (e.target === box) chiudi(); });
    input.addEventListener('input', () => aggiorna(input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); evidenzia(attiva + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); evidenzia(attiva - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); apriVoce(voci[attiva], e.metaKey || e.ctrlKey); }
      else if (e.key === 'Escape') { e.preventDefault(); chiudi(); }
    });
    lista.addEventListener('mousemove', (e) => {
      const el = e.target.closest('[data-i]');
      if (el && +el.dataset.i !== attiva) evidenzia(+el.dataset.i, false);
    });
    lista.addEventListener('click', (e) => {
      const el = e.target.closest('[data-i]');
      if (el) { e.preventDefault(); apriVoce(voci[+el.dataset.i], e.metaKey || e.ctrlKey); }
    });
  };
  const evidenzia = (n, scorri = true) => {
    if (!voci.length) return;
    attiva = (n + voci.length) % voci.length;
    lista.querySelectorAll('[data-i]').forEach(el => el.setAttribute('aria-selected', +el.dataset.i === attiva ? 'true' : 'false'));
    const el = lista.querySelector(`[data-i="${attiva}"]`);
    if (el && scorri) el.scrollIntoView({ block: 'nearest' });
  };
  const disegna = (gruppi, testo) => {
    voci = [];
    let html = '';
    for (const [nome, elenco] of gruppi) {
      if (!elenco.length) continue;
      html += `<div class="palette-gruppo">${_esc(nome)}</div>`;
      for (const v of elenco) {
        const i = voci.push(v) - 1;
        html += `<a class="palette-voce" role="option" data-i="${i}" href="${_esc(v.url)}">${svg(v.tipo === 'risultato' ? 'risultato' : v.tipo)}<span>${_esc(v.titolo)}</span>${v.sotto ? `<span class="sotto">${_esc(v.sotto)}</span>` : ''}</a>`;
      }
    }
    if (!voci.length) html = `<div class="palette-vuoto">Nessun risultato per «${_esc(testo)}»</div>`;
    lista.innerHTML = html;
    attiva = 0;
    evidenzia(0);
  };
  const filtra = (elenco, testo) => {
    const parole = _norm(testo).split(/\s+/).filter(Boolean);
    return elenco.filter(v => parole.every(p => _norm(v.titolo + ' ' + v.sotto).includes(p)));
  };
  const remoti = _debounce(async (testo) => {
    controller?.abort();
    controller = new AbortController();
    try {
      const r = await fetch(box.dataset.url + '?q=' + encodeURIComponent(testo), { signal: controller.signal, headers: { 'Accept': 'application/json' } });
      if (!r.ok || testo !== ultimaRicerca) return;
      const d = await r.json();
      const gruppi = d.gruppi.map(g => [g.nome, g.voci.map(v => ({ tipo: 'risultato', ...v }))]);
      gruppi.push(['Pagine', filtra(pagine(), testo).slice(0, 4)]);
      gruppi.push(['Azioni', filtra(azioni(), testo).slice(0, 4)]);
      gruppi.push(['Ricerca completa', [{ tipo: 'cerca', titolo: `Cerca «${testo}» in tutto il gestionale`, sotto: '', url: box.dataset.cercaUrl + '?q=' + encodeURIComponent(testo) }]]);
      disegna(gruppi, testo);
    } catch (e) { /* richiesta superata da una più nuova */ }
  }, 140);
  const aggiorna = (testo) => {
    ultimaRicerca = testo = testo.trim();
    if (!testo) {
      disegna([['Azioni', azioni()], ['Pagine', pagine()]], '');
      return;
    }
    disegna([['Pagine', filtra(pagine(), testo).slice(0, 6)], ['Azioni', filtra(azioni(), testo).slice(0, 6)]], testo);
    if (testo.length >= 2 || /^\d$/.test(testo)) remoti(testo);
  };
  const apriVoce = (v, nuovaScheda) => {
    if (!v) return;
    if (nuovaScheda) { window.open(v.url, '_blank'); return; }
    chiudi();
    caricamento.avvia(0);
    location.href = v.url;
  };
  const apri = (testo = '') => {
    if (!box) crea();
    const el = document.getElementById('palette-dati');
    box.dataset.url = el?.dataset.url || '/cerca/rapida/';
    box.dataset.cercaUrl = el?.dataset.cercaUrl || '/cerca/';
    box.hidden = false;
    input.value = testo;
    aggiorna(testo);
    input.focus();
  };
  const chiudi = () => { if (box) box.hidden = true; };
  return { apri, chiudi, aperta: () => box && !box.hidden };
})();
window.UI.palette = palette;

document.querySelectorAll('[data-apri-palette]').forEach(el => {
  el.readOnly = true;                      // si scrive nella palette
  el.addEventListener('focus', () => { el.blur(); palette.apri(el.value || ''); });
  el.addEventListener('click', (e) => { e.preventDefault(); palette.apri(el.value || ''); });
});
if (!_mac) {
  document.querySelectorAll('[data-tasto-mod]').forEach(k => { k.textContent = 'Ctrl'; });
  document.querySelectorAll('[data-tasto-mod-k]').forEach(k => { k.textContent = 'Ctrl K'; });
}

// ---- Scorciatoie da tastiera ------------------------------------------------
// ⌘K / Ctrl+K o «/»: palette · ⌘S / Ctrl+S: salva il form della pagina ·
// «n»: nuovo (il pulsante [data-scorciatoia="n"] della pagina) · «?»: aiuto
document.addEventListener('keydown', (e) => {
  const mod = _mac ? e.metaKey : e.ctrlKey;
  const inCampo = e.target.closest('input, textarea, select, [contenteditable="true"]');
  if (mod && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    palette.aperta() ? palette.chiudi() : palette.apri();
    return;
  }
  if (mod && e.key.toLowerCase() === 's') {
    const form = document.querySelector('form[data-salva-rapido]');
    if (form) { e.preventDefault(); form.requestSubmit(); }
    return;
  }
  if (inCampo || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); palette.apri(); }
  else if (e.key === 'n') {
    const nuovo = document.querySelector('[data-scorciatoia="n"]');
    if (nuovo) { e.preventDefault(); nuovo.click(); }
  } else if (e.key === '?') {
    document.getElementById('dialogo-tasti')?.showModal();
  } else if (e.key === 'Escape') {
    toggleSidebar(false);
  }
});

// ---- Export con i filtri correnti (aggiornati via HTMX senza ricaricare) ----
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-export-filtri]');
  if (a) a.href = a.href.split('?')[0] + location.search;
});

// ---- Dialog: apertura dai pulsanti [data-apri-dialogo="id"] -----------------
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-apri-dialogo]');
  if (btn) { e.preventDefault(); document.getElementById(btn.dataset.apriDialogo)?.showModal(); }
  const chiudi = e.target.closest('[data-chiudi-dialogo]');
  if (chiudi) { e.preventDefault(); chiudi.closest('dialog')?.close(); }
});
