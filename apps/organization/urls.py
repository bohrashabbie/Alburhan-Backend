from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("currencies", views.CurrencyViewSet)
router.register("countries", views.CountryViewSet)
router.register("companies", views.CompanyViewSet)
router.register("branches", views.BranchViewSet)
router.register("warehouses", views.WarehouseViewSet)
router.register("exchange-rates", views.ExchangeRateViewSet)
router.register("document-sequences", views.DocumentSequenceViewSet)

urlpatterns = router.urls
