from datetime import datetime, timedelta

from django.utils import timezone


def gregorian_to_jalali(gy, gm, gd):
    """الگوریتم ۳۳ ساله؛ همان پیاده‌سازی Dart در Flutter."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
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
        + g_d_m[gm - 1]
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


def start_of_day(dt=None):
    """شروع روز به وقت تهران (aware)."""
    dt = timezone.localtime(dt or timezone.now())
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def start_of_jalali_month(dt=None):
    """ساعت ۰۰:۰۰ روز اول ماه شمسی جاری، به وقت تهران."""
    day = start_of_day(dt)
    _, _, jd = gregorian_to_jalali(day.year, day.month, day.day)
    # با تاریخ ساده کم می‌کنیم تا تغییر ساعت تابستانی/زمستانی مشکلی نسازد
    first = day.date() - timedelta(days=jd - 1)
    return timezone.make_aware(datetime(first.year, first.month, first.day))
