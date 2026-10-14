"""قالب JSON مهمان‌ها؛ قرارداد با Guest.fromJson / GuestGroup.fromJson در Flutter."""


def guest_dict(g):
    """یک ثبت مهمان به‌همراه اطلاعات خلاصه‌ی سفارشش (برای صفحه‌ی جزئیات)."""
    return {
        "id": g.id,
        "name": g.name,
        "amount": g.amount,
        "status": g.status,
        "order_id": str(g.order_id),
        "order_number": g.order.number,
        "table_number": g.order.table.number,
        "created_at": g.created_at.isoformat(),
        "cancelled_at": g.cancelled_at.isoformat() if g.cancelled_at else None,
    }


def group_dict(row):
    """گروه مهمان‌ها بر اساس نام: جمع مبلغ، تعداد سفارش و آخرین ثبت."""
    return {
        "name": row["name"],
        "total_amount": row["total_amount"] or 0,
        "order_count": row["order_count"] or 0,
        "last_at": row["last_at"].isoformat() if row["last_at"] else None,
    }


def summary_dict(*, total_amount, guest_count, order_count):
    return {
        "total_amount": total_amount or 0,
        "guest_count": guest_count or 0,
        "order_count": order_count or 0,
    }
