# Lawin — راهنمای استقرار روی لپ‌تاپ کافه (Ubuntu)

این سند، کل مسیر انتقال و راه‌اندازی «قطعی» سرویس Lawin روی لپ‌تاپ کافه را
توضیح می‌دهد؛ طوری که لپ‌تاپ شخصی توسعه‌دهنده دیگر برای سرویس‌دهی لازم نباشد.

---

## ۱. معماری نهایی

```
اندروید مشتری ─┐
               ├─ (HTTP/HTTPS عمومی) ─→ [Tunnel: Tailscale Funnel / Cloudflare]
اپ Flutter ────┘                                   │  HTTPS (TLS)
                                                   ▼
                                       Nginx  127.0.0.1:8080
                                       (static + media + reverse proxy)
                                                   │
                                                   ▼
                              Daphne (ASGI)  127.0.0.1:8000
                              Django + Channels (REST + WebSocket)
                                                   │
                                                   ▼
                              PostgreSQL  127.0.0.1:5432  (فقط local)
```

- **Daphne** تنها سرور برنامه است (نه `runserver`). به یک سوکت محلی گوش می‌دهد.
- **Nginx** فایل‌های استاتیک/رسانه را سرو و REST/WebSocket را پروکسی می‌کند.
- **PostgreSQL** فقط روی `127.0.0.1` و هرگز در معرض اینترنت نیست.
- **Tunnel** لایه‌ی TLS و ورودی عمومی را بدون Port Forwarding و بدون نیاز به IP
  ثابت فراهم می‌کند (مناسب اینترنت کافه که ممکن است CGNAT باشد).

---

## ۲. پیش‌نیازهای سیستم کافه

- Ubuntu 24.04 / 26.04 LTS (یا جدیدتر)، معماری x86_64
- حداقل ۲ هسته CPU، ۴GB RAM، ۲۰GB دیسک آزاد
- دسترسی `sudo` برای نصب اولیه
- اینترنت فعال (برای نصب بسته‌ها و تونل)

نصب پیش‌نیازها خودکار انجام می‌شود (`bootstrap.sh`): Python، venv، build-essential،
libpq-dev، PostgreSQL، Nginx، git، curl.

---

## ۳. انتقال کد (دو روش)

### روش الف — Git (اصلی، وقتی اینترنت/GitHub در دسترس است)

```bash
# روی لپ‌تاپ کافه
sudo mkdir -p /opt/lawin/app && sudo chown "$USER" /opt/lawin/app
git clone https://github.com/danyal99-kh/Lawin_user.git /opt/lawin/app
cd /opt/lawin/app && git log --oneline -1       # تأیید Commit
```
به‌روزرسانی در آینده:
```bash
cd /opt/lawin/app && git fetch --all && git status   # قبل از Pull تغییرات محلی را ببینید
git pull --ff-only origin main
sudo deploy/scripts/update.sh
```
بازگشت به نسخه‌ی قبل: `git checkout <commit-قبلی>` سپس `sudo deploy/scripts/update.sh`.

> ⚠️ فایل `lawin_database.sql` (دامپ دیتای توسعه) در مخزن commit شده است. **قبل از
> عمومی‌شدن مخزن** آن را از Git خارج کنید: `git rm --cached lawin_database.sql`
> و به `.gitignore` بیفزایید.

### روش ب — بسته‌ی فشرده (جایگزین، بدون اینترنت)

```bash
# روی سیستم توسعه (فقط کد و فایل‌های لازم؛ بدون .env/دیتابیس/media)
git -C /home/danyal/Projects/Lawin_user/Lawin_User archive \
    --format=tar.gz -o /tmp/lawin-<commit>.tar.gz HEAD
# انتقال: scp /tmp/lawin-<commit>.tar.gz cafe@<ip>:/tmp/
# روی لپ‌تاپ کافه:
sudo mkdir -p /opt/lawin/app
sudo tar -xzf /tmp/lawin-<commit>.tar.gz -C /opt/lawin/app
```
بازگشت: بسته‌ی نسخه‌ی قبلی را دوباره باز کنید و `update.sh` را اجرا کنید.
فایل‌های رسانه‌ای (`media/`) جداگانه با `rsync` منتقل می‌شوند (بخش ۸).

---

## ۴. راه‌اندازی اولیه (یک‌بار)

