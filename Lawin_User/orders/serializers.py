"""قالب JSON سفارش دقیقاً مطابق Order.fromJson در Flutter."""


def item_dict(i):
    return {"product_id": i.product_id, "product_name": i.product_name, "quantity": i.quantity,
            "unit_price": i.unit_price, "note": i.note or None}


def order_dict(o):
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "id": str(o.id), "number": o.number, "source": o.source,
        "table": {"id": o.table_id, "number": o.table.number},
        "status": o.status, "payment_status": o.payment_status, "payment_method": o.payment_method,
        "customer_note": o.customer_note or None,
        "items": [item_dict(i) for i in o.items.all()],
        "total": o.total, "created_at": iso(o.created_at), "paid_at": iso(o.paid_at),
        "bar_printed_at": iso(o.bar_printed_at), "version": o.version,
        "session_id": str(o.session_id), "updated_at": iso(o.updated_at),
    }
