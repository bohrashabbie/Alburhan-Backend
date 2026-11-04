from django.db import migrations

UNITS = [
    # code, name, name_ar, allow_decimal
    ("PCS", "Piece", "قطعة", False),
    ("SET", "Set", "طقم", False),
    ("BOX", "Box", "صندوق", False),
    ("ROLL", "Roll", "لفة", False),
    ("M", "Metre", "متر", True),
    ("HR", "Hour", "ساعة", True),
    ("LS", "Lump sum", "مقطوعية", False),
]


def load(apps, schema_editor):
    Unit = apps.get_model("catalog", "UnitOfMeasure")
    for code, name, name_ar, allow_decimal in UNITS:
        Unit.objects.update_or_create(
            code=code, defaults={"name": name, "name_ar": name_ar, "allow_decimal": allow_decimal}
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
