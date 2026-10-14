"""قالب JSON سفارش دقیقاً مطابق Order.fromJson در Flutter."""


def item_dict(i):
    return {"product_id": i.product_id, "product_name": i.product_name, "quantity": i.quantity,
            "unit_price": i.unit_price, "original_price": i.original_price,
            "discount_amount": i.discount_amount, "note": i.note or None}


def payment_dict(p):
    return {"id": p.id, "method": p.method, "amount": p.amount,
            "created_at": p.created_at.isoformat()}


def order_dict(o):
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    # مهمان یک ثبت جداگانه است (OneToOne). اگر سفارش مهمان نباشد این دسترسی
    # None می‌دهد، پس UI می‌تواند بدون کوئری اضافه وضعیت مهمان را ببیند.
    guest = getattr(o, "guest", None)
    return {
        "id": str(o.id), "number": o.number, "source": o.source,
        "table": {"id": o.table_id, "number": o.table.number},
        "status": o.status, "payment_status": o.payment_status, "payment_method": o.payment_method,
        "customer_note": o.customer_note or None,
        "items": [item_dict(i) for i in o.items.all()],
        "payments": [payment_dict(p) for p in o.payments.all()],
        "guest_name": guest.name if guest else None,
        "guest_status": guest.status if guest else None,
        "total": o.total, "created_at": iso(o.created_at), "paid_at": iso(o.paid_at),
        "bar_printed_at": iso(o.bar_printed_at), "version": o.version,
        "session_id": str(o.session_id), "updated_at": iso(o.updated_at),
    }