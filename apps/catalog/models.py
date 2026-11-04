"""Product catalogue, shared by every company in the group (the same SKU is sold in Kuwait and
the UAE). Selling prices are per company, in price lists."""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, QuantityField
from apps.core.images import upload_path
from apps.core.models import AuditedModel, CompanyScopedModel


class Brand(AuditedModel):
    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)
    is_own_brand = models.BooleanField(
        default=False, help_text=_("Made in the group's own factory (MS Lighting).")
    )
    country = models.ForeignKey(
        "organization.Country", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductCategory(AuditedModel):
    """Product family, e.g. Recessed Down Light. Optional parent for sub-families."""

    class Group(models.TextChoices):
        INDOOR = "indoor", _("Indoor")
        OUTDOOR = "outdoor", _("Outdoor")
        MAGNETIC_LINEAR = "magnetic_linear", _("Magnetic & linear")
        ACCESSORY = "accessory", _("Accessories & drivers")
        SERVICE = "service", _("Services")

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)
    group = models.CharField(max_length=20, choices=Group.choices, default=Group.INDOOR)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    image = models.ImageField(upload_to=upload_path("categories"), blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["group", "name"]
        verbose_name_plural = "product categories"

    def __str__(self):
        return self.name

    def clean(self):
        node, depth = self.parent, 0
        while node is not None:
            if node.pk == self.pk:
                raise ValidationError({"parent": _("A category cannot be inside itself.")})
            node, depth = node.parent, depth + 1
            if depth > 10:
                break


class UnitOfMeasure(AuditedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=50)
    name_ar = models.CharField(max_length=50, blank=True)
    allow_decimal = models.BooleanField(
        default=False, help_text=_("e.g. metres of LED strip can be sold as 2.5.")
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "unit of measure"

    def __str__(self):
        return self.code


IP_RATING = RegexValidator(r"^IP[0-6][0-9]$", _("Use the form IP20, IP44, IP65…"))
IK_RATING = RegexValidator(r"^IK(0[0-9]|10)$", _("Use the form IK06, IK08, IK10…"))


class Product(AuditedModel):
    class ProductType(models.TextChoices):
        STOCK = "stock", _("Stock item")
        NON_STOCK = "non_stock", _("Non-stock item")
        SERVICE = "service", _("Service")

    class Application(models.TextChoices):
        INDOOR = "indoor", _("Indoor")
        OUTDOOR = "outdoor", _("Outdoor")
        BOTH = "both", _("Indoor & outdoor")

    class Dimming(models.TextChoices):
        NONE = "none", _("Non-dimmable")
        TRIAC = "triac", _("TRIAC / phase")
        ZERO_TEN = "0_10v", _("0-10V")
        DALI = "dali", _("DALI")
        DALI2 = "dali2", _("DALI-2")
        CASAMBI = "casambi", _("Casambi")
        DMX = "dmx", _("DMX")
        OTHER = "other", _("Other")

    # Identity
    sku = models.CharField(_("SKU"), max_length=50, unique=True)
    model_code = models.CharField(
        max_length=50, blank=True, db_index=True, help_text=_("Groups variants of one model.")
    )
    barcode = models.CharField(max_length=50, unique=True, null=True, blank=True)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    product_type = models.CharField(
        max_length=10, choices=ProductType.choices, default=ProductType.STOCK
    )
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, null=True, blank=True, related_name="products"
    )
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    application = models.CharField(
        max_length=10, choices=Application.choices, default=Application.INDOOR
    )

    # Lighting specification (all optional; services and accessories leave them empty)
    wattage = models.DecimalField(
        _("power (W)"),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    lumens = models.PositiveIntegerField(_("luminous flux (lm)"), null=True, blank=True)
    cct = models.CharField(
        _("colour temperature"), max_length=40, blank=True, help_text=_("e.g. 3000K, 2700-6500K")
    )
    cri = models.PositiveSmallIntegerField(
        "CRI", null=True, blank=True, validators=[MaxValueValidator(100)]
    )
    beam_angle = models.CharField(max_length=30, blank=True, help_text=_("e.g. 24°, 15/24/36°"))
    ip_rating = models.CharField("IP", max_length=4, blank=True, validators=[IP_RATING])
    ik_rating = models.CharField("IK", max_length=4, blank=True, validators=[IK_RATING])
    input_voltage = models.CharField(max_length=40, blank=True, help_text=_("e.g. 220-240V AC"))
    dimming = models.CharField(max_length=10, choices=Dimming.choices, default=Dimming.NONE)
    driver = models.CharField(max_length=80, blank=True, help_text=_("e.g. Meanwell"))
    led_source = models.CharField(max_length=80, blank=True, help_text=_("e.g. Cree, Osram"))
    finish = models.CharField(max_length=60, blank=True, help_text=_("Colour / finish"))
    body_material = models.CharField(max_length=60, blank=True)
    dimensions = models.CharField(max_length=80, blank=True, help_text=_("e.g. Ø90 × 55 mm"))
    cutout = models.CharField(max_length=40, blank=True, help_text=_("e.g. Ø75 mm"))
    lifetime_hours = models.PositiveIntegerField(null=True, blank=True)
    warranty_months = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)]
    )

    image = models.ImageField(upload_to=upload_path("products"), blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["sku"]
        indexes = [models.Index(fields=["category", "is_active"])]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def save(self, *args, **kwargs):
        # An empty barcode is stored as NULL so many products can have none.
        if not self.barcode:
            self.barcode = None
        super().save(*args, **kwargs)


class PriceList(CompanyScopedModel):
    """Selling prices for one company, in one currency, e.g. "Kuwait retail", "Kuwait contractors"."""

    code = models.SlugField(max_length=30)
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)
    currency = models.ForeignKey(
        "organization.Currency", on_delete=models.PROTECT, related_name="+"
    )
    is_default = models.BooleanField(
        default=False, help_text=_("Used when a customer has no price list of their own.")
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_price_list_code"),
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(is_default=True),
                name="one_default_price_list_per_company",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_from__isnull=True)
                | models.Q(valid_to__gte=models.F("valid_from")),
                name="price_list_valid_range",
            ),
        ]

    def __str__(self):
        return f"{self.company.code} {self.name}"


class PriceListItem(AuditedModel):
    """A product's price in a list. Several rows with different `min_quantity` give price breaks."""

    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="prices")
    min_quantity = QuantityField(default=1)
    unit_price = MoneyField()

    class Meta:
        ordering = ["product__sku", "min_quantity"]
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "product", "min_quantity"], name="uniq_price_break"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="price_not_negative"
            ),
            models.CheckConstraint(condition=models.Q(min_quantity__gt=0), name="min_qty_positive"),
        ]

    def __str__(self):
        return f"{self.price_list} · {self.product.sku} ≥{self.min_quantity}: {self.unit_price}"
