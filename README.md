# Gestionale CRM — AIGIS lab

CRM server-rendered in Django 5, tradotto dal design handoff "gestionale-4.zip" (Claude Design). Nessun frontend React/SPA: il design è realizzato con template Django + HTMX + Alpine.js.

## Screens implementate (tutte)

1. **Login** — `/accounts/login/` (split-screen come da design, autofocus email)
2. **Dashboard** — `/` (KPI reali, bar chart fatturato, activity feed, pipeline Kanban)
3. **Clienti** — `/clienti/` (lista con filtri a chip, ricerca, paginazione)
4. **Cliente detail** — `/clienti/<id>/` (tab: Panoramica, Opportunità, Contatti, Attività, Documenti, Note)
5. **Nuovo / Modifica cliente** — `/clienti/nuovo/` · `/clienti/<id>/modifica/` (3 sezioni + sidebar assegnazione + validazione P.IVA async)
6. **Contatti** — `/clienti/contatti/` (tabella cross-cliente)
7. **Aziende** — `/clienti/aziende/` (grid card, subset clienti tipo=azienda)
8. **Pipeline opportunità** — `/opportunita/` (5 colonne Kanban con drag & drop HTMX)
9. **Opportunità detail / form** — `/opportunita/<id>/`
10. **Attività** — `/attivita/` (lista, filtri tipo/stato, toggle completata via HTMX)
11. **Report** — `/report/` (KPI Q2, bar chart vs obiettivo, donut SVG settori, top 5 clienti)
12. **Impostazioni → Utenti** — `/impostazioni/` (tabella utenti con select ruolo inline HTMX, matrice permessi)
13. **Impostazioni → Organizzazione, Ruoli, Integrazioni, Fatturazione, API, Audit log, Backup**
14. **Ricerca globale** — `/cerca/?q=…` cross-model con highlight `<mark>` e paginazione per gruppo (`?group=clienti&page=2`)
15. **Import CSV wizard (2 step)** — `/clienti/import-csv/` upload → preview + mapping colonne auto-guessato → conferma e import
16. **Export CSV clienti** — `/clienti/export-csv/` (rispetta i filtri di lista)
17. **Export PDF report** — `/report/pdf/?periodo=…` (via xhtml2pdf)
18. **Audit log** — eventi tracciati automaticamente (create/update/delete + login/logout/import/export), UI in `/impostazioni/audit/`
19. **API REST read-only** — `/api/v1/{health,clienti,opportunita,attivita}` con Bearer token
20. **Webhook outbound** — POST JSON su URL configurati a ogni evento di dominio, firma HMAC-SHA256 opzionale

## Stack

- **Django 5.2** + `django-htmx`
- **Alpine.js 3** (dropdown, toggle, show/hide password, tab)
- **HTMX 1.9** (filtri clienti, toggle attività, cambio ruolo, drag & drop pipeline)
- **Plain CSS** (il `static/css/app.css` è copiato 1:1 dal design handoff, con ~150 righe di estensioni per stati, toast, pagination, drag & drop)
- **SQLite** (dev). Switch a Postgres in `settings.py::DATABASES`.
- **python-stdnum** per checksum P.IVA (endpoint `/clienti/valida-piva/`).
- **xhtml2pdf** per l'export PDF del report (pure-Python, niente dipendenze native).

## Struttura

