"""One error shape for every API response, so both frontends handle errors the same way:

{"code": "validation_error", "message": "Check the highlighted fields.",
 "fields": {"branch": "Branch must belong to the same company."}}
"""

from django.db.models import ProtectedError, RestrictedError
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

CODES = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_429_TOO_MANY_REQUESTS: "throttled",
}


def _first_message(value) -> str:
    if isinstance(value, list):
        return _first_message(value[0]) if value else ""
    if isinstance(value, dict):
        return _first_message(next(iter(value.values()), ""))
    return str(value)


def api_exception_handler(exc, context):
    if isinstance(exc, ProtectedError | RestrictedError):
        set_rollback()
        return Response(
            {
                "code": "in_use",
                "message": "This is used by other records, so it can't be deleted. "
                "Mark it inactive instead.",
                "fields": {},
            },
            status=status.HTTP_409_CONFLICT,
        )

    response = exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    fields: dict[str, str] = {}
    message = ""

    if isinstance(exc, exceptions.ValidationError) and isinstance(data, dict):
        for key, value in data.items():
            if key in ("non_field_errors", "detail"):
                message = _first_message(value)
            else:
                fields[key] = _first_message(value)
        if not message:
            message = "Check the highlighted fields." if fields else "The request is not valid."
    elif isinstance(exc, exceptions.ValidationError):
        message = _first_message(data)
    elif isinstance(data, dict) and "detail" in data:
        message = str(data["detail"])
    else:
        message = _first_message(data)

    code = CODES.get(response.status_code, "error")
    if isinstance(data, dict) and getattr(data.get("detail"), "code", None) == "token_not_valid":
        code = "token_not_valid"

    response.data = {"code": code, "message": message, "fields": fields}
    return response