```bash
cd /opt/lawin/app
sudo deploy/scripts/bootstrap.sh
```
این اسکریپت: بسته‌ها را نصب، کاربر سرویس `lawin` را می‌سازد، venv و وابستگی‌های
قفل‌شده را نصب، `.env` را با `SECRET_KEY` و رمز دیتابیسِ تصادفی می‌سازد، دیتابیس و
کاربر PostgreSQL را ایجاد، Migration و collectstatic را اجرا، و سرویس‌ها
(`lawin.service`, `lawin-backup.timer`, Nginx) را فعال می‌کند.

سپس `.env` را ویرایش و این‌ها را پر کنید:
```
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,<هاست-تونل>
DJANGO_CSRF_ORIGINS=https://<هاست-تونل>
PUBLIC_BASE_URL=https://<هاست-تونل>
```
و کاربر مدیریت اولیه و QRها:
```bash
sudo -u lawin /opt/lawin/venv/bin/python /opt/lawin/app/manage.py createsuperuser
sudo -u lawin /opt/lawin/venv/bin/python /opt/lawin/app/manage.py make_qr
sudo systemctl restart lawin
```
> هیچ داده‌ای از دیتابیس توسعه منتقل نمی‌شود؛ دیتابیس عملیاتی تازه و خالی است.

---

## ۵. دسترسی اینترنتی (بدون دامنه)

سه گزینه؛ **گزینه‌ی ۱ توصیه می‌شود چون دامنه لازم ندارد و URL پایدار می‌دهد.**

