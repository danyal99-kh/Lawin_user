from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import services
from .models import WaiterCall
from .serializers import call_dict


@api_view(["GET"])
def calls(request):
    """?active=1 فقط pending/acknowledged (برای صدای هشدار و نمایش در بخش میزها)."""
    qs = WaiterCall.objects.select_related("table")
    qs = qs.filter(status__in=services.ACTIVE) if request.query_params.get("active") else qs.order_by("-created_at")[:100]
    return Response([call_dict(c) for c in qs])


@api_view(["POST"])
def acknowledge(request, pk):
    return Response(call_dict(services.acknowledge(pk, request.user)))


@api_view(["POST"])
def complete(request, pk):
    return Response(call_dict(services.complete(pk)))
