from django.urls import path

from . import views


app_name = 'magazzino'


urlpatterns = [
    path('', views.StockListView.as_view(), name='stock'),
]
