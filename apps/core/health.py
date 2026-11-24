"""GET /health/ for the container's health check.

The check calls http://127.0.0.1:8000/health/ from inside the container, and 127.0.0.1 is not one of
the site's allowed host names, so Django would refuse it (400) and Docker would mark the API
unhealthy. This answers before anything reads the host name. It says only that the process is up
and reveals nothing else."""

from django.http import JsonResponse

PATH = "/health/"


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == PATH and request.method in ("GET", "HEAD"):
            return JsonResponse({"status": "ok"})
        return self.get_response(request)
