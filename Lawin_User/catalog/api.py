from django.db.models import Count
from rest_framework import serializers, viewsets

from core.errors import Conflict
from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ["id", "name", "product_count", "sort_order", "is_active"]


class ProductSerializer(serializers.ModelSerializer):
    category_id = serializers.PrimaryKeyRelatedField(source="category", queryset=Category.objects.all())
    image_url = serializers.SerializerMethodField()
    image = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = Product
        fields = ["id", "name", "category_id", "price", "description", "image_url", "image",
                  "is_active", "sort_order"]

    def get_image_url(self, p):
        if not p.image:
            return None
        req = self.context.get("request")
        return req.build_absolute_uri(p.image.url) if req else p.image.url


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    queryset = Category.objects.annotate(product_count=Count("products"))

    def perform_destroy(self, obj):
        if obj.products.exists():
            raise Conflict("دسته‌ای که محصول دارد قابل حذف نیست.")
        obj.delete()


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related("category")

    def perform_destroy(self, obj):
        from orders.constants import OPEN_STATUSES
        if obj.orderitem_set.filter(order__status__in=OPEN_STATUSES).exists():
            raise Conflict("این محصول در یک سفارش باز استفاده شده؛ آن را غیرفعال کنید.")
        obj.delete()
