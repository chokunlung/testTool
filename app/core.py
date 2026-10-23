from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = app_dir()
CONFIG_PATH = BASE_DIR / "config.yaml"
EXAMPLE_PATH = (Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "config.example.yaml")


def ensure_config() -> None:
    if not CONFIG_PATH.exists():
        source = EXAMPLE_PATH if EXAMPLE_PATH.exists() else BASE_DIR / "config.example.yaml"
        CONFIG_PATH.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    ensure_config()
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    data.setdefault("common_headers", {})
    database = data.setdefault("database", {})
    database.setdefault("type", "db2")
    database.setdefault("service_name", "")
    database.setdefault("columns", {}).setdefault("api_name", "API_NAME")
    for project in data.setdefault("projects", []):
        project.setdefault("common_headers", {})
        for env in project.setdefault("environments", []):
            env.setdefault("common_headers", {})
    return data


def save_config(data: dict[str, Any]) -> None:
    validate_config(data)
    temp = CONFIG_PATH.with_suffix(".yaml.tmp")
    temp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temp.replace(CONFIG_PATH)


def safe_config(data: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(data)

    def mask(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"password", "token", "secret", "api_key"} and item:
                    value[key] = "********"
                else:
                    mask(item)
        elif isinstance(value, list):
            for item in value:
                mask(item)

    mask(result)
    return result


def validate_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")):
        raise ValueError(f"{label} 只能包含英文字母、數字與底線")
    return value


def validate_config(data: dict[str, Any]) -> None:
    db = data.get("database", {})
    if db.get("schema"):
        validate_identifier(db.get("schema", ""), "Schema")
    if db.get("table"):
        validate_identifier(db.get("table", ""), "Table")
    for key, value in db.get("columns", {}).items():
        if value:
            validate_identifier(value, f"欄位 {key}")
    for project in data.get("projects", []):
        if not project.get("id") or not project.get("name"):
            raise ValueError("每個專案都必須設定 id 與 name")
        for env in project.get("environments", []):
            if not str(env.get("base_url", "")).startswith(("http://", "https://")):
                raise ValueError(f"環境 {env.get('name', '')} 的 Base URL 無效")


def merge_secret(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(incoming)

    def merge(old: Any, new: Any) -> None:
        if isinstance(old, dict) and isinstance(new, dict):
            for key, value in new.items():
                if value == "********" and key in old:
                    new[key] = old[key]
                elif key in old:
                    merge(old[key], value)
        elif isinstance(old, list) and isinstance(new, list):
            for index, value in enumerate(new):
                if index < len(old):
                    merge(old[index], value)

    merge(existing, result)
    return result


def json_path_get(document: Any, path: str) -> Any:
    if not path.startswith("$."):
        raise ValueError("Return Code Path 必須以 $. 開頭")
    current = document
    for part in path[2:].split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def parse_headers(raw: Any) -> dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    text = str(raw).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items()}
    except Exception:
        pass
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result
