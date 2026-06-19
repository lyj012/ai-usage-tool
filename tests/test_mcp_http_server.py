import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import mcp_http_server


class McpHttpServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "aiusage-config.json"
        self.evil_config = self.root / "evil-config.json"
        self.data_dir = self.root / "data"
        self.evil_data_dir = self.root / "evil-data"
        report_dir = self.data_dir / "reports" / "2026-06-15"
        report_dir.mkdir(parents=True)
        evil_report_dir = self.evil_data_dir / "reports" / "2026-06-15"
        evil_report_dir.mkdir(parents=True)
        self.config.write_text(
            json.dumps({"projects": [], "data_dir": str(self.data_dir)}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.evil_config.write_text(
            json.dumps({"projects": [], "data_dir": str(self.evil_data_dir)}, ensure_ascii=False),
            encoding="utf-8",
        )
        (report_dir / "daily-report.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "date": "2026-06-15",
                    "person": "tester",
                    "overview": {"ai_turn_count": 1, "commit_count": 1},
                    "ai_usage": {
                        "turns": [
                            {
                                "session_id": "session-secret-123",
                                "project": "demo",
                                "project_cwd": "C:\\Users\\alice\\work\\private-repo",
                                "source_file": "C:\\Users\\alice\\.codex\\sessions\\session-secret-123.jsonl",
                                "input_text": "full private prompt with password-like secret",
                                "input_preview": "full private prompt with password-like secret",
                            }
                        ]
                    },
                    "git_workload": {
                        "commits": [
                            {
                                "hash": "abcdef1234567890",
                                "short_hash": "abcdef1",
                                "message": "feat: update report",
                                "author_email": "alice@example.com",
                                "repo_path": "C:\\Users\\alice\\work\\private-repo",
                                "file_summaries": [{"path": "C:\\Users\\alice\\work\\private-repo\\secret.py"}],
                            }
                        ],
                        "file_changes": [],
                    },
                    "technical_topics": [],
                    "rework_and_exceptions": [],
                    "associations": [{"session_id": "session-secret-123", "matched_commits": [{"commit_hash": "abcdef1234567890"}]}],
                    "unmatched_ai_sessions": [{"session_id": "session-secret-123", "reason": "local path C:\\Users\\alice\\work"}],
                    "warnings": [],
                    "today_outcome": "http mcp test",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (evil_report_dir / "daily-report.json").write_text(
            json.dumps({"date": "2026-06-15", "today_outcome": "evil config should not be used"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.old_token = os.environ.get(mcp_http_server.TOKEN_ENV_NAME)
        self.old_config = os.environ.get(mcp_http_server.CONFIG_ENV_NAME)
        os.environ[mcp_http_server.TOKEN_ENV_NAME] = "test-token"
        os.environ[mcp_http_server.CONFIG_ENV_NAME] = str(self.config)
        self.server = mcp_http_server.build_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.old_token is None:
            os.environ.pop(mcp_http_server.TOKEN_ENV_NAME, None)
        else:
            os.environ[mcp_http_server.TOKEN_ENV_NAME] = self.old_token
        if self.old_config is None:
            os.environ.pop(mcp_http_server.CONFIG_ENV_NAME, None)
        else:
            os.environ[mcp_http_server.CONFIG_ENV_NAME] = self.old_config
        self.tmp.cleanup()

    def request_json(self, path, method="GET", body=None, token=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def request_raw(self, path, method="GET", body=None, token=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read().decode("utf-8")

    def test_health(self):
        status, headers, raw = self.request_raw("/health?probe=1")
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertNotIn("version", payload)
        self.assertNotIn("tools", payload)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_initialize_tools_list_and_call(self):
        status, initialize = self.request_json(
            "/mcp",
            method="POST",
            token="test-token",
            body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(initialize["result"]["serverInfo"]["name"], "ai-usage-tool")

        _, tools = self.request_json(
            "/mcp",
            method="POST",
            token="test-token",
            body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        names = {tool["name"] for tool in tools["result"]["tools"]}
        self.assertIn("get_daily_work_report", names)

        _, report = self.request_json(
            "/mcp",
            method="POST",
            token="test-token",
            body={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_daily_work_report",
                    "arguments": {"date": "2026-06-15", "config": str(self.evil_config)},
                },
            },
        )
        self.assertFalse(report["result"]["isError"])
        self.assertEqual(report["result"]["structuredContent"]["report"]["date"], "2026-06-15")
        self.assertEqual(report["result"]["structuredContent"]["report"]["today_outcome"], "http mcp test")

    def test_missing_date_returns_structured_error(self):
        _, payload = self.request_json(
            "/mcp",
            method="POST",
            token="test-token",
            body={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "get_daily_work_report",
                    "arguments": {"config": str(self.config)},
                },
            },
        )
        self.assertEqual(payload["error"]["code"], -32602)
        self.assertIn("date", payload["error"]["message"])

    def test_auth_rules(self):
        self.assertFalse(mcp_http_server.is_authorized("127.0.0.1", None, "test-token"))
        self.assertFalse(mcp_http_server.is_authorized("::1", None, "test-token"))
        self.assertFalse(mcp_http_server.is_authorized("203.0.113.10", None, "test-token"))
        self.assertFalse(mcp_http_server.is_authorized("203.0.113.10", "Bearer wrong", "test-token"))
        self.assertTrue(mcp_http_server.is_authorized("203.0.113.10", "Bearer test-token", "test-token"))
        self.assertTrue(mcp_http_server.is_authorized("127.0.0.1", None, None))
        self.assertFalse(mcp_http_server.is_authorized("203.0.113.10", None, None))

    def test_wrong_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request_json(
                "/mcp",
                method="POST",
                token="wrong",
                body={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
            )
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()

    def test_missing_token_is_rejected_when_token_is_configured(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request_json(
                "/mcp",
                method="POST",
                body={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
            )
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()

    def test_localhost_allows_missing_token_only_when_token_is_not_configured(self):
        os.environ.pop(mcp_http_server.TOKEN_ENV_NAME, None)
        status, payload = self.request_json(
            "/mcp",
            method="POST",
            body={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}},
        )
        self.assertEqual(status, 200)
        self.assertIn("tools", payload["result"])

    def test_remote_response_redacts_sensitive_fields(self):
        _, payload = self.request_json(
            "/mcp",
            method="POST",
            token="test-token",
            body={
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "get_ai_session_details",
                    "arguments": {"date": "2026-06-15", "config": str(self.evil_config)},
                },
            },
        )
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("input_text", raw)
        self.assertNotIn("source_file", raw)
        self.assertNotIn("project_cwd", raw)
        self.assertNotIn("C:\\Users\\alice", raw)
        self.assertNotIn("alice@example.com", raw)
        self.assertNotIn("session-secret-123", raw)
        self.assertNotIn("abcdef1234567890", raw)
        self.assertIn("session_ref", raw)

    def test_resource_limits_and_methods(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request_raw("/mcp", method="PUT", body={"x": 1}, token="test-token")
        self.assertEqual(raised.exception.code, 405)
        raised.exception.close()

        large_body = {"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {"padding": "x" * (mcp_http_server.MAX_BODY_BYTES + 1)}}
        with self.assertRaises(urllib.error.HTTPError) as too_large:
            self.request_json("/mcp", method="POST", body=large_body, token="test-token")
        self.assertEqual(too_large.exception.code, 413)
        too_large.exception.close()


if __name__ == "__main__":
    unittest.main()
