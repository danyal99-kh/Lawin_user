from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.authtoken.models import Token

from catalog.models import Category, Product
from core.models import Expense, WelcomeMessage
from inventory.models import InventoryItem, Recipe, RecipeItem
from tables.models import Table

CATS = ["قهوه", "چای", "نوشیدنی سرد", "کیک", "غذا", "دسر"]
PRODUCTS = [  # (نام، دسته، قیمت، توضیح)
    ("لاته", 0, 95000, "اسپرسو دوبل با شیر بخارداده و فوم نرم"), ("کاپوچینو", 0, 90000, "اسپرسو، شیر و فوم غلیظ"),
    ("اسپرسو", 0, 65000, ""), ("موکا", 0, 105000, "اسپرسو، شیر و شکلات"),
    ("چای ماسالا", 1, 70000, ""), ("چای سیاه", 1, 45000, ""),
    ("آیس‌آمریکانو", 2, 85000, ""), ("لیموناد", 2, 75000, ""),
    ("چیزکیک", 3, 120000, "چیزکیک نیویورکی با سس توت‌فرنگی"), ("کیک شکلاتی", 3, 110000, ""),
    ("ساندویچ مرغ", 4, 145000, ""), ("تیرامیسو", 5, 130000, "تیرامیسوی ایتالیایی با قهوه و کاکائو"),
    ("براونی", 5, 95000, ""),
]
INVENTORY = {  # نام: (واحد، موجودی، حداقل، قیمت هر واحد)
    "دانه‌ی قهوه اسپرسو": ("g", 4200, 2000, 900), "شیر": ("ml", 3500, 10000, 55), "شکر": ("g", 6000, 2000, 40),
    "سیروپ کارامل": ("ml", 300, 500, 120), "پودر کاکائو": ("g", 0, 500, 300), "چای سیاه": ("g", 2500, 1000, 220),
    "لیوان کاغذی": ("piece", 250, 100, 2500), "خامه": ("ml", 4000, 1500, 150),
}
RECIPES = {"لاته": {"دانه‌ی قهوه اسپرسو": 18, "شیر": 200, "شکر": 5}, "کاپوچینو": {"دانه‌ی قهوه اسپرسو": 18, "شیر": 120},
           "اسپرسو": {"دانه‌ی قهوه اسپرسو": 18}, "چای ماسالا": {"چای سیاه": 6}, "چای سیاه": {"چای سیاه": 4}}


class Command(BaseCommand):
    help = "داده‌ی نمونه برای توسعه (هم‌راستا با MockDatabase پنل Flutter) و ساخت کاربر ادمین."

    def handle(self, *a, **kw):
        cats = [Category.objects.get_or_create(name=n, defaults={"sort_order": i})[0] for i, n in enumerate(CATS)]
        for i, (name, c, price, desc) in enumerate(PRODUCTS):
            Product.objects.get_or_create(category=cats[c], name=name,
                                          defaults={"price": price, "description": desc, "sort_order": i})
        inv = {n: InventoryItem.objects.get_or_create(name=n, defaults={
            "unit": u, "current_stock": Decimal(s), "min_stock": Decimal(m), "unit_cost": Decimal(c)})[0]
            for n, (u, s, m, c) in INVENTORY.items()}
        for pname, parts in RECIPES.items():
            recipe, _ = Recipe.objects.get_or_create(product=Product.objects.get(name=pname))
            for iname, q in parts.items():
                RecipeItem.objects.get_or_create(recipe=recipe, inventory_item=inv[iname], defaults={"quantity": Decimal(q)})
        for n in range(1, 11):
            Table.objects.get_or_create(number=n)
        WelcomeMessage.load()
        if not Expense.objects.exists():
            Expense.objects.create(title="خرید شیر", amount=850000, category="raw_materials", date=timezone.now() - timedelta(hours=3))
        if settings.DEBUG:
            U = get_user_model()
            u, made = U.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True})
            if made:
                u.set_password("admin12345"); u.save()
            self.stdout.write(f"ادمین: admin / admin12345 — توکن API: {Token.objects.get_or_create(user=u)[0].key}")
        self.stdout.write(self.style.SUCCESS("انجام شد. لینک QR میزها: python manage.py make_qr"))
