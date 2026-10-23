from __future__ import annotations

from typing import Any

from .core import validate_identifier


def _driver():
    try:
        import oracledb
        return oracledb
    except Exception as exc:
        raise RuntimeError(f"無法載入 Oracle Driver：{exc}") from exc


def connect(cfg: dict[str, Any]):
    driver = _driver()
    dsn = driver.makedsn(
        cfg.get("host", ""),
        int(cfg.get("port", 1521)),
        service_name=cfg.get("service_name") or cfg.get("database", ""),
    )
    return driver.connect(user=cfg.get("username", ""), password=cfg.get("password", ""), dsn=dsn)


def test_connection(cfg: dict[str, Any]) -> dict[str, Any]:
    with connect(cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'DB_NAME') FROM DUAL")
            row = cursor.fetchone()
    return {"ok": True, "server": row[0] if row else cfg.get("service_name")}


def fetch_success_logs(cfg: dict[str, Any], api_name: str | None = None) -> list[dict[str, Any]]:
    schema = validate_identifier(cfg["schema"], "Schema")
    table = validate_identifier(cfg["table"], "Table")
    columns = cfg["columns"]
    selected_keys = [
        "id", "request_time", "http_method", "request_uri", "query_string",
        "request_headers", "request_body", "content_type", "log_return_code", "system_code", "api_name",
    ]
    selected = [(key, validate_identifier(columns[key], key)) for key in selected_keys if columns.get(key)]
    select_sql = ", ".join(f'{column} AS "{key}"' for key, column in selected)
    rc_column = validate_identifier(columns["log_return_code"], "log_return_code")
    time_column = validate_identifier(columns["request_time"], "request_time")
    limit = min(max(int(cfg.get("query", {}).get("fetch_limit", 100)), 1), 10000)
    conditions = [f"{rc_column} = :return_code"]
    params: dict[str, Any] = {"return_code": str(cfg.get("query", {}).get("success_return_code", "200"))}
    system_code = str(cfg.get("query", {}).get("system_code", "")).strip()
    if system_code and columns.get("system_code"):
        conditions.append(f"{validate_identifier(columns['system_code'], 'system_code')} = :system_code")
        params["system_code"] = system_code
    if api_name and columns.get("api_name"):
        conditions.append(f"{validate_identifier(columns['api_name'], 'api_name')} = :api_name")
        params["api_name"] = api_name
    sql = (
        f"SELECT {select_sql} FROM {schema}.{table} "
        f"WHERE {' AND '.join(conditions)} ORDER BY {time_column} DESC FETCH FIRST {limit} ROWS ONLY"
    )
    with connect(cfg) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            names = [str(item[0]).lower() for item in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
