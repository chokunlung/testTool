from __future__ import annotations

from typing import Any


def _kind(cfg: dict[str, Any]) -> str:
    return str(cfg.get("type", "db2")).strip().lower()


def test_connection(cfg: dict[str, Any]) -> dict[str, Any]:
    kind = _kind(cfg)
    if kind == "db2":
        from .db2_service import test_connection as run
    elif kind == "oracle":
        from .oracle_service import test_connection as run
    else:
        raise ValueError(f"不支援的資料庫類型：{kind}")
    result = run(cfg)
    result["type"] = kind
    return result


def fetch_success_logs(cfg: dict[str, Any], api_name: str | None = None) -> list[dict[str, Any]]:
    kind = _kind(cfg)
    if kind == "db2":
        from .db2_service import fetch_success_logs as run
    elif kind == "oracle":
        from .oracle_service import fetch_success_logs as run
    else:
        raise ValueError(f"不支援的資料庫類型：{kind}")
    return run(cfg, api_name)
