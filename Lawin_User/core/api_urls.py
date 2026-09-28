from django.urls import path
from . import api

urlpatterns = [path("settings/welcome/", api.welcome_settings)]
