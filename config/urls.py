from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health(_request):
    return JsonResponse({"status": "ok"})


api_v1 = [
    path("", include("apps.users.urls")),
    path("access/", include("apps.access.urls")),
    path("organization/", include("apps.organization.urls")),
    path("catalog/", include("apps.catalog.urls")),
    path("partners/", include("apps.partners.urls")),
    path("accounting/", include("apps.accounting.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("sales/", include("apps.sales.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

admin.site.site_header = "Al-Burhan ERP"
admin.site.site_title = "Al-Burhan ERP"
