"""Every country and its currency (Unicode CLDR via Babel; see data/*.json). Adds what is
missing and fills empty Arabic names; never overwrites values edited in the system."""

import json
from pathlib import Path

from django.db import migrations

DATA = Path(__file__).resolve().parent.parent / "data"


def load(apps, schema_editor):
    Currency = apps.get_model("organization", "Currency")
    Country = apps.get_model("organization", "Country")
    for row in json.loads((DATA / "currencies.json").read_text(encoding="utf-8")):
        currency, created = Currency.objects.get_or_create(
            code=row["code"],
            defaults={
                "name": row["name"],
                "name_ar": row["name_ar"],
                "symbol": row["symbol"],
                "decimal_places": row["decimal_places"],
            },
        )
        if not created and not currency.name_ar and row["name_ar"]:
            currency.name_ar = row["name_ar"]
            currency.save(update_fields=["name_ar"])
    for row in json.loads((DATA / "countries.json").read_text(encoding="utf-8")):
        country, created = Country.objects.get_or_create(
            code=row["code"],
            defaults={
                "name": row["name"],
                "name_ar": row["name_ar"],
                "default_currency_id": row["currency"],
            },
        )
        if not created and not country.name_ar and row["name_ar"]:
            country.name_ar = row["name_ar"]
            country.save(update_fields=["name_ar"])


class Migration(migrations.Migration):
    dependencies = [("organization", "0003_reference_data")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
