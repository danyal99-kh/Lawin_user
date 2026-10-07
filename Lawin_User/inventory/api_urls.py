from django.urls import path
from . import api

urlpatterns = [
    path("inventory/items/", api.items),
    path("inventory/items/<int:pk>/", api.item_detail),
    path("inventory/purchases/", api.purchases),
    path("inventory/purchases/<int:pk>/", api.purchase_detail),
    path("inventory/wastes/", api.wastes),
    path("inventory/wastes/<int:pk>/", api.waste_detail),
]
