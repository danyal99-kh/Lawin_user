"""قالب JSON کالای انبار، مطابق InventoryItem.fromJson در Flutter."""


def item_dict(i):
    return {
        "id": i.id,
        "name": i.name,
        "unit": i.unit,
        "current_stock": float(i.current_stock),
        "min_stock": float(i.min_stock),
        "unit_cost": float(i.unit_cost),
        "description": i.description or None,
    }


def _item_name(t):
    """نام کالا در لحظه‌ی ثبت؛ برای ردیف‌های قدیمیِ بدون snapshot به نام فعلی برمی‌گردیم."""
    return t.item_name_snapshot or t.item.name


def purchase_dict(t):
    """t یک InventoryTransaction با kind=purchase است."""
    return {
        "id": t.id,
        "item_id": t.item_id,
        "item_name": _item_name(t),
        "unit": t.item.unit,
        "quantity": float(t.quantity),
        "unit_cost": float(t.unit_cost or 0),
        "total_cost": float(t.quantity * (t.unit_cost or 0)),
        "purchased_at": t.created_at.isoformat(),
        "note": t.note or None,
    }


def waste_dict(t):
    """t یک InventoryTransaction با kind=waste است."""
    qty = abs(t.quantity)  # کسر با علامت منفی ذخیره می‌شود؛ به کاربر مثبت نشان می‌دهیم
    return {
        "id": t.id,
        "item_id": t.item_id,
        "item_name": _item_name(t),
        "unit": t.item.unit,
        "quantity": float(qty),
        "unit_cost": float(t.unit_cost or 0),
        "total_cost": float(qty * (t.unit_cost or 0)),
        "reason": t.reason or "other",
        "wasted_at": t.created_at.isoformat(),
        "note": t.note or None,
    }


def recipe_dict(product, items):
    return {
        "product_id": product.id,
        "product_name": product.name,
        "items": [
            {
                "inventory_item_id": ri.inventory_item_id,
                "inventory_item_name": ri.inventory_item.name,
                "unit": ri.inventory_item.unit,
                "quantity": float(ri.quantity),
            }
            for ri in items
        ],
    }
