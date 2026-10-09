from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .core import BASE_DIR, json_path_get, parse_headers
from .db2_service import fetch_success_logs


def endpoint_regex(path: str) -> re.Pattern[str]:
    escaped = re.escape(path.rstrip("/") or "/")
    escaped = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
    return re.compile(rf"^{escaped}/?$", re.IGNORECASE)


def clean_path(uri: str) -> str:
    try:
        return urlsplit(str(uri)).path or "/"
    except Exception:
        return str(uri).split("?", 1)[0]


async def discover_openapi(env: dict[str, Any]) -> list[dict[str, str]]:
    url = env["base_url"].rstrip("/") + "/" + env.get("openapi_path", "/v3/api-docs").lstrip("/")
    async with httpx.AsyncClient(verify=env.get("verify_ssl", True), timeout=env.get("timeout_seconds", 30)) as client:
        response = await client.get(url)
        response.raise_for_status()
        document = response.json()
    endpoints: list[dict[str, str]] = []
    for path, methods in document.get("paths", {}).items():
        for method, spec in methods.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                endpoints.append({"method": method.upper(), "path": path, "summary": spec.get("summary", "")})
    return sorted(endpoints, key=lambda item: (item["path"], item["method"]))


def match_cases(endpoints: list[dict[str, str]], logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for endpoint in endpoints:
        pattern = endpoint_regex(endpoint["path"])
        found = next((row for row in logs if str(row.get("http_method", "")).upper() == endpoint["method"] and pattern.match(clean_path(row.get("request_uri", "")))), None)
        cases.append({**endpoint, "log": found})
    return cases


def build_headers(log: dict[str, Any], project: dict[str, Any], token: str, remove: list[str]) -> dict[str, str]:
    blocked = {item.lower() for item in remove}
    headers = {k: v for k, v in parse_headers(log.get("request_headers")).items() if k.lower() not in blocked}
    content_type = log.get("content_type")
    if content_type:
        headers["Content-Type"] = str(content_type)
    auth = project.get("authentication", {})
    if token:
        prefix = str(auth.get("prefix", "")).strip()
        headers[auth.get("header_name", "Authorization")] = f"{prefix} {token}".strip()
    return headers


async def execute_case(client: httpx.AsyncClient, case: dict[str, Any], project: dict[str, Any], env: dict[str, Any], token: str, remove: list[str], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    log = case.get("log")
    if not log:
        return {"method": case["method"], "path": case["path"], "status": "NO_TEST_DATA"}
    path = clean_path(log.get("request_uri", ""))
    query = str(log.get("query_string") or "").lstrip("?")
    url = env["base_url"].rstrip("/") + "/" + path.lstrip("/") + (f"?{query}" if query else "")
    headers = build_headers(log, project, token, remove)
    body = log.get("request_body")
    started = datetime.now()
    result = {"method": case["method"], "path": case["path"], "url": url, "log_id": str(log.get("id", "")), "request_time": str(log.get("request_time", ""))}
    try:
        async with semaphore:
            response = await client.request(case["method"], url, headers=headers, content=str(body).encode("utf-8") if body is not None else None)
        result["http_status"] = response.status_code
        result["elapsed_ms"] = round((datetime.now() - started).total_seconds() * 1000)
        result["response_preview"] = response.text[:4000]
        if response.status_code != 200:
            result["status"] = "FAIL_HTTP"
            return result
        try:
            payload = response.json()
            code = json_path_get(payload, project.get("response", {}).get("return_code_path", "$.returnCode"))
            result["return_code"] = str(code)
            result["status"] = "PASS" if str(code) == str(project.get("response", {}).get("success_code", "0000")) else "FAIL_CODE"
        except Exception as exc:
            result.update(status="FAIL_FORMAT", error=str(exc))
    except httpx.TimeoutException:
        result["status"] = "TIMEOUT"
    except Exception as exc:
        result.update(status="CONNECTION_ERROR", error=str(exc))
    return result


async def run_tests(config: dict[str, Any], project: dict[str, Any], env: dict[str, Any], token: str, selected: list[str] | None = None) -> dict[str, Any]:
    endpoints = await discover_openapi(env)
    if selected:
        wanted = set(selected)
        endpoints = [e for e in endpoints if f"{e['method']} {e['path']}" in wanted]
    logs = await asyncio.to_thread(fetch_success_logs, config["database"])
    cases = match_cases(endpoints, logs)
    semaphore = asyncio.Semaphore(max(1, int(project.get("max_concurrency", 5))))
    async with httpx.AsyncClient(verify=env.get("verify_ssl", True), timeout=env.get("timeout_seconds", 30), follow_redirects=True) as client:
        results = await asyncio.gather(*(execute_case(client, case, project, env, token, config.get("security", {}).get("remove_headers", []), semaphore) for case in cases))
    summary: dict[str, int] = {}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    report = {"project": project["name"], "environment": env["name"], "base_url": env["base_url"], "executed_at": datetime.now().isoformat(timespec="seconds"), "summary": summary, "results": results}
    report_dir = BASE_DIR / config.get("reports", {}).get("directory", "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"report_{project['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    (report_dir / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_file"] = str(report_dir / filename)
    return report
