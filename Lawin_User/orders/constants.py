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
    # مهمان: سفارش به‌عنوان پذیرایی رایگان بسته می‌شود. این یک «روش پرداخت»
    # پولی نیست: هیچ ردیف Payment، هیچ ورود به صندوق/بانک و هیچ طلبی ثبت
    # نمی‌شود؛ فقط سند هزینه‌ی مهمان‌نوازی در دفتر می‌آید. چون در UI کنار
    # بقیه‌ی روش‌ها نمایش داده می‌شود اینجا فهرست شده، ولی مسیر تسویه‌ی
    # پولی (`_split_lines`/`_settle`) عمداً آن را رد می‌کند.
    GUEST = "guest", "مهمان"


class OrderSource(models.TextChoices):
    ADMIN = "admin", "ادمین"
    CUSTOMER = "customer", "مشتری"
