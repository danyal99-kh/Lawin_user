from django.urls import path
from . import api, dashboard, expenses

urlpatterns = [
    path("settings/welcome/", api.welcome_settings),
    path("dashboard/summary/", dashboard.dashboard_summary),
    path("accounting/expenses/", expenses.expenses),
    path("accounting/expenses/<int:pk>/", expenses.expense_detail),
]
