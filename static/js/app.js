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
      headers: { 'X-CSRFToken': getCookie('csrftoken'), 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'theme=' + encodeURIComponent(theme),
    });
  },
  setDensity(density) {
    applyUI(document.documentElement.getAttribute('data-theme') || 'light', density);
    fetch('/ui/density/', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken'), 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'density=' + encodeURIComponent(density),
    });
  },
};

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : '';
}

// ---- Toast stack ------------------------------------------------------------
function ensureToastLayer() {
  let layer = document.querySelector('.toast-layer');
  if (!layer) {
    layer = document.createElement('div');
    layer.className = 'toast-layer';
    document.body.appendChild(layer);
  }
  return layer;
}

window.toast = function (message, { level = 'info', timeout = 4000 } = {}) {
  const layer = ensureToastLayer();
  const el = document.createElement('div');
  el.className = 'toast ' + level;
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
  if (!card || !card.dataset.oppId) return;
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
  if (!col) return;
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

document.addEventListener('drop', (e) => {
  const col = e.target.closest('.pipe-col');
  if (!col) return;
  e.preventDefault();
  col.classList.remove('drop-target');

  const oppId = e.dataTransfer.getData('text/opp-id');
  const stadio = col.dataset.stadio;
  if (!oppId || !stadio) return;

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

  fetch(`/opportunita/${oppId}/sposta/`, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCookie('csrftoken'), 'HX-Request': 'true' },
    body: form,
  })
    .then(r => r.text())
    .then(html => {
      const board = document.getElementById('pipeline-board');
      if (board) board.innerHTML = html;
      toast('Opportunità aggiornata', { level: 'success' });
    })
    .catch(() => toast('Errore aggiornamento stadio', { level: 'error' }));
});

// ---- Filter chip dropdown: close on outside click --------------------------
document.addEventListener('click', (e) => {
  document.querySelectorAll('[data-dropdown].open').forEach(drop => {
    if (!drop.contains(e.target)) drop.classList.remove('open');
  });
});

// ---- Mobile sidebar toggle --------------------------------------------------
window.toggleSidebar = () => {
  document.querySelector('.sidebar')?.classList.toggle('open');
};

// ---- Sidebar collapsible groups: persist open/closed in localStorage --------
(function persistSidebarGroups() {
  const groups = document.querySelectorAll('.sidebar-group[data-group]');
  groups.forEach(group => {
    const key = 'sidebar.group.' + group.dataset.group;
    const stored = localStorage.getItem(key);
    if (stored === 'open') group.open = true;
    else if (stored === 'closed') group.open = false;
    group.addEventListener('toggle', () => {
      localStorage.setItem(key, group.open ? 'open' : 'closed');
    });
  });
})();

// ---- htmx: show a subtle indicator on main content -------------------------
document.body.addEventListener('htmx:responseError', () => {
  toast('Errore di rete — riprova', { level: 'error' });
});
