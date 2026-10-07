from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from catalog.models import Category, Product
from inventory.models import InventoryItem, Recipe, RecipeItem
from tables.models import Table


def make_world():
    cat = Category.objects.create(name="قهوه")
    latte = Product.objects.create(category=cat, name="لاته", price=95000)
    cake = Product.objects.create(category=cat, name="کیک", price=110000)  # بدون Recipe
    off = Product.objects.create(
        category=cat, name="موکا", price=105000, is_active=False
    )
    milk = InventoryItem.objects.create(
        name="شیر", unit="ml", current_stock=Decimal(1000), min_stock=100
    )
    beans = InventoryItem.objects.create(
        name="قهوه", unit="g", current_stock=Decimal(500), min_stock=50
    )
    r = Recipe.objects.create(product=latte)
    RecipeItem.objects.create(recipe=r, inventory_item=milk, quantity=Decimal(200))
    RecipeItem.objects.create(recipe=r, inventory_item=beans, quantity=Decimal(18))
    tables = [Table.objects.create(number=n) for n in (2, 5, 8)]
    admin = get_user_model().objects.create_user("admin", password="x", is_staff=True)
    token = Token.objects.create(user=admin)
    return SimpleNamespace(
        cat=cat,
        latte=latte,
        cake=cake,
        off=off,
        milk=milk,
        beans=beans,
        tables=tables,
        t2=tables[0],
        t5=tables[1],
        t8=tables[2],
        admin=admin,
        token=token.key,
    )


def make_financial_world():
    """world + رمز امنیتی و یک بلیت مالیِ معتبر برای تست‌های گیت‌شده.

    تست‌های حسابداری/هزینه‌ها/دفتر به هدر `X-Security-Ticket` نیاز دارند؛ اینجا
    رمزِ تست مستقیم هش می‌شود (وارد endpoint رمز نمی‌شود) تا تست‌ها مستقل از
    جریان ورود امنیتی باشند.
    """
    from django.contrib.auth.hashers import make_password

    from core.models import SecuritySettings
    from core.security import issue_ticket

    w = make_world()
    s = SecuritySettings.load()
    s.security_hash = make_password("dev-secret")
    s.save()
    w.security_ticket = issue_ticket(w.admin.id)
    return w


def scan(client, table):
    """شبیه‌سازی اسکن QR."""
    return client.get(f"/menu/table/{table.public_token}/")
