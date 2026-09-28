from django.urls import path
from . import api

urlpatterns = [
    path("waiter-calls/", api.calls),
    path("waiter-calls/<uuid:pk>/acknowledge/", api.acknowledge),
    path("waiter-calls/<uuid:pk>/complete/", api.complete),
]
