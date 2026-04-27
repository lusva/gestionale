from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls', namespace='accounts')),
    path('', include('dashboard.urls', namespace='dashboard')),
    path('clienti/', include('clienti.urls', namespace='clienti')),
    path('anagrafiche/', include('anagrafiche.urls', namespace='anagrafiche')),
    path('documenti/', include('documenti.urls', namespace='documenti')),
    path('magazzino/', include('magazzino.urls', namespace='magazzino')),
    path('opportunita/', include('opportunita.urls', namespace='opportunita')),
    path('attivita/', include('attivita.urls', namespace='attivita')),
    path('report/', include('reports.urls', namespace='reports')),
    path('impostazioni/', include('core_settings.urls', namespace='settings')),
    path('cerca/', include('search.urls', namespace='search')),
    path('api/', include('api.urls', namespace='api')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
