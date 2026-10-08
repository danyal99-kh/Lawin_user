"""منطق نسیه (Accounts Receivable): ساخت طلب، وصول (تسویه) و برگشت.

سه قانون این ماژول:

1. **درآمد لحظه‌ی ثبت نسیه شناسایی می‌شود** — ورودی فروش را `orders._settle`
   با `post_sale(method="credit")` می‌زند و حساب «بدهکاران» بدهکار می‌شود.
   پول واقعی فقط لحظه‌ی تسویه وارد صندوق/بانک می‌شود.
2. **تسویه قدیمی‌ترین نسیه اول** (oldest-first) تا مانده‌ی بدهکاران همیشه با
   جمع مانده‌ی نسیه‌ها بخواند (همان چیزی که `ledger.health()` چک می‌کند).
3. **بدون حذف**: نسیه و پرداختش هرگز پاک نمی‌شوند؛ برگشت سفارش وضعیت را
   `refunded` می‌کند و اثرش را در دفتر خنثا می‌کند.

همه‌ی عملیات داخل تراکنش‌اند و قفل ردیفی دارند؛ مبلغِ بیش از مانده هرگز
پذیرفته نمی‌شود (اصل «بیش‌پرداخت ممنوع»).
"""

from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone

from core import events, ledger
from core.errors import Conflict, Invalid, NotFound
from core.models import CashAccount

from .models import Credit, CreditPayment, Debtor
from .serializers import credit_dict, payment_dict

MAX_AMOUNT = 10**12 - 1
MAX_NOTE = 200
ACCOUNTS = {c for c, _ in CashAccount.choices}


def _clean_amount(raw):
    if isinstance(raw, bool):
        raise Invalid("مبلغ تسویه را درست وارد کنید.")
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int):
        raise Invalid("مبلغ تسویه را درست وارد کنید.")
    if raw <= 0:
        raise Invalid("مبلغ تسویه باید بیشتر از صفر باشد.")
    if raw > MAX_AMOUNT:
        raise Invalid("مبلغ واردشده معتبر نیست.")
    return raw


def _clean_note(raw):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise Invalid("یادداشت نامعتبر است.")
    return raw.strip()[:MAX_NOTE]


# ----------------------------------------------------------------- ساخت طلب
@transaction.atomic
def create_credit(*, order, debtor_name, amount, user=None):
    """یک نسیه برای سفارش می‌سازد. نام بدهکار لازم است و خودکار ساخته می‌شود.

    فقط از `orders._settle` صدا زده می‌شود؛ ورودی فروش را همان‌جا `post_sale`
    ثبت کرده، پس اینجا دست به دفتر نمی‌زنیم تا دوباره‌ثبت نشود.
    """
    name = Debtor.normalize(debtor_name)
    if not name:
        raise Invalid("نام بدهکار را وارد کنید.")
    amount = _clean_amount(amount)
    if Credit.objects.filter(order=order).exists():
        raise Conflict("برای این سفارش قبلاً نسیه ثبت شده است.")

    debtor, _created = Debtor.objects.get_or_create(name=name)
    credit = Credit.objects.create(
        debtor=debtor,
        order=order,
        amount=amount,
        remaining_amount=amount,
        created_by=user if getattr(user, "pk", None) else None,
    )
    events.publish_admin(events.CREDIT_CREATED, credit_dict(credit))
    return credit


def get_or_create_debtor(*, name, phone="", note=""):
    """بدهکار را با نام نرمال‌شده پیدا می‌کند یا می‌سازد (تایپ + ساخت خودکار)."""
    clean = Debtor.normalize(name)
    if not clean:
        raise Invalid("نام بدهکار را وارد کنید.")
    return Debtor.objects.get_or_create(
        name=clean, defaults={"phone": str(phone or "")[:40],
                              "note": str(note or "")[:MAX_NOTE]}
    )[0]


