import json
import uuid
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from catalog.models import Category, Product
from catalog.services import is_available
from core.errors import DomainError, Invalid, TooManyRequests
from core.models import CafeSettings, WelcomeMessage
from orders import services as order_services
from orders.constants import OrderSource
from orders.models import Order
from orders.serializers import order_dict
from tables.models import Table
from waiter_calls import services as call_services
from waiter_calls.models import WaiterCall
from waiter_calls.serializers import call_dict

def cafe_name():
    """نام کافه از تنظیمات پنل خوانده می‌شود؛ همان singleton تنظیمات."""
    return CafeSettings.load().name


def require_table(view):
    """فقط نشستی که از QR معتبر ساخته شده. میز همیشه از نشست سرور خوانده می‌شود، نه از Client."""
    @wraps(view)
    def wrapper(request, *a, **kw):
        tid, key = request.session.get("table_id"), request.session.get("customer_key")
        table = Table.objects.filter(pk=tid).first() if tid and key else None
        if table is None:
            if request.path.startswith("/api/"):
                return JsonResponse({"error": {"code": "no_table",
                                               "message": "لطفاً QR Code روی میز را دوباره اسکن کنید."}}, status=403)
            return render(request, "customer/no_table.html", status=403)
        request.table, request.customer_key = table, key
        return view(request, *a, **kw)
    return wrapper


def api_view(view):
    """DomainError → JSON استاندارد."""
    @wraps(view)
    def wrapper(request, *a, **kw):
        try:
            return view(request, *a, **kw)
        except DomainError as e:
            return JsonResponse(e.payload(), status=e.status)
    return wrapper


def enter(request, token):
    """مقصد QR: /menu/table/<توکن-امن>/ — توکن نامعتبر = 404."""
    table = get_object_or_404(Table, public_token=token)
    request.session["table_id"] = table.id
    request.session.setdefault("customer_key", uuid.uuid4().hex)
    request.session["welcomed"] = False
    return redirect("customer:welcome" if WelcomeMessage.load().enabled else "customer:menu")


@require_table
def welcome(request):
    w = WelcomeMessage.load()
    if not w.enabled:
        return redirect("customer:menu")
    return render(request, "customer/welcome.html", {"w": w, "table": request.table, "cafe": cafe_name()})


@require_table
@ensure_csrf_cookie
def menu(request):
    cfg = {"cafe": cafe_name(), "table": request.table.number,
           "wsPath": "/ws/customer/", "urls": {"menu": "/api/customer/menu/", "orders": "/api/customer/orders/",
                                              "waiter": "/api/customer/waiter/"}}
    return render(request, "customer/menu.html", {"cfg": cfg, "table": request.table, "cafe": cafe_name()})


@require_GET
@require_table
@api_view
def api_menu(request):
    products = Product.objects.order_by("sort_order", "id").prefetch_related("recipe__items__inventory_item")
    cats = Category.objects.filter(is_active=True).prefetch_related(Prefetch("products", queryset=products))
    return JsonResponse({"table": request.table.number, "categories": [
        {"id": c.id, "name": c.name, "products": [
            {"id": p.id, "name": p.name, "description": p.description, "price": p.price,
             "image_url": p.image.url if p.image else None, "available": is_available(p)}
            for p in c.products.all()]}
        for c in cats]})


def _throttle(key):
    limit, window = settings.CUSTOMER_ORDER_LIMIT
    k = f"order-rate:{key}"
    cache.add(k, 0, window)
    if cache.incr(k) > limit:
        raise TooManyRequests("درخواست‌های شما زیاد است. کمی صبر کنید.")


def _own_orders(request):
    return (Order.objects.select_related("table").prefetch_related("items")
            .filter(customer_key=request.customer_key, table=request.table))


@require_table
@api_view
def api_orders(request):
    if request.method == "POST":
        _throttle(request.customer_key)
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            raise Invalid("درخواست نامعتبر است.")
        if not isinstance(data, dict):
            raise Invalid("درخواست نامعتبر است.")
        # فقط product_id/quantity/note پذیرفته می‌شود؛ قیمت و مبلغ هرگز از Client خوانده نمی‌شود.
        order = order_services.create_order(
            table_id=request.table.id, items=data.get("items"), source=OrderSource.CUSTOMER,
            customer_key=request.customer_key, customer_note=data.get("note") or "")
        return JsonResponse(order_dict(_own_orders(request).get(pk=order.pk)), status=201)
    if request.method != "GET":
        return JsonResponse({"error": {"code": "method", "message": "روش نامعتبر."}}, status=405)
    return JsonResponse({"orders": [order_dict(o) for o in _own_orders(request)[:20]]})


@require_GET
@require_table
@api_view
def api_order_detail(request, pk):
    o = get_object_or_404(_own_orders(request), pk=pk)  # سفارش دیگران = 404
    return JsonResponse(order_dict(o))


@require_table
@api_view
def api_waiter(request):
    if request.method == "POST":
        call, created = call_services.request_call(request.table.id)
        return JsonResponse({"call": call_dict(call), "created": created,
                             "message": (f"درخواست شما برای میز {request.table.number} ارسال شد." if created else
                                         "درخواست شما ثبت شده است، گارسون به زودی مراجعه می‌کند.")},
                            status=201 if created else 200)
    call = WaiterCall.objects.select_related("table").filter(
        table=request.table, status__in=call_services.ACTIVE).first()
    return JsonResponse({"call": call_dict(call) if call else None})
