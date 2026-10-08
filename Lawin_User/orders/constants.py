from django.db import models


class OrderStatus(models.TextChoices):
    NEW = "new", "ثبت شد"
    PREPARING = "preparing", "در حال آماده‌سازی"
    READY = "ready", "آماده شد"
    DELIVERED = "delivered", "تحویل داده شد"
    PAID = "paid", "پرداخت‌شده"
    CANCELLED = "cancelled", "لغو شده"


OPEN_STATUSES = [OrderStatus.NEW, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.DELIVERED]
NEXT_STATUS = {OrderStatus.NEW: OrderStatus.PREPARING, OrderStatus.PREPARING: OrderStatus.READY,
               OrderStatus.READY: OrderStatus.DELIVERED}


class PaymentMethod(models.TextChoices):
    CASH = "cash", "نقدی"
    CARD_READER = "card_reader", "کارتخوان"
    CARD_TRANSFER = "card_transfer", "کارت‌به‌کارت"
    # نسیه: پول هنوز گرفته نشده؛ در دفتر «طلب» (بدهکاران) ثبت می‌شود و فقط
    # لحظه‌ی تسویه به صندوق/بانک می‌نشیند. جزء روش‌های معتبر پرداخت است.
    CREDIT = "credit", "نسیه"


class OrderSource(models.TextChoices):
    ADMIN = "admin", "ادمین"
    CUSTOMER = "customer", "مشتری"
