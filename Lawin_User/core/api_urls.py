from django.urls import path
from . import api, dashboard, expense_api, accounting_api

urlpatterns = [
    path("settings/welcome/", api.welcome_settings),
    path("accounting/transactions/", accounting_api.transactions),
    path("reports/", accounting_api.reports),
    path("dashboard/summary/", dashboard.summary),
    path("accounting/expenses/", expense_api.expenses),
    path("accounting/expenses/<int:pk>/", expense_api.expense_detail),
]
