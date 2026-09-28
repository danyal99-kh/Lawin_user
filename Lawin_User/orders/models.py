import uuid

from django.conf import settings
from django.db import models

from .constants import OPEN_STATUSES, OrderSource, OrderStatus, PaymentMethod


class Order(models.Model):
    """Table → TableSession → Order → OrderItem → Payment"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(unique=True)
    source = models.CharField(max_length=10, choices=OrderSource.choices, default=OrderSource.CUSTOMER)
    table = models.ForeignKey("tables.Table", on_delete=models.PROTECT, related_name="orders")
    session = models.ForeignKey("tables.TableSession", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=10, choices=OrderStatus.choices, default=OrderStatus.NEW, db_index=True)
    payment_status = models.CharField(max_length=10, default="unpaid")  # unpaid | paid
    payment_method = models.CharField(max_length=15, choices=PaymentMethod.choices, null=True, blank=True)
    customer_note = models.CharField(max_length=200, blank=True)
    # کلید نشست مرورگر مشتری؛ دسترسی مشتری فقط به سفارش‌های همین کلید است.
    customer_key = models.CharField(max_length=40, blank=True, db_index=True)
    total = models.PositiveBigIntegerField(default=0)  # فقط در Backend محاسبه می‌شود
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    bar_printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", null=True, on_delete=models.SET_NULL)
    product_name = models.CharField(max_length=80)      # snapshot
    unit_price = models.PositiveIntegerField()          # snapshot: قیمت واقعی از دیتابیس
    quantity = models.PositiveSmallIntegerField()
    note = models.CharField(max_length=120, blank=True)

    @property
    def line_total(self): return self.unit_price * self.quantity


class Payment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="payment")
    method = models.CharField(max_length=15, choices=PaymentMethod.choices)
    amount = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
