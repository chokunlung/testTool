from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from app import storage
from app import reporting
from app.api_discovery import parse_openapi
from app.runner import _assert_response, _replace_path
from app.session_lifecycle import SessionLifecycle


class OfflineTests(unittest.TestCase):
    def test_parse_openapi_generates_request(self):
        document = {
            "openapi": "3.0.0",
            "paths": {
                "/orders/{id}": {
                    "get": {
                        "operationId": "getOrder",
                        "parameters": [
                            {"in": "path", "name": "id", "required": True, "schema": {"type": "string", "example": "A001"}},
                            {"in": "query", "name": "detail", "schema": {"type": "boolean", "default": True}},
                        ],
                    }
                }
            },
        }
        items = parse_openapi(document)
        self.assertEqual(items[0]["api_name"], "getOrder")
        self.assertEqual(items[0]["request"]["path_params"]["id"], "A001")
        self.assertTrue(items[0]["request"]["query_params"]["detail"])

    def test_sqlite_case_roundtrip_preserves_manual_data(self):
        with tempfile.TemporaryDirectory() as folder:
            original = storage.DB_PATH
            storage.DB_PATH = Path(folder) / "test.db"
            try:
                cases = storage.upsert_endpoints("p1", [{"method": "POST", "path": "/orders", "operation_id": "createOrder", "request": {"body": {"id": 1}}}])
                case = storage.update_case(cases[0]["id"], {"name": "自訂", "request": {"body": {"id": 99}}})
                storage.upsert_endpoints("p1", [{"method": "POST", "path": "/orders", "request": {"body": {"id": 2}}}])
                reloaded = storage.get_case(case["id"])
                self.assertEqual(reloaded["name"], "自訂")
                self.assertEqual(reloaded["request"]["body"]["id"], 99)
            finally:
                storage.DB_PATH = original

    def test_path_parameter_and_response_assertion(self):
        self.assertEqual(_replace_path("/orders/{id}", {"id": "A B"}), "/orders/A%20B")
        response = httpx.Response(200, json={"returnCode": "0000"})
        result = _assert_response(response, {"http_statuses": [200], "return_code_path": "$.returnCode", "success_codes": ["0000"]}, {}, 5)
        self.assertEqual(result[0], "PASS")

    def test_html_report_is_created_and_masks_sensitive_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            original = reporting.BASE_DIR
            reporting.BASE_DIR = Path(folder)
            try:
                report = {
                    "project": "Demo",
                    "environment": "Local",
                    "executed_at": "2026-10-23T10:00:00",
                    "summary": {"PASS": 1},
                    "results": [{
                        "status": "PASS", "method": "GET", "path": "/ping", "case_name": "正常",
                        "request": {"headers": {"Authorization": "Bearer secret"}}, "response": {"body": "ok"},
                    }],
                }
                json_file, html_file = reporting.write_report(report, {"directory": "reports"}, "demo")
                content = html_file.read_text(encoding="utf-8")
                self.assertTrue(json_file.is_file())
                self.assertIn("********", content)
                self.assertNotIn("Bearer secret", content)
            finally:
                reporting.BASE_DIR = original

    def test_browser_leave_waits_and_reload_cancels_shutdown(self):
        lifecycle = SessionLifecycle(exit_fn=lambda _: None, shutdown_delay_seconds=3, stale_seconds=120)
        lifecycle.heartbeat("session-123", now=10)
        lifecycle.leave("session-123", now=11)
        self.assertFalse(lifecycle.check(now=13))
        lifecycle.heartbeat("session-123", now=13.5)
        self.assertFalse(lifecycle.check(now=20))
        lifecycle.leave("session-123", now=21)
        self.assertTrue(lifecycle.check(now=24.1))


if __name__ == "__main__":
    unittest.main()
