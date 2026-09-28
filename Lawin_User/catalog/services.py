def is_available(product):
    """آیا یک واحد از محصول قابل سفارش است؟ (فعال بودن + موجودی انبار طبق Recipe).
    برای کارایی انتظار می‌رود recipe__items__inventory_item از قبل prefetch شده باشد."""
    if not product.is_active:
        return False
    try:
        recipe = product.recipe
    except Exception:  # RelatedObjectDoesNotExist: محصول بدون دستور مصرف
        return True
    return all(ri.inventory_item.current_stock >= ri.quantity for ri in recipe.items.all())
