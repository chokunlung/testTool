from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .core import BASE_DIR


DB_PATH = BASE_DIR / "test-data.db"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                operation_id TEXT NOT NULL DEFAULT '',
                api_name TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT 'manual',
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, method, path)
            );

            CREATE TABLE IF NOT EXISTS test_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '預設案例',
                enabled INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'openapi',
                request_json TEXT NOT NULL DEFAULT '{}',
                expected_json TEXT NOT NULL DEFAULT '{}',
                user_modified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(endpoint_id) REFERENCES api_endpoints(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                json_file TEXT NOT NULL,
                html_file TEXT NOT NULL
            );
            """
        )


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("tags_json", "request_json", "expected_json", "summary_json"):
        if key in item:
            value = item.pop(key)
            item[key.removesuffix("_json")] = json.loads(value or "{}")
    for key in ("enabled", "user_modified"):
        if key in item:
            item[key] = bool(item[key])
    return item


def upsert_endpoints(project_id: str, endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    init_db()
    now = _now()
    with connection() as conn:
        for endpoint in endpoints:
            method = str(endpoint.get("method", "GET")).upper()
            path = str(endpoint.get("path", "/"))
            conn.execute(
                """
                INSERT INTO api_endpoints
                    (project_id, method, path, summary, operation_id, api_name, tags_json, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, method, path) DO UPDATE SET
                    summary=excluded.summary,
                    operation_id=excluded.operation_id,
                    api_name=CASE WHEN api_endpoints.api_name='' THEN excluded.api_name ELSE api_endpoints.api_name END,
                    tags_json=excluded.tags_json,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    method,
                    path,
                    endpoint.get("summary", ""),
                    endpoint.get("operation_id", ""),
                    endpoint.get("api_name") or endpoint.get("operation_id") or f"{method} {path}",
                    json.dumps(endpoint.get("tags", []), ensure_ascii=False),
                    endpoint.get("source", "openapi"),
                    now,
                ),
            )
            endpoint_row = conn.execute(
                "SELECT id FROM api_endpoints WHERE project_id=? AND method=? AND path=?",
                (project_id, method, path),
            ).fetchone()
            existing_case = conn.execute(
                "SELECT id, user_modified FROM test_cases WHERE endpoint_id=? ORDER BY id LIMIT 1",
                (endpoint_row["id"],),
            ).fetchone()
            if existing_case is None:
                conn.execute(
                    """
                    INSERT INTO test_cases
                        (endpoint_id, name, enabled, source, request_json, expected_json, user_modified, created_at, updated_at)
                    VALUES (?, '預設案例', 1, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        endpoint_row["id"],
                        endpoint.get("request_source", "openapi"),
                        json.dumps(endpoint.get("request", {}), ensure_ascii=False),
                        json.dumps(endpoint.get("expected", {"http_statuses": [200]}), ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            elif not existing_case["user_modified"] and endpoint.get("request"):
                conn.execute(
                    "UPDATE test_cases SET request_json=?, updated_at=? WHERE id=?",
                    (json.dumps(endpoint["request"], ensure_ascii=False), now, existing_case["id"]),
                )
    return list_cases(project_id)


def list_cases(project_id: str, enabled_only: bool = False) -> list[dict[str, Any]]:
    init_db()
    where = "WHERE e.project_id=?"
    params: list[Any] = [project_id]
    if enabled_only:
        where += " AND c.enabled=1"
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, e.project_id, e.method, e.path, e.summary, e.operation_id,
                   e.api_name, e.tags_json, e.source AS endpoint_source
            FROM test_cases c
            JOIN api_endpoints e ON e.id=c.endpoint_id
            {where}
            ORDER BY e.path, e.method, c.id
            """,
            params,
        ).fetchall()
    return [_decode(row) for row in rows]


def get_case(case_id: int) -> dict[str, Any] | None:
    init_db()
    with connection() as conn:
        row = conn.execute(
            """
            SELECT c.*, e.project_id, e.method, e.path, e.summary, e.operation_id,
                   e.api_name, e.tags_json, e.source AS endpoint_source
            FROM test_cases c JOIN api_endpoints e ON e.id=c.endpoint_id
            WHERE c.id=?
            """,
            (case_id,),
        ).fetchone()
    return _decode(row) if row else None


def update_case(case_id: int, data: dict[str, Any], user_modified: bool = True) -> dict[str, Any]:
    current = get_case(case_id)
    if not current:
        raise KeyError(f"找不到測試案例 {case_id}")
    request = data.get("request", current["request"])
    expected = data.get("expected", current["expected"])
    with connection() as conn:
        conn.execute(
            """
            UPDATE test_cases SET name=?, enabled=?, source=?, request_json=?, expected_json=?,
                                  user_modified=?, updated_at=?
            WHERE id=?
            """,
            (
                data.get("name", current["name"]),
                1 if data.get("enabled", current["enabled"]) else 0,
                data.get("source", current["source"]),
                json.dumps(request, ensure_ascii=False),
                json.dumps(expected, ensure_ascii=False),
                1 if user_modified else 0,
                _now(),
                case_id,
            ),
        )
        api_name = data.get("api_name")
        if api_name is not None:
            conn.execute("UPDATE api_endpoints SET api_name=?, updated_at=? WHERE id=?", (api_name, _now(), current["endpoint_id"]))
    return get_case(case_id) or {}


def add_manual_case(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    endpoint = {
        "method": str(data.get("method", "GET")).upper(),
        "path": data.get("path", "/"),
        "summary": data.get("summary", ""),
        "operation_id": data.get("operation_id", ""),
        "api_name": data.get("api_name", ""),
        "source": "manual",
        "request": data.get("request", {}),
        "expected": data.get("expected", {"http_statuses": [200]}),
        "request_source": "manual",
    }
    cases = upsert_endpoints(project_id, [endpoint])
    matching = next(item for item in cases if item["method"] == endpoint["method"] and item["path"] == endpoint["path"])
    return update_case(matching["id"], {**data, "source": "manual"})


def record_run(project_id: str, environment: str, executed_at: str, summary: dict[str, int], json_file: str, html_file: str) -> int:
    init_db()
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO run_history(project_id, environment, executed_at, summary_json, json_file, html_file) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, environment, executed_at, json.dumps(summary, ensure_ascii=False), json_file, html_file),
        )
        return int(cursor.lastrowid)


def list_runs(project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM run_history WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, max(1, min(limit, 500))),
        ).fetchall()
    return [_decode(row) for row in rows]


def get_run(run_id: int) -> dict[str, Any] | None:
    init_db()
    with connection() as conn:
        row = conn.execute("SELECT * FROM run_history WHERE id=?", (run_id,)).fetchone()
    return _decode(row) if row else None


def delete_repo_data(project_id: str) -> dict[str, Any]:
    init_db()
    with connection() as conn:
        endpoint_count = int(conn.execute("SELECT COUNT(*) FROM api_endpoints WHERE project_id=?", (project_id,)).fetchone()[0])
        case_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM test_cases c JOIN api_endpoints e ON e.id=c.endpoint_id WHERE e.project_id=?",
                (project_id,),
            ).fetchone()[0]
        )
        runs = conn.execute(
            "SELECT json_file, html_file FROM run_history WHERE project_id=?",
            (project_id,),
        ).fetchall()
        conn.execute("DELETE FROM run_history WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM api_endpoints WHERE project_id=?", (project_id,))
    return {
        "endpoints": endpoint_count,
        "cases": case_count,
        "runs": len(runs),
        "report_files": [str(row[key]) for row in runs for key in ("json_file", "html_file") if row[key]],
    }
