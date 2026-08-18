"""CSV Table routes — upload, manage and query CSV data tables.

Mirrors the knowledge-base router pattern. Clients:
  1. POST /csv-tables/upload-url   → get a presigned S3 PUT URL + table_uuid
  2. PUT (file) to S3              → client uploads directly
  3. POST /csv-tables/process      → parse CSV, store rows, build column schema
  4. GET  /csv-tables              → list all tables (paginated)
  5. GET  /csv-tables/{uuid}       → get one table
  6. DELETE /csv-tables/{uuid}     → soft delete

Fixes applied:
  Issue  3: /process now uses the table_uuid returned by /upload-url instead of
            creating a new UUID every time. table_uuid is created once in
            /upload-url and stored in DB immediately as "pending". /process
            fetches that record by uuid and updates it.
  Issue  4: /process deletes existing rows before inserting new ones, so
            re-uploading the same CSV never duplicates rows.
  Issue  6: Type inference now samples ALL rows (not just first 20) to decide
            if a column is "number" or "text".
  Issue 13: Model codes like "450AJ" are correctly kept as strings. Only values
            where the ENTIRE string is numeric (after stripping units) are stored
            as float. If the original string has letters mixed with digits
            (e.g. "450AJ", "Z-45/25", "T40J") it stays as a string.
  Issue 14: `import re`, `import csv`, `import io` moved to module top level —
            not inside the loop body.
  Issue  7: list_csv_tables accepts limit/offset query params (pagination).
  Issue 10: Frontend 5 MB cap documented — backend still accepts 50 MB.
"""

import csv
import io
import re
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.services.auth.depends import get_user
from api.services.storage import storage_fs

