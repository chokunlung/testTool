from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import BASE_DIR


SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}


def redact_headers(headers: dict[str, Any]) -> dict[str, Any]:
    return {key: "********" if key.lower() in SENSITIVE_HEADERS else value for key, value in headers.items()}


def _pretty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _render_result(item: dict[str, Any]) -> str:
    status = html.escape(str(item.get("status", "")))
    request = item.get("request", {})
    response = item.get("response", {})
    detail = {
        "request": {**request, "headers": redact_headers(request.get("headers", {}))},
        "response": response,
        "error": item.get("error", ""),
    }
    return f"""
      <tr data-status="{status}">
        <td><span class="pill {status}">{status}</span></td>
        <td>{html.escape(str(item.get('method', '')))}</td>
        <td><code>{html.escape(str(item.get('path', '')))}</code><div class="small">{html.escape(str(item.get('case_name', '')))}</div></td>
        <td>{html.escape(str(item.get('http_status', '—')))}</td>
        <td>{html.escape(str(item.get('return_code', '—')))}</td>
        <td>{html.escape(str(item.get('elapsed_ms', '—')))} ms</td>
        <td><details><summary>查看</summary><pre>{html.escape(_pretty(detail))}</pre></details></td>
      </tr>"""


def _render_html(report: dict[str, Any]) -> str:
    summary = "".join(
        f'<div class="metric"><span>{html.escape(key)}</span><strong>{value}</strong></div>'
        for key, value in report.get("summary", {}).items()
    )
    rows = "".join(_render_result(item) for item in report.get("results", []))
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>API 測試報告 - {html.escape(str(report.get('project', '')))}</title>
<style>
body{{font-family:Segoe UI,Microsoft JhengHei,sans-serif;margin:0;background:#08101f;color:#e8eef8}}main{{max-width:1500px;margin:auto;padding:30px}}h1{{margin-bottom:6px}}.muted,.small{{color:#91a2bd;font-size:12px}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}}.metric{{background:#111e32;border:1px solid #263754;border-radius:10px;padding:14px 20px;min-width:110px}}.metric span{{display:block;color:#91a2bd;font-size:12px}}.metric strong{{font-size:28px}}input{{width:100%;padding:10px;background:#0c1729;color:white;border:1px solid #304464;border-radius:8px;margin-bottom:16px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #263754;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#111e32}}code,pre{{font-family:Consolas,monospace}}pre{{white-space:pre-wrap;word-break:break-word;background:#050b14;padding:12px;border-radius:7px;max-width:760px;max-height:500px;overflow:auto}}.pill{{padding:4px 8px;border-radius:99px;font-weight:700;font-size:11px}}.PASS{{background:#16442e;color:#75e5a3}}.FAIL_HTTP,.FAIL_ASSERTION,.INVALID_RESPONSE,.CONNECTION_ERROR,.TIMEOUT,.INVALID_REQUEST{{background:#512436;color:#ff9aaa}}.SKIPPED,.NO_TEST_DATA{{background:#4d4020;color:#ffd774}}
</style></head><body><main>
<h1>API 測試報告</h1><div class="muted">Repo：{html.escape(str(report.get('project', '')))}　環境：{html.escape(str(report.get('environment', '')))}　目標：{html.escape(str(report.get('base_url', '')))}　執行時間：{html.escape(str(report.get('executed_at', '')))}</div>
<div class="cards">{summary}</div><input id="search" placeholder="搜尋 Method、Path、結果">
<table><thead><tr><th>結果</th><th>Method</th><th>API／案例</th><th>HTTP</th><th>Return Code</th><th>耗時</th><th>明細</th></tr></thead><tbody>{rows}</tbody></table>
</main><script>document.getElementById('search').oninput=e=>{{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.innerText.toLowerCase().includes(q))}}</script></body></html>"""


def write_report(report: dict[str, Any], reports_cfg: dict[str, Any], project_id: str) -> tuple[Path, Path]:
    report_dir = BASE_DIR / reports_cfg.get("directory", "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = f"report_{project_id}_{stamp}"
    json_file = report_dir / f"{base}.json"
    html_file = report_dir / f"{base}.html"
    json_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_file.write_text(_render_html(report), encoding="utf-8")
    return json_file, html_file
