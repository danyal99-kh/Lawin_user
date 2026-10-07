"""هم‌ترازی خودکار دفتر حسابداری با هر مسیر نوشتن.

قانون کلیدی سیستم: «هیچ تغییر مالی نباید بدون اثر حسابداری اتفاق بیفتد». اگر فقط
لایه‌ی سرویس این اثر را ثبت کند، هر مسیر نوشتن دیگری (پنل ادمین، پوسته‌ی
manage.py، اسکریپت، تست) می‌تواند Expense را عوض کند بی‌آنکه دفتر به‌روز شود.

برای همین هر ذخیره/حذف Expense اینجا دوباره در دفتر نوشته می‌شود. چون
`ledger.post` با قید یکتایی `uniq_journal_source` کار می‌کند، ثبت دوباره بی‌اثر
و بی‌خطر است و `replace=True` تضمین می‌کند ویرایش مبلغ/حساب/تاریخ هم درست اعمال
شود. همچنین داخل `transaction.atomic` است تا نوشتن دفتر با نوشتن هزینه یک‌جا
برگردد.

Purchase/Waste عمداً سیگنال ندارند: مبلغشان از فرزندان (InventoryTransactionLine)
می‌آید و سیگنالِ والد پیش از ذخیره‌ی فرزندان اجرا می‌شد. آن‌ها فقط از طریق
`inventory.services` نوشته می‌شوند و برای جلوگیری از دور زدن، در پنل ادمین
فقط‌خواندنی شده‌اند (`inventory/admin.py`).
"""

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import ledger
from .models import Expense


@receiver(post_save, sender=Expense, dispatch_uid="ledger_sync_expense")
def _sync_expense(sender, instance, **kwargs):
    with transaction.atomic():
        ledger.post_expense(expense=instance, replace=True)


@receiver(post_delete, sender=Expense, dispatch_uid="ledger_unpost_expense")
def _unsync_expense(sender, instance, **kwargs):
    with transaction.atomic():
        ledger.remove(kind="expense", source_type="expense", source_id=instance.pk)
