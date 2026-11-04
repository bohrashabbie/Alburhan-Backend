"""Attach the product-family photographs published on mslighting.alburhan-regional.com to the
matching families in the ERP (stored in our own S3 bucket like any upload)."""

import io
import urllib.request

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image

from apps.catalog.models import ProductCategory

SITE_IMAGES = "https://alburhan-asset.s3.eu-north-1.amazonaws.com/site/families/{slug}.webp"

# ERP family code -> image slug on the website
SLUGS = {
    "recessed-down-light": "recessed-down-light",
    "surface-down-light": "surface-mounted-down-light",
    "recessed-grille-spot": "recessed-grille-spot-light",
    "recessed-panel": "recessed-panel-light",
    "recessed-spot": "recessed-spot-light",
    "module-series": "module-series",
    "ceiling-light": "ceiling-light",
    "high-bay": "high-bay",
    "track-spot": "track-spot-light",
    "magnet-light": "magnet-light",
    "linear-light": "linear-light",
    "wall-light": "wall-light",
    "lawn-light": "lawn-light",
    "street-light": "street-light",
    "flood-light": "flood-light",
}


# Families without a family banner on the website: a representative product photo instead.
FALLBACK_IMAGES = {
    "recessed-down-light": "https://alburhan-asset.s3.eu-north-1.amazonaws.com/ms-lighting/products/30b5725771534f419eeca37217c1c80e.webp",
    "surface-down-light": "https://alburhan-asset.s3.eu-north-1.amazonaws.com/ms-lighting/products/827adbd78bd547d68a6f77246843524c.webp",
    "recessed-grille-spot": "https://alburhan-asset.s3.eu-north-1.amazonaws.com/site/renders/MS-220GR.png",
    "recessed-panel": "https://alburhan-asset.s3.eu-north-1.amazonaws.com/ms-lighting/products/37df37aa94c44737aaa81ec2b4928faa.webp",
    "recessed-spot": "https://alburhan-asset.s3.eu-north-1.amazonaws.com/ms-lighting/products/12efad04b84d4069ac0458a70b4a64df.webp",
}


class Command(BaseCommand):
    help = "Download the family photos from the MS Lighting website and attach them to product families."

    def add_arguments(self, parser):
        parser.add_argument("--replace", action="store_true", help="Also replace families that already have a photo.")

    def handle(self, *args, replace, **options):
        done = skipped = missing = 0
        for code, slug in SLUGS.items():
            family = ProductCategory.objects.filter(code=code).first()
            if family is None:
                continue
            if family.image and not replace:
                skipped += 1
                continue
            raw = None
            for url in (SITE_IMAGES.format(slug=slug), FALLBACK_IMAGES.get(code)):
                if not url:
                    continue
                try:
                    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
                        raw = response.read()
                    break
                except Exception:  # noqa: BLE001 - try the next source
                    continue
            if raw is None:
                self.stdout.write(self.style.WARNING(f"{code}: no photo on the website"))
                missing += 1
                continue
            # Re-encode as a square JPEG (centre crop), the shape the ERP shows everywhere.
            source = Image.open(io.BytesIO(raw))
            if source.mode in ("RGBA", "LA", "P"):
                source = source.convert("RGBA")
                flat = Image.new("RGB", source.size, (255, 255, 255))
                flat.paste(source, mask=source.split()[-1])
                source = flat
            image = source.convert("RGB")
            side = min(image.size)
            left, top = (image.width - side) // 2, (image.height - side) // 2
            image = image.crop((left, top, left + side, top + side)).resize((800, 800), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=85, optimize=True)
            if family.image:
                family.image.delete(save=False)
            family.image.save(f"{slug}.jpg", ContentFile(buffer.getvalue()), save=True)
            self.stdout.write(f"{code}: photo attached")
            done += 1
        self.stdout.write(self.style.SUCCESS(f"Done: {done} attached, {skipped} already had one, {missing} not found."))
