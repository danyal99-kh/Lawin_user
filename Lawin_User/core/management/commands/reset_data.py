from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Expense, Counter
from inventory.models import InventoryTransaction
from orders.models import Order, OrderItem, Payment
from tables.models import Table, TableSession
from waiter_calls.models import WaiterCall


class Command(BaseCommand):
    help = "پاک کردن سفارش‌ها، پرداخت‌ها، هزینه‌ها، خرید/ضایعات و نشست‌ها (محصول، دسته، کالا و میز می‌مانند)"

    def add_arguments(self, p):
        p.add_argument("--yes", action="store_true", help="بدون پرسش تأیید")
        p.add_argument(
            "--reset-stock", action="store_true", help="موجودی همه‌ی کالاها را صفر کند"
        )

    @transaction.atomic
    def handle(self, *a, yes=False, reset_stock=False, **kw):
        if (
            not yes
            and input("همه‌ی سفارش‌ها/هزینه‌ها/خرید/ضایعات پاک می‌شود. ادامه؟ (yes) ")
            != "yes"
        ):
            self.stdout.write("لغو شد.")
            return

        WaiterCall.objects.all().delete()
        Payment.objects.all().delete()
        InventoryTransaction.objects.all().delete()  # خرید + ضایعات + مصرف سفارش
        OrderItem.objects.all().delete()
        Order.objects.all().delete()
        TableSession.objects.all().delete()
        Expense.objects.all().delete()
        Counter.objects.filter(name="order").delete()  # شماره‌ی سفارش از اول
        Table.objects.exclude(status="empty").update(status="empty")

        if reset_stock:
            from inventory.models import InventoryItem

            InventoryItem.objects.update(current_stock=0)

        self.stdout.write(self.style.SUCCESS("انجام شد."))
