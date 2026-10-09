from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .core import BASE_DIR, CONFIG_PATH, load_config, merge_secret, safe_config, save_config
from .db2_service import test_connection
from .runner import discover_openapi, run_tests


STATIC_DIR = Path(getattr(__import__('sys'), "_MEIPASS", BASE_DIR)) / "static"
app = FastAPI(title="API 回歸測試工具")


class RunRequest(BaseModel):
    project_id: str
    environment: str
    token: str = ""
    selected: list[str] | None = None


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


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
        cfg = load_config()["database"] if data is None else merge_secret(load_config(), {"database": data})["database"]
        return test_connection(cfg)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/environments/{environment}/apis")
async def apis(project_id: str, environment: str):
    config = load_config()
    project = find_project(config, project_id)
    env = find_env(project, environment)
    try:
        return await discover_openapi(env)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/runs")
async def execute(request: RunRequest):
    config = load_config()
    project = find_project(config, request.project_id)
    env = find_env(project, request.environment)
    blocked = [x.lower() for x in config.get("security", {}).get("blocked_host_keywords", [])]
    if any(word and word in env["base_url"].lower() for word in blocked):
        raise HTTPException(400, "目標 Domain 命中正式環境阻擋規則")
    try:
        return await run_tests(config, project, env, request.token, request.selected)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def start() -> None:
    config = load_config()
    host = config.get("application", {}).get("host", "127.0.0.1")
    port = int(config.get("application", {}).get("port", 8765))
    if config.get("application", {}).get("open_browser", True):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start()
