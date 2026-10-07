from django.urls import path
from . import api, dashboard, expense_api, accounting_api, security_api

urlpatterns = [
    path("settings/", api.cafe_settings),
    path("settings/welcome/", api.welcome_settings),
    path("cafe/status/", api.cafe_status),
    path("security/verify/", security_api.verify),
    path("security/password/", security_api.change_password),
    path("accounting/transactions/", accounting_api.transactions),
    path("accounting/verify/", accounting_api.verify),
    path("reports/", accounting_api.reports),
    path("reports/inventory/", accounting_api.inventory_report),
    path("reports/payment-methods/", accounting_api.payment_methods_report),
    path("reports/waste/", accounting_api.waste_report),
    path("reports/purchases/", accounting_api.purchase_report),
    path("dashboard/summary/", dashboard.summary),
    path("accounting/expenses/", expense_api.expenses),
    path("accounting/expenses/<int:pk>/", expense_api.expense_detail),
]
