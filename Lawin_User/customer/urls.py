from django.urls import path
from . import views

app_name = "customer"
urlpatterns = [
    path("menu/table/<str:token>/", views.enter, name="enter"),
    path("welcome/", views.welcome, name="welcome"),
    path("menu/", views.menu, name="menu"),
    path("api/customer/menu/", views.api_menu),
    path("api/customer/orders/", views.api_orders),
    path("api/customer/orders/<uuid:pk>/", views.api_order_detail),
    path("api/customer/waiter/", views.api_waiter),
]
