# کافه‌کتاب — Backend (Django) + بخش مشتری

Customer (Django Templates + JS) ⇄ Django (مرجع اصلی) ⇄ WebSocket ⇄ Flutter Admin

## اجرا
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py makemigrations core accounts catalog tables inventory orders waiter_calls
python manage.py migrate
python manage.py seed_demo        # داده‌ی نمونه + ادمین admin/admin12345 + توکن API (فقط DEBUG)
python manage.py make_qr          # media/qr/table-N.png  (PUBLIC_BASE_URL را تنظیم کنید)
python manage.py runserver        # daphne (ASGI) + WebSocket
python manage.py test             # تست‌ها (سناریوهای ۱ تا ۸)
```
مسیر مشتری: `/menu/table/<توکن-QR>/` → `/welcome/` → `/menu/`.
Production: PostgreSQL (`POSTGRES_DB`…) و Redis (`REDIS_URL`) الزامی است؛ SQLite قفل ردیف ندارد.

## معماری
| اپ | مسئولیت |
|---|---|
| core | شمارنده‌ی سفارش، WelcomeMessage، Expense، رویدادها (`events.py`)، Consumerها، قالب خطا |
| tables | Table، TableSession، قانون باز/بسته‌شدن نشست (`services.py`) |
| catalog | Category، Product (کوچک‌سازی تصویر)، دسترسی‌پذیری |
| inventory | InventoryItem، Recipe، RecipeItem، InventoryTransaction (دفتر حرکت) |
| orders | Order، OrderItem، Payment و **تمام منطق حساس** در `services.py` (transaction.atomic) |
| waiter_calls | WaiterCall + قید یکتایی دیتابیس (یک درخواست فعال برای هر میز) |
| customer | View/Template/Static مشتری؛ هیچ منطق مالی ندارد |

## API ادمین (`Authorization: Token <key>`)
`POST /api/v1/auth/login/` · `GET /tables/` · `POST /tables/{id}/reserve|pay/` ·
`GET|POST /orders/` · `GET /orders/changes/?cursor=` · `POST /orders/{id}/status|pay|bar-printed/` ·
`GET /waiter-calls/?active=1` · `POST /waiter-calls/{id}/acknowledge|complete/` ·
`GET|PUT /settings/welcome/` · CRUD `/categories/` `/products/`
خطا: `{"error": {"code": "insufficient_stock|inactive_product|conflict|validation|not_found|unauthorized", "message": "فارسی"}}`

## WebSocket
- ادمین: `ws://host/ws/admin/?token=<key>` (بسته‌شدن با کد 4401 = توکن نامعتبر)
- مشتری: `/ws/customer/` (نشست QR)
- قالب پیام: `{"event", "data", "id", "ts"}`؛ رویدادها: `order_created`, `order_status_changed`,
  `waiter_call_created|acknowledged|completed`, `table_status_changed`, `payment_completed`
- بعد از هر (باز)اتصال کلاینت باید با REST همگام شود (`/orders/changes/`, `/tables/`, `/waiter-calls/?active=1`).
- صدای هشدار هر ۳۰ ثانیه سمت Flutter است: تا وقتی درخواستی با `status=pending` در Provider هست Timer فعال بماند.
