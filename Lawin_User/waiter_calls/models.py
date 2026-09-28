import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class WaiterCall(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار رسیدگی"
        ACKNOWLEDGED = "acknowledged", "در حال رسیدگی"
        COMPLETED = "completed", "انجام شد"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey("tables.Table", on_delete=models.PROTECT, related_name="waiter_calls")
    session = models.ForeignKey("tables.TableSession", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=13, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(
            fields=["table"], name="one_active_call_per_table",
            condition=Q(status__in=["pending", "acknowledged"]))]  # ضد Spam در سطح دیتابیس
