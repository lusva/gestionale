"""Middleware che registra la request corrente in thread-local.

Serve ai signal handlers di audit per accedere all'utente e all'IP senza
modificare le signature dei modelli.
"""
from .utils import clear_current_request, set_current_request


class ThreadLocalRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            return self.get_response(request)
        finally:
            clear_current_request()
