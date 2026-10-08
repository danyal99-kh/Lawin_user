from django.conf import settings
from django.db import models

from core.models import CashAccount


class Debtor(models.Model):
    """بدهکارِ نسیه. معیار پیدا کردن «نام» است (تایپ + ساخت خودکار).

    نام بعد از نرمال‌سازی (فشردن فاصله‌ها) یکتاست تا «علی رضا» و «علی  رضا»
    یک بدهکار نشوند. هیچ‌وقت حذف نمی‌شود چون به Credit/CreditPayment وابسته است.
    """

    name = models.CharField("نام", max_length=80, unique=True)
    phone = models.CharField("تلفن", max_length=40, blank=True, default="")
    note = models.CharField("یادداشت", max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    @staticmethod
    def normalize(raw):
        """نام را برای ذخیره/جست‌وجو نرمال می‌کند: فشردن فاصله + برش طول."""
        return " ".join(str(raw or "").split())[:80]

    def __str__(self):
        return self.name


class Credit(models.Model):
    """یک طلب (نسیه) از یک بدهکار بابت یک سفارش.

    `order` با PROTECT است: سفارشِ نسیه‌ای نباید حذف شود چون تاریخچه‌ی مالی
    به آن وصل است. مبلغ اولیه ثابت می‌ماند؛ فقط `remaining_amount` با تسویه
    کم می‌شود و وقتی صفر شد نسیه بسته می‌شود.
    """

    class Status(models.TextChoices):
        OPEN = "open", "باز"
        SETTLED = "settled", "تسویه‌شده"
        REFUNDED = "refunded", "برگشت‌خورده"

    debtor = models.ForeignKey(
        Debtor, on_delete=models.PROTECT, related_name="credits"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="credits"
    )
    amount = models.PositiveBigIntegerField("مبلغ نسیه")
    remaining_amount = models.PositiveBigIntegerField("مانده")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        # قدیمی‌ترین نسیه اول تسویه می‌شود (oldest-first)؛ این ترتیب همان چیزی است
        # که سرویس تسویه هم روش تخصیص را بر اساسش می‌چیند.
        ordering = ["created_at", "id"]
        constraints = [
            # یک سفارش فقط یک نسیه دارد؛ جلوگیری از ثبت دوباره‌ی طلب یک سفارش.
            models.UniqueConstraint(
                fields=["order"], name="uniq_credit_per_order"
            ),
            models.CheckConstraint(
                check=models.Q(remaining_amount__lte=models.F("amount")),
                name="credit_remaining_lte_amount",
            ),
        ]

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def __str__(self):
        return f"{self.debtor.name} — {self.remaining_amount}/{self.amount}"


class CreditPayment(models.Model):
    """یک قسط وصول‌شده از یک نسیه. پول واقعاً وارد صندوق/بانک شده است.

    کلید `idempotency_key` تکرار درخواست تسویه را بی‌اثر می‌کند؛ چون یک تسویه
    می‌تواند چند نسیه را دربر بگیرد، یکتایی روی جفت (کلید, نسیه) است.
    """

    credit = models.ForeignKey(
        Credit, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.PositiveBigIntegerField("مبلغ")
    # از کدام حساب پولی وصول شد؛ برای گردش نقدینگی و برگشت احتمالی لازم است.
    account = models.CharField(
        max_length=5, choices=CashAccount.choices, default=CashAccount.CASH
    )
    note = models.CharField(max_length=200, blank=True, default="")
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key", "credit"],
                name="uniq_credit_payment_idem",
            ),
            # نسیه بدون پول تسویه نمی‌شود؛ ردیف صفری معنا ندارد.
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name="credit_payment_amount_gt_zero"
            ),
        ]

    @property
    def debtor(self):
        return self.credit.debtor

    @property
    def order(self):
        return self.credit.order

    def __str__(self):
        return f"{self.credit.debtor.name} +{self.amount}"
