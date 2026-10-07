from django.db import models

from orders import constants as orders_constants


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
    # موجودی اولیه‌ی پول پیش از شروع ثبت در سیستم؛ پایه‌ی گزارش گردش نقدینگی.
    opening_cash = models.PositiveBigIntegerField("موجودی اولیه صندوق", default=0)
    opening_bank = models.PositiveBigIntegerField("موجودی اولیه بانک", default=0)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        """سطر تنظیمات را می‌خواند و اگر نبود می‌سازد (مسیرهای نوشتن)."""
        return cls.objects.get_or_create(pk=1)[0]

    @classmethod
    def current(cls):
        """تنظیمات فقط برای خواندن، بدون ساختن سطر.

        داشبورد و گزارش‌ها نباید با یک GET سطر جدید در دیتابیس بسازند؛ اگر سطری
        نبود، مقادیر پیش‌فرض (از جمله موجودی اولیه‌ی صفر) برگردانده می‌شود.
        """
        found = cls.objects.filter(pk=1).first()
        return found if found is not None else cls(pk=1)

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


class CafeStatus(models.Model):
    """وضعیت باز/بسته بودن کافه؛ فقط یک ردیف (pk=1) وجود دارد.

    منبع حقیقت وضعیت کافه همین‌جاست، نه در Flutter. پنل فقط می‌خواند و
    تغییر وضعیت را از همین‌جا از طریق API انجام می‌دهد؛ زمان باز/بسته شدن
    هم ثبت می‌شود تا داشبورد «شروع فعالیت» و «آخرین فعالیت» را نشان دهد.
    مثل CafeSettings، یک سطر بیشتر ساخته نمی‌شود.
    """
    is_open = models.BooleanField("باز است", default=False)
    opened_at = models.DateTimeField("زمان باز شدن", null=True, blank=True)
    closed_at = models.DateTimeField("زمان بسته شدن", null=True, blank=True)

    @classmethod
    def load(cls):
        """سطر وضعیت را می‌خواند و اگر نبود می‌سازد (مسیرهای نوشتن)."""
        return cls.objects.get_or_create(pk=1)[0]

    @classmethod
    def current(cls):
        """وضعیت فقط برای خواندن، بدون ساختن سطر.

        GET داشبورد نباید در دیتابیس سطر جدید بسازد؛ اگر هنوز باز/بسته
        نشده باشد، حالت پیش‌فرض (بسته، بدون زمان) برگردانده می‌شود.
        """
        found = cls.objects.filter(pk=1).first()
        return found if found is not None else cls(pk=1)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self): return "باز" if self.is_open else "بسته"


class SecuritySettings(models.Model):
    """رمز امنیتی بخش‌های مالی (حسابداری، هزینه‌ها، گزارش‌ها)؛ صرفاً یک ردیف.

    هرگز متن رمز ذخیره نمی‌شود؛ فقط هش Django (PBKDF2 پیش‌فرض) نگهداری می‌شود و
    `updated_at` برای باطل‌شدن فوری بلیت‌های امنیتیِ قبلاً صادرشده استفاده
    می‌شود. تا وقتی `security_hash` خالی است یعنی رمز هنوز تنظیم نشده.
    """
    security_hash = models.CharField(
        "هش رمز امنیتی", max_length=128, blank=True, default=""
    )
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    @classmethod
    def load(cls):
        """سطر تنظیمات امنیتی را می‌خواند و اگر نبود می‌سازد (مسیرهای نوشتن)."""
        return cls.objects.get_or_create(pk=1)[0]

    @classmethod
    def current(cls):
        """فقط برای خواندن، بدون ساختن سطر."""
        found = cls.objects.filter(pk=1).first()
        return found if found is not None else cls(pk=1)

    @property
    def is_configured(self):
        return bool(self.security_hash)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self): return "تنظیم شده" if self.is_configured else "تنظیم نشده"


class CashAccount(models.TextChoices):
    """حساب‌های پولی نقد. صندوق = وجه نقد دست، بانک = کارتخوان/کارت‌به‌کارت."""

    CASH = "cash", "صندوق (نقد)"
    BANK = "bank", "بانک / کارت"


