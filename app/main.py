from __future__ import annotations

import asyncio
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .api_discovery import discover_repo, discover_url
from .core import BASE_DIR, CONFIG_PATH, load_config, merge_secret, safe_config, save_config
from .database_service import test_connection
from .runner import generate_case_from_database, run_saved_cases
from .session_lifecycle import lifecycle
from .storage import add_manual_case, get_case, get_run, init_db, list_cases, list_runs, update_case, upsert_endpoints


STATIC_DIR = Path(getattr(__import__("sys"), "_MEIPASS", BASE_DIR)) / "static"
app = FastAPI(title="API 回歸測試工具")


class RunRequest(BaseModel):
    project_id: str
    environment: str
    token: str = ""
    case_ids: list[int] | None = None
    run_headers: dict[str, str] = Field(default_factory=dict)


class ImportRequest(BaseModel):
    source: str = "openapi_url"
    environment: str = ""


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=200)


def find_project(config: dict[str, Any], project_id: str) -> dict[str, Any]:
    project = next((p for p in config.get("projects", []) if p.get("id") == project_id), None)
    if not project:
        raise HTTPException(404, "找不到專案")
    return project


def find_env(project: dict[str, Any], name: str) -> dict[str, Any]:
    env = next((e for e in project.get("environments", []) if e.get("name") == name), None)
    if not env:
        raise HTTPException(404, "找不到環境")
    return env


@app.on_event("startup")
def startup() -> None:
    init_db()
    lifecycle.start()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/session/heartbeat")
def session_heartbeat(request: SessionRequest):
    lifecycle.heartbeat(request.session_id)
    return {"ok": True}


@app.post("/api/session/leave")
def session_leave(request: SessionRequest):
    lifecycle.leave(request.session_id)
    return {"ok": True, "shutdown_in_seconds": 3}


@app.get("/api/config")
def get_config():
    return safe_config(load_config())


@app.put("/api/config")
def put_config(data: dict[str, Any]):
    try:
        save_config(merge_secret(load_config(), data))
        return {"ok": True, "path": str(CONFIG_PATH)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/database/test")
def database_test(data: dict[str, Any] | None = None):
    try:
        cfg = load_config().get("database", {}) if data is None else merge_secret(load_config(), {"database": data}).get("database", {})
        return test_connection(cfg)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/apis/import")
async def import_apis(project_id: str, request: ImportRequest):
    config = load_config()
    project = find_project(config, project_id)
    try:
        if request.source == "repo":
            endpoints = await asyncio.to_thread(discover_repo, project.get("repo_path", ""))
        elif request.source == "openapi_url":
            env = find_env(project, request.environment)
            endpoints = await discover_url(env)
        elif request.source == "saved":
            return list_cases(project_id)
        else:
            raise ValueError("API 來源必須是 saved、repo 或 openapi_url")
        return upsert_endpoints(project_id, endpoints)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/environments/{environment}/apis")
async def legacy_apis(project_id: str, environment: str):
    config = load_config()
    project = find_project(config, project_id)
    env = find_env(project, environment)
    try:
        return upsert_endpoints(project_id, await discover_url(env))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/cases")
def cases(project_id: str):
    find_project(load_config(), project_id)
    return list_cases(project_id)


@app.post("/api/projects/{project_id}/cases")
def create_case(project_id: str, data: dict[str, Any]):
    find_project(load_config(), project_id)
    try:
        return add_manual_case(project_id, data)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/cases/{case_id}")
def case_detail(case_id: int):
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "找不到測試案例")
    return case


@app.put("/api/cases/{case_id}")
def save_case(case_id: int, data: dict[str, Any]):
    try:
        return update_case(case_id, data)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/cases/{case_id}/generate-from-database")
async def case_from_database(case_id: int):
    config = load_config()
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "找不到測試案例")
    project = find_project(config, case["project_id"])
    try:
        return await asyncio.to_thread(generate_case_from_database, config, project, case_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/runs")
async def execute(request: RunRequest):
    config = load_config()
    project = find_project(config, request.project_id)
    env = find_env(project, request.environment)
    blocked = [str(x).lower() for x in config.get("security", {}).get("blocked_host_keywords", [])]
    if any(word and word in env["base_url"].lower() for word in blocked):
        raise HTTPException(400, "目標 Domain 命中正式環境阻擋規則")
    try:
        return await run_saved_cases(config, project, env, request.token, request.case_ids, request.run_headers)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/runs")
def runs(project_id: str):
    find_project(load_config(), project_id)
    return list_runs(project_id)


@app.get("/api/reports/{run_id}/{kind}")
def report_file(run_id: int, kind: str):
    run = get_run(run_id)
    if not run or kind not in {"html", "json"}:
        raise HTTPException(404, "找不到報告")
    path = Path(run[f"{kind}_file"]).resolve()
    report_root = (BASE_DIR / load_config().get("reports", {}).get("directory", "reports")).resolve()
    if report_root not in path.parents or not path.is_file():
        raise HTTPException(404, "報告檔案不存在")
    return FileResponse(path, media_type="text/html" if kind == "html" else "application/json", filename=path.name)


def start() -> None:
    # PyInstaller --noconsole sets stdout/stderr to None when launched from
    # Explorer. Uvicorn's default formatter calls isatty(), so give it safe
    # sinks before logging is configured.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    config = load_config()
    host = config.get("application", {}).get("host", "127.0.0.1")
    port = int(config.get("application", {}).get("port", 8765))
    if config.get("application", {}).get("open_browser", True):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start()
