"""سرویس مرکزی قیمت‌گذاری آفرها.

قانون‌های این ماژول:

- قیمت پایه‌ی محصول (`Product.price`) هرگز تغییر نمی‌کند؛ فقط قیمت مؤثر محاسبه
  می‌شود.
- زنجیره‌ی آفرهای فعالِ قابل اعمال روی یک محصول بر اساس `(created_at, id)` مرتب
  می‌شود. پس آفر جدید (که دیرتر ساخته شده) روی قیمت تخفیف‌خورده‌ی فعلی می‌نشیند.
- هر آفر یک ردیف مستقل است و دقیقاً یک‌بار اعمال می‌شود؛ پس تخفیف دوبار حساب
  نمی‌شود و با جابه‌جایی محصول بین دسته‌بندی‌ها، زنجیره خودکار بازمحاسبه می‌شود.
- درصدها جمع نمی‌شوند؛ متوالی و ضربی روی قیمت مرحله‌ی قبل اعمال می‌شوند.

این تنها منبع محاسبه‌ی قیمت مؤثر است؛ منوی مشتری، ثبت سفارش، پیش‌نمایش و
فعال‌سازی همه از همین‌جا استفاده می‌کنند تا هیچ‌جا منطق تکراری نداشته باشیم.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Offer

MAX_PERCENT = Offer.MAX_PERCENT


def _round_money(value: Decimal) -> int:
    """گردکردن مبلغ به تومان صحیح، نیم‌بها به بالا (ROUND_HALF_UP)."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_percent(amount: int, percent: int) -> int:
    """یک درصد را روی یک مبلغ اعمال می‌کند.

    گردکردن در هر مرحله انجام می‌شود تا نتیجه در همه‌ی مسیرها یکسان باشد.
    """
    if percent <= 0:
        return int(amount)
    factor = Decimal(100 - percent) / Decimal(100)
    return _round_money(Decimal(int(amount)) * factor)


def is_within_window(offer: Offer, now) -> bool:
    """آیا پنجره‌ی زمانی آفر شامل لحظه‌ی `now` است؟"""
    if offer.starts_at is not None and offer.starts_at > now:
        return False
    if offer.ends_at is not None and offer.ends_at < now:
        return False
    return True


def is_valid(offer: Offer, now=None) -> bool:
    """آفر «الان» معتبر است: فعال مدیریتی + داخل پنجره‌ی زمانی."""
    now = now or timezone.now()
    return bool(offer.is_active) and is_within_window(offer, now)


def offers_for_category(category, now=None, *, include_inactive=False):
    """آفرهای دسته‌ایِ یک دسته‌بندی (مرتب‌شده)."""
    now = now or timezone.now()
    rows = [
        o for o in category.offers.all()
        if include_inactive or is_valid(o, now)
    ]
    rows.sort(key=lambda o: (o.created_at, o.id))
    return rows


def active_offers_for(product, now=None) -> list[Offer]:
    """آفرهای فعالِ قابل اعمال روی یک محصول: آفرهای خودِ محصول + آفرهای دسته.

    چون `product.offers` و `product.category.offers` می‌توانند prefetch شده
    باشند، این تابع در فهرست منو کوئری اضافه نمی‌سازد.
    """
    now = now or timezone.now()
    rows = [o for o in product.offers.all() if is_valid(o, now)]
    category = product.category
    rows += [o for o in category.offers.all() if is_valid(o, now)]
    rows.sort(key=lambda o: (o.created_at, o.id))
    return rows


def quote(amount: int, offers, *, base: int | None = None) -> dict:
    """زنجیره‌ی آفرها را روی یک مبلغ اعمال و نتیجه را توصیف می‌کند.

    `base` برای محاسبه‌ی «درصد کل نسبت به قیمت اصلی» استفاده می‌شود.
    """
    base = int(amount) if base is None else int(base)
    final = int(amount)
    for offer in offers:
        final = apply_percent(final, offer.percent)
    if final < 0:
        final = 0
    total_percent = 0 if base <= 0 else _round_money(
        Decimal(base - final) * Decimal(100) / Decimal(base)
    )
    return {
        "base": base,
        "final": final,
        "saving": base - final,
        "total_percent": total_percent,
        "percentages": [o.percent for o in offers],
        "offer": offers[-1] if offers else None,  # آخرین آفر اعمال‌شده (جدیدترین)
    }


def quote_product(product, now=None) -> dict:
    """قیمت مؤثر یک محصول با کل زنجیره‌ی آفرهای فعال."""
    return quote(
        int(product.price),
        active_offers_for(product, now),
        base=int(product.price),
    )


def effective_price(product, now=None) -> int:
    """فقط عدد قیمت مؤثر؛ برای مسیر ثبت سفارش."""
    return quote_product(product, now)["final"]


def _iso(value):
    return value.isoformat() if value else None


def fingerprint(scope, items, percent, starts_at, ends_at) -> str:
    """اثر انگشت ورودی‌های مؤثر بر پیش‌نمایش.

    `items` فهرست دیکشنری‌های (product_id, base, before) است. اگر بین پیش‌نمایش
    و تأیید، قیمت پایه یا ترکیب آفرهای مؤثر تغییر کند، اثر انگشت عوض می‌شود و
    تأیید رد می‌شود تا قیمت متفاوتی بی‌اطلاع مدیر فعال نشود.
    """
    payload = {
        "scope": scope,
        "percent": int(percent),
        "starts_at": _iso(starts_at),
        "ends_at": _iso(ends_at),
        "items": sorted(items, key=lambda i: i["product_id"]),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _product_items(product, percent, now):
    q = quote_product(product, now)
    after = apply_percent(q["final"], percent)
    return {
        "product_id": product.id,
        "product_name": product.name,
        "base": q["base"],
        "before": q["final"],
        "final": after,
        "saving": q["base"] - after,
        "current_percent": q["total_percent"],
    }


def preview_product(product, percent, starts_at, ends_at, now=None) -> dict:
    """پیش‌نمایش آفر یک محصول؛ هیچ آفر فعلی را تغییر نمی‌دهد."""
    now = now or timezone.now()
    item = _product_items(product, percent, now)
    return {
        "scope": Offer.Scope.PRODUCT,
        "percent": percent,
        "starts_at": _iso(starts_at),
        "ends_at": _iso(ends_at),
        "products": [item],
        "product_count": 1,
        "fingerprint": fingerprint(
            Offer.Scope.PRODUCT, [{"product_id": product.id, "base": item["base"],
                                   "before": item["before"]}],
            percent, starts_at, ends_at,
        ),
    }


def preview_category(category, percent, starts_at, ends_at, now=None) -> dict:
    """پیش‌نمایش آفر دسته‌ای: فهرست محصولات فعال دسته با قیمت قبل و بعد."""
    now = now or timezone.now()
    products = list(
        category.products.filter(is_active=True)
        .select_related("category")
        .prefetch_related("offers", "category__offers")
        .order_by("sort_order", "id")
    )
    items = [_product_items(p, percent, now) for p in products]
    return {
        "scope": Offer.Scope.CATEGORY,
        "percent": percent,
        "starts_at": _iso(starts_at),
        "ends_at": _iso(ends_at),
        "products": items,
        "product_count": len(items),
        "fingerprint": fingerprint(
            Offer.Scope.CATEGORY,
            [{"product_id": i["product_id"], "base": i["base"], "before": i["before"]}
             for i in items],
            percent, starts_at, ends_at,
        ),
    }