# ------------------------------------------------------------------- تسویه
def _open_credits_for(*, debtor_id=None, credit_id=None):
    """نسیه‌های بازِ مشخص‌شده، قفل‌شده و به ترتیب قدیمی‌ترین اول.

    `remaining_amount__gt=0` عمداً هست: نسیه‌ای که مانده‌اش صفر است «پرداخت
    نشدنی» است و اگر وارد حلقه شود ردیف صفری می‌سازد که قید دیتابیس آن را رد
    می‌کند. چنین ردیفی را همان `health()` با کد `credit_status_mismatch` گزارش
    می‌کند.
    """
    qs = Credit.objects.select_for_update().filter(
        status=Credit.Status.OPEN, remaining_amount__gt=0
    )
    if credit_id is not None:
        qs = qs.filter(pk=credit_id)
    elif debtor_id is not None:
        qs = qs.filter(debtor_id=debtor_id)
    else:
        raise Invalid("بدهکار یا نسیه را مشخص کنید.")
    return list(qs.select_related("debtor", "order").order_by("created_at", "id"))


def _idempotent(key):
    """اگر این کلید قبلاً تسویه شده باشد، همان نتیجه‌ی قبلی برمی‌گردد."""
    if not key:
        return None
    done = list(
        CreditPayment.objects.filter(idempotency_key=key)
        .select_related("credit", "credit__debtor", "credit__order")
        .order_by("created_at", "id")
    )
    return done or None


@transaction.atomic
def settle(*, amount, account=CashAccount.CASH, debtor_id=None, credit_id=None,
           user=None, idempotency_key=None, note=""):
    """وصول نسیه: از قدیمی‌ترین نسیه‌ی باز شروع و به ترتیب کم می‌کند.

    مجموعِ بازِ بدهکار باید ≥ مبلغ باشد؛ بیش‌پرداخت رد می‌شود. قفل ردیفی +
    به‌روزرسانی شرطی `remaining_amount__gte` باعث می‌شود دو تسویه‌ی همزمان
    هرگز یک مانده را دو بار خرج نکنند (حتی روی SQLite که قفل ردیفی ندارد).
    """
    amount = _clean_amount(amount)
    account = str(account or CashAccount.CASH)
    if account not in ACCOUNTS:
        raise Invalid("حساب پرداخت نامعتبر است.")
    key = str(idempotency_key or "").strip()[:64] or None
    note = _clean_note(note)

    already = _idempotent(key)
    if already is not None:
        return _result(already)

    credits = _open_credits_for(debtor_id=debtor_id, credit_id=credit_id)
    if not credits:
        raise Conflict("نسیه بازی برای این بدهکار وجود ندارد.")
    if credit_id is not None and debtor_id is not None:
        if credits[0].debtor_id != debtor_id:
            raise Invalid("این نسیه متعلق به آن بدهکار نیست.")

    total_open = sum(c.remaining_amount for c in credits)
    if amount > total_open:
        raise Conflict(
            f"مبلغ تسویه ({amount:,}) از مانده‌ی نسیه ({total_open:,}) بیشتر است."
        )

    payments, left = [], amount
    for c in credits:
        if left <= 0:
            break
        take = min(left, c.remaining_amount)
        # به‌روزرسانی شرطی: اگر همزمان مانده کمتر شده باشد، هیچ ردیفی نمی‌خورد
        # و کل تراکنش برمی‌گردد؛ پس هرگز بیش از مانده وصول نمی‌شود.
        locked = Credit.objects.filter(
            pk=c.pk, remaining_amount__gte=take
        ).update(remaining_amount=F("remaining_amount") - take)
        if not locked:
            raise Conflict("مانده‌ی نسیه همزمان تغییر کرد؛ دوباره تلاش کنید.")
        c.remaining_amount -= take

        p = CreditPayment.objects.create(
            credit=c, amount=take, account=account, note=note,
            idempotency_key=key,
            created_by=user if getattr(user, "pk", None) else None,
        )
        # Dr صندوق/بانک ، Cr بدهکاران — پول واقعاً وارد شد.
        ledger.post_credit_payment(
            payment=p, amount=take, account=account, occurred_at=p.created_at
        )
        payments.append(p)
        left -= take

    settled = _close_settled(credits)

    payload = {
        "payments": [payment_dict(p) for p in payments],
        "credits": [credit_dict(c) for c in {p.credit_id: p.credit for p in payments}.values()],
        "total": amount,
        "debtor_id": credits[0].debtor_id,
        "debtor_name": credits[0].debtor.name,
    }
    events.publish_admin(events.CREDIT_PAYMENT_CREATED, payload)
    for c in settled:
        events.publish_admin(events.CREDIT_SETTLED, credit_dict(c))
    return payload


