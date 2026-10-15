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
8. **Pipeline opportunità** — `/opportunita/` (6 colonne Kanban, filtri, valore ponderato, follow-up e drag & drop)
9. **Opportunità detail / form** — `/opportunita/<id>/`
10. **Agenda attività** — `/attivita/` (ricerca, scadute/oggi/settimana, mie attività, KPI, completamento via HTMX)
11. **Report** — `/report/` (KPI Q2, bar chart vs obiettivo, donut SVG settori, top 5 clienti)
12. **Impostazioni → Utenti** — `/impostazioni/` (tabella utenti con select ruolo inline HTMX, matrice permessi)
13. **Impostazioni → Organizzazione, Ruoli, Integrazioni, Fatturazione, API, Audit log, Backup**
14. **Ricerca globale** — `/cerca/?q=…` cross-model con highlight `<mark>` e paginazione per gruppo (`?group=clienti&page=2`)
15. **Import CSV guidato (3 passi)** — `/clienti/import-csv/` caricamento → collegamento colonne → anteprima delle modifiche ed errori → conferma e riepilogo
16. **Export CSV clienti** — `/clienti/export-csv/` (rispetta i filtri di lista)
17. **Export PDF report** — `/report/pdf/?periodo=…` (via xhtml2pdf)
18. **Audit log** — eventi tracciati automaticamente (create/update/delete + login/logout/import/export), UI in `/impostazioni/audit/`
19. **API REST read-only** — `/api/v1/{health,clienti,opportunita,attivita}` con Bearer token
19b. **API v2 (lettura e scrittura)** — `/api/v2/{health,clienti,posizioni-iva,forme-pagamento,conti-correnti,fatture}`: `POST /api/v2/fatture` crea una fattura (TD01 di default, IVA e pagamento predefiniti del cliente), `?anteprima=1` calcola totali e scadenze senza salvare, l'header `Idempotency-Key` evita i doppioni. Serve un token con «Può scrivere» (Impostazioni › API) e il permesso `documenti.modifica` del ruolo. Per integrazioni esterne: NIKI usa il server MCP. Logica in `documenti/servizi.py`, test in `api/tests.py`.
19c. **Server MCP** — `/mcp` (Model Context Protocol, JSON-RPC su HTTP, stesso token Bearer): 30 tool di lettura e scrittura su clienti, forme di pagamento, documenti del ciclo attivo e workflow, incassi, acquisti, magazzino, CRM e cashflow. Le scritture accettano `anteprima` (eseguite e annullate: si vede il risultato senza salvare) e `chiave` (idempotenza). `tools/list` mostra solo i tool permessi dal ruolo e, per le scritture, dai token con «Può scrivere». Lo usa NIKI; funziona anche con Claude Desktop (`mcp-remote`). Codice in `api/mcp.py` e `api/mcp_tools.py`, logica in `documenti/servizi.py`, test in `api/tests_mcp.py`.
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
python manage.py createsuperuser        # admin per /admin/, associato all'azienda iniziale (ID 1)
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
- Il superuser bypassa la matrice dei ruoli, ma resta limitato ai dati della propria azienda.

## Accesso multi-azienda

Il sito può ospitare più aziende nello stesso database. Ogni utente appartiene a
**una sola azienda** tramite `Profile.azienda`; username e identità di login
rimangono globali, quindi aziende diverse devono usare username distinti.
L'azienda della richiesta è determinata sul server dal profilo autenticato o
dall'utente del token API/MCP. Il browser non può scegliere un'altra azienda
inviando un ID nel form, nell'URL o nella richiesta API.

I modelli applicativi ereditano `TenantModel`: il manager `objects` limita le
query all'azienda corrente e le scritture verificano la coerenza delle relazioni.
L'isolamento si applica anche a utenti e Team, ricerca, statistiche, documenti,
allegati, integrazioni e amministrazione Django. Il superuser web vede la propria
azienda. Questa è una separazione applicativa nell'ORM, non una separazione fisica
dei database; l'accesso diretto al database e `all_objects` sono riservati a
operazioni server esplicite. I nuovi endpoint e comandi devono preservare questo
confine e usare `azienda_context()` quando operano fuori da una richiesta.

Le migrazioni creano l'azienda iniziale con **ID 1** e assegnano a questa i dati e
gli utenti già presenti. Per creare da interfaccia un nuovo utente che inserirà
da sé i dati della propria azienda:

1. Accedere a `/admin/` con un **superuser Django**.
2. Aprire **Utenti → Aggiungi utente** e inserire username e password.
3. Selezionare **Nuova azienda — configurazione al primo accesso** nel campo
   **Azienda** e salvare.
4. Comunicare le credenziali all'utente, che accederà dal normale login del sito.

L'account viene creato come amministratore della nuova azienda, senza accesso
all'amministrazione Django. Dopo il salvataggio si torna alla pagina di aggiunta:
il nuovo utente non compare nell'elenco dell'azienda del gestore, perché
appartiene a un'altra azienda. L'opzione predefinita **Stessa azienda dell’amministratore** continua
a creare colleghi nella stessa azienda; solo i superuser possono scegliere una
nuova azienda. Non vengono inviate email automaticamente.

In alternativa, dal server si può omettere il nome azienda nel comando:

```bash
python manage.py crea_azienda --username nuovo-admin --email nuovo-admin@example.test
```

Entrambe le modalità preparano un'azienda vuota e separata. Al primo accesso l'utente vede
**Configura la tua azienda**: deve inserire il nome o la ragione sociale; partita
IVA, codice fiscale, sede e contatti sono facoltativi e completabili in seguito.
Fino al salvataggio il gestionale rimanda a questa pagina e i token API/MCP
non consentono l'accesso. Dopo il salvataggio si apre la dashboard della nuova
azienda. Non è una registrazione pubblica: l'account iniziale viene creato
dal gestore del sito.

Se l'account è stato creato per errore nell'azienda attuale, un superuser può
aprire **Admin → Utenti → Modifica → Accesso aziendale → Assegna questo utente
a una nuova azienda**. La pagina mostra l'azienda attuale e richiede la conferma.
L'operazione conserva username e password, prepara un'azienda vuota e chiude
le sessioni dell'utente; al login successivo questi compila i dati aziendali.
Il semplice accesso precedente è ammesso: i relativi eventi restano nello storico
originale, senza un collegamento al profilo trasferito. Account con dati,
attività aziendali, token, allegati, permessi Django o accesso all'admin non possono
essere assegnati: in questi casi occorre un account nuovo. L'utente trasferito
non compare più nell'elenco della vecchia azienda. L'operazione richiede il
backend delle sessioni su database, già usato dalla configurazione del progetto.

Per aggiungere invece un'azienda già nominata con un amministratore, senza la
configurazione obbligatoria al primo accesso:

```bash
python manage.py crea_azienda "Azienda B" --username admin-b --email admin-b@example.test
```

Il comando richiede la password due volte, senza mostrarla, e crea anche
l'anagrafica e l'organizzazione iniziali. Non invia email e non abilita i flag
Django `is_staff` o `is_superuser`: il nuovo account ha il ruolo applicativo
**Amministratore**. Per automazioni è disponibile `--password-env NOME_VARIABILE`;
la password deve essere già nella variabile indicata, mai negli argomenti del
comando. Dopo il login l'amministratore può invitare colleghi da **Impostazioni →
Utenti**: i nuovi utenti vengono associati alla sua azienda.

Per dati dimostrativi usare `python manage.py seed --azienda ID` in sviluppo.
Il seed senza `--azienda` continua a usare l'azienda 1; `--reset` cancella i dati
CRM e gli utenti non-superuser della sola azienda selezionata. Le aziende con
ID maggiore di 1 usano username demo come `azienda2.marco@studiorossi.it`, stampati
dal comando. Le password demo note restano adatte soltanto ad ambienti di prova.

I comandi operativi `sdi_pull`, `sdi_verifica`, `genera_fatture_ricorrenti`,
`invia_promemoria_scadenze` e `allinea_registrazione_acquisti` accettano
`--azienda ID`. Senza questo parametro elaborano separatamente **tutte le aziende
attive e configurate**, così i timer esistenti continuano a funzionare senza
elaborare quelle ancora in attesa del primo accesso. I richiami dal sito
passano sempre l'azienda della sessione. Le configurazioni PEC, SDI, webhook e
fatturazione vanno completate per ciascuna azienda prima di usarne le integrazioni.