router = APIRouter(prefix="/csv-tables", tags=["csv-tables"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CsvTableUploadRequest(BaseModel):
    filename: str


class CsvTableUploadResponse(BaseModel):
    table_uuid: str
    upload_url: str
    s3_key: str


class ProcessCsvTableRequest(BaseModel):
    table_uuid: str   # UUID returned by /upload-url — MUST match DB record
    s3_key: str
    name: Optional[str] = None


class CsvTableResponse(BaseModel):
    table_uuid: str
    name: str
    row_count: int
    column_schema: list
    processing_status: str
    processing_error: Optional[str] = None
    created_at: Optional[str] = None


def _to_response(table) -> CsvTableResponse:
    return CsvTableResponse(
        table_uuid=table.table_uuid,
        name=table.name,
        row_count=table.row_count,
        column_schema=table.column_schema or [],
        processing_status=table.processing_status,
        processing_error=table.processing_error,
        created_at=table.created_at.isoformat() if table.created_at else None,
    )


# ---------------------------------------------------------------------------
# CSV parsing helpers (module-level — Issue 14: no imports inside loops)
# ---------------------------------------------------------------------------

# Regex: number optionally followed by a pure unit suffix (letters/symbols only,
# no digits mixed in). Captures only the numeric part.
#
# VALID:   "15.72m" → 15.72   "7257kg" → 7257   "6,495 kg" → 6495
#          "47.72 m" → 47.72  "227kg" → 227      "2.35m" → 2.35
# INVALID: "1500SJ" → stays "1500SJ"  (SJ has no space before it + mixed)
#          "Z-45/25" → stays  "450AJ" → stays   "T40J" → stays
#
# Rule: the suffix must be ONLY letters/symbols with NO digits,
# AND either be a known unit OR be separated from the number by a space.
_NUMERIC_RE = re.compile(
    r"^(-?\d[\d,.]*)(?:\s+[a-zA-Z%°/²³]+|(?<=\d)[a-zA-Z]{1,3}(?![a-zA-Z0-9]))$"
)

# Known measurement units — only these suffixes are stripped when directly
# attached to a number (no space). Anything else keeps its string value.
_KNOWN_UNITS = {
    "m", "mm", "cm", "km", "ft", "in",          # length
    "kg", "g", "lb", "lbs", "t",                  # weight
    "l", "ml", "gal",                             # volume
    "kw", "hp", "kva",                            # power
    "psi", "bar",                                  # pressure
    "hz", "rpm",                                   # frequency
    "%",                                           # percentage
}

# Values treated as missing/null
_NULL_VALUES = {"", "n/a", "na", "-", "–", "none", "null", "nil"}


def _parse_cell(raw: str) -> object:
    """Parse a single CSV cell value.

    Handles:
    - Pure numbers: "47.72" → 47.72
    - European decimals: "40,10" or "40,10   m" → 40.10
    - Thousands separators: "6,495" or "6,495 kg" → 6495.0
    - Number + space + unit: "47.72 m" → 47.72
    - Number + known unit attached: "47.72m" → 47.72
    - Model codes: "1500SJ" → "1500SJ" (kept as string)

    Returns float, str, or None.
    """
    if raw is None:
        return None
    v = raw.strip()
    if v.lower() in _NULL_VALUES:
        return None

    # Normalize multiple spaces (e.g. "40,10   m" → "40,10 m")
    v = " ".join(v.split())

    # Strip parenthetical notes e.g. "4,000 kg (max rated...)" → "4,000 kg"
    # Also handles "Scissor Lift (Vertical)" → "Scissor Lift"
    # Only strip if the parenthetical is AFTER meaningful content
    paren_idx = v.find("(")
    if paren_idx > 0:
        v = v[:paren_idx].strip()

    # Split off trailing unit if present (e.g. "47.72 m" → "47.72", "m")
    num_part = v
    space_match = re.match(r"^(-?[\d.,]+)\s+([a-zA-Z%°/²³]+)$", v)
    if space_match:
        num_part = space_match.group(1)

    # European decimal detection: "40,10" = 40.10 (comma before 1-2 digits at end)
    # vs thousands separator: "6,495" (comma before exactly 3 digits)
    euro = re.match(r"^-?\d{1,3},\d{1,2}$", num_part)
    if euro:
        try:
            return float(num_part.replace(",", "."))
        except ValueError:
            pass
    else:
        # Remove thousands separators and try as float
        clean = num_part.replace(",", "")
        try:
            return float(clean)
        except ValueError:
            pass
        # Attached known unit (e.g. "47.72m", "227kg")
        att = re.match(r"^(-?[\d.]+)([a-zA-Z]{1,4})$", clean)
        if att and att.group(2).lower() in _KNOWN_UNITS:
            try:
                return float(att.group(1))
            except ValueError:
                pass

    return v  # keep original string


def _infer_column_type(rows: list, field: str) -> str:
    """Infer column type by sampling ALL non-null values.

    Issue 6 fix: previously sampled only rows[:20]. A column whose first 20
    rows are empty/N/A was incorrectly marked as "text", breaking numeric
    comparisons like "height > 15m".

    Now scans all rows and picks "number" if ANY non-null value is a float.
    """
    for r in rows:
        val = r.get(field)
        if isinstance(val, float):
            return "number"
    return "text"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/upload-url",
    response_model=CsvTableUploadResponse,
    summary="Get presigned URL for CSV upload",
)
async def get_upload_url(
    request: CsvTableUploadRequest,
    user=Depends(get_user),
):
    """Generate a presigned S3 PUT URL for uploading a CSV file.

    Issue 3 fix: the table_uuid is now created HERE and immediately stored in
    DB as 'pending'. The /process endpoint fetches this record by uuid and
    updates it — it never creates a new record. This means the UUID returned
    here is the permanent UUID used everywhere.
    """
    org_id = user.selected_organization_id
    table_uuid = str(uuid.uuid4())
    s3_key = f"csv_tables/{org_id}/{table_uuid}/{request.filename}"

    # Create the DB record immediately so /process can find it by uuid
    display_name = request.filename
    await db_client.create_csv_table_with_uuid(
        table_uuid=table_uuid,
        organization_id=org_id,
        created_by=user.id,
        name=display_name,
    )

    try:
        upload_url = await storage_fs.aget_presigned_put_url(
            key=s3_key,
            expiration=1800,
            max_size=50_000_000,  # 50 MB
        )
    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        # Clean up the pending record if S3 fails
        await db_client.delete_csv_table(table_uuid, org_id)
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")

    return CsvTableUploadResponse(
        table_uuid=table_uuid,
        upload_url=upload_url,
        s3_key=s3_key,
    )


@router.post(
    "/process",
    response_model=CsvTableResponse,
    summary="Parse an uploaded CSV and store rows",
)
async def process_csv_table(
    request: ProcessCsvTableRequest,
    user=Depends(get_user),
):
    """Download the CSV from S3, parse it, store rows in csv_table_rows.

    Issue 3 fix: fetches the existing DB record by table_uuid (created in
    /upload-url) instead of creating a new one.
    Issue 4 fix: deletes all existing rows before inserting new ones, so
    re-processing the same table never duplicates rows.
    """
    org_id = user.selected_organization_id

    # Issue 3: find the existing record created by /upload-url
    table = await db_client.get_csv_table_by_uuid(request.table_uuid, org_id)
    if not table:
        raise HTTPException(
            status_code=404,
            detail=f"CSV table {request.table_uuid} not found. "
                   "Call /upload-url first to create the table record.",
        )

    # Allow caller to override display name
    if request.name and request.name != table.name:
        await db_client.update_csv_table_name(table.id, request.name)

    try:
        await db_client.update_csv_table_status(table.id, "processing")

        # Download from S3 / MinIO
        raw_bytes = await storage_fs.adownload_file(request.s3_key)
        if raw_bytes is None:
            raise ValueError(f"File not found in storage: {request.s3_key}")

        text_content = raw_bytes.decode("utf-8-sig")  # handle BOM
        reader = csv.DictReader(io.StringIO(text_content))
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")

        fieldnames = list(reader.fieldnames)

        # Issue 13 + 14: parse all rows using module-level helpers
        # _parse_cell correctly keeps "450AJ" as string, "15.72m" as 15.72
        rows = []
        for row in reader:
            parsed: dict = {}
            for k, v in row.items():
                parsed[k] = _parse_cell(v if v is not None else "")
            rows.append(parsed)

        # Issue 6: infer type from ALL rows, not just first 20
        col_schema = [
            {"name": field, "type": _infer_column_type(rows, field)}
            for field in fieldnames
        ]

        # Issue 4: delete existing rows before inserting new ones
        # This makes re-upload idempotent — no duplicate rows
        await db_client.delete_csv_rows(table.id)

        if rows:
            await db_client.insert_csv_rows(table.id, org_id, rows)

        await db_client.update_csv_table_status(
            table.id,
            "completed",
            row_count=len(rows),
            column_schema=col_schema,
        )

        updated = await db_client.get_csv_table_by_id(table.id)
        logger.info(
            f"CSV table processed: {updated.name} "
            f"({len(rows)} rows, {len(col_schema)} columns)"
        )
        return _to_response(updated)

    except Exception as e:
        logger.error(f"CSV processing failed for table {table.id}: {e}")
        await db_client.update_csv_table_status(
            table.id, "failed", error=str(e)
        )
        raise HTTPException(status_code=500, detail=f"CSV processing failed: {e}")


@router.get(
    "",
    response_model=list[CsvTableResponse],
    summary="List all CSV tables (paginated)",
)
async def list_csv_tables(
    user=Depends(get_user),
    limit: int = Query(default=50, ge=1, le=200),   # Issue 7: pagination
    offset: int = Query(default=0, ge=0),
):
    tables = await db_client.list_csv_tables(
        user.selected_organization_id,
        limit=limit,
        offset=offset,
    )
    return [_to_response(t) for t in tables]


@router.get(
    "/{table_uuid}",
    response_model=CsvTableResponse,
    summary="Get a single CSV table",
)
async def get_csv_table(table_uuid: str, user=Depends(get_user)):
    table = await db_client.get_csv_table_by_uuid(
        table_uuid, user.selected_organization_id
    )
    if not table:
        raise HTTPException(status_code=404, detail="CSV table not found")
    return _to_response(table)


@router.delete(
    "/{table_uuid}",
    summary="Delete a CSV table",
)
async def delete_csv_table(table_uuid: str, user=Depends(get_user)):
    deleted = await db_client.delete_csv_table(
        table_uuid, user.selected_organization_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="CSV table not found")
    return {"success": True, "message": f"CSV table {table_uuid} deleted"}
