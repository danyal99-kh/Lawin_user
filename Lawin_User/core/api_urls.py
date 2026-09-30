from django.urls import path
from . import api, dashboard, expense_api

urlpatterns = [
    path("settings/welcome/", api.welcome_settings),
    path("dashboard/summary/", dashboard.summary),
    path("accounting/expenses/", expense_api.expenses),
    path("accounting/expenses/<int:pk>/", expense_api.expense_detail),
]
