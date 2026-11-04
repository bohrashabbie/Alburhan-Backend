"""Product families and brands as published on alburhan-regional.com / mslighting site.
Safe to run repeatedly; existing rows keep any edits made since."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Brand, ProductCategory

G = ProductCategory.Group
FAMILIES = [
    ("recessed-down-light", "Recessed Down Light", "إنارة سقفية غاطسة", G.INDOOR),
    ("surface-down-light", "Surface Mounted Down Light", "إنارة سقفية سطحية", G.INDOOR),
    ("recessed-grille-spot", "Recessed Grille Spot Light", "سبوت شبكي غاطس", G.INDOOR),
    ("recessed-panel", "Recessed Panel Light", "بانل غاطس", G.INDOOR),
    ("recessed-spot", "Recessed Spot Light", "سبوت غاطس", G.INDOOR),
    ("module-series", "Module Series", "سلسلة الموديول", G.INDOOR),
    ("ceiling-light", "Ceiling Light", "إنارة سقف", G.INDOOR),
    ("high-bay", "High Bay", "إنارة المستودعات", G.INDOOR),
    ("track-spot", "Track Spot Light", "سبوت تراك", G.MAGNETIC_LINEAR),
    ("magnet-light", "Magnet Light", "إنارة مغناطيسية", G.MAGNETIC_LINEAR),
    ("linear-light", "Linear Light", "إنارة خطية", G.MAGNETIC_LINEAR),
    ("wall-light", "Wall Light", "إنارة جدارية", G.OUTDOOR),
    ("lawn-light", "Lawn Light", "إنارة حدائق", G.OUTDOOR),
    ("street-light", "Street Light", "إنارة شوارع", G.OUTDOOR),
    ("flood-light", "Flood Light", "كشاف", G.OUTDOOR),
    ("drivers-accessories", "Drivers & Accessories", "محولات وملحقات", G.ACCESSORY),
    ("installation-services", "Installation Services", "خدمات التركيب", G.SERVICE),
]

BRANDS = [
    # code, name, own brand, country
    ("ms-lighting", "MS Lighting", True, "CN"),
    ("iluminia", "Iluminia", False, None),
    ("inovara", "INOVARA", False, None),
    ("metsan", "Metsan", False, None),
    ("kosla", "Kosla", False, None),
]


class Command(BaseCommand):
    help = "Create the standard product families and brands (does not overwrite edits)."

    @transaction.atomic
    def handle(self, *args, **options):
        made = 0
        for code, name, name_ar, group in FAMILIES:
            _, created = ProductCategory.objects.get_or_create(
                code=code, defaults={"name": name, "name_ar": name_ar, "group": group}
            )
            made += created
        for code, name, own, country in BRANDS:
            _, created = Brand.objects.get_or_create(
                code=code, defaults={"name": name, "is_own_brand": own, "country_id": country}
            )
            made += created
        self.stdout.write(self.style.SUCCESS(f"Catalog defaults loaded ({made} new)."))
