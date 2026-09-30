from django.urls import path
from . import recipe_api

urlpatterns = [
    path("recipes/", recipe_api.recipes),
    path("products/<int:pk>/recipe/", recipe_api.product_recipe),
]
