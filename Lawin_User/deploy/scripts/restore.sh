#!/usr/bin/env bash
# ============================================================================
# Lawin — بازیابی از پشتیبان (برای محیط آزمایشی یا Disaster Recovery)
#
# استفاده:
#   sudo -u lawin deploy/scripts/restore.sh /opt/lawin/backups/lawin_20260101_033000
#
# هشدار: بازیابیِ دیتابیس روی دیتابیسِ فعال، داده‌های فعلی را بازنویسی می‌کند.
# این اسکریپت به‌صراحت تأیید می‌خواهد و پیش‌فرض روی بازیابی «غیرمخرب» به یک
# دیتابیس موقت برای آزمون صحت است.
# ============================================================================
set -euo pipefail

APP_DIR="/opt/lawin/app"
ENV_FILE="$APP_DIR/.env"
SRC="${1:-}"
[[ -n "$SRC" && -d "$SRC" ]] || { echo "مسیر بسته‌ی پشتیبان را بدهید." >&2; exit 2; }
[[ -f "$SRC/db.dump" ]] || { echo "db.dump در بسته نیست." >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo ".env یافت نشد." >&2; exit 2; }

get_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }
export PGHOST="$(get_env POSTGRES_HOST)"; PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="$(get_env POSTGRES_PORT)"; PGPORT="${PGPORT:-5432}"
export PGUSER="$(get_env POSTGRES_USER)"; PGUSER="${PGUSER:-lawin}"
export PGPASSWORD="$(get_env POSTGRES_PASSWORD)"
DB_NAME="$(get_env POSTGRES_DB)"; DB_NAME="${DB_NAME:-lawin}"

echo "این عملیات داده‌های دیتابیس «$DB_NAME» را بازنویسی می‌کند."
read -r -p "برای ادامه کلمه  RESTORE  را تایپ کنید: " ans
[[ "$ans" == "RESTORE" ]] || { echo "لغو شد."; exit 1; }

echo "==> بازیابی دیتابیس"
pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DB_NAME" "$SRC/db.dump"

if [[ -f "$SRC/media.tar.gz" ]]; then
  echo "==> بازیابی فایل‌های رسانه‌ای (بکاپ قبلی media جابه‌جا می‌شود)"
  ts="$(date +%Y%m%d_%H%M%S)"
  [[ -d "$APP_DIR/media" ]] && mv "$APP_DIR/media" "$APP_DIR/media.bak_$ts"
  tar -xzf "$SRC/media.tar.gz" -C "$APP_DIR"
fi

echo "==> بازیابی انجام شد. برای اطمینان:"
echo "    systemctl restart lawin && systemctl status lawin --no-pager"
echo "    # سپس در محیط آزمایشی یک ورود/سفارش واقعی را بررسی کنید."
