from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from .context import azienda_context


class AziendaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from accounts.models import Profile, StatoUtente

        # Authenticate membership without the previous request's company scope.
        with azienda_context(None):
            azienda_id = None
            da_configurare = False
            if request.user.is_authenticated:
                profile = Profile.all_objects.select_related('azienda').filter(
                    user_id=request.user.pk,
                ).first()
                if profile is not None and profile.azienda.attiva and profile.stato != StatoUtente.SOSPESO:
                    azienda_id = profile.azienda_id
                    da_configurare = not profile.azienda.configurata
                elif not request.path.startswith(('/accounts/logout/', '/api/', '/mcp')):
                    return HttpResponseForbidden('Nessuna azienda attiva associata al tuo account.')
            request.azienda_id = azienda_id
            with azienda_context(azienda_id):
                if da_configurare:
                    allowed = {reverse('accounts:configura_azienda'), reverse('accounts:logout')}
                    # Bearer authentication checks its own company, which can
                    # differ from the session. No business page can skip setup.
                    api = request.path.startswith('/api/') or request.path in {'/mcp', '/mcp/'}
                    static_prefix = '/' + settings.STATIC_URL.lstrip('/')
                    if request.path not in allowed and not api and not request.path.startswith(static_prefix):
                        response = redirect('accounts:configura_azienda')
                        if request.headers.get('HX-Request') == 'true':
                            response['HX-Redirect'] = reverse('accounts:configura_azienda')
                        return response
                response = self.get_response(request)
                # A streaming generator can run after middleware unwinds.
                if getattr(response, 'streaming', False):
                    # Token authentication may have selected a different
                    # company from the browser session (or anonymous scope).
                    stream_azienda_id = request.azienda_id
                    content = response.streaming_content

                    def scoped_stream():
                        iterator = iter(content)
                        while True:
                            with azienda_context(stream_azienda_id):
                                try:
                                    chunk = next(iterator)
                                except StopIteration:
                                    return
                            # Do not leave a company context installed while
                            # the consumer is paused between chunks.
                            yield chunk

                    async def scoped_async_stream():
                        iterator = aiter(content)
                        while True:
                            with azienda_context(stream_azienda_id):
                                try:
                                    chunk = await anext(iterator)
                                except StopAsyncIteration:
                                    return
                            yield chunk

                    response.streaming_content = scoped_async_stream() if response.is_async else scoped_stream()
                return response
