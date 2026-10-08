"""قالب JSON نسیه‌ها؛ قرارداد با Credit.fromJson / Debtor.fromJson در Flutter."""


def credit_dict(c):
    return {
        "id": c.id,
        "debtor_id": c.debtor_id,
        "debtor_name": c.debtor.name,
        "order_id": str(c.order_id),
        "order_number": c.order.number,
        "amount": c.amount,
        "remaining_amount": c.remaining_amount,
        "status": c.status,
        "created_at": c.created_at.isoformat(),
    }


def payment_dict(p):
    return {
        "id": p.id,
        "credit_id": p.credit_id,
        "debtor_id": p.credit.debtor_id,
        "debtor_name": p.credit.debtor.name,
        "order_id": str(p.credit.order_id),
        "order_number": p.credit.order.number,
        "amount": p.amount,
        "account": p.account,
        "note": p.note or None,
        "created_at": p.created_at.isoformat(),
    }


def debtor_dict(d, *, debt=0, open_credits=0, extended=0, last_activity=None):
    """بدهکار + جمع‌های مالی‌اش.

    `debt` = مانده‌ی باز (چقدر باید بگیریم)، `extended` = کل نسیه‌ای که تا حالا
    ثبت کرده‌ایم. هر دو از خودِ Credit می‌آیند، نه محاسبه‌ی جداگانه.
    """
    return {
        "id": d.id,
        "name": d.name,
        "phone": d.phone or None,
        "note": d.note or None,
        "debt": debt,
        "open_credits": open_credits,
        "extended": extended,
        "last_activity": last_activity.isoformat() if last_activity else None,
        "created_at": d.created_at.isoformat(),
    }
