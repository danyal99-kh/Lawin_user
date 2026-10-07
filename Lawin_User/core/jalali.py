"""ابزارهای تبدیل تاریخ میلادی و محاسبات بازه‌های روز/ماه شمسی."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

_GDM = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def to_jalali(gy, gm, gd):
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621

    gy2 = gy + 1 if gm > 2 else gy

    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + _GDM[gm - 1]
    )

    jy += 33 * (days // 12053)
    days %= 12053

    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30

    return jy, jm, jd


def jalali_day(d):
    """روزِ ماهِ شمسیِ یک date میلادی."""
    return to_jalali(d.year, d.month, d.day)[2]


def start_of_day(value):
    """
    شروع روز تقویمی بر اساس timezone فعال Django.

    ورودی می‌تواند `date` یا `datetime` باشد. خروجی یک datetime timezone-aware در
    ساعت 00:00 همان روز است.
    """
    if isinstance(value, datetime) and timezone.is_naive(value):
        value = timezone.make_aware(value)
    elif not isinstance(value, datetime):
        # `date` در میانه‌ی شب به وقت تهران تفسیر می‌شود تا ۰۰:۰۰ همان روز باشد.
        value = timezone.make_aware(
            datetime(value.year, value.month, value.day)
        )

    local_value = timezone.localtime(value)

    return local_value.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def start_of_jalali_month(value):
    """
    شروع ماه شمسیِ شامل value.

    مثال:
    اگر value در 1405/07/09 باشد،
    شروع بازه = 1405/07/01 ساعت 00:00.
    """
    if timezone.is_naive(value):
        value = timezone.make_aware(value)

    local_value = timezone.localtime(value)

    jy, jm, _ = to_jalali(
        local_value.year,
        local_value.month,
        local_value.day,
    )

    # پیدا کردن اولین روز میلادی متناظر با روز اول ماه شمسی.
    #
    # به جای پیاده‌سازی دوباره الگوریتم تبدیل معکوس،
    # از چند روز قبل شروع می‌کنیم و اولین تاریخی را پیدا می‌کنیم
    # که روز شمسی آن 1 و ماه آن jm باشد.
    current = local_value.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    for _ in range(32):
        y, m, d = to_jalali(
            current.year,
            current.month,
            current.day,
        )

        if y == jy and m == jm and d == 1:
            return current

        current -= timedelta(days=1)

    raise RuntimeError("Unable to determine start of Jalali month.")
