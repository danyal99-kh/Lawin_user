from django.db import models


class Counter(models.Model):
    """شمارنده‌ی اتمیک (مثلاً شماره‌ی سفارش)."""
    name = models.CharField(max_length=40, primary_key=True)
    value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next(cls, name, start=1000):
        """باید داخل transaction.atomic صدا زده شود."""
        obj, _ = cls.objects.select_for_update().get_or_create(name=name, defaults={"value": start})
        obj.value += 1
        obj.save(update_fields=["value"])
        return obj.value


class WelcomeMessage(models.Model):
    """تنظیم صفحه‌ی خوشامدگویی؛ فقط یک ردیف (pk=1) وجود دارد."""
    title = models.CharField("عنوان", max_length=120, default="به کافه‌کتاب خوش آمدید ☕📚")
    message = models.TextField("متن", max_length=400,
                               default="لحظه‌ای برای خودتان، یک فنجان برای حالتان.")
    enabled = models.BooleanField("فعال", default=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self): return self.title


class Expense(models.Model):
    """هزینه؛ مقادیر category با ExpenseCategory پنل Flutter یکی است."""
    CATEGORIES = [(c, c) for c in (
        "raw_materials", "salary", "rent", "water", "electricity", "gas", "internet",
        "repairs", "equipment", "advertising", "transport", "supplies", "other")]
    title = models.CharField(max_length=120)
    amount = models.PositiveBigIntegerField()
    category = models.CharField(max_length=20, choices=CATEGORIES, default="other")
    date = models.DateTimeField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]
