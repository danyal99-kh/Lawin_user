from django.urls import path
from . import api

urlpatterns = [
    path("tables/", api.tables),
    path("tables/<int:pk>/reserve/", api.reserve),
    path("tables/<int:pk>/pay/", api.pay_table),
]
