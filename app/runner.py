from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

from .api_discovery import discover_url
from .core import json_path_get, parse_headers
from .database_service import fetch_success_logs
from .reporting import redact_headers, write_report
from .storage import get_case, list_cases, record_run, update_case


def endpoint_regex(path: str) -> re.Pattern[str]:
    escaped = re.escape(path.rstrip("/") or "/")
    escaped = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
    return re.compile(rf"^{escaped}/?$", re.IGNORECASE)


def clean_path(uri: str) -> str:
    try:
        return urlsplit(str(uri)).path or "/"
    except Exception:
        return str(uri).split("?", 1)[0]


async def discover_openapi(env: dict[str, Any]) -> list[dict[str, Any]]:
    return await discover_url(env)


def _headers(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            if isinstance(item, dict) and item.get("enabled", True) and item.get("name"):
                result[str(item["name"])] = str(item.get("value", ""))
        return result
    return {}


def log_to_request(log: dict[str, Any]) -> dict[str, Any]:
    query = str(log.get("query_string") or "").lstrip("?")
    body = log.get("request_body")
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            pass
    return {
        "path_params": {},
        "query_params": dict(parse_qsl(query, keep_blank_values=True)),
        "headers": parse_headers(log.get("request_headers")),
        "content_type": str(log.get("content_type") or ""),
        "body": body,
        "source_uri": str(log.get("request_uri") or ""),
        "source_log_id": str(log.get("id") or ""),
        "source_request_time": str(log.get("request_time") or ""),
    }


def generate_case_from_database(config: dict[str, Any], project: dict[str, Any], case_id: int) -> dict[str, Any]:
    case = get_case(case_id)
    if not case or case["project_id"] != project["id"]:
        raise KeyError("找不到測試案例")
    db_cfg = {**config.get("database", {}), **project.get("database", {})}
    logs = fetch_success_logs(db_cfg, case.get("api_name") or case.get("operation_id") or None)
    pattern = endpoint_regex(case["path"])
    found = next(
        (
            row for row in logs
            if str(row.get("http_method", "")).upper() == case["method"]
            and pattern.match(clean_path(row.get("request_uri", "")))
        ),
        logs[0] if logs else None,
    )
    if not found:
        raise ValueError(f"查無 {case.get('api_name') or case['path']} 的歷史 Request")
    return update_case(case_id, {"request": log_to_request(found), "source": "database"}, user_modified=False)


def _replace_path(path: str, values: dict[str, Any]) -> str:
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name)
        if value is None or value == "":
            missing.append(name)
            return match.group(0)
        return quote(str(value), safe="")

    result = re.sub(r"\{([^{}]+)\}", replace, path)
    if missing:
        raise ValueError("缺少 Path Parameter：" + ", ".join(missing))
    return result


