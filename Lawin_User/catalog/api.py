from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.errors import Conflict, Invalid, NotFound

from . import pricing
from .models import Category, Offer, Product


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ["id", "name", "product_count", "sort_order", "is_active"]


class ProductSerializer(serializers.ModelSerializer):
    category_id = serializers.PrimaryKeyRelatedField(source="category", queryset=Category.objects.all())
    image_url = serializers.SerializerMethodField()
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    # فیلدهای فقط‌خواندنیِ آفر (قیمت پایه دست‌نخورده می‌ماند).
    effective_price = serializers.SerializerMethodField()
    discount_percent = serializers.SerializerMethodField()
    offer_active = serializers.SerializerMethodField()
    offer_ends_at = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "category_id", "price", "description", "image_url", "image",
                  "is_active", "sort_order", "effective_price", "discount_percent",
                  "offer_active", "offer_ends_at"]

    def get_image_url(self, p):
        if not p.image:
            return None
        req = self.context.get("request")
        return req.build_absolute_uri(p.image.url) if req else p.image.url

    def _quote(self, p):
        # quote_product از prefetch آفرها استفاده می‌کند (بدون کوئری اضافه).
        return pricing.quote_product(p)

    def get_effective_price(self, p):
        return self._quote(p)["final"]

    def get_discount_percent(self, p):
        return self._quote(p)["total_percent"]

    def get_offer_active(self, p):
        return bool(self._quote(p)["percentages"])

    def get_offer_ends_at(self, p):
        offer = self._quote(p)["offer"]
        return offer.ends_at.isoformat() if offer and offer.ends_at else None

    def update(self, instance, validated_data):
        if 'image' in validated_data:
            new_image = validated_data.pop('image')
            if new_image is None:
                if instance.image:
                    instance.image.delete(save=False)
                instance.image = None
            else:
                if instance.image:
                    try:
                        instance.image.delete(save=False)
                    except Exception:
                        pass
                instance.image = new_image
        return super().update(instance, validated_data)


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    queryset = Category.objects.annotate(product_count=Count("products"))

    def perform_destroy(self, obj):
        if obj.products.exists():
            raise Conflict("دسته‌ای که محصول دارد قابل حذف نیست.")
        obj.delete()


from rest_framework.parsers import JSONParser, MultiPartParser, FormParser


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    queryset = (Product.objects.select_related("category")
                .prefetch_related("offers", "category__offers"))
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def perform_destroy(self, obj):
        from orders.constants import OPEN_STATUSES
        if obj.orderitem_set.filter(order__status__in=OPEN_STATUSES).exists():
            raise Conflict("این محصول در یک سفارش باز استفاده شده؛ آن را غیرفعال کنید.")
        obj.delete()


# --------------------------------------------------------------------- آفرها
class OfferSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Offer
        fields = ["id", "scope", "product", "product_name", "category", "category_name",
                  "percent", "is_active", "starts_at", "ends_at", "created_at"]