I backup completi includono tutte le aziende e sono disponibili solo dal server:
`python manage.py backup` oppure il timer `deploy/gestionale-backup.timer`.
Creazione e download via web sono disabilitati anche per il superuser.

**Prima di aprire l'accesso ad aziende diverse in produzione**, applicare le
migrazioni e aggiornare la configurazione nginx seguendo
`deploy/nginx-gestionale.conf`: tutto `/media/` deve passare da Django e la
location `/media-protetti/` deve essere `internal`. Una vecchia configurazione che
serve direttamente i file aggirerebbe i controlli applicativi. Il controllo
anonimo dei media nel deploy verifica questa configurazione. L'implementazione
locale e i test non applicano da soli né il deploy né le migrazioni al database
in produzione.

Verifica dell'isolamento con database di test separato:

```bash
python manage.py test tenancy accounts
```

## Import / Export CSV clienti

L'importazione in `/clienti/import-csv/` richiede il permesso `clienti.importa`
e procede in tre passi:

1. **Carica il file**: massimo **2 MB**, **2.000 righe di dati** e 100 colonne,
   con intestazioni non vuote e non duplicate. Sono riconosciuti i separatori
   virgola, punto e virgola e tabulazione, con codifica UTF-8 o Latin-1.
   Il CSV di esempio è scaricabile dalla pagina (`?esempio=1`);
   i dati dimostrativi vanno sostituiti prima di importare.
2. **Collega le colonne**: gli abbinamenti riconosciuti vengono proposti
   automaticamente. Ogni campo cliente può essere collegato a una sola colonna;
   **Ragione sociale** e **Partita IVA** sono obbligatori. Le altre colonne possono
   essere ignorate. La nazione usata per verificare la partita IVA è **IT** se
   non specificata: per i clienti esteri occorre collegare la colonna **Nazione**
   e valorizzarla con la sigla di due lettere.
3. **Controlla e importa**: l'anteprima confronta realmente il file con i dati
   del CRM, senza salvare clienti, settori o eventi di importazione. Mostra le
   righe da creare, da aggiornare, invariate e con errori, con differenze
   **Prima/Dopo**, filtri per esito e pagine da 50 righe. La conferma applica tutte
   le righe valide del file, anche quelle nascoste dal filtro o in altre pagine.
   Il riepilogo finale distingue clienti creati, aggiornati, righe invariate e
   scartate.

La **partita IVA** identifica i clienti già presenti. I campi vuoti nel file
conservano i valori esistenti; le righe invariate non vengono risalvate. Gli
errori sono associati al numero di riga del CSV: una partita IVA ripetuta nel
file esclude **tutte le sue occorrenze**; una corrispondenza con più clienti
esistenti richiede prima la correzione del duplicato in anagrafica. Le righe con
errori vengono scartate, mentre quelle valide possono essere confermate.

Il caricamento e l'anteprima restano disponibili per **un'ora dal caricamento**;
alla scadenza occorre selezionare di nuovo il file. Se i dati del CRM cambiano
dopo il controllo, l'anteprima va aggiornata e confermata nuovamente. Il
salvataggio delle righe valide e della ricevuta è **atomico**: un conflitto
annulla l'intero salvataggio. La ricevuta rende la conferma **idempotente**:
un doppio invio dello stesso caricamento restituisce lo stesso riepilogo senza
applicare di nuovo le modifiche, anche dopo la scadenza della sessione.

Campi disponibili:
`ragione_sociale,tipo,settore,partita_iva,codice_fiscale,codice_sdi,pec,indirizzo,cap,citta,provincia,nazione,stato,note`.
La preparazione e l'applicazione del piano sono in `clienti/importazione.py`;
il flusso e le ricevute sono gestiti da `clienti/views.py` e
`EsitoImportazioneClienti`.

L'export `/clienti/export-csv/` rispetta i filtri della lista presenti in
querystring e scrive il BOM UTF-8 per compatibilità Excel.

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

## Fatturazione elettronica — SDI via PEC

Le fatture partono dalla PEC aziendale verso il SDI; ricevute (RC/MC/NS/NE)
e fatture passive (`.xml` e `.xml.p7m` firmate) arrivano sulla stessa casella
e le legge `sdi_pull`. Il codice è in `documenti/sdi.py`.

