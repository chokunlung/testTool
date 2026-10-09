from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _driver():
    """Load DB2 lazily so the UI can open even when the native client needs repair."""
    candidates = []
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend([root / "clidriver" / "bin", root / "ibm_db" / "clidriver" / "bin"])
    candidates.append(Path(__file__).resolve().parent.parent / ".venv" / "Lib" / "site-packages" / "clidriver" / "bin")
    for path in candidates:
        if path.is_dir() and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(path))
            break
    try:
        import ibm_db
        return ibm_db
    except Exception as exc:
        raise RuntimeError(
            "無法載入 IBM DB2 驅動。請確認已安裝 Microsoft Visual C++ 2015-2022 x64 Runtime，"
            f"並重新啟動工具。原始錯誤：{exc}"
        ) from exc

from .core import validate_identifier


def connection_string(cfg: dict[str, Any]) -> str:
    parts = {
        "DATABASE": cfg.get("database", ""),
        "HOSTNAME": cfg.get("host", ""),
        "PORT": cfg.get("port", 50000),
        "PROTOCOL": cfg.get("protocol", "TCPIP"),
        "UID": cfg.get("username", ""),
        "PWD": cfg.get("password", ""),
    }
    return ";".join(f"{key}={value}" for key, value in parts.items()) + ";"


def connect(cfg: dict[str, Any]):
    ibm_db = _driver()
    return ibm_db.connect(connection_string(cfg), "", "")


def test_connection(cfg: dict[str, Any]) -> dict[str, Any]:
    ibm_db = _driver()
    conn = connect(cfg)
    try:
        stmt = ibm_db.exec_immediate(conn, "VALUES CURRENT SERVER")
        row = ibm_db.fetch_tuple(stmt)
        return {"ok": True, "server": row[0] if row else cfg.get("database")}
    finally:
        ibm_db.close(conn)


def fetch_success_logs(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    ibm_db = _driver()
    schema = validate_identifier(cfg["schema"], "Schema")
    table = validate_identifier(cfg["table"], "Table")
    columns = cfg["columns"]
    selected_keys = [
        "id", "request_time", "http_method", "request_uri", "query_string",
        "request_headers", "request_body", "content_type", "log_return_code", "system_code",
    ]
    selected = [(key, validate_identifier(columns[key], key)) for key in selected_keys if columns.get(key)]
    select_sql = ", ".join(f"{column} AS {key.upper()}" for key, column in selected)
    rc_column = validate_identifier(columns["log_return_code"], "log_return_code")
    time_column = validate_identifier(columns["request_time"], "request_time")
    limit = min(max(int(cfg.get("query", {}).get("fetch_limit", 10000)), 1), 100000)
    conditions = [f"{rc_column} = ?"]
    params: list[Any] = [str(cfg.get("query", {}).get("success_return_code", "200"))]
    system_code = str(cfg.get("query", {}).get("system_code", "")).strip()
    if system_code and columns.get("system_code"):
        conditions.append(f"{validate_identifier(columns['system_code'], 'system_code')} = ?")
        params.append(system_code)
    sql = (
        f"SELECT {select_sql} FROM {schema}.{table} "
        f"WHERE {' AND '.join(conditions)} ORDER BY {time_column} DESC FETCH FIRST {limit} ROWS ONLY"
    )
    conn = connect(cfg)
    try:
        stmt = ibm_db.prepare(conn, sql)
        for index, value in enumerate(params, 1):
            ibm_db.bind_param(stmt, index, value)
        ibm_db.execute(stmt)
        rows: list[dict[str, Any]] = []
        row = ibm_db.fetch_assoc(stmt)
        while row:
            rows.append({str(k).lower(): v for k, v in row.items()})
            row = ibm_db.fetch_assoc(stmt)
        return rows
    finally:
        ibm_db.close(conn)