def _parse_dt(raw, field):
    """رشته‌ی ISO را به datetime آگاه (timezone-aware) تبدیل می‌کند."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise Invalid(f"زمان {field} نامعتبر است.")
    value = parse_datetime(raw)
    if value is None:
        raise Invalid(f"زمان {field} نامعتبر است.")
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _clean_percent(raw):
    if isinstance(raw, bool):
        raise Invalid("درصد تخفیف نامعتبر است.")
    try:
        percent = int(raw)
    except (TypeError, ValueError):
        raise Invalid("درصد تخفیف نامعتبر است.")
    if not 1 <= percent <= pricing.MAX_PERCENT:
        raise Invalid(f"درصد تخفیف باید بین ۱ تا {pricing.MAX_PERCENT} باشد.")
    return percent


def _resolve_target(data):
    """scope و هدف (محصول/دسته) را از بدنه استخراج و اعتبارسنجی می‌کند."""
    scope = data.get("scope")
    if scope == Offer.Scope.PRODUCT:
        pid = data.get("product_id")
        try:
            product = Product.objects.select_related("category").get(pk=int(pid))
        except (TypeError, ValueError, Product.DoesNotExist):
            raise NotFound("محصول پیدا نشد.")
        return scope, product, None
    if scope == Offer.Scope.CATEGORY:
        cid = data.get("category_id")
        try:
            category = Category.objects.get(pk=int(cid))
        except (TypeError, ValueError, Category.DoesNotExist):
            raise NotFound("دسته‌بندی پیدا نشد.")
        return scope, None, category
    raise Invalid("نوع آفر باید محصول یا دسته‌بندی باشد.")


def _build_preview(scope, product, category, percent, starts_at, ends_at):
    if scope == Offer.Scope.PRODUCT:
        return pricing.preview_product(product, percent, starts_at, ends_at)
    return pricing.preview_category(category, percent, starts_at, ends_at)


def _preview_from_request(data):
    percent = _clean_percent(data.get("percent"))
    starts_at = _parse_dt(data.get("starts_at"), "شروع")
    ends_at = _parse_dt(data.get("ends_at"), "پایان")
    if starts_at and ends_at and ends_at <= starts_at:
        raise Invalid("زمان پایان باید بعد از زمان شروع باشد.")
    scope, product, category = _resolve_target(data)
    return scope, product, category, percent, starts_at, ends_at


@api_view(["GET"])
@permission_classes([IsAdminUser])
def offers(request):
    """فهرست آفرها با فیلتر ?product=/&category=/&active=1/0."""
    qs = Offer.objects.select_related("product", "category")
    if pid := request.query_params.get("product"):
        qs = qs.filter(product_id=pid)
    if cid := request.query_params.get("category"):
        qs = qs.filter(category_id=cid)
    active = request.query_params.get("active")
    if active in ("1", "true", "True"):
        qs = qs.filter(is_active=True)
    elif active in ("0", "false", "False"):
        qs = qs.filter(is_active=False)
    return Response(OfferSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def offer_preview(request):
    """پیش‌نمایش آفر (محصولی/دسته‌ای). هیچ آفر فعالی را تغییر نمی‌دهد."""
    scope, product, category, percent, starts_at, ends_at = _preview_from_request(request.data)
    return Response(_build_preview(scope, product, category, percent, starts_at, ends_at))


@api_view(["POST"])
@permission_classes([IsAdminUser])
def offer_create(request):
    """پیش‌نمایش را دوباره محاسبه و در صورت تطابق اثر انگشت، آفر را فعال می‌کند."""
    data = request.data
    scope, product, category, percent, starts_at, ends_at = _preview_from_request(data)
    provided = data.get("fingerprint")
    idem = data.get("idempotency_key")

    if idem:
        existing = Offer.objects.filter(idempotency_key=idem).first()
        if existing is not None:
            return Response(OfferSerializer(existing).data, status=201)

    # قفل ردیف هدف تا درخواست‌های هم‌زمان نتیجه‌ی ناسازگار نسازند.
    with transaction.atomic():
        if scope == Offer.Scope.PRODUCT:
            product = Product.objects.select_for_update().select_related("category").get(pk=product.pk)
        else:
            category = Category.objects.select_for_update().get(pk=category.pk)

        preview = _build_preview(scope, product, category, percent, starts_at, ends_at)
        if provided != preview["fingerprint"]:
            raise Conflict("قیمت‌ها از زمان پیش‌نمایش تغییر کرده‌اند؛ لطفاً دوباره پیش‌نمایش بگیرید.")
        if preview["product_count"] == 0:
            raise Invalid("آفر دسته‌ای روی دسته‌ی بدون محصول فعال نمی‌شود.")
        if any(item["final"] <= 0 for item in preview["products"]):
            raise Invalid("قیمت نهایی نامعتبر است؛ آفر با قیمت صفر مجاز نیست.")

        offer = Offer.objects.create(
            scope=scope, product=product, category=category, percent=percent,
            starts_at=starts_at, ends_at=ends_at,
            idempotency_key=idem or None,
            created_by=request.user if getattr(request.user, "pk", None) else None,
        )
    return Response(OfferSerializer(offer).data, status=201)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def offer_deactivate(request, pk):
    """غیرفعال‌سازی دستی یک آفر. سفارش‌های قبلی دست‌نخورده می‌مانند."""
    with transaction.atomic():
        try:
            offer = Offer.objects.select_for_update().get(pk=pk)
        except Offer.DoesNotExist:
            raise NotFound("آفر پیدا نشد.")
        if offer.is_active:
            offer.is_active = False
            offer.ends_at = timezone.now()
            offer.save(update_fields=["is_active", "ends_at"])
    return Response(OfferSerializer(offer).data)
