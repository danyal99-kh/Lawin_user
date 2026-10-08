from django.urls import path

from . import api

urlpatterns = [
    path("credits/", api.credits),
    path("credits/debtors/", api.debtors),
    path("credits/debtors/<int:pk>/", api.debtor_detail),
    path("credits/payments/", api.payments),
]
