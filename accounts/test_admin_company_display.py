from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Ruolo
from tenancy.context import azienda_context
from tenancy.models import Azienda


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
)
class AdminCompanyDisplayTests(TestCase):
    password = 'Password-admin-company-2026!'

    @classmethod
    def setUpTestData(cls):
        cls.company = Azienda.objects.create(nome='Azienda visibile in modifica')
        cls.other_company = Azienda.objects.create(nome='Azienda estranea riservata')
        with azienda_context(cls.company.pk):
            cls.superuser = get_user_model().objects.create_superuser(
                username='gestore-display', password=cls.password,
            )
            cls.staff = get_user_model().objects.create_user(
                username='staff-display', password=cls.password, is_staff=True,
            )
            cls.staff.user_permissions.add(*Permission.objects.filter(
                content_type__app_label='auth',
                codename__in=['add_user', 'change_user', 'view_user'],
            ))
            cls.user = get_user_model().objects.create_user(
                username='utente-display', password=cls.password,
            )
            cls.profile_id = cls.user.profile.pk
        with azienda_context(cls.other_company.pk):
            cls.other_user = get_user_model().objects.create_user(
                username='utente-estraneo-display', password=cls.password,
            )

    def setUp(self):
        self.change_url = reverse('admin:auth_user_change', args=[self.user.pk])
        self.add_url = reverse('admin:auth_user_add')
        self.new_company_url = self.add_url + '?destinazione_azienda=nuova'
        with azienda_context(self.company.pk):
            self.client.force_login(self.superuser)

    def test_change_shows_current_company_readonly_and_new_company_link(self):
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        adminform = response.context['adminform']
        self.assertIn('azienda_associata', adminform.readonly_fields)
        self.assertIn('last_login', adminform.readonly_fields)
        self.assertNotIn('azienda_associata', adminform.form.fields)
        self.assertNotIn('last_login', adminform.form.fields)
        self.assertNotIn('destinazione_azienda', adminform.form.fields)
        self.assertContains(response, self.company.nome)
        self.assertContains(response, f'ID {self.company.pk}')
        self.assertContains(response, f'href="{self.new_company_url}"')
        self.assertNotContains(response, self.other_company.nome)

    def test_new_company_link_preselects_new_company_without_creating_records(self):
        previous_companies = Azienda.objects.count()
        previous_users = get_user_model().objects.count()
        response = self.client.get(self.new_company_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['adminform'].form['destinazione_azienda'].value(), 'nuova',
        )
        self.assertEqual(Azienda.objects.count(), previous_companies)
        self.assertEqual(get_user_model().objects.count(), previous_users)
        ordinary_add = self.client.get(self.add_url)
        self.assertEqual(
            ordinary_add.context['adminform'].form['destinazione_azienda'].value(), 'attuale',
        )

    def test_staff_sees_current_company_without_cross_company_creation_link(self):
        with azienda_context(self.company.pk):
            self.client.force_login(self.staff)
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.company.nome)
        self.assertNotContains(response, self.other_company.nome)
        self.assertNotContains(response, f'href="{self.new_company_url}"')
        response = self.client.get(self.new_company_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('destinazione_azienda', response.context['adminform'].form.fields)

    def test_forged_company_fields_do_not_move_existing_user(self):
        previous_companies = Azienda.objects.count()
        last_login = timezone.now()
        with azienda_context(self.company.pk):
            self.user.last_login = last_login
            self.user.save(update_fields=['last_login'])
        response = self.client.post(self.change_url, {
            'username': self.user.username,
            'first_name': 'Nome aggiornato',
            'is_active': 'on',
            'date_joined_0': self.user.date_joined.strftime('%Y-%m-%d'),
            'date_joined_1': self.user.date_joined.strftime('%H:%M:%S'),
            'last_login_0': '',
            'last_login_1': '',
            'profile-TOTAL_FORMS': '1',
            'profile-INITIAL_FORMS': '1',
            'profile-MIN_NUM_FORMS': '0',
            'profile-MAX_NUM_FORMS': '1',
            'profile-0-id': str(self.profile_id),
            'profile-0-user': str(self.user.pk),
            'profile-0-ruolo': Ruolo.VISUALIZZATORE,
            'profile-0-stato': 'attivo',
            'profile-0-tema': 'light',
            'profile-0-densita': 'normal',
            'azienda': str(self.other_company.pk),
            'azienda_associata': str(self.other_company.pk),
            'profile-0-azienda': str(self.other_company.pk),
            'destinazione_azienda': 'nuova',
            '_save': 'Salva',
        })
        self.assertRedirects(
            response, reverse('admin:auth_user_changelist'), fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        profile = Profile.all_objects.get(user=self.user)
        self.assertEqual(self.user.first_name, 'Nome aggiornato')
        self.assertEqual(self.user.last_login, last_login)
        self.assertEqual(profile.ruolo, Ruolo.VISUALIZZATORE)
        self.assertEqual(profile.azienda_id, self.company.pk)
        self.assertEqual(Azienda.objects.count(), previous_companies)

    def test_company_display_does_not_expose_foreign_user_change_page(self):
        response = self.client.get(reverse('admin:auth_user_change', args=[self.other_user.pk]))
        self.assertRedirects(response, reverse('admin:index'), fetch_redirect_response=False)
        self.assertNotContains(response, self.other_company.nome, status_code=302)

    def test_company_display_distinguishes_pending_setup(self):
        # Pending accounts are redirected to onboarding by middleware; verify
        # the display also remains accurate if used while setup is pending.
        user_admin = admin.site._registry[get_user_model()]
        with azienda_context(self.company.pk):
            user = get_user_model().objects.get(pk=self.user.pk)
            configured = str(user_admin.azienda_associata(user))
            Azienda.objects.filter(pk=self.company.pk).update(configurata=False)
            user = get_user_model().objects.get(pk=self.user.pk)
            pending = str(user_admin.azienda_associata(user))
        self.assertNotEqual(configured, pending)
        self.assertIn(self.company.nome, pending)
        self.assertIn(f'ID {self.company.pk}', pending)
        self.assertIn('configura', pending.lower())
