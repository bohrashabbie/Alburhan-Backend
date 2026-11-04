from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """50 rows a page by default; ?page_size= up to 500 for pickers and dropdowns."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500
