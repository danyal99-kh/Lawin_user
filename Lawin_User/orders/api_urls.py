from django.urls import path
from . import api

urlpatterns = [
    path("orders/", api.orders),
    path("orders/changes/", api.orders_changes),
    path("orders/<uuid:pk>/", api.order_detail),
    path("orders/<uuid:pk>/status/", api.order_status),
    path("orders/<uuid:pk>/pay/", api.order_pay),
    path("orders/<uuid:pk>/refund/", api.order_refund),
    path("orders/<uuid:pk>/bar-printed/", api.order_bar_printed),
]
