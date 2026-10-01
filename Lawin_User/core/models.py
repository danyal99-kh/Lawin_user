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


class CafeSettings(models.Model):
    """تنظیمات کلی کافه (پنل Flutter)؛ فقط یک ردیف (pk=1) وجود دارد.

    پیام خوشامدگویی عمداً اینجا نیست و مدل جداگانه‌ی خودش را دارد
    (WelcomeMessage)، چون صفحه‌ی خوشامدگویی مشتری جداگانه نمایش/غیرفعال
    می‌شود و قرارداد API آن از قبل وجود دارد.
    """
    name = models.CharField("نام کافه", max_length=120, default="کافه‌کتاب")
    address = models.TextField("آدرس", max_length=400, blank=True, default="")
    phone = models.CharField("شماره تماس", max_length=40, blank=True, default="")
    receipt_note = models.TextField("یادداشت پایین رسید", max_length=150,
                                    blank=True, default="")
    auto_print = models.BooleanField("چاپ خودکار سفارش بار", default=True)
    low_stock_alert = models.BooleanField("هشدار موجودی کم در داشبورد", default=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self): return self.name


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
