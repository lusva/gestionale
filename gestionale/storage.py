from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class StaticConHash(ManifestStaticFilesStorage):
    """File statici con l'hash del contenuto nel nome (``app.3f2a9c1b4d5e.css``),
    scritti da ``collectstatic``: dopo un deploy il browser scarica subito CSS e
    JS nuovi invece di tenere quelli in cache, e nginx può servirli con una
    scadenza lunghissima.

    Senza manifest (sviluppo con DEBUG, test) o per un file non raccolto si usa
    il nome senza hash invece di dare errore."""

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            return name
