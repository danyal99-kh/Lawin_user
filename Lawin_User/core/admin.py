from django.contrib import admin
from .models import Expense, WelcomeMessage

admin.site.register(WelcomeMessage)
admin.site.register(Expense)