```
gestionale-crm/
├── manage.py
├── requirements.txt
├── gestionale/            # project settings + urls root
├── accounts/              # Profile (ruolo, stato, tema, densità) + login/logout
│   └── management/commands/seed.py
├── clienti/               # Cliente, Contatto, Tag + CRUD + P.IVA endpoint
├── opportunita/           # Opportunita + Kanban pipeline + HTMX move
├── attivita/              # Attivita + toggle completata
├── reports/               # Aggregazioni ORM per KPI/grafici/donut/top5
├── core_settings/         # Organizzazione, utenti, ruoli, permessi matrix
│   ├── templatetags/ui.py # icon, stato_badge, stadio_badge, avatar, euro, sparkline
│   └── context_processors.py # tema/densità + sidebar counts
├── dashboard/             # Home + theme/density toggle endpoints
├── search/                # Ricerca globale cross-model (/cerca/)
├── audit/                 # AuditLog + signals + middleware thread-local
├── api/                   # API REST token-based + Webhook outbound
├── templates/
│   ├── base.html          # sidebar + topbar + theme/density via data-*
│   ├── registration/login.html
│   ├── components/        # _sidebar, _topbar, _kpi, _barchart
│   ├── clienti/           # list, _list_table, detail, form, confirm_delete, contatto_form, aziende_list
│   ├── opportunita/       # pipeline, _pipeline_board, detail, form, confirm_delete
│   ├── attivita/          # list, _row, form, detail, confirm_delete
│   ├── reports/index.html
│   ├── settings/          # _settings_base, utenti, _user_row, ruoli, organizzazione, invita, placeholder
│   └── dashboard/dashboard.html
└── static/
    ├── css/app.css        # design tokens + estensioni
    ├── img/logo.png       # AIGIS lab logo
    └── js/app.js          # theme/density, toast, drag&drop pipeline, htmx glue
```

## Setup da zero

```bash
cd gestionale-crm
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed              # popola dati demo (10 clienti, 16 opp, 5 utenti)
python manage.py runserver
```

Apri http://localhost:8000/

**Credenziali demo (dopo `seed`):**
- `marco@studiorossi.it` / `demo1234` — Amministratore (staff + superuser)
- `giulia@studiorossi.it` / `demo1234` — Account manager
- `lucia@studiorossi.it` / `demo1234` — Account manager
- `stefano@studiorossi.it` / `demo1234` — Visualizzatore

Re-seed: `python manage.py seed --reset` (mantiene i superuser, cancella CRM data).

## Variabili d'ambiente (opzionali)

