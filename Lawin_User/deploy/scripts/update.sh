#!/usr/bin/env bash
# ============================================================================
# Lawin — انتشار نسخه‌ی جدید (Update / Redeploy)
# پیش‌فرض: کد جدید از قبل در APP_DIR قرار گرفته (git pull یا کپی) است.
# این اسکریپت وابستگی‌ها را هم‌گام، Migration را اجرا، استاتیک را جمع و سرویس
# را بی‌وقفه راه‌اندازی مجدد می‌کند. با sudo اجرا شود.
# ============================================================================
set -euo pipefail

APP_USER="lawin"
APP_DIR="/opt/lawin/app"
VENV_DIR="/opt/lawin/venv"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
[[ $EUID -eq 0 ]] || { echo "با sudo اجرا کنید." >&2; exit 1; }

log "پشتیبان سریع قبل از به‌روزرسانی (اختیاری)"
sudo -u "$APP_USER" "$APP_DIR/deploy/scripts/backup.sh" || \
  printf '\033[1;33m[هشدار] پشتیبان‌گیری ممکن نشد؛ ادامه می‌دهیم.\033[0m\n'

log "هم‌گام‌سازی وابستگی‌ها"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/deploy/requirements.lock"

log "اجرای Migration"
sudo -u "$APP_USER" "$VENV_DIR/bin/python" "$APP_DIR/manage.py" migrate --noinput

log "گردآوری فایل‌های استاتیک"
sudo -u "$APP_USER" "$VENV_DIR/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

log "بررسی تنظیمات Production"
sudo -u "$APP_USER" "$VENV_DIR/bin/python" "$APP_DIR/manage.py" check --deploy || true

log "راه‌اندازی مجدد سرویس‌ها"
systemctl restart lawin.service
systemctl reload nginx
systemctl --no-pager --lines=5 status lawin.service || true

log "پایان به‌روزرسانی."
