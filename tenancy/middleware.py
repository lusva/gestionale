from django.http import HttpResponseForbidden

from .context import azienda_context


class AziendaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from accounts.models import Profile, StatoUtente

        # Authenticate membership without the previous request's company scope.
        with azienda_context(None):
            azienda_id = None
            if request.user.is_authenticated:
                profile = Profile.all_objects.select_related('azienda').filter(
                    user_id=request.user.pk,
                ).first()
                if profile is not None and profile.azienda.attiva and profile.stato != StatoUtente.SOSPESO:
                    azienda_id = profile.azienda_id
                elif not request.path.startswith(('/accounts/logout/', '/api/', '/mcp')):
                    return HttpResponseForbidden('Nessuna azienda attiva associata al tuo account.')
            request.azienda_id = azienda_id
            with azienda_context(azienda_id):
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
