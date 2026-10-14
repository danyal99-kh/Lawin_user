import os
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from PIL import Image, ImageOps


class Category(models.Model):
    name = models.CharField(max_length=40, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name_plural = "categories"

    def __str__(self): return self.name


class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True)
    price = models.PositiveIntegerField(help_text="تومان")
    image = models.ImageField(upload_to="products/", blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["category", "name"], name="uniq_product_name_in_category")]

    def save(self, *args, **kwargs):
        f = self.image
        if f and not getattr(f, "_committed", True):  # فقط آپلود جدید: کوچک‌سازی و JPEG
            img = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
            img.thumbnail((800, 800))
            buf = BytesIO()
            img.save(buf, "JPEG", quality=82, optimize=True)
            name = os.path.splitext(os.path.basename(f.name))[0] + ".jpg"
            f.save(name, ContentFile(buf.getvalue()), save=False)
        super().save(*args, **kwargs)

    def __str__(self): return self.name


class Offer(models.Model):
    """آفر درصدی روی یک محصول یا کل یک دسته‌بندی.

    قیمت پایه‌ی `Product.price` هرگز تغییر نمی‌کند؛ قیمت مؤثر در
    `catalog.pricing` از زنجیره‌ی آفرهای فعال ساخته می‌شود. هر آفر یک ردیف
    مستقل است و دقیقاً یک‌بار اعمال می‌شود، پس تخفیف دوبار حساب نمی‌شود و با
    تغییر دسته‌بندی، زنجیره خودکار بازمحاسبه می‌شود.

    ترتیب اعمال آفرها قطعی است: بر اساس `(created_at, id)`. آفر جدید (که دیرتر
    ساخته شده) روی قیمت تخفیف‌خورده‌ی فعلی می‌نشیند.

    وضعیت مدیریتی (`is_active`) از اعتبار زمانی (`starts_at`/`ends_at`) جداست؛
    اعتبار فقط در لحظه‌ی محاسبه در سرور ارزیابی می‌شود، نه با scheduler.
    """

    MAX_PERCENT = 99  # ۱۰۰٪ مجاز نیست: سفارش با مبلغ صفر قابل پرداخت نیست.

    class Scope(models.TextChoices):
        PRODUCT = "product", "محصول"
        CATEGORY = "category", "دسته‌بندی"

    scope = models.CharField(max_length=10, choices=Scope.choices)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.CASCADE, related_name="offers"
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.CASCADE, related_name="offers"
    )
    percent = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    # کلید یکتای اختیاری کلاینت برای جلوگیری از ثبت دوباره‌ی یک آفر.
    idempotency_key = models.CharField(max_length=64, blank=True, null=True, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="offers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(percent__gte=1, percent__lte=99),
                name="offer_percent_1_99",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope="product", product__isnull=False, category__isnull=True)
                    | models.Q(scope="category", category__isnull=False, product__isnull=True)
                ),
                name="offer_scope_target_consistent",
            ),
        ]

    def __str__(self):
        target = self.product.name if self.product_id else (self.category.name if self.category_id else "?")
        return f"{target} ‑ {self.percent}٪"
