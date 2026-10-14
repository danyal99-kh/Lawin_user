from django.urls import path
from rest_framework.routers import SimpleRouter

from . import api
from .api import CategoryViewSet, ProductViewSet

router = SimpleRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("products", ProductViewSet, basename="product")

urlpatterns = [
    path("offers/", api.offers),
    path("offers/preview/", api.offer_preview),
    path("offers/create/", api.offer_create),
    path("offers/<int:pk>/deactivate/", api.offer_deactivate),
] + router.urls
