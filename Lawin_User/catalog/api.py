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
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Product
        fields = ["id", "name", "category_id", "price", "description", "image_url", "image",
                  "is_active", "sort_order"]

    def get_image_url(self, p):
        if not p.image:
            return None
        req = self.context.get("request")
        return req.build_absolute_uri(p.image.url) if req else p.image.url

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
    queryset = Product.objects.select_related("category")
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def perform_destroy(self, obj):
        from orders.constants import OPEN_STATUSES
        if obj.orderitem_set.filter(order__status__in=OPEN_STATUSES).exists():
            raise Conflict("این محصول در یک سفارش باز استفاده شده؛ آن را غیرفعال کنید.")
        obj.delete()