def _effective_headers(config: dict[str, Any], project: dict[str, Any], env: dict[str, Any], request: dict[str, Any], run_headers: dict[str, Any], token: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for layer in (
        config.get("common_headers", {}),
        project.get("common_headers", {}),
        env.get("common_headers", {}),
        run_headers,
        request.get("headers", {}),
    ):
        headers.update(_headers(layer))
    remove = {str(item).lower() for item in config.get("security", {}).get("remove_headers", [])}
    headers = {key: value for key, value in headers.items() if key.lower() not in remove}
    content_type = request.get("content_type")
    if content_type:
        headers["Content-Type"] = str(content_type)
    auth = project.get("authentication", {})
    if token:
        prefix = str(auth.get("prefix", "")).strip()
        headers[str(auth.get("header_name", "Authorization"))] = f"{prefix} {token}".strip()
    return headers


def _assert_response(response: httpx.Response, expected: dict[str, Any], project: dict[str, Any], elapsed_ms: int) -> tuple[str, str | None, str | None]:
    allowed = [int(value) for value in (expected.get("http_statuses") or [200])]
    if response.status_code not in allowed:
        return "FAIL_HTTP", None, f"HTTP {response.status_code} 不在預期值 {allowed}"
    max_elapsed = expected.get("max_elapsed_ms")
    if max_elapsed and elapsed_ms > int(max_elapsed):
        return "FAIL_ASSERTION", None, f"回應時間 {elapsed_ms} ms 超過 {max_elapsed} ms"
    path = expected.get("return_code_path")
    codes = expected.get("success_codes") or []
    if not path and project.get("response", {}).get("return_code_path"):
        path = project["response"]["return_code_path"]
        codes = codes or [project.get("response", {}).get("success_code", "0000")]
    if path:
        try:
            code = str(json_path_get(response.json(), str(path)))
        except Exception as exc:
            return "INVALID_RESPONSE", None, f"無法讀取 {path}：{exc}"
        if codes and code not in {str(value) for value in codes}:
            return "FAIL_ASSERTION", code, f"Return Code {code} 不在預期值 {codes}"
        return "PASS", code, None
    return "PASS", None, None


async def execute_case(client: httpx.AsyncClient, case: dict[str, Any], config: dict[str, Any], project: dict[str, Any], env: dict[str, Any], token: str, run_headers: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    request = case.get("request") or {}
    base = {"case_id": case["id"], "case_name": case.get("name", ""), "method": case["method"], "path": case["path"], "source": case.get("source", "")}
    if not case.get("enabled", True):
        return {**base, "status": "SKIPPED"}
    try:
        path = _replace_path(case["path"], request.get("path_params", {}))
        url = env["base_url"].rstrip("/") + "/" + path.lstrip("/")
        headers = _effective_headers(config, project, env, request, run_headers, token)
        params = request.get("query_params") or {}
        body = request.get("body")
    except Exception as exc:
        return {**base, "status": "INVALID_REQUEST", "error": str(exc)}
    request_report = {"url": url, "query_params": params, "headers": redact_headers(headers), "body": body}
    started = datetime.now()
    try:
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        if body is not None:
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["content"] = str(body).encode("utf-8")
        async with semaphore:
            response = await client.request(case["method"], url, **kwargs)
        elapsed_ms = round((datetime.now() - started).total_seconds() * 1000)
        status, return_code, error = _assert_response(response, case.get("expected", {}), project, elapsed_ms)
        result = {
            **base,
            "status": status,
            "http_status": response.status_code,
            "return_code": return_code,
            "elapsed_ms": elapsed_ms,
            "request": request_report,
            "response": {"headers": redact_headers(dict(response.headers)), "body": response.text[:20000]},
        }
        if error:
            result["error"] = error
        return result
    except httpx.TimeoutException:
        return {**base, "status": "TIMEOUT", "request": request_report, "error": "Request timeout"}
    except Exception as exc:
        return {**base, "status": "CONNECTION_ERROR", "request": request_report, "error": str(exc)}


async def run_saved_cases(config: dict[str, Any], project: dict[str, Any], env: dict[str, Any], token: str, case_ids: list[int] | None, run_headers: dict[str, Any] | None = None) -> dict[str, Any]:
    all_cases = list_cases(project["id"])
    if case_ids is not None:
        if not case_ids:
            raise ValueError("尚未勾選任何測試案例")
        wanted = set(case_ids)
        cases = [item for item in all_cases if item["id"] in wanted]
        if len(cases) != len(wanted):
            raise ValueError("部分測試案例不存在或不屬於目前 Repo")
    else:
        cases = [item for item in all_cases if item.get("enabled", True)]
    if not cases:
        raise ValueError("目前沒有可執行的測試案例，請先匯入或新增 API")
    semaphore = asyncio.Semaphore(max(1, int(project.get("max_concurrency", 5))))
    async with httpx.AsyncClient(verify=env.get("verify_ssl", True), timeout=env.get("timeout_seconds", 30), follow_redirects=True) as client:
        results = await asyncio.gather(*(execute_case(client, case, config, project, env, token, run_headers or {}, semaphore) for case in cases))
    summary: dict[str, int] = {}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    executed_at = datetime.now().isoformat(timespec="seconds")
    report = {"project": project["name"], "environment": env["name"], "base_url": env["base_url"], "executed_at": executed_at, "summary": summary, "results": results}
    json_file, html_file = write_report(report, config.get("reports", {}), project["id"])
    run_id = record_run(project["id"], env["name"], executed_at, summary, str(json_file), str(html_file))
    report["run_id"] = run_id
    report["json_report_file"] = str(json_file)
    report["html_report_file"] = str(html_file)
    return report