**Configurazione** — Impostazioni › Integrazioni (solo amministratori): form
con le password che non tornano mai nella pagina (vuote = si tengono quelle
salvate), pulsante «Aruba PEC» che riempie server e porte, «Verifica
connessione» (login SMTP e IMAP, nessun invio), stato della ricezione, ultimi
messaggi del SDI, fatture scartate o rifiutate e «Controlla ora la PEC» (`sdi_pull`
su 30 email). Ogni modifica finisce nell'audit log, con i nomi dei campi e mai
le password. Esempio Aruba PEC:

| Campo | Valore |
|---|---|
| `sdi_provider` | PEC SMTP/IMAP |
| `pec_mittente`, `pec_smtp_user`, `pec_imap_user` | indirizzo PEC aziendale |
| `pec_smtp_password`, `pec_imap_password` | password della casella PEC |
| `pec_smtp_host` / `pec_smtp_port` | `smtps.pec.aruba.it` / `465` (vuoto = 465) |
| `pec_imap_host` / `pec_imap_port` | `imaps.pec.aruba.it` / `993` |
| `pec_destinatario_sdi` | `sdi01@pec.fatturapa.it` (poi lo aggiorna `sdi_pull`) |

**Primo invio** — il canale PEC non ha un ambiente di prova: ogni invio è una
fattura emessa.

```bash
python manage.py sdi_verifica             # login SMTP/IMAP, nessun invio
# invio dal dettaglio fattura → "Invia al SDI"
python manage.py sdi_pull --dry-run       # cosa farebbe, senza applicare
python manage.py sdi_pull                 # applica ricevute, importa passive
python manage.py sdi_pull --giorni 365    # fatture passive più vecchie
```

Il SDI risponde al primo invio da una casella `sdiNN@pec.fatturapa.it`, da
usare per gli invii successivi: `sdi_pull` la salva da sola su
`pec_destinatario_sdi`.

`sdi_pull` non tocca i flag letto/non letto della casella: tiene traccia
delle email elaborate nel database (`MessaggioPecElaborato`) e considera solo
quelle spedite dal SDI. Ogni run si registra in `EsecuzioneComando`
(`core_settings/esecuzioni.py`): la pagina Integrazioni avvisa se la PEC non
viene controllata da più di un'ora (timer fermo) e due run non partono mai
insieme (timer e «Controlla ora»).

**Esecuzione automatica** (ogni 15 minuti, una volta sul server):

```bash
cp deploy/gestionale-sdi-pull.service deploy/gestionale-sdi-pull.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gestionale-sdi-pull.timer
journalctl -u gestionale-sdi-pull -n 50     # output delle ultime run
```

Se le fatture si emettono da un altro canale (es. Aruba Fatturazione
Elettronica) lascia `sdi_provider` su **Disabilitato**: "Invia al SDI"
riemetterebbe fatture già emesse. Le fatture ricevute su quel canale si
importano da Acquisti → Importa XML: più file insieme o lo zip dello
scaricamento multiplo (`.xml`, `.xml.p7m`; metadati e PDF vengono ignorati,
le fatture già presenti saltate).

Le fatture con esito NS (scartata) restano non emesse: vanno corrette e
reinviate entro 5 giorni. Consigliato registrare la PEC aziendale come
indirizzo telematico in «Fatture e Corrispettivi», così tutte le fatture
passive arrivano lì.

## Velocità e uso quotidiano

