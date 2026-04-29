/* global window */
// Sample data for all 5 document types

const SAMPLE_COMPANY = {
  name: 'AIGIS Lab S.r.l.',
  address: 'Via dell\'Innovazione 42',
  city: '20121 Milano (MI)',
  vat: 'IT12345678901',
  email: 'amministrazione@aigislab.ai',
  phone: '+39 02 1234 5678',
  website: 'aigislab.ai',
};

const SAMPLE_CLIENT = {
  name: 'Northwind Solutions S.p.A.',
  address: 'Corso Italia 18',
  city: '10134 Torino (TO)',
  vat: 'IT09876543210',
  sdi: 'M5UXCR1',
};

const SAMPLE_DOCS = {
  fattura: {
    type: 'Fattura',
    number: '2026/0142',
    date: '24/04/2026',
    dueDate: '24/05/2026',
    company: SAMPLE_COMPANY,
    client: SAMPLE_CLIENT,
    vatRate: 22,
    items: [
      { name: 'Sviluppo modulo AI per gestionale', desc: 'Integrazione LLM con backend Django + interfaccia di review', qty: 1, unit: 'forf.', price: 4800.00 },
      { name: 'Consulenza architetturale', desc: 'Analisi requisiti, definizione data model, code review', qty: 24, unit: 'h', price: 95.00 },
      { name: 'Setup infrastruttura cloud', desc: 'Container Docker, CI/CD GitHub Actions, monitoring', qty: 1, unit: 'forf.', price: 1200.00, discount: 10 },
      { name: 'Training del team', desc: 'Workshop di 2 giornate sulle nuove API', qty: 2, unit: 'gg', price: 600.00 },
    ],
    payment: { method: 'Bonifico bancario · 30 gg DFFM', iban: 'IT60 X054 2811 1010 0000 0123 456' },
    extraFields: { orderRef: 'ORD-2026-0089' },
    notes: 'Operazione soggetta a IVA ai sensi del DPR 633/72. Pagamento entro la data di scadenza. In caso di ritardo si applicheranno gli interessi di mora ex D.Lgs. 231/2002.',
  },
  offerta: {
    type: 'Offerta',
    number: 'OFF-2026-031',
    date: '24/04/2026',
    dueDate: '24/05/2026',
    company: SAMPLE_COMPANY,
    client: SAMPLE_CLIENT,
    vatRate: 22,
    items: [
      { name: 'Piattaforma gestionale custom', desc: 'Backend Django + frontend React, multi-tenant, role-based access', qty: 1, unit: 'forf.', price: 28000.00 },
      { name: 'Modulo fatturazione elettronica', desc: 'Generazione XML, invio SDI, archiviazione sostitutiva', qty: 1, unit: 'forf.', price: 6500.00 },
      { name: 'Manutenzione evolutiva', desc: 'Pacchetto annuale, 8h/mese di sviluppo', qty: 12, unit: 'mesi', price: 760.00, discount: 5 },
    ],
    payment: { method: '40% all\'ordine, 30% a SAL, 30% al collaudo' },
    extraFields: { orderRef: '' },
    notes: 'Offerta valida 30 giorni. Tempistiche stimate: 14 settimane dalla firma. Sviluppo iterativo con review settimanali. Codice sorgente di proprietà del cliente al saldo finale.',
  },
  ordine: {
    type: 'Ordine',
    number: 'ORD-2026-089',
    date: '24/04/2026',
    company: SAMPLE_COMPANY,
    client: SAMPLE_CLIENT,
    vatRate: 22,
    items: [
      { name: 'Licenza Postgres Pro Enterprise', desc: '1 anno, 8 core, supporto 24/7', qty: 1, unit: 'lic.', price: 3200.00 },
      { name: 'Workstation dev (MacBook Pro M4)', desc: '14", 36GB RAM, 1TB SSD — per nuovo collaboratore', qty: 2, unit: 'pz', price: 2890.00 },
      { name: 'Monitor 27" 4K', desc: 'Dell U2725QE', qty: 2, unit: 'pz', price: 720.00 },
    ],
    payment: { method: 'Bonifico anticipato', iban: 'IT60 X054 2811 1010 0000 0123 456' },
    extraFields: { orderRef: 'RDA-2026-104' },
    notes: 'Consegna prevista entro 10 giorni lavorativi presso la sede di Milano. Garanzia hardware 2 anni.',
  },
  nota_credito: {
    type: 'Nota di Credito',
    number: 'NC-2026-007',
    date: '24/04/2026',
    company: SAMPLE_COMPANY,
    client: SAMPLE_CLIENT,
    vatRate: 22,
    items: [
      { name: 'Storno parziale fattura 2026/0128', desc: 'Riduzione del 15% per ritardo nella consegna del modulo reportistica come da accordo del 18/04/2026', qty: 1, unit: 'forf.', price: 1350.00 },
    ],
    payment: { method: 'Compensazione su prossima fattura' },
    extraFields: { orderRef: 'FT-2026-0128' },
    notes: 'La presente nota di credito storna parzialmente la fattura n. 2026/0128 del 02/04/2026. L\'importo verrà compensato sul prossimo documento contabile.',
  },
  ddt: {
    type: 'Documento di Trasporto',
    number: 'DDT-2026-204',
    date: '24/04/2026',
    company: SAMPLE_COMPANY,
    client: SAMPLE_CLIENT,
    vatRate: 0,
    items: [
      { name: 'Server rack Dell PowerEdge R760', desc: 'S/N: DLPR-2026-A4471', qty: 1, unit: 'pz', price: 0 },
      { name: 'Switch managed Cisco Catalyst 9300', desc: 'S/N: CSCAT-9300-B891', qty: 2, unit: 'pz', price: 0 },
      { name: 'Cavi patch Cat6a 3m', desc: 'Confezione da 24', qty: 1, unit: 'cf', price: 0 },
    ],
    payment: null,
    extraFields: { orderRef: 'ORD-2026-091' },
    notes: 'Causale del trasporto: vendita. Trasporto a cura del mittente. Aspetto esteriore: 4 colli su 1 pallet. Peso lordo: 78 kg.',
  },
};

window.SAMPLE_DOCS = SAMPLE_DOCS;
window.SAMPLE_COMPANY = SAMPLE_COMPANY;
window.SAMPLE_CLIENT = SAMPLE_CLIENT;
