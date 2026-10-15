import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import ClienteForm
from .models import Cliente, Contatto, Settore


class RicercaClientiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='clienti')
        cls.settore = Settore.objects.create(nome='Consulenza')
        cls.azienda = Cliente.objects.create(
            ragione_sociale='Alfa Servizi Srl', settore=cls.settore, stato='attivo',
        )
        for nome in ('Mario', 'Maria'):
            Contatto.objects.create(
                cliente=cls.azienda, nome=nome, cognome='Rossi', email=f'{nome}@example.com',
            )
        Cliente.objects.create(ragione_sociale='Alfa Secondaria', stato='prospect')

    def setUp(self):
        self.client.force_login(self.user)

    def test_ricerca_referente_non_duplica_clienti(self):
        response = self.client.get(reverse('clienti:list'), {'q': 'Rossi Alfa'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['clienti']), [self.azienda])

    def test_export_contiene_stessi_clienti_della_lista(self):
        params = {'q': 'Rossi Alfa', 'stato': 'attivo', 'settore': self.settore.slug}
        listing = self.client.get(reverse('clienti:list'), params)
        export = self.client.get(reverse('clienti:export_csv'), params)
        self.assertEqual(export.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(export.content.decode('utf-8-sig'))))
        self.assertEqual(
            [row['ragione_sociale'] for row in rows],
            [c.ragione_sociale for c in listing.context['clienti']],
        )
        self.assertEqual(len(rows), 1)

    def test_contatti_per_nome_completo(self):
        response = self.client.get(reverse('clienti:contatti_list'), {'q': 'Mario Rossi'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c.nome for c in response.context['contatti']], ['Mario'])


class ContattoPrincipaleTest(TestCase):
    def setUp(self):
        self.azienda = Cliente.objects.create(ragione_sociale='Alfa')
        self.principale = Contatto.objects.create(
            cliente=self.azienda, nome='Mario', cognome='Rossi', primary=True,
        )

    def test_nuovo_principale_sostituisce_il_precedente(self):
        nuovo = Contatto.objects.create(cliente=self.azienda, nome='Anna', cognome='Verdi', primary=True)
        self.assertEqual(list(self.azienda.contatti.filter(primary=True)), [nuovo])

    def test_salvataggio_fallito_preserva_il_principale(self):
        nuovo = Contatto(cliente=self.azienda, nome='Anna', cognome='Verdi', primary=True)
        with patch('django.db.models.Model.save', side_effect=RuntimeError('save failed')):
            with self.assertRaises(RuntimeError):
                nuovo.save()
        self.principale.refresh_from_db()
        self.assertTrue(self.principale.primary)
        self.assertEqual(self.azienda.contatti.count(), 1)

    def test_aggiornamento_parziale_non_ripristina_un_principale_obsoleto(self):
        nuovo = Contatto.objects.create(cliente=self.azienda, nome='Anna', cognome='Verdi', primary=True)
        self.principale.email = 'mario@example.com'
        self.principale.save(update_fields=['email'])
        self.assertEqual(list(self.azienda.contatti.filter(primary=True)), [nuovo])


class SalvataggioClienteFormTest(TestCase):
    def form(self, **extra):
        return ClienteForm(data={
            'ragione_sociale': 'Nuovo cliente', 'tipo': 'azienda',
            'partita_iva': '12345678901', 'nazione': 'IT', 'stato': 'prospect',
            'ref_nome': 'Mario', 'ref_cognome': 'Rossi', **extra,
        })

    def test_errore_referente_non_salva_cliente_a_meta(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        with patch('clienti.forms.Contatto.objects.update_or_create', side_effect=RuntimeError('save failed')):
            with self.assertRaises(RuntimeError):
                form.save()
        self.assertFalse(Cliente.objects.exists())

    def test_limiti_referente_validati_prima_del_salvataggio(self):
        for field, value in (('ref_nome', 'a' * 101), ('ref_cognome', 'a' * 101), ('ref_tel', '1' * 31)):
            with self.subTest(field=field):
                form = self.form(**{field: value})
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
