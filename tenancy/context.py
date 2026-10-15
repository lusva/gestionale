"""Company scope. Web requests always set/reset it, including anonymous ones.

The default is the original company for backwards-compatible local management
commands. Scheduled jobs explicitly iterate companies; HTTP never uses this
default. A scope of None denies access to all company data.
"""
from contextlib import contextmanager
from contextvars import ContextVar


_azienda_id = ContextVar('crm_azienda_id', default=1)


def current_azienda_id():
    return _azienda_id.get()


@contextmanager
def azienda_context(azienda_id):
    token = _azienda_id.set(azienda_id)
    try:
        yield
    finally:
        _azienda_id.reset(token)