class Expense(models.Model):
    """هزینه‌ی عملیاتی. «خرید مواد اولیه» دسته‌ی جداگانه ندارد؛ خرید از مسیر
    inventory/purchases ثبت می‌شود تا در سود و زیان دو بار شمرده نشود."""

    CATEGORIES = [(c, c) for c in (
        "salary", "rent", "water", "electricity", "gas", "internet",
        "repairs", "equipment", "advertising", "transport", "supplies", "other")]
    title = models.CharField(max_length=120)
    amount = models.PositiveBigIntegerField()
    category = models.CharField(max_length=20, choices=CATEGORIES, default="other")
    # از کدام حساب پولی پرداخت شده؛ برای گزارش گردش نقدینگی لازم است.
    account = models.CharField(
        max_length=5, choices=CashAccount.choices, default=CashAccount.CASH
    )
    date = models.DateTimeField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]


class JournalKind(models.TextChoices):
    """نوع رویداد مالی. هر مقدار یک بار از یک منبع مشخص ثبت می‌شود."""

    SALE = "sale", "فروش"
    COGS = "cogs", "بهای تمام‌شده"
    COGS_REVERSE = "cogs_rev", "برگشت بهای تمام‌شده"
    PURCHASE = "purchase", "خرید"
    WASTE = "waste", "ضایعات"
    EXPENSE = "expense", "هزینه"
    REFUND = "refund", "برگشت از فروش"
    # کالای موجود در انبار پیش از شروع ثبت در سیستم؛ فقط اثر موجودی دارد.
    OPENING_STOCK = "opening_stock", "موجودی اولیه"
    # تغییر قیمتِ کالای موجود؛ فقط ارزش انبار را هم‌تراز می‌کند، نه پول را.
    REVALUATION = "revaluation", "تجدید ارزش"


class LedgerAccount(models.TextChoices):
    """حساب‌های دفتر. دارایی‌ها بدهکار مثبت، حساب‌های نتیجه بستانکار مثبت."""

    CASH = "cash", "صندوق"
    BANK = "bank", "بانک"
    INVENTORY = "inventory", "موجودی انبار"
    REVENUE = "revenue", "درآمد فروش"
    COGS = "cogs", "بهای تمام‌شده"
    WASTE = "waste", "ضایعات"
    EXPENSE = "expense", "هزینه‌های عملیاتی"


class Side(models.TextChoices):
    DEBIT = "debit", "بدهکار"
    CREDIT = "credit", "بستانکار"


class JournalEntry(models.Model):
    """سرآیند یک رویداد مالی در دفتر دوطرفه؛ منبع واحد حقیقت مالی.

    قید یکتایی (kind, source_type, source_id) تضمین می‌کند یک رویداد مالی هرگز
    دوبار ثبت نشود؛ به همین دلیل پردازش دوباره‌ی یک سفارش یا خرید بی‌اثر است.
    """

    # ۱۵ تا برای جا شدن بلندترین مقدار یعنی "opening_stock" (۱۳ کاراکتر).
    kind = models.CharField(max_length=15, choices=JournalKind.choices)
    source_type = models.CharField(
        max_length=20
    )  # order | payment | purchase | waste | expense | item
    source_id = models.CharField(max_length=40)
    occurred_at = models.DateTimeField(db_index=True)
    title = models.CharField(max_length=160, blank=True)
    detail = models.CharField(max_length=240, blank=True)
    # روش پرداخت/حساب پولی برای گزارش تفکیک؛ فقط برای sale/refund/expense/purchase
    method = models.CharField(
        max_length=15, choices=orders_constants.PaymentMethod.choices, blank=True
    )
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="journal_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "source_type", "source_id"],
                name="uniq_journal_source",
            )
        ]


class JournalLine(models.Model):
    """یک سطر بدهکار/بستانکار. هر سرآیند باید متعادل باشد (جمع بدهکار = جمع بستانکار)."""

    entry = models.ForeignKey(
        JournalEntry, on_delete=models.CASCADE, related_name="lines"
    )
    account = models.CharField(max_length=10, choices=LedgerAccount.choices)
    side = models.CharField(max_length=6, choices=Side.choices)
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "account", "side"], name="uniq_journal_line"
            )
        ]
