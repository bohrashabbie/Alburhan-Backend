from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("brands", views.BrandViewSet)
router.register("categories", views.ProductCategoryViewSet)
router.register("units", views.UnitOfMeasureViewSet)
router.register("products", views.ProductViewSet)
router.register("price-lists", views.PriceListViewSet)
router.register("price-list-items", views.PriceListItemViewSet)

urlpatterns = router.urls
