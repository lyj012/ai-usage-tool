import json
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path

import mcp_chatgpt_server


class McpChatgptServerTest(unittest.TestCase):
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
                    "ai_usage": {
                        "turns": [
                            {
                                "session_id": "session-secret-123",
                                "project_cwd": "C:\\Users\\alice\\private",
                                "input_text": "private token=super-secret-token",
                                "input_preview": "private token=super-secret-token",
                            }
                        ]
                    },
                    "git_workload": {"commits": [], "file_changes": []},
                    "technical_topics": [{"topic": "MCP", "related_task_count": 1}],
                    "rework_and_exceptions": [],
                    "associations": [{"session_id": "session-secret-123", "matched_commits": []}],
                    "unmatched_ai_sessions": [],
                    "warnings": [],
                    "today_outcome": "chatgpt mcp wrapper test",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_call_remote_tool_uses_remote_safe_handler(self):
        result = mcp_chatgpt_server.call_remote_tool(
            "get_daily_work_report",
            {"date": "2026-06-15"},
            str(self.config),
        )
        raw = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["report"]["date"], "2026-06-15")
        self.assertNotIn("config", raw)
        self.assertNotIn("session-secret-123", raw)
        self.assertNotIn("C:\\Users\\alice", raw)
        self.assertNotIn("super-secret-token", raw)
        self.assertIn("session_ref", raw)

    def test_call_remote_tool_returns_structured_error(self):
        result = mcp_chatgpt_server.call_remote_tool(
            "get_daily_work_report",
            {},
            str(self.config),
        )
        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("date", result["error"]["message"])

    def test_build_mcp_missing_dependency_message(self):
        if find_spec("mcp") is not None:
            self.skipTest("MCP SDK is installed in this environment.")
        with self.assertRaisesRegex(RuntimeError, r"\.\[chatgpt\]"):
            mcp_chatgpt_server.build_mcp(str(self.config))


if __name__ == "__main__":
    unittest.main()
