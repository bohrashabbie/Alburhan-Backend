from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """ERP user. What they can do, and in which company/branch, comes from their UserRole
    assignments (apps.access). Django's own groups/permissions are only used for /admin."""

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        ARABIC = "ar", "العربية"

    email = models.EmailField(_("email address"), unique=True)
    phone = models.CharField(max_length=32, blank=True)
    preferred_language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.ENGLISH
    )
    default_company = models.ForeignKey(
        "organization.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Company opened after login; also the user's home company."),
    )
    default_branch = models.ForeignKey(
        "organization.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # People sign in with their email address.
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    # --- ERP access (delegates to apps.access.services) ---

    def has_erp_permission(self, code, company_id=None, branch_id=None) -> bool:
        from apps.access.services import has_permission

        return has_permission(self, code, company_id, branch_id)

    def permission_scope(self, code) -> tuple[set[int], set[int]]:
        from apps.access.services import permission_scope

        return permission_scope(self, code)

    def allowed_company_ids(self) -> set[int]:
        """Companies where the user holds any role (all companies for a superuser)."""
        if not hasattr(self, "_allowed_company_ids"):
            from apps.access.services import load_grants
            from apps.organization.models import Company

            if self.is_superuser:
                self._allowed_company_ids = set(Company.objects.values_list("pk", flat=True))
            else:
                self._allowed_company_ids = {g.company_id for g in load_grants(self)}
        return self._allowed_company_ids

    def allowed_branch_ids(self) -> set[int]:
        """Branches the user can work in: every branch of a company-wide role, plus single branches."""
        if not hasattr(self, "_allowed_branch_ids"):
            from apps.access.services import load_grants
            from apps.organization.models import Branch

            if self.is_superuser:
                whole = self.allowed_company_ids()
                single: set[int] = set()
            else:
                grants = load_grants(self)
                whole = {g.company_id for g in grants if g.branch_id is None}
                single = {g.branch_id for g in grants if g.branch_id is not None}
            self._allowed_branch_ids = single | set(
                Branch.objects.filter(company_id__in=whole).values_list("pk", flat=True)
            )
        return self._allowed_branch_ids