### گزینه ۱ — Tailscale Funnel (توصیه‌شده، بدون دامنه، HTTPS پایدار)
یک URL ثابت `https://<machine>.<tailnet>.ts.net/` می‌دهد؛ مناسب چاپ QR.
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg --https=443 http://127.0.0.1:8080
sudo tailscale funnel --bg 443 on       # انتشار عمومی برای اینترنت موبایل
tailscale funnel status                 # دیدن URL نهایی
```
- TLS معتبر توسط Tailscale مدیریت می‌شود (به Nginx دست نزنید).
- سرویس `tailscaled` خودش با systemd بالا می‌آید؛ نیازی به unit جدا نیست.
- در `.env` هاستِ `*.ts.net` را در `DJANGO_ALLOWED_HOSTS` و `PUBLIC_BASE_URL` بگذارید.

### گزینه ۲ — Cloudflare Tunnel (وقتی دامنه دارید)
قالب سرویس در `deploy/systemd/cloudflared.service` موجود است. نیازمند دامنه‌ی
متصل به Cloudflare و tunnel نام‌دار. مزیت: دامنه‌ی برند خودتان.

### گزینه ۳ — IP عمومی + Port Forwarding (فقط اگر ISP اجازه دهد)
نیازمند IP ثابت (یا DDNS)، عدم وجود CGNAT، باز کردن فقط پورت ۴۴۳ روی مودم و
گواهی TLS (certbot). ریسک امنیتی بالاتر؛ در صورت امکان از تونل استفاده کنید.

### الزامات امنیتی (همه‌ی گزینه‌ها)
- PostgreSQL هرگز مستقیماً منتشر نشود.
- SSH و پنل مدیریت (admin/`) به‌صورت عمومی/ناامن منتشر نشوند.
- فقط HTTPS عمومی باشد؛ HTTP به HTTPS هدایت شود.
- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` دقیقاً مطابق دامنه تنظیم شوند.
- CORS فقط در صورت نیاز و هرگز به‌جای احراز هویت.

---

## ۶. اپ Flutter (Linux و Android)

آدرس API از `--dart-define=API_BASE_URL` در زمان Build تزریق می‌شود (هیچ آدرسی در
کد hardcode نیست).

### Linux
```bash
cd ~/Projects/Lawin/lawin
flutter build linux --release --dart-define=API_BASE_URL=https://<هاست-تونل>
# خروجی: build/linux/x64/release/bundle/
```
اجرا روی لپ‌تاپ کافه (پک کردن bundle و اجرای فایل `lawin`).

### Android
```bash
flutter build apk --release --dart-define=API_BASE_URL=https://<هاست-تونل>
# خروجی: build/app/outputs/flutter-apk/app-release.apk
```
نصب روی دستگاه: `adb install -r app-release.apk` یا انتقال فایل و نصب دستی
(«نصب از منابع نامعلوم»). برای به‌روزرسانی، همان APK جدید را با `-r` نصب کنید.

> ⚠️ **دو اصلاح لازم که در همین پروژه اعمال شد:** Manifest نسخه‌ی release فاقد
> مجوز `INTERNET` بود و برای HTTP محلی (قبل از HTTPS) نیز تنظیم cleartext نداشت.
> هر دو در `android/app/src/main/AndroidManifest.xml` و
> `network_security_config.xml` اصلاح شده‌اند. برای انتشار نهایی حتماً کلید امضای
> release بسازید (فعلاً با کلید debug امضا می‌شود) و `applicationId` را از
> `com.example.lawin` تغییر دهید.

### QR میزها
```bash
sudo -u lawin /opt/lawin/venv/bin/python /opt/lawin/app/manage.py make_qr
# برای ابطال QRهای قدیمی و تولید توکن جدید:  make_qr --rotate
```
QRها در `media/qr/table-<شماره>.png` ساخته می‌شوند و فقط توکن امن میز را در خود
دارند (نه توکن ادمین). **قبل از چاپ، هر QR را با Wi-Fi کافه و اینترنت موبایل
واقعاً تست کنید.**

---

## ۷. مدیریت سرویس‌ها و لاگ‌ها

```bash
sudo systemctl status lawin nginx --no-pager
sudo systemctl restart lawin        # راه‌اندازی مجدد بک‌اند
sudo journalctl -u lawin -f         # لاگ زنده‌ی بک‌اند
sudo journalctl -u lawin --since "1 hour ago"
sudo tail -f /var/log/nginx/error.log
sudo systemctl list-timers lawin-backup.timer
```
هر سه سرویس (lawin، nginx، tailscaled) با `systemctl enable` پس از ری‌استارت
لپ‌تاپ خودکار بالا می‌آیند.

---

## ۸. بکاپ و بازیابی

- اسکریپت: `deploy/scripts/backup.sh` (دیتابیس + media + MANIFEST، با مجوز 700).
- زمان‌بندی: `lawin-backup.timer` هر شب ساعت ۰۳:۳۰ (Asia/Tehran).
- نگهداری: پیش‌فرض ۱۴ روز (`BACKUP_RETENTION_DAYS`).
- بازیابی: `deploy/scripts/restore.sh <مسیر-بسته>` (تأیید صریح با تایپ `RESTORE`).

```bash
sudo systemctl start lawin-backup.service    # اجرای دستی بکاپ
sudo journalctl -u lawin-backup.service -n 20
ls -la /opt/lawin/backups/
```
**حداقل یک نسخه خارج از لپ‌تاپ کافه نگه دارید** (rsync به سیستم دوم یا فضای
ابری، از طریق کانال امن). بکاپ را تا زمانی که بازیابی‌اش را عملاً آزمایش
نکرده‌اید «موفق» تلقی نکنید.

---

## ۹. عیب‌یابی سریع

| نشانه | بررسی |
|---|---|
| اپ به سرور وصل نمی‌شود | `curl -I https://<host>/api/v1/` و بررسی `ALLOWED_HOSTS` |
| خطای 400 DisallowedHost | هاست تونل در `DJANGO_ALLOWED_HOSTS` نیست |
| 403 روی POST | `CSRF_TRUSTED_ORIGINS` یا نبود `X-Forwarded-Proto` |
| WebSocket قطع می‌شود | بلوک `location /ws/` در Nginx و `Connection upgrade` |
| تصویر محصول بالا نمی‌رود | `client_max_body_size`، مجوز پوشه‌ی `media` |
| صفحه‌ی مشتری بدون فونت | اتصال اینترنت برای CDN فونت Vazirmatn (بخش ریسک‌ها) |
| سرویس بالا نمی‌آید | `journalctl -u lawin -n 50` (معمولاً `.env` ناقص) |

---

## ۱۰. چک‌لیست تست نهایی

- [ ] `manage.py check --deploy` بدون هشدار بحرانی
- [ ] Migrationها بدون خطای حل‌نشده
- [ ] ورود ادمین معتبر/نامعتبر (رد صحیح)
- [ ] ثبت سفارش مشتری از QR (Wi-Fi و اینترنت موبایل جداگانه)
- [ ] نمایش منو/تصاویر، تغییر وضعیت سفارش، Call Waiter
- [ ] عملیات حسابداری/انبار (پس از ورود رمز امنیتی)
- [ ] HTTPS معتبر و هدایت HTTP→HTTPS
- [ ] راه‌اندازی خودکار پس از ری‌استارت لپ‌تاپ
- [ ] بکاپ گرفته و بازیابی آن آزمایش شده
- [ ] قطع/وصل اینترنت: رفتار برنامه بررسی شده
