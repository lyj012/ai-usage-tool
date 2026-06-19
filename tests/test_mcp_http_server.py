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
        self.data_dir = self.root / "data"
        report_dir = self.data_dir / "reports" / "2026-06-15"
        report_dir.mkdir(parents=True)
        self.config.write_text(
            json.dumps({"projects": [], "data_dir": str(self.data_dir)}, ensure_ascii=False),
            encoding="utf-8",
        )
        (report_dir / "daily-report.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "date": "2026-06-15",
                    "person": "tester",
                    "overview": {"ai_turn_count": 1, "commit_count": 1},
                    "ai_usage": {"turns": [{"session_id": "s1", "project": "demo"}]},
                    "git_workload": {"commits": [], "file_changes": []},
                    "technical_topics": [],
                    "rework_and_exceptions": [],
                    "associations": [],
                    "unmatched_ai_sessions": [],
                    "warnings": [],
                    "today_outcome": "http mcp test",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.old_token = os.environ.get(mcp_http_server.TOKEN_ENV_NAME)
        os.environ[mcp_http_server.TOKEN_ENV_NAME] = "test-token"
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

    def test_health(self):
        status, payload = self.request_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["transport"], "http")

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
                    "arguments": {"date": "2026-06-15", "config": str(self.config)},
                },
            },
        )
        self.assertFalse(report["result"]["isError"])
        self.assertEqual(report["result"]["structuredContent"]["report"]["date"], "2026-06-15")

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
        self.assertTrue(mcp_http_server.is_authorized("127.0.0.1", None, "test-token"))
        self.assertTrue(mcp_http_server.is_authorized("::1", None, "test-token"))
        self.assertFalse(mcp_http_server.is_authorized("203.0.113.10", None, "test-token"))
        self.assertFalse(mcp_http_server.is_authorized("203.0.113.10", "Bearer wrong", "test-token"))
        self.assertTrue(mcp_http_server.is_authorized("203.0.113.10", "Bearer test-token", "test-token"))

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

    def test_localhost_allows_missing_token(self):
        status, payload = self.request_json(
            "/mcp",
            method="POST",
            body={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
        )
        self.assertEqual(status, 200)
        self.assertIn("tools", payload["result"])


if __name__ == "__main__":
    unittest.main()
