from django.urls import path

from . import api

urlpatterns = [
    path("guests/", api.guests),
    path("guests/summary/", api.summary),
    path("guests/orders/", api.guest_orders),
    path("guests/<int:pk>/", api.guest_detail),
    path("guests/<int:pk>/cancel/", api.guest_cancel),
]
