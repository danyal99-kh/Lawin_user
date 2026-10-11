#!/usr/bin/env bash
# ============================================================================
# Lawin — آماده‌سازی اولیه‌ی لپ‌تاپ کافه (Ubuntu)
# این اسکریپت باید یک‌بار و با کاربر root/sudo اجرا شود. idempotent است.
#
# پیش‌نیاز: کد پروژه از قبل در APP_DIR کپی شده باشد (روش انتقال در README).
# هیچ رمزی چاپ نمی‌شود؛ مقادیر حساس مستقیماً داخل .env تولید می‌شوند.
# ============================================================================
set -euo pipefail

APP_USER="lawin"
APP_GROUP="lawin"
APP_DIR="/opt/lawin/app"
VENV_DIR="/opt/lawin/venv"
DB_NAME="lawin"
DB_USER="lawin"
APT_PACKAGES=(
  python3 python3-venv python3-dev build-essential libpq-dev
  postgresql postgresql-contrib nginx git curl ca-certificates
)

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[هشدار] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[خطا] %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "این اسکریپت را با sudo اجرا کنید."

log "۱/۸ نصب بسته‌های سیستم"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"

log "۲/۸ ساخت کاربر سرویس اختصاصی (بدون shell ورود)"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/opt/lawin" --shell /usr/sbin/nologin "$APP_USER"
else
  warn "کاربر $APP_USER از قبل وجود دارد."
fi

log "۳/۸ ساخت مسیرهای برنامه"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 755 /opt/lawin
install -d -o "$APP_USER" -g "$APP_GROUP" -m 755 "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 750 "$APP_DIR/media"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 750 "$APP_DIR/staticfiles"

[[ -f "$APP_DIR/manage.py" ]] || die "کد پروژه در $APP_DIR نیست. ابتدا کد را منتقل کنید (بخش انتقال در README)."

log "۴/۸ ساخت محیط مجازی و نصب وابستگی‌ها"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
fi
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/deploy/requirements.lock"

log "۵/۸ ساخت/بررسی فایل محیطی .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/deploy/env.production.example" "$APP_DIR/.env"
  SECRET="$(sudo -u "$APP_USER" "$VENV_DIR/bin/python" -c 'import secrets;print(secrets.token_urlsafe(64))')"
  DBPASS="$(sudo -u "$APP_USER" "$VENV_DIR/bin/python" -c 'import secrets;print(secrets.token_urlsafe(24))')"
  # جای‌گذاری مقادیر حساس بدون چاپ در خروجی
  sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=${SECRET}|" "$APP_DIR/.env"
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${DBPASS}|" "$APP_DIR/.env"
  chown "$APP_USER:$APP_GROUP" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  warn "فایل .env ساخته شد. مقادیر DJANGO_ALLOWED_HOSTS و PUBLIC_BASE_URL را بعداً پر کنید."
  warn "رمز دیتابیس به‌صورت خودکار تولید و داخل .env ذخیره شد (چاپ نشد)."
else
  warn ".env از قبل وجود دارد؛ دست‌نخورده ماند."
fi
# خواندن رمز دیتابیس از .env برای ساخت role
# shellcheck disable=SC1091
DBPASS="$(grep -E '^POSTGRES_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2-)"
[[ -n "$DBPASS" ]] || die "POSTGRES_PASSWORD در .env خالی است."

log "۶/۸ ساخت کاربر و دیتابیس PostgreSQL"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DBPASS}';
  ELSE
    ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DBPASS}';
  END IF;
END
\$\$;
SQL
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
fi

log "۷/۸ اجرای Migration و گردآوری فایل‌های استاتیک"
# settings.py خودش فایل .env کنار manage.py را می‌خواند، پس نیازی به export دستی نیست.
sudo -u "$APP_USER" "$VENV_DIR/bin/python" "$APP_DIR/manage.py" migrate --noinput
sudo -u "$APP_USER" "$VENV_DIR/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

log "۸/۸ نصب سرویس‌های systemd و Nginx"
install -m 644 "$APP_DIR/deploy/systemd/lawin.service"          /etc/systemd/system/lawin.service
install -m 644 "$APP_DIR/deploy/systemd/lawin-backup.service"   /etc/systemd/system/lawin-backup.service
install -m 644 "$APP_DIR/deploy/systemd/lawin-backup.timer"     /etc/systemd/system/lawin-backup.timer
install -m 644 "$APP_DIR/deploy/nginx/lawin.conf"               /etc/nginx/sites-available/lawin.conf
ln -sf /etc/nginx/sites-available/lawin.conf /etc/nginx/sites-enabled/lawin.conf
[[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
systemctl daemon-reload
nginx -t
systemctl enable --now nginx
systemctl enable --now lawin.service
systemctl enable --now lawin-backup.timer

cat <<'NEXT'

============================================================
مراحل باقی‌مانده (دستی):
  1) ویرایش /opt/lawin/app/.env و پرکردن:
       DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_ORIGINS, PUBLIC_BASE_URL
  2) ساخت کاربر مدیریت اولیه:
       sudo -u lawin /opt/lawin/venv/bin/python /opt/lawin/app/manage.py createsuperuser
  3) فعال‌سازی دسترسی اینترنتی امن (Tailscale Funnel یا Cloudflare Tunnel)
       → جزئیات در deploy/README.md
  4) بعد از تنظیم دامنه/هاست:  sudo systemctl restart lawin
  5) بررسی سلامت:  systemctl status lawin nginx --no-pager
============================================================
NEXT
