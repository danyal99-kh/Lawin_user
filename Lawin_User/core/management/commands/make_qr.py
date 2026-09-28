from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from tables.models import Table


class Command(BaseCommand):
    help = "ساخت QR Code (PNG) برای هر میز در media/qr/. QR فقط توکن امن میز را دارد."

    def add_arguments(self, p):
        p.add_argument("--rotate", action="store_true", help="توکن‌ها را عوض کن (QRهای قدیمی باطل می‌شوند)")

    def handle(self, *a, rotate=False, **kw):
        import qrcode
        out = Path(settings.MEDIA_ROOT) / "qr"
        out.mkdir(parents=True, exist_ok=True)
        for t in Table.objects.all():
            if rotate:
                t.rotate_token()
            url = f"{settings.PUBLIC_BASE_URL}/menu/table/{t.public_token}/"
            qrcode.make(url, box_size=10, border=3).save(out / f"table-{t.number}.png")
            self.stdout.write(f"میز {t.number}: {url}")
