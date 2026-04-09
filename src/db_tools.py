import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from crewai import LLM
from crewai.tools import BaseTool
from dotenv import find_dotenv, load_dotenv
from langchain_core.outputs import LLMResult
from langchain_core.callbacks.base import BaseCallbackHandler

from dataset_store import DB_PATH, list_chat_tables, quote_identifier


load_dotenv()
load_dotenv(find_dotenv())

MODEL_NAME = os.getenv("LLM_MODEL", "groq/llama-3.3-70b-versatile")

ACTIVE_TABLES: list[str] | None = None
LAST_QUERY_CONTEXT: dict[str, Any] = {
    "sql": None,
    "columns": [],
    "rows": [],
    "error": None,
}


@dataclass
class Event:
    event: str
    timestamp: str
    text: str


def _current_time() -> str:
    return datetime.now(timezone.utc).isoformat()


class LLMCallbackHandler(BaseCallbackHandler):
    def __init__(self, log_path: Path):
        self.log_path = log_path

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> Any:
        assert len(prompts) == 1
        event = Event(event="llm_start", timestamp=_current_time(), text=prompts[0])
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        generation = response.generations[-1][-1].message.content
        event = Event(event="llm_end", timestamp=_current_time(), text=generation)
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def load_llm() -> LLM:
    return LLM(
        model=MODEL_NAME,
        temperature=0.1,
        callbacks=[LLMCallbackHandler(Path("chat_log/prompts.jsonl"))],
    )


def set_active_tables(table_names: list[str] | None) -> None:
    global ACTIVE_TABLES
    ACTIVE_TABLES = list(table_names) if table_names else None


def get_active_tables() -> list[str]:
    all_tables = list_chat_tables()
    if ACTIVE_TABLES is None:
        return all_tables
    return [table for table in all_tables if table in ACTIVE_TABLES]


def clear_query_state() -> None:
    LAST_QUERY_CONTEXT["sql"] = None
    LAST_QUERY_CONTEXT["columns"] = []
    LAST_QUERY_CONTEXT["rows"] = []
    LAST_QUERY_CONTEXT["error"] = None


def get_last_query_context() -> dict[str, Any]:
    return {
        "sql": LAST_QUERY_CONTEXT["sql"],
        "columns": list(LAST_QUERY_CONTEXT["columns"]),
        "rows": list(LAST_QUERY_CONTEXT["rows"]),
        "error": LAST_QUERY_CONTEXT["error"],
    }


def parse_table_list(raw_tables: str | None) -> list[str]:
    if raw_tables is None or not raw_tables.strip():
        return get_active_tables()
    parsed = [item.strip() for item in raw_tables.split(",")]
    return [item for item in parsed if item]


def validate_table_scope(sql_query: str) -> None:
    allowed_tables = set(get_active_tables())
    if not allowed_tables:
        return

    referenced_tables: set[str] = set()
    for pattern in [r"\bfrom\s+([a-zA-Z_][\w]*)", r"\bjoin\s+([a-zA-Z_][\w]*)"]:
        for match in re.findall(pattern, sql_query, flags=re.IGNORECASE):
            referenced_tables.add(match)

    if referenced_tables and not referenced_tables.issubset(allowed_tables):
        forbidden_tables = ", ".join(sorted(referenced_tables - allowed_tables))
        allowed_text = ", ".join(sorted(allowed_tables))
        raise ValueError(
            f"SQL dang truy cap bang ngoai pham vi dataset hien tai: {forbidden_tables}. "
            f"Chi duoc dung cac bang: {allowed_text}."
        )


def fetch_schema_for_tables(table_names: list[str]) -> str:
    allowed_tables = set(get_active_tables())
    valid_table_names = [table for table in table_names if table in allowed_tables]
    if not valid_table_names:
        valid_table_names = get_active_tables()

    if not valid_table_names:
        return "Khong co bang nao kha dung trong dataset hien tai."

    with sqlite3.connect(DB_PATH) as connection:
        sections: list[str] = []
        for table_name in valid_table_names:
            columns = connection.execute(
                f"PRAGMA table_info({quote_identifier(table_name)})"
            ).fetchall()
            formatted_columns = ", ".join(
                f"{row[1]} {row[2] or 'TEXT'}" for row in columns
            )
            sample_rows = connection.execute(
                f"SELECT * FROM {quote_identifier(table_name)} LIMIT 3"
            ).fetchall()
            sections.append(
                f"Table {table_name}\nColumns: {formatted_columns}\nSample rows: {sample_rows}"
            )
        return "\n\n".join(sections)


def run_sql_query(sql_query: str) -> str:
    validate_table_scope(sql_query)
    normalized = sql_query.strip().lower()

    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.cursor()
        if normalized.startswith("select") or normalized.startswith("with"):
            cursor.execute(sql_query)
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description or []]
            LAST_QUERY_CONTEXT["sql"] = sql_query
            LAST_QUERY_CONTEXT["columns"] = columns
            LAST_QUERY_CONTEXT["rows"] = rows[:200]
            LAST_QUERY_CONTEXT["error"] = None

            if not rows:
                return "Query executed successfully. No rows returned."

            preview_rows = [dict(zip(columns, row)) for row in rows[:10]]
            return json.dumps(preview_rows, ensure_ascii=False, indent=2)

        cursor.execute(sql_query)
        connection.commit()
        LAST_QUERY_CONTEXT["sql"] = sql_query
        LAST_QUERY_CONTEXT["columns"] = []
        LAST_QUERY_CONTEXT["rows"] = []
        LAST_QUERY_CONTEXT["error"] = None
        return f"Statement executed successfully. Rows affected: {cursor.rowcount}"


class ListTablesTool(BaseTool):
    name: str = "list_tables"
    description: str = "Danh sach cac bang duoc phep su dung trong dataset hien tai. Truyen bat ky gia tri nao."

    def _run(self, placeholder: str = "") -> str:
        available_tables = get_active_tables()
        if not available_tables:
            return "Khong co bang nao kha dung."
        return ", ".join(available_tables)


class TablesSchemaTool(BaseTool):
    name: str = "tables_schema"
    description: str = (
        "Nhap danh sach bang tach boi dau phay va nhan ve schema cung du lieu mau "
        "chi trong pham vi dataset dang chon."
    )

    def _run(self, tables: str) -> str:
        return fetch_schema_for_tables(parse_table_list(tables))


class ExecuteSQLTool(BaseTool):
    name: str = "execute_sql"
    description: str = (
        "Thuc hien truy van SQL tren SQLite trong pham vi dataset hien tai va tra ve ket qua."
    )

    def _run(self, sql_query: str) -> str:
        return run_sql_query(sql_query)


class CheckSQLTool(BaseTool):
    name: str = "check_sql"
    description: str = "Cong cu giu lai de tuong thich, hien khong su dung de tiet kiem quota."

    def _run(self, sql_query: str) -> str:
        validate_table_scope(sql_query)
        return "SQL scope looks valid for the active dataset."

