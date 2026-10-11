#!/usr/bin/env bash
# ============================================================================
# Lawin — پشتیبان‌گیری از دیتابیس PostgreSQL و فایل‌های رسانه‌ای
# اجرا توسط: lawin-backup.service / lawin-backup.timer  (یا دستی)
# خروجی: /opt/lawin/backups/lawin_<TIMESTAMP>/{db.dump, media.tar.gz, MANIFEST.txt}
# هیچ رمز/توکنی داخل بسته‌ی پشتیبان نوشته نمی‌شود (فقط داده‌ی دیتابیس و media).
# ============================================================================
set -euo pipefail

APP_DIR="/opt/lawin/app"
BACKUP_ROOT="/opt/lawin/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
ENV_FILE="$APP_DIR/.env"

log() { printf '[backup] %s\n' "$*"; }
die() { printf '[backup][خطا] %s\n' "$*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || die ".env یافت نشد: $ENV_FILE"

# خواندن متغیرهای دیتابیس از .env (بدون چاپ)
get_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }
export PGHOST="$(get_env POSTGRES_HOST)"; PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="$(get_env POSTGRES_PORT)"; PGPORT="${PGPORT:-5432}"
export PGUSER="$(get_env POSTGRES_USER)"; PGUSER="${PGUSER:-lawin}"
export PGPASSWORD="$(get_env POSTGRES_PASSWORD)"
DB_NAME="$(get_env POSTGRES_DB)"; DB_NAME="${DB_NAME:-lawin}"
[[ -n "$PGPASSWORD" ]] || die "POSTGRES_PASSWORD خالی است."

TS="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_ROOT/lawin_$TS"
mkdir -p "$DEST"
chmod 700 "$BACKUP_ROOT" "$DEST"

log "پشتیبان‌گیری دیتابیس «$DB_NAME»"
pg_dump --format=custom --no-owner --no-privileges --file="$DEST/db.dump" "$DB_NAME"

log "پشتیبان‌گیری فایل‌های رسانه‌ای"
if [[ -d "$APP_DIR/media" ]]; then
  tar -czf "$DEST/media.tar.gz" -C "$APP_DIR" media
else
  log "پوشه‌ی media وجود ندارد؛ رد شد."
fi

log "ساخت MANIFEST و بررسی سلامت"
{
  echo "created_at=$TS"
  echo "db=$DB_NAME"
  pg_restore --list "$DEST/db.dump" >/dev/null 2>&1 && echo "db_dump=OK" || echo "db_dump=UNVERIFIED"
  [[ -f "$DEST/media.tar.gz" ]] && tar -tzf "$DEST/media.tar.gz" >/dev/null 2>&1 && echo "media_archive=OK" || echo "media_archive=NA"
  du -sh "$DEST" | cut -f1 | sed 's/^/size=/'
} > "$DEST/MANIFEST.txt"

chmod -R go-rwx "$DEST"

log "پاک‌سازی بکاپ‌های قدیمی‌تر از $RETENTION_DAYS روز"
find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'lawin_*' -mtime "+$RETENTION_DAYS" -exec rm -rf {} + 2>/dev/null || true

log "پایان موفق. مسیر: $DEST"

# --- انتقال خارج از لپ‌تاپ کافه (اختیاری اما توصیه‌شده) ---
# یک نسخه در سیستم دیگری نگه دارید. مثال با rsync روی شبکه‌ی محلی:
#   rsync -a --delete "$DEST/" user@backup-host:/srv/lawin-backups/
# برای مقصد خارجی امن از کلید SSH محدود یا تونل استفاده کنید.
