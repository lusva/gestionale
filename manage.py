#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def _ensure_weasyprint_libs_macos():
    """Su macOS, WeasyPrint cerca pango/cairo/gobject installati via brew
    in ``/opt/homebrew/lib`` (Apple Silicon) o ``/usr/local/lib`` (Intel).
    dyld legge ``DYLD_FALLBACK_LIBRARY_PATH`` solo all'avvio del processo,
    quindi se la variabile non è settata facciamo re-exec di Python con il
    path corretto. Tutto questo è skip su Linux dove le libs sono già nel
    loader path di sistema.
    """
    if sys.platform != 'darwin':
        return
    if os.environ.get('_WEASYPRINT_LIBS_BOOTSTRAPPED'):
        return
    candidate_dirs = ['/opt/homebrew/lib', '/usr/local/lib']
    extra = [d for d in candidate_dirs if os.path.isdir(d)]
    if not extra:
        return
    current = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
    parts = current.split(':') if current else []
    if all(d in parts for d in extra):
        return
    os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = ':'.join(extra + parts)
    os.environ['_WEASYPRINT_LIBS_BOOTSTRAPPED'] = '1'
    os.execv(sys.executable, [sys.executable, *sys.argv])


def main():
    """Run administrative tasks."""
    _ensure_weasyprint_libs_macos()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestionale.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
