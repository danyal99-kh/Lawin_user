import os
from io import BytesIO

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
