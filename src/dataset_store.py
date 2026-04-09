import base64
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from dotenv import load_dotenv

load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "data" / "sales.db"
SAMPLE_JSON_PATH = PROJECT_ROOT / "data" / "samples" / "sales_demo.json"

DATASET_REGISTRY_TABLE = "app_dataset_registry"
INGESTION_JOBS_TABLE = "app_ingestion_jobs"
SYSTEM_TABLES = {DATASET_REGISTRY_TABLE, INGESTION_JOBS_TABLE}
DEFAULT_DATASET_ID = "core_sales"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def sanitize_identifier(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        normalized = "field"
    if normalized[0].isdigit():
        normalized = f"col_{normalized}"
    return normalized


def unique_identifiers(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for column in columns:
        base_name = sanitize_identifier(column)
        counter = seen.get(base_name, 0)
        seen[base_name] = counter + 1
        if counter:
            result.append(f"{base_name}_{counter + 1}")
        else:
            result.append(base_name)
    return result


def ensure_metadata_tables() -> None:
    with get_connection() as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {DATASET_REGISTRY_TABLE} (
                dataset_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                table_names_json TEXT NOT NULL,
                source_file TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                table_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {INGESTION_JOBS_TABLE} (
                job_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_file TEXT,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def get_table_columns(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"PRAGMA table_info({quote_identifier(table_name)})"
    ).fetchall()
    return [
        {
            "name": row[1],
            "type": row[2] or "TEXT",
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": bool(row[5]),
        }
        for row in rows
    ]


def get_table_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) FROM {quote_identifier(table_name)}"
    ).fetchone()
    return int(row[0]) if row else 0


def upsert_dataset_record(
    connection: sqlite3.Connection,
    dataset_id: str,
    display_name: str,
    source_type: str,
    table_names: list[str],
    source_file: str | None,
    description: str,
    created_at: str,
    row_count: int,
) -> None:
    connection.execute(
        f"""
        INSERT INTO {DATASET_REGISTRY_TABLE} (
            dataset_id,
            display_name,
            source_type,
            table_names_json,
            source_file,
            description,
            created_at,
            row_count,
            table_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_id) DO UPDATE SET
            display_name = excluded.display_name,
            source_type = excluded.source_type,
            table_names_json = excluded.table_names_json,
            source_file = excluded.source_file,
            description = excluded.description,
            created_at = excluded.created_at,
            row_count = excluded.row_count,
            table_count = excluded.table_count
        """,
        (
            dataset_id,
            display_name,
            source_type,
            json.dumps(table_names, ensure_ascii=False),
            source_file,
            description,
            created_at,
            row_count,
            len(table_names),
        ),
    )


def register_ingestion_job(
    connection: sqlite3.Connection,
    dataset_id: str,
    source_type: str,
    source_file: str | None,
    status: str,
    message: str,
) -> None:
    connection.execute(
        f"""
        INSERT INTO {INGESTION_JOBS_TABLE} (
            job_id,
            dataset_id,
            source_type,
            source_file,
            status,
            message,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid4()), dataset_id, source_type, source_file, status, message, utc_now_iso()),
    )


def sync_core_sales_dataset() -> None:
    """Disabled — no longer auto-register built-in tables as a dataset.
    Users must import data explicitly."""
    ensure_metadata_tables()


def dataset_from_row(connection: sqlite3.Connection, row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    table_names = json.loads(row[3])
    schema_summary = build_schema_summary(connection, table_names)
    return {
        "dataset_id": row[0],
        "display_name": row[1],
        "source_type": row[2],
        "table_names": table_names,
        "source_file": row[4],
        "description": row[5] or "",
        "created_at": row[6],
        "row_count": int(row[7] or 0),
        "table_count": int(row[8] or 0),
        "schema_summary": schema_summary,
    }


def list_datasets() -> list[dict[str, Any]]:
    ensure_metadata_tables()
    sync_core_sales_dataset()
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                dataset_id,
                display_name,
                source_type,
                table_names_json,
                source_file,
                description,
                created_at,
                row_count,
                table_count
            FROM {DATASET_REGISTRY_TABLE}
            ORDER BY CASE WHEN dataset_id = ? THEN 0 ELSE 1 END, created_at DESC
            """,
            (DEFAULT_DATASET_ID,),
        ).fetchall()
        return [dataset_from_row(connection, row) for row in rows]


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    ensure_metadata_tables()
    sync_core_sales_dataset()
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT
                dataset_id,
                display_name,
                source_type,
                table_names_json,
                source_file,
                description,
                created_at,
                row_count,
                table_count
            FROM {DATASET_REGISTRY_TABLE}
            WHERE dataset_id = ?
            """,
            (dataset_id,),
        ).fetchone()
        if row is None:
            return None
        return dataset_from_row(connection, row)


def list_chat_tables() -> list[str]:
    ensure_metadata_tables()
    sync_core_sales_dataset()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [row[0] for row in rows if row[0] not in SYSTEM_TABLES]


def load_table_preview(table_name: str, limit: int = 10) -> pd.DataFrame:
    with get_connection() as connection:
        return pd.read_sql_query(
            f"SELECT * FROM {quote_identifier(table_name)} LIMIT {int(limit)}",
            connection,
        )


def build_schema_summary(connection: sqlite3.Connection, table_names: list[str]) -> str:
    sections: list[str] = []
    for table_name in table_names:
        columns = get_table_columns(connection, table_name)
        if not columns:
            continue
        formatted_columns = ", ".join(
            f"{column['name']} {column['type']}" for column in columns[:10]
        )
        if len(columns) > 10:
            formatted_columns += ", ..."
        sample_rows = connection.execute(
            f"SELECT * FROM {quote_identifier(table_name)} LIMIT 2"
        ).fetchall()
        sections.append(
            f"Table {table_name}: columns [{formatted_columns}] sample_rows={sample_rows}"
        )
    return "\n".join(sections)


def load_dataset_previews(dataset_id: str, limit: int = 10) -> dict[str, pd.DataFrame]:
    dataset = get_dataset(dataset_id)
    if dataset is None:
        return {}
    previews: dict[str, pd.DataFrame] = {}
    for table_name in dataset["table_names"]:
        previews[table_name] = load_table_preview(table_name, limit=limit)
    return previews


def serialize_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def sanitize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    sanitized = dataframe.copy()
    sanitized.columns = unique_identifiers([str(column) for column in sanitized.columns])
    for column in sanitized.columns:
        sanitized[column] = sanitized[column].map(serialize_value)
    return sanitized


def find_record_collection(node: Any) -> list[Any] | None:
    if isinstance(node, list):
        if not node:
            return []
        if all(isinstance(item, dict) for item in node):
            return node
        if all(not isinstance(item, (dict, list)) for item in node):
            return [{"value": item} for item in node]
        for item in node:
            candidate = find_record_collection(item)
            if candidate is not None:
                return candidate
        return None
    if isinstance(node, dict):
        for value in node.values():
            candidate = find_record_collection(value)
            if candidate is not None:
                return candidate
        return [node]
    return None


def dataframe_from_json_text(raw_text: str) -> pd.DataFrame:
    payload = json.loads(raw_text)
    record_collection = find_record_collection(payload)
    if record_collection is None:
        raise ValueError("Khong tim thay tap ban ghi hop le trong file JSON.")
    if isinstance(record_collection, list) and record_collection and isinstance(record_collection[0], dict):
        dataframe = pd.json_normalize(record_collection, sep="_")
    else:
        dataframe = pd.DataFrame(record_collection)
    if dataframe.empty:
        raise ValueError("File JSON khong co ban ghi de import.")
    return sanitize_dataframe(dataframe)


def _detect_header_rows(file_bytes: bytes, encoding: str) -> int:
    """Detect if CSV has multi-level headers (2 rows) or single header (1 row).

    Heuristic: read first 2 rows. If row 0 has many empty/unnamed cells
    (merged parent columns) and row 1 has mostly non-empty cells (child columns),
    treat as 2-row header.
    """
    try:
        df_peek = pd.read_csv(BytesIO(file_bytes), encoding=encoding, header=None, nrows=2)
    except Exception:
        return 1

    if len(df_peek) < 2:
        return 1

    row0 = df_peek.iloc[0]
    row1 = df_peek.iloc[1]

    # Count empty/blank cells in row 0 vs row 1
    empty_row0 = sum(1 for v in row0 if pd.isna(v) or str(v).strip() == "")
    empty_row1 = sum(1 for v in row1 if pd.isna(v) or str(v).strip() == "")
    total = len(row0)

    if total == 0:
        return 1

    # If row 0 has significantly more blanks than row 1, it's likely merged parent headers
    if empty_row0 > 0 and empty_row0 > empty_row1:
        return 2

    # Also check: if row 1 looks like text labels (not numeric data), might be sub-headers
    numeric_row1 = sum(1 for v in row1 if not pd.isna(v) and str(v).strip() != "" and _is_numeric(str(v)))
    text_row1 = total - empty_row1 - numeric_row1
    if text_row1 > total * 0.5 and empty_row0 > 0:
        return 2

    return 1


def _is_numeric(value: str) -> bool:
    try:
        float(value.replace(",", ""))
        return True
    except ValueError:
        return False


def _flatten_multiindex_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns into single-level by joining parent_child."""
    flat_columns: list[str] = []
    # Forward-fill parent names across merged (empty) cells
    parents = [str(c[0]) if not pd.isna(c[0]) and str(c[0]).strip() != "" else "" for c in dataframe.columns]
    last_parent = ""
    for i, p in enumerate(parents):
        if p and not p.startswith("Unnamed"):
            last_parent = p
        else:
            parents[i] = last_parent

    for i, col_tuple in enumerate(dataframe.columns):
        parent = parents[i]
        child = str(col_tuple[1]) if not pd.isna(col_tuple[1]) and str(col_tuple[1]).strip() != "" else ""
        if child and child.startswith("Unnamed"):
            child = ""
        if parent and child:
            flat_columns.append(f"{parent}_{child}")
        elif parent:
            flat_columns.append(parent)
        elif child:
            flat_columns.append(child)
        else:
            flat_columns.append(f"col_{i}")

    dataframe.columns = flat_columns
    return dataframe


def dataframe_from_csv_bytes(file_bytes: bytes) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "cp1252"]
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            header_rows = _detect_header_rows(file_bytes, encoding)
            if header_rows == 2:
                dataframe = pd.read_csv(BytesIO(file_bytes), encoding=encoding, header=[0, 1])
                dataframe = _flatten_multiindex_columns(dataframe)
            else:
                dataframe = pd.read_csv(BytesIO(file_bytes), encoding=encoding)
            return sanitize_dataframe(dataframe)
        except Exception as error:  # pragma: no cover - fallback path
            last_error = error
    if last_error is None:
        raise ValueError("Khong doc duoc file CSV.")
    raise last_error


def build_table_name(display_name: str) -> str:
    slug = sanitize_identifier(display_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"user_{slug}_{timestamp}"


def import_dataframe_dataset(
    dataframe: pd.DataFrame,
    display_name: str,
    source_type: str,
    source_file: str | None,
    description: str,
) -> dict[str, Any]:
    ensure_metadata_tables()
    table_name = build_table_name(display_name)
    dataset_id = str(uuid4())
    created_at = utc_now_iso()

    with get_connection() as connection:
        dataframe.to_sql(table_name, connection, if_exists="replace", index=False)
        upsert_dataset_record(
            connection=connection,
            dataset_id=dataset_id,
            display_name=display_name,
            source_type=source_type,
            table_names=[table_name],
            source_file=source_file,
            description=description,
            created_at=created_at,
            row_count=len(dataframe),
        )
        register_ingestion_job(
            connection=connection,
            dataset_id=dataset_id,
            source_type=source_type,
            source_file=source_file,
            status="success",
            message=f"Imported {len(dataframe)} rows into {table_name}.",
        )
        connection.commit()

    dataset = get_dataset(dataset_id)
    if dataset is None:
        raise ValueError("Khong the tai dataset vua import.")
    return dataset


def import_json_dataset(display_name: str, raw_text: str, source_file: str | None) -> dict[str, Any]:
    dataframe = dataframe_from_json_text(raw_text)
    description = "Dataset duoc import tu file JSON va map sang bang SQLite dong."
    return import_dataframe_dataset(
        dataframe=dataframe,
        display_name=display_name,
        source_type="json",
        source_file=source_file,
        description=description,
    )


def import_multi_json_dataset(
    display_name: str,
    files: list[dict[str, str]],
) -> dict[str, Any]:
    """Import nhieu file JSON vao cung 1 dataset, moi file thanh 1 bang rieng.

    Args:
        display_name: Ten hien thi cua dataset.
        files: List cac dict {"name": ten_file, "text": noi_dung_json}.
    """
    ensure_metadata_tables()
    dataset_id = str(uuid4())
    created_at = utc_now_iso()
    table_names: list[str] = []
    total_rows = 0
    source_files: list[str] = []

    with get_connection() as connection:
        for file_info in files:
            file_name = file_info["name"]
            raw_text = file_info["text"]
            dataframe = dataframe_from_json_text(raw_text)
            table_label = Path(file_name).stem
            table_name = build_table_name(table_label)
            dataframe.to_sql(table_name, connection, if_exists="replace", index=False)
            table_names.append(table_name)
            total_rows += len(dataframe)
            source_files.append(file_name)
            register_ingestion_job(
                connection=connection,
                dataset_id=dataset_id,
                source_type="json",
                source_file=file_name,
                status="success",
                message=f"Imported {len(dataframe)} rows into {table_name}.",
            )

        description = f"Dataset gom {len(files)} file JSON: {', '.join(source_files)}."
        upsert_dataset_record(
            connection=connection,
            dataset_id=dataset_id,
            display_name=display_name,
            source_type="json",
            table_names=table_names,
            source_file=", ".join(source_files),
            description=description,
            created_at=created_at,
            row_count=total_rows,
        )
        connection.commit()

    dataset = get_dataset(dataset_id)
    if dataset is None:
        raise ValueError("Khong the tai dataset vua import.")
    return dataset


def import_csv_dataset(display_name: str, file_bytes: bytes, source_file: str | None) -> dict[str, Any]:
    dataframe = dataframe_from_csv_bytes(file_bytes)
    description = "Dataset duoc import tu file CSV va map sang bang SQLite dong."
    return import_dataframe_dataset(
        dataframe=dataframe,
        display_name=display_name,
        source_type="csv",
        source_file=source_file,
        description=description,
    )


def load_sample_json_text() -> str:
    return SAMPLE_JSON_PATH.read_text(encoding="utf-8")


def list_recent_ingestion_jobs(limit: int = 8) -> list[dict[str, Any]]:
    ensure_metadata_tables()
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT dataset_id, source_type, source_file, status, message, created_at
            FROM {INGESTION_JOBS_TABLE}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "dataset_id": row[0],
                "source_type": row[1],
                "source_file": row[2],
                "status": row[3],
                "message": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]


def extract_table_from_image(image_bytes: bytes, mime_type: str) -> pd.DataFrame:
    """Use Gemini Vision to extract table data from an image."""
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("Thieu GEMINI_API_KEY hoac GOOGLE_API_KEY trong .env")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    image_part = {"mime_type": mime_type, "data": b64_data}

    prompt = (
        "Extract ALL data from the table in this image. "
        "If the table has multi-level headers (parent columns spanning multiple sub-columns), "
        "flatten them by combining parent and child names with underscore, e.g. 'Revenue_Q1'. "
        "Return the result as a JSON array of objects. Each object is one row. "
        "Keys are the flattened column names. Values are the cell values (use numbers for numeric cells). "
        "Return ONLY the JSON array, no markdown, no explanation."
    )

    response = model.generate_content([prompt, image_part])
    raw_text = response.text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```\w*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

    records = json.loads(raw_text)
    if not isinstance(records, list) or not records:
        raise ValueError("Gemini khong tra ve du lieu bang hop le.")

    dataframe = pd.DataFrame(records)
    return sanitize_dataframe(dataframe)


def import_image_dataset(
    display_name: str, image_bytes: bytes, mime_type: str, source_file: str | None
) -> dict[str, Any]:
    dataframe = extract_table_from_image(image_bytes, mime_type)
    description = "Dataset duoc import tu anh bang qua Gemini Vision OCR."
    return import_dataframe_dataset(
        dataframe=dataframe,
        display_name=display_name,
        source_type="image",
        source_file=source_file,
        description=description,
    )


def load_sql_result(sql_query: str, limit: int = 200) -> pd.DataFrame | None:
    cleaned_query = sql_query.strip().rstrip(";")
    normalized = cleaned_query.lower()
    if not normalized.startswith("select") and not normalized.startswith("with"):
        return None
    limited_query = f"SELECT * FROM ({cleaned_query}) LIMIT {int(limit)}"
    with get_connection() as connection:
        return pd.read_sql_query(limited_query, connection)
