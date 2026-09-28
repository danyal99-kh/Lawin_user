import secrets
import uuid

from django.db import models
from django.db.models import Q


def new_token():
    return secrets.token_urlsafe(9)  # ~72 بیت؛ غیرقابل حدس، بدون اطلاعات حساس


class Table(models.Model):
    class Status(models.TextChoices):
        EMPTY = "empty", "خالی"
        ACTIVE = "active", "فعال"
        RESERVED = "reserved", "رزرو شده"

    number = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.EMPTY, db_index=True)
    # شناسه‌ی داخل QR. شماره‌ی میز قابل حدس است، پس QR فقط این توکن را حمل می‌کند.
    public_token = models.CharField(max_length=32, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]

    def rotate_token(self):
        self.public_token = new_token()
        self.save(update_fields=["public_token"])

    def __str__(self):
        return f"میز {self.number}"


class TableSession(models.Model):
    """از اولین سفارش تا پرداخت. ورود/خروج هرگز دستی وارد نمی‌شود."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(Table, on_delete=models.PROTECT, related_name="sessions")
    entered_at = models.DateTimeField()
    exited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-entered_at"]
        constraints = [models.UniqueConstraint(
            fields=["table"], condition=Q(exited_at__isnull=True), name="one_open_session_per_table")]

    @property
    def is_open(self):
        return self.exited_at is None