- `DJANGO_SECRET_KEY` — default: chiave dev (sostituire in prod)
- `DJANGO_DEBUG` — default `True`
- `DJANGO_ALLOWED_HOSTS` — CSV, default `localhost,127.0.0.1`
- `SITE_BASE_URL` — usato nei link nelle email (default `http://localhost:8000`)
- `DJANGO_EMAIL_BACKEND` — override esplicito del backend; in `DEBUG=True` default è `console`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`
- `DEFAULT_FROM_EMAIL` — default `Gestionale CRM <noreply@aigislab.ai>`

## Differenze rispetto al design handoff

- I **mock data** del design sono stati portati nel seed (`python manage.py seed`).
- Il **theme/density toggle** è in topbar (menu con icona sole) invece del pannello "Tweaks" fluttuante che serviva solo al design-canvas.
- La **P.IVA checksum** (stdnum) è esposta come endpoint async `/clienti/valida-piva/` chiamato dall'Alpine al blur del campo. Al salvataggio il checksum è un warning morbido — il formato (11 cifre) è invece bloccante.
- Il **pannello "documenti"** nel dettaglio cliente è stub (vedi design: non era implementato nemmeno lì).
- I **layout varianti** del design (rail / topbar) non sono implementati: ho usato solo il layout principale "sidebar" — l'utente può attivarli in futuro cambiando `layout` nel `base.html`.

## Comandi utili

```bash
python manage.py createsuperuser        # admin separato per /admin/
python manage.py makemigrations         # dopo modifiche ai modelli
python manage.py collectstatic          # deploy
python manage.py shell_plus             # (se installi django-extensions)
```

## Endpoint HTMX

- `POST /opportunita/<pk>/sposta/` — body `stadio=<valore>&posizione=<n>` → ritorna `_pipeline_board.html` aggiornato
- `POST /attivita/<pk>/toggle/` — toggle `completata` → ritorna `_row.html`
- `POST /impostazioni/utenti/<pk>/ruolo/` — body `ruolo=<valore>` → ritorna `_user_row.html`
- `POST /ui/theme/` e `POST /ui/density/` — persist nel profile + cookie (204 No Content)
- `GET /clienti/valida-piva/?partita_iva=IT…` — JSON `{valid, message}`

## Permessi (RBAC)

I permessi sono modellati in `accounts/permissions.py` (dict `PERM_MATRIX`) e mappano le
chiavi della matrice UI (visualizza clienti, elimina clienti, …) ai tre ruoli del Profile
(Amministratore · Account manager · Visualizzatore).

- **Decoratore per view funzionali:** `@require_perm('clienti.elimina')`
- **Mixin per CBV:** `class X(PermRequiredMixin, DeleteView): required_perm = 'clienti.elimina'`
- **Nei template:** `{% if user_perms_map|get_item:'clienti.elimina' %}…{% endif %}`
- Il superuser bypassa sempre la matrice.

## Import / Export CSV clienti

- `/clienti/import-csv/` — upsert per `partita_iva`. Header attesi:
  `ragione_sociale,tipo,settore,partita_iva,codice_fiscale,codice_sdi,pec,indirizzo,cap,citta,provincia,nazione,stato,note`.
  Supporta separatori `,` / `;` e encoding UTF-8 / Latin-1.
- `/clienti/export-csv/` — rispetta i filtri (`q`, `stato`, `settore`) presenti in querystring,
  scrive il BOM UTF-8 per compatibilità Excel.

## Export PDF report

`/report/pdf/?periodo=30g|q2|anno` genera un PDF A4 con KPI, fatturato per settore e top 5
clienti. Il periodo default è lo stesso del filtro UI.

## Audit log

Ogni create/update/delete su Cliente, Opportunità, Attività, User viene registrato
automaticamente tramite Django signal (`audit/signals.py`). Login / logout / tentativi
falliti arrivano dai signal di `django.contrib.auth`. Gli eventi sono mostrati in
`/impostazioni/audit/` con filtri per azione, tipo di target e ricerca libera.

Import CSV, export CSV e export PDF producono eventi dedicati (`import`, `export`).
L'attore e l'IP arrivano al signal tramite il middleware `audit.middleware.ThreadLocalRequestMiddleware`.

## API REST (token Bearer)

Gestione token da `/impostazioni/api/` — il valore in chiaro è mostrato una sola volta
al momento della creazione, poi visibili solo nome/stato.

Autenticazione: header `Authorization: Bearer <token>`.

| Metodo | Endpoint                        | Note                                  |
| ------ | ------------------------------- | ------------------------------------- |
| GET    | `/api/v1/health`                | `{status, user, version}`             |
| GET    | `/api/v1/clienti`               | `?page`, `?page_size` (≤100), `?q`    |
| GET    | `/api/v1/clienti/<id>`          | dettaglio                             |
| GET    | `/api/v1/opportunita`           | `?stadio=chiusa_win`                  |
| GET    | `/api/v1/opportunita/<id>`      |                                       |
| GET    | `/api/v1/attivita`              |                                       |

Esempio:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/clienti?page_size=5
```

## Webhook outbound

Gestiti da `/impostazioni/api/`. Ogni webhook ha:
- `name`, `url` (obbligatori), `secret` (opzionale HMAC-SHA256)
- lista di `eventi` — vuota = tutti

Eventi disponibili: `cliente.creato`, `cliente.modificato`, `cliente.eliminato`,
`opportunita.creata`, `opportunita.modificata`, `opportunita.chiusa_win`,
`opportunita.chiusa_lost`.

Payload POST:
```json
{"evento": "cliente.creato", "data": { ...modello... }, "at": "2026-04-24T..."}
```

Header aggiuntivi: `X-CRM-Event`, `X-CRM-Signature: sha256=<hmac>` (se `secret` impostato).
Delivery **sincrono** (timeout 5s) — per traffici seri, sostituire con una coda.

## Da fare

_Tutte le feature pianificate (prima + seconda pass) sono state completate. Idee future:_

- [ ] Webhook delivery asincrono con retry (oggi sincrono, no queue)
- [ ] API write endpoints (oggi read-only)
- [ ] Ricerca full-text (Postgres `SearchVector` / Meilisearch) invece di `icontains`
- [ ] Import CSV: dry-run che mostra preview upsert prima di scrivere
- [ ] Audit log: diff campo-per-campo sugli update (oggi salva solo il fatto)
- [ ] Fatturazione / integrazioni / backup: le sezioni settings sono ancora placeholder
