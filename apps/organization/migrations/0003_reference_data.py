from django.db import migrations

CURRENCIES = [
    # code, name, name_ar, symbol, decimal places
    ("KWD", "Kuwaiti Dinar", "دينار كويتي", "KD", 3),
    ("AED", "UAE Dirham", "درهم إماراتي", "AED", 2),
    ("CNY", "Chinese Yuan", "يوان صيني", "¥", 2),
    ("EGP", "Egyptian Pound", "جنيه مصري", "E£", 2),
    ("USD", "US Dollar", "دولار أمريكي", "$", 2),
    ("EUR", "Euro", "يورو", "€", 2),
]

COUNTRIES = [
    ("KW", "Kuwait", "الكويت", "KWD"),
    ("AE", "United Arab Emirates", "الإمارات العربية المتحدة", "AED"),
    ("CN", "China", "الصين", "CNY"),
    ("EG", "Egypt", "مصر", "EGP"),
]


def load(apps, schema_editor):
    Currency = apps.get_model("organization", "Currency")
    Country = apps.get_model("organization", "Country")
    for code, name, name_ar, symbol, decimals in CURRENCIES:
        Currency.objects.update_or_create(
            code=code,
            defaults={"name": name, "name_ar": name_ar, "symbol": symbol, "decimal_places": decimals},
        )
    for code, name, name_ar, currency in COUNTRIES:
        Country.objects.update_or_create(
            code=code,
            defaults={"name": name, "name_ar": name_ar, "default_currency_id": currency},
        )


class Migration(migrations.Migration):
    dependencies = [("organization", "0002_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
