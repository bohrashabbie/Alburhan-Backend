"""ViewSet mixins enforcing the multi-company access rules for every module."""

from django.db import transaction
from django.db.models import Q
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from .permissions import OPEN, required_permission


class AuditedViewSetMixin:
    """Stamps created_by / updated_by from the request user. Every write runs in one transaction
    (requests are not wrapped as a whole, so reads stay cheap)."""

    def perform_create(self, serializer):
        with transaction.atomic():
            serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance.delete()


class CompanyScopedViewSetMixin:
    """Shows only rows in companies/branches where the user holds the action's permission, and
    blocks creating or moving rows into a company/branch where they don't.

    - `company_lookup`: ORM path from the model to its Company (e.g. "company", or "pk" for Company).
    - `branch_lookup`: ORM path to its Branch, if the model has one. Users whose role is assigned
      to a single branch then see that branch's rows only. Rows without a branch need a
      company-wide assignment.
    - `branch_field`: serializer field holding the branch on create/update (None if no branch).
    - `branch_roles_cover_company`: for company-level records that branch staff also work with
      (customers, price lists). A role held for any one branch then counts for the whole company.
    """

    company_lookup = "company"
    branch_lookup: str | tuple[str, ...] | None = None
    branch_field: str | None = None
    distinct_scope = False
    branch_roles_cover_company = False

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return queryset
        code = required_permission(self)
        if code in (None, OPEN):
            return queryset.none()
        company_ids, branch_ids = user.permission_scope(code)
        if self.branch_roles_cover_company:
            from apps.access.services import companies_with

            company_ids = companies_with(user, code)
        condition = Q(**{f"{self.company_lookup}__in": company_ids})
        if self.branch_lookup and branch_ids:
            # One path, or several (e.g. a transfer belongs to both its source and target branch).
            paths = (
                (self.branch_lookup,) if isinstance(self.branch_lookup, str) else self.branch_lookup
            )
            for path in paths:
                condition |= Q(**{f"{path}__in": branch_ids})
        queryset = queryset.filter(condition)
        return queryset.distinct() if self.distinct_scope else queryset

    def target_company(self, data, instance=None):
        """The company a create/update writes into. Override for rows that reach their company
        through a parent, e.g. a price-list line → its price list → company."""
        return data.get("company", getattr(instance, "company", None))

    def target_branch(self, data, instance=None):
        """The branch a create/update writes into (None = company level). Override for rows that
        reach their branch through a parent, e.g. a stock document → its warehouse → branch."""
        if not self.branch_field:
            return None
        return data.get(self.branch_field, getattr(instance, self.branch_field, None))

    def _assert_allowed(self, serializer, instance=None):
        user = self.request.user
        if user.is_superuser:
            return
        data = serializer.validated_data
        company = self.target_company(data, instance)
        if company is None:
            return
        branch = self.target_branch(data, instance)
        code = required_permission(self)
        if self.branch_roles_cover_company:
            from apps.access.services import companies_with

            allowed = company.pk in companies_with(user, code)
        else:
            allowed = user.has_erp_permission(
                code, company_id=company.pk, branch_id=branch.pk if branch else None
            )
        if not allowed:
            raise PermissionDenied("You do not have this permission for that company or branch.")

    def perform_create(self, serializer):
        self._assert_allowed(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._assert_allowed(serializer, serializer.instance)
        super().perform_update(serializer)


class AuditedModelViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    pass


class CompanyScopedModelViewSet(
    CompanyScopedViewSetMixin, AuditedViewSetMixin, viewsets.ModelViewSet
):
    pass