def _close_settled(credits):
    """نسیه‌هایی که مانده‌شان صفر شد را می‌بندد و برمی‌گرداندشان."""
    closed = []
    for c in credits:
        if c.remaining_amount == 0 and c.status == Credit.Status.OPEN:
            # شرط روی وضعیت، جلوی باز شدن دوباره‌ی نسیه‌ی همزمان را می‌گیرد.
            if Credit.objects.filter(
                pk=c.pk, status=Credit.Status.OPEN
            ).update(status=Credit.Status.SETTLED):
                c.status = Credit.Status.SETTLED
                closed.append(c)
    return closed


def _result(payments):
    """خروجی یک‌نواخت برای مسیر idempotent (همان فرمت `settle`)."""
    credits = {p.credit_id: p.credit for p in payments}
    return {
        "payments": [payment_dict(p) for p in payments],
        "credits": [credit_dict(c) for c in credits.values()],
        "total": sum(p.amount for p in payments),
        "debtor_id": payments[0].credit.debtor_id if payments else None,
        "debtor_name": payments[0].credit.debtor.name if payments else None,
    }


# ------------------------------------------------------------------- برگشت
@transaction.atomic
def refund_credits_for_order(order, *, occurred_at=None):
    """برگشت یک سفارش: طلبِ باز آن نسیه را صفر و هر تسویه‌اش را برمی‌گرداند.

    تسویه‌ی قبلاً وصول‌شده باید به حسابی که آمده بود برگردد، وگرنه با
    `post_refund(method="credit")` (که کل طلب را از بدهکاران کم می‌کند)
    مانده‌ی بدهکاران منفی می‌شد.
    """
    when = occurred_at or timezone.now()
    affected = list(
        Credit.objects.select_for_update()
        .filter(order=order)
        .exclude(status=Credit.Status.REFUNDED)
        .select_related("debtor")
    )
    for c in affected:
        for p in c.payments.all():
            ledger.reverse_credit_payment(
                payment=p, amount=p.amount, account=p.account, occurred_at=when
            )
        Credit.objects.filter(pk=c.pk).update(
            status=Credit.Status.REFUNDED, remaining_amount=0
        )
        c.status = Credit.Status.REFUNDED
        c.remaining_amount = 0
        events.publish_admin(events.CREDIT_SETTLED, credit_dict(c))
    return affected


# ------------------------------------------------------------------- خواندن
def debtor_totals(qs=None):
    """بدهکارها را با جمع‌های مالی‌شان برمی‌گرداند (یک کوئری)."""
    rows = (
        (qs or Debtor.objects.all())
        .annotate(
            debt=Sum(
                "credits__remaining_amount",
                filter=Q(credits__status=Credit.Status.OPEN),
            ),
            open_credits=Count(
                "credits", filter=Q(credits__status=Credit.Status.OPEN)
            ),
            extended=Sum("credits__amount"),
            last_activity=Max("credits__created_at"),
        )
        .order_by("name")
    )
    return rows


def debtor_totals_for(debtor):
    """جمع‌های یک بدهکار؛ برای صفحه‌ی جزئیات.

    `debtor_totals().get()` ردیف مدل را با صفت‌های annotation برمی‌گرداند، پس
    با صفت خوانده می‌شود نه با کلید.
    """
    agg = debtor_totals(Debtor.objects.filter(pk=debtor.pk)).get()
    return {
        "debt": agg.debt or 0,
        "open_credits": agg.open_credits or 0,
        "extended": agg.extended or 0,
        "last_activity": agg.last_activity,
    }
