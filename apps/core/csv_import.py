"""Reading an uploaded CSV file for the import screens (products, customers).

Each import goes row by row: a good row is saved, a bad one is skipped and reported with its
line number, so one typo doesn't block a file of thousands.
"""

import csv
import io

from django.db import transaction
from rest_framework.exceptions import ValidationError

MAX_ROWS = 20000


def read_rows(request, required: list[str]) -> list[dict]:
    """The file's rows as dicts keyed by lower-case header. Refuses a file without the required
    columns, so a wrong file is caught before anything is saved."""
    upload = request.FILES.get("file")
    if upload is None:
        raise ValidationError({"file": "Choose a CSV file."})
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError(
            {"file": "The file is larger than 5 MB. Split it and import in parts."}
        )
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError({"file": "Save the file as CSV (UTF-8) and try again."}) from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [(name or "").strip().lower() for name in reader.fieldnames or []]
    missing = [name for name in required if name not in headers]
    if missing:
        raise ValidationError({"file": f"Missing column(s): {', '.join(missing)}."})
    rows = []
    for raw in reader:
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        if any(row.values()):
            rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValidationError({"file": f"More than {MAX_ROWS} rows. Split the file."})
    return rows


def run_import(rows: list[dict], save_row) -> dict:
    """Calls `save_row(row)` for each row in its own transaction; it returns "created" or
    "updated", or raises ValueError with a message for the person to fix."""
    result = {"created": 0, "updated": 0, "errors": []}
    for number, row in enumerate(rows, start=2):  # line 1 is the header
        try:
            with transaction.atomic():
                result[save_row(row)] += 1
        except ValueError as exc:
            result["errors"].append({"row": number, "message": str(exc)})
        except Exception as exc:  # a database rule we did not foresee: report, keep going
            result["errors"].append({"row": number, "message": str(exc)[:200]})
    result["errors"] = result["errors"][:200]
    return result
