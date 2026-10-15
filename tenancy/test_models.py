from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from accounts.models import Profile
from clienti.models import Cliente, Contatto

from .context import azienda_context, current_azienda_id
from .middleware import AziendaMiddleware
from .models import Azienda


class BulkBoundaryTests(TestCase):
    def setUp(self):
        self.other = Azienda.objects.create(nome='Altra azienda')
        with azienda_context(1):
            self.own = Cliente.objects.create(ragione_sociale='Cliente A')
            self.user = get_user_model().objects.create_user(username='profilo-immutabile')
        with azienda_context(self.other.pk):
            self.foreign = Cliente.objects.create(ragione_sociale='Cliente B')

    def test_bulk_writes_cannot_cross_company_boundary(self):
        with azienda_context(1):
            with self.assertRaises(ValidationError):
                Contatto.objects.bulk_create([Contatto(nome='Non consentito', cliente=self.foreign)])
            with self.assertRaises(PermissionDenied):
                Cliente.objects.bulk_update([self.foreign], ['ragione_sociale'])
            with self.assertRaises(PermissionDenied):
                Cliente(pk=self.foreign.pk, ragione_sociale='Sovrascrittura').save()
            with self.assertRaises(PermissionDenied):
                Profile.objects.filter(user=self.user).update(user_id=self.user.pk)
            with self.assertRaises(PermissionDenied):
                Profile.objects.bulk_create(
                    [Profile(user=self.user)], update_conflicts=True,
                    update_fields=['ruolo'], unique_fields=['user'],
                )
        with azienda_context(self.other.pk):
            self.assertEqual(Cliente.objects.get(pk=self.foreign.pk).ragione_sociale, 'Cliente B')

    def test_queryset_declared_outside_request_resolves_company_at_execution(self):
        with azienda_context(None):
            lazy = Cliente.objects.order_by('pk')
        for company, expected in ((1, self.own), (self.other.pk, self.foreign)):
            with azienda_context(company):
                self.assertEqual(list(lazy.all()), [expected])
        with azienda_context(None):
            self.assertFalse(lazy.exists())


class StreamingBoundaryTests(SimpleTestCase):
    def test_stream_scope_is_restored_between_chunks(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()

        def view(request):
            # Simulate a valid API token selecting its company's context.
            request.azienda_id = 42

            def chunks():
                yield str(current_azienda_id())
                yield str(current_azienda_id())

            return StreamingHttpResponse(chunks())

        with azienda_context(1):
            response = AziendaMiddleware(view)(request)
            self.assertEqual(current_azienda_id(), 1)
            stream = iter(response.streaming_content)
            self.assertEqual(next(stream), b'42')
            self.assertEqual(current_azienda_id(), 1)
            self.assertEqual(next(stream), b'42')
            self.assertEqual(current_azienda_id(), 1)
            response.close()
