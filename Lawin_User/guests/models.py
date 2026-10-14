from django.conf import settings
from django.db import models


class Guest(models.Model):
    """یک ثبت مهمان‌نوازی برای یک سفارش (پذیرایی رایگان).

    هر سفارش حداکثر یک ثبت مهمان دارد (قید یکتایی `order`). «نام» تنها چیزی
    است که از مهمان نگه می‌داریم و گروه‌بندی مهمان‌ها بر پایه‌ی نامِ نرمال‌شده
    انجام می‌شود. چون فقط نام ذخیره می‌شود، دو نام یکسان لزوماً یک شخص حقیقی
    نیستند و سیستم چنین ادعایی نمی‌کند؛ گروه‌بندی صرفاً برای جمع‌زدن هزینه‌ی
    پذیراییِ ثبت‌شده با همان نام است.

    مبلغ مهمان‌نوازی هیچ اثری روی صندوق/بانک یا طلب ندارد؛ فقط سند هزینه در
    دفتر ثبت می‌شود (نگاه کنید به `core.ledger.post_guest`).
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "فعال"
        CANCELLED = "cancelled", "لغوشده"

    # حداکثر طولی که در core.errors هم برای نام بدهکار استفاده می‌شود.
    MAX_NAME = 80

    name = models.CharField("نام مهمان", max_length=MAX_NAME, db_index=True)
    order = models.OneToOneField(
        "orders.Order", on_delete=models.PROTECT, related_name="guest"
    )
    amount = models.PositiveBigIntegerField("مبلغ مهمان‌نوازی")
    # وضعیتی که سفارش پیش از مهمان‌شدن داشت؛ فقط برای ردگیری/تاریخچه.
    previous_status = models.CharField(max_length=10, blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # یک سفارش فقط یک ثبت مهمان دارد؛ از شمارش دوباره جلوگیری می‌کند.
            models.UniqueConstraint(fields=["order"], name="uniq_guest_per_order"),
        ]

    @staticmethod
    def normalize(raw):
        """نام مهمان را برای ذخیره/گروه‌بندی نرمال می‌کند: فشردن فاصله + برش."""
        return " ".join(str(raw or "").split())[: Guest.MAX_NAME]

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def __str__(self):
        return f"{self.name} — {self.amount}"