- **Menu laterale** — Dashboard in alto, poi Vendite, Acquisti, Incassi e pagamenti, Clienti e CRM, Analisi, Anagrafiche; Impostazioni in fondo accanto all'utente. Vendite e Incassi e pagamenti sono aperti finché non li chiudi, gli altri gruppi restano come li lasci; il gruppo della pagina aperta è sempre aperto. Ognuno vede solo le voci che può aprire. Il menu e le azioni della palette sono definiti una volta sola in `core_settings/menu.py`.
- **Palette comandi** — <kbd>⌘K</kbd> / <kbd>Ctrl K</kbd> o <kbd>/</kbd> (anche cliccando la ricerca in alto): cerca mentre scrivi fatture (anche «12/2026»), fatture di acquisto, clienti, contatti, fornitori, articoli e porta a pagine e azioni («Nuova fattura»…). Endpoint `/cerca/rapida/`.
- **Scorciatoie** — <kbd>⌘S</kbd> salva il documento aperto, <kbd>N</kbd> «Nuovo» nella lista, <kbd>Invio</kbd> sull'ultima riga di un documento ne aggiunge una, <kbd>?</kbd> mostra l'elenco.
- **Da fare** — campanella in alto e card in dashboard: fatture scadute da incassare, pagamenti a fornitori scaduti o entro 7 giorni, scadenze fiscali, fatture scartate dal SDI o da inviare, fatture di acquisto da confermare (`dashboard/da_fare.py`).
- **Form dei documenti** (fattura, offerta, ordine, DDT, nota di credito) — riepilogo di imponibile, IVA, totale e scadenze calcolato dal server mentre si scrive (`documenti/anteprima.py`, gli stessi conti di PDF e XML); cliente → forma di pagamento e IVA abituali (si impostano nella scheda cliente); articolo → descrizione, prezzo, UM e IVA; clienti e liste lunghe con ricerca; righe che si tolgono col cestino; avviso se si esce senza salvare.
- **Duplica** — «Duplica» su fatture e documenti apre un documento nuovo uguale, datato oggi, da rivedere e salvare (`?da=<id>`); «Nuova fattura» dalla scheda cliente (`?cliente=<id>`).
- **Lista fatture** — filtri e ricerca istantanei (HTMX, l'indirizzo resta condivisibile), totale con IVA, colonna scadenza con i giorni di ritardo, filtro «Scadute»; export CSV e ZIP con gli stessi filtri della lista.
- **Incassi parziali** — nel dettaglio fattura, «Scadenze e incassi»: per ogni rata incassato, residuo e stato (aperta, parziale, incassata, scaduta), «+» per registrare un incasso anche parziale (data, importo, modalità, note) ed elenco degli incassi con il cestino per eliminarli. La fattura risulta pagata quando tutte le rate hanno residuo zero, alla data dell'ultimo incasso; «Segna incassata» / «Incassa il resto» salda il residuo. Scadenziario clienti, cashflow, «Da fare», dashboard, estratto conto, solleciti, promemoria, export e pacchetto per il commercialista contano il residuo; in lista il badge «Parziale · resta …». Con incassi registrati le scadenze non si ricalcolano e non si cambiano dal form. Per la chat c'è il tool `registra_incasso`. Le fatture già segnate incassate prima di questa funzione hanno un incasso per rata creato dalla migrazione `documenti/0010` (nota «Registrato dalla migrazione 0010…»).
- **Note di credito al SDI** — dal dettaglio della nota: «XML FatturaPA» (TD04, importi positivi, `DatiFattureCollegate` con numero e data delle fatture stornate) e «Invia al SDI» / «Reinvia» come per le fatture, con stato SDI e messaggi; trasmessa al SDI, non si modifica né si elimina più. Le note nuove propongono il sezionale **NC** (`<Numero>NC/1/2026</Numero>`: il SDI distingue comunque le TD04 dalle fatture nel controllo doppioni 00404). Il progressivo del file è `N` + 4 caratteri per le note e numerico per le fatture: i nomi non si sovrappongono mai. `sdi_pull` riconduce RC/NS/MC alla nota giusta. Nel form si scelgono le «Fatture stornate»; la nota ne compensa le rate aperte con un incasso «compensata dalla nota di credito» (sparisce eliminando la nota), così la fattura stornata esce da scadenziario, solleciti e cashflow. Filtro per stato SDI in lista e voci nel «Da fare». Il pacchetto per il commercialista include gli XML delle note. Le TD08 (semplificate) non si generano. Lo schema XSD ufficiale v1.2.2 è in `documenti/xsd/` per validare l'XML senza rete (`fatt_el.schema_fatturapa()`).
- **Dettaglio fattura** — email e **sollecito di pagamento** con destinatario e testo modificabili prima dell'invio; «Reinvia al SDI» dopo uno scarto; una fattura già emessa al SDI non si modifica più (si corregge con nota di credito); un doppio clic su «Invia al SDI» non spedisce due volte.
- **Fatture ricorrenti** — Vendite › Fatture ricorrenti: canoni mensili, trimestrali, annuali… con righe, forma di pagamento e note. Ogni periodo nasce una fattura vera, datata il giorno dell'emissione (mai nel passato: i periodi saltati escono con la data di oggi, numerati in sequenza); nelle descrizioni e nelle note `{mese}`, `{anno}`, `{periodo}` («ottobre 2026», «1° trimestre 2026»), `{dal}`, `{al}`. Il giorno 31 vuol dire «ultimo del mese». Si fattura il periodo in corso o quello appena finito. Facoltativi l'invio automatico al SDI (solo se attivo) e per email. Storico delle emissioni con gli errori, «Emetti ora», «Sospendi» / «Riattiva» (i periodi della sospensione si saltano, salvo chiedere di recuperarli). Dal dettaglio di una fattura: «Altro» › «Crea una ricorrenza da questa fattura». Le emissioni previste entrano nel cashflow come incassi attesi e nel «Da fare» (non riuscite, emesse oggi, entro 7 giorni). Codice in `documenti/ricorrenti.py`; l'invio al SDI e per email sta in `documenti/invio.py` (lo usano anche i pulsanti del dettaglio).
- **Fatture di acquisto inserite a mano** — Acquisti › Fatture di acquisto › «Nuova fattura di acquisto» (o <kbd>N</kbd>): per le fatture cartacee e dei fornitori esteri. Righe con sconto, IVA e categoria di costo (proposta dalla riga sopra), riepilogo live, scadenze dalla forma di pagamento (quella abituale si imposta nella scheda del fornitore) e PDF o scansione allegati (`media/fatture_acquisto_allegati/`, solo con il permesso di vedere i documenti). Un numero già registrato per lo stesso fornitore e data viene rifiutato. Le fatture importate dall'XML si aprono in versione ridotta: categorie di costo, stato, data di registrazione, note e allegato; importi e righe restano quelli dell'XML. Si eliminano solo quelle inserite a mano e senza pagamenti.
- **Scheda cliente** — estratto conto: fatturato, incassato, da incassare, scaduto e l'elenco delle fatture.
- **Pacchetto per il commercialista** — `/documenti/export-commercialista/`: zip del periodo con gli XML emessi e ricevuti, registro vendite e acquisti in CSV, PDF a richiesta.
- **Backup** — Solo dal server con `python manage.py backup`: database (copia consistente anche in WAL) e file caricati di tutte le aziende in `backups/`, si tengono gli ultimi 14. Ogni notte: `deploy/gestionale-backup.timer`. Impostazioni › Backup mostra informazioni, senza creazione o download.

### Note tecniche e di deploy

- Il login pubblico richiede HTTPS con un certificato valido per
  `gestionale.aigislab.ai`; HTTP deve redirigere allo stesso indirizzo in HTTPS.
  Dopo il riavvio, il workflow esegue
  `python deploy/check_https.py https://gestionale.aigislab.ai`: controlla
  certificato, redirect, cookie Secure e invio del form con e senza token CSRF.
  Non usa credenziali e non autentica utenti. Un processo Gunicorn attivo, da solo,
  non basta a considerare riuscito il deploy.

- **Fatture ricorrenti**: le emette ogni mattina alle 6:00 il comando `genera_fatture_ricorrenti` (idempotente: un periodo non si emette mai due volte). Una volta sul server:

  ```bash
  cp deploy/gestionale-ricorrenti.service deploy/gestionale-ricorrenti.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now gestionale-ricorrenti.timer
  python manage.py genera_fatture_ricorrenti --dry-run     # cosa emetterebbe oggi
  ```

- Font, htmx e Alpine sono serviti dal sito (`static/fonts`, `static/vendor`): nessuna richiesta a Google Fonts/unpkg. I link interni si precaricano al passaggio del mouse (Speculation Rules, browser Chromium).
- File statici con l'hash nel nome (`gestionale.storage.StaticConHash`): dopo un deploy i browser scaricano subito CSS e JS nuovi. Serve `collectstatic` (lo fa già il workflow di deploy).
- `deploy/nginx-gestionale.conf` protegge anche `/media/fatture_acquisto_allegati/` (passa da Django come gli XML): va ricopiato sul server dopo l'aggiornamento.
- `deploy/nginx-gestionale.conf` ora comprime CSS/JS/JSON (gzip) e mette in cache per un anno i file con l'hash: va ricopiato sul server (`nginx -t && systemctl reload nginx`).
- Il logo caricato si mostra (sidebar) e si stampa (PDF) da copie ridotte generate al primo uso in `media/anagrafica/ridotti/`; i PDF leggono CSS e immagini dal disco invece che via HTTP.
- SQLite è in modalità WAL con transazioni `IMMEDIATE`: per copiare il database usa `manage.py backup`, non il solo file `db.sqlite3`.

## CRM operativo — ottobre 2026

La gestione delle attività collegate alle trattative e la visibilità delle
opportunità ferme sono le priorità adottate anche nella
[gestione pipeline di Pipedrive](https://www.pipedrive.com/en/features/pipeline-management)
e nella sua [vista delle opportunità](https://support.pipedrive.com/en/article/pipeline-view).
Nel gestionale sono disponibili:

- **Pipeline filtrabile** per testo, responsabile (anche «le mie» e «da assegnare»),
  stato, stadio e priorità. L'indirizzo conserva i filtri e può essere salvato nei
  preferiti. Le sei colonne comprendono vinte e perse; i KPI aperti escludono entrambe.
  Il valore ponderato è la somma di `valore × probabilità / 100` delle sole aperte.
- **Prossimo passo** su ogni trattativa: prima attività ancora aperta, indicazione
  del ritardo e collegamento «Pianifica follow-up» con cliente e opportunità già
  selezionati. Le priorità evidenziano chiusure previste superate, permanenza nello
  stesso stadio da almeno 14 giorni e assenza di attività aperte. «Ferma» si riferisce
  allo stadio, non al tempo dall'ultimo contatto.
- **Agenda** con ricerca, tipo, stato, «Solo le mie», scadute, oggi e prossimi sette
  giorni (oggi incluso, nel fuso Europe/Rome). Per default mostra le aperte, dalle
  più urgenti. Il completamento aggiorna elenco, contatori e campanella, conserva i
  filtri e registra l'utente nell'audit. Funziona anche tramite normale form POST.
- **Dashboard** con agenda personale, promemoria commerciali e riepilogo delle sole
  opportunità aperte. Vendite vinte e imponibile delle fatture hanno indicatori
  distinti: non vengono sommati. Il grafico fatture considera i documenti registrati
  fino a oggi, prima di IVA e note di credito; il tasso di chiusura è storico.
- **Ricerca** multi-parola tra nomi, contatti e aziende, riferimenti documento come
  `12/2026`, attività nella palette e ricerca del numero documento anche a una cifra.
  L'export clienti applica gli stessi filtri dell'elenco.
- **Affidabilità e accessibilità**: salvataggio atomico cliente/referente e contatto
  principale, validazione dei collegamenti cliente-opportunità, probabilità e date
  coerenti al cambio stadio, riordino compatibile con filtri, gestione degli errori
  del server, pulsanti coerenti con i permessi, focus visibile, salto al contenuto
  e menu chiudibile da tastiera. I pulsanti di salvataggio vengono ripristinati
  correttamente anche tornando indietro nel browser.

Le precedenti migliorie di agenda e pipeline non richiedono nuove migrazioni.
Il nuovo import CSV richiede invece
`clienti/0005_esito_importazione_clienti`, che crea le ricevute necessarie a
evitare conferme duplicate. Al deploy, nell'ambiente configurato del server,
applicare le migrazioni e raccogliere gli statici, compreso `import-clienti.css`
oltre a `pipeline.css` e `attivita.css`:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Le verifiche usano un database di test separato:

```bash
DJANGO_DEBUG=True venv/bin/python manage.py test --noinput
DJANGO_DEBUG=True venv/bin/python manage.py makemigrations --check --dry-run
node --test tests/app.test.cjs
node --check static/js/app.js
```

## Da fare

Stato degli interventi e prossime evoluzioni da valutare:

- [ ] Webhook delivery asincrono con retry (oggi sincrono, no queue)
- [ ] Ricerca full-text (Postgres `SearchVector` / Meilisearch) invece di `icontains`
- [x] Import CSV: anteprima effettiva prima del salvataggio, differenze per campo,
  errori e duplicati, conferma atomica e idempotente
- [ ] Audit log: diff campo-per-campo sugli update (oggi salva solo il fatto)
- [ ] Sincronizzazione calendario con gestione esplicita di conflitti e fusi orari
- [ ] Regole configurabili per creare follow-up al cambio stadio
- [ ] Rilevamento duplicati con anteprima dell'unione di clienti e contatti
