import json
import tempfile
import unittest
from pathlib import Path

import mcp_server


def assert_schema_matches(testcase, schema, value):
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if value is None:
            testcase.assertIn("null", expected_type)
            return
        testcase.assertTrue(any(type_matches(t, value) for t in expected_type), f"{value!r} does not match {expected_type}")
    elif expected_type:
        testcase.assertTrue(type_matches(expected_type, value), f"{value!r} does not match {expected_type}")
    if expected_type == "object":
        properties = schema.get("properties") or {}
        for key in schema.get("required") or []:
            testcase.assertIn(key, value)
        if schema.get("additionalProperties") is False:
            testcase.assertFalse(set(value.keys()) - set(properties.keys()))
        for key, child in properties.items():
            if key in value:
                assert_schema_matches(testcase, child, value[key])
    if expected_type == "array":
        item_schema = schema.get("items") or {}
        for item in value:
            assert_schema_matches(testcase, item_schema, item)


def type_matches(expected_type, value):
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


class McpServerTest(unittest.TestCase):
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
                                "session_id": "s1",
                                "project": "demo",
                                "input_preview": "fix redis issue",
                            }
                        ]
                    },
                    "git_workload": {
                        "commits": [
                            {
                                "short_hash": "abc123",
                                "message": "fix redis issue",
                            }
                        ],
                        "file_changes": [],
                    },
                    "technical_topics": [
                        {
                            "topic": "Redis",
                            "related_task_count": 1,
                            "evidence": ["redis"],
                            "worth_learning": False,
                        }
                    ],
                    "rework_and_exceptions": [],
                    "associations": [{"session_id": "s1", "matched_commits": []}],
                    "unmatched_ai_sessions": [],
                    "warnings": [],
                    "today_outcome": "fixed redis issue",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_tools(self):
        tools = mcp_server.list_tools()
        names = {tool["name"] for tool in tools}
        self.assertIn("get_daily_work_report", names)
        self.assertIn("get_work_trend", names)
        self.assertIn("search_work_records", names)
        self.assertIn("get_git_activity", names)
        self.assertIn("get_ai_session_details", names)
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        self.assertTrue(all(tool["annotations"]["openWorldHint"] is False for tool in tools))
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in tools))
        self.assertTrue(all("outputSchema" in tool for tool in tools))
        daily = next(tool for tool in tools if tool["name"] == "get_daily_work_report")
        self.assertIn("config", daily["inputSchema"]["properties"])
        self.assertNotIn("structuredContent", daily["outputSchema"]["properties"])

    def test_get_daily_report_tool(self):
        result = mcp_server.get_daily_work_report(
            {"date": "2026-06-15", "config": str(self.config)}
        )
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["report"]["date"], "2026-06-15")

    def test_missing_report_is_error(self):
        result = mcp_server.get_daily_work_report(
            {"date": "2026-06-16", "config": str(self.config)}
        )
        self.assertTrue(result["isError"])
        self.assertIn("warnings", result["structuredContent"])

    def test_search_records_tool(self):
        result = mcp_server.search_work_records(
            {
                "query": "redis",
                "from": "2026-06-15",
                "to": "2026-06-15",
                "config": str(self.config),
            }
        )
        self.assertFalse(result["isError"])
        self.assertGreaterEqual(len(result["structuredContent"]["matches"]), 1)

    def test_trend_git_and_session_tools(self):
        trend = mcp_server.get_work_trend(
            {
                "from": "2026-06-15",
                "to": "2026-06-15",
                "config": str(self.config),
            }
        )
        self.assertFalse(trend["isError"])
        self.assertEqual(trend["structuredContent"]["trend"]["report_type"], "range")

        git = mcp_server.get_git_activity(
            {"date": "2026-06-15", "config": str(self.config)}
        )
        self.assertFalse(git["isError"])
        self.assertIn("git_workload", git["structuredContent"])

        session = mcp_server.get_ai_session_details(
            {
                "date": "2026-06-15",
                "session_id": "s1",
                "config": str(self.config),
            }
        )
        self.assertFalse(session["isError"])
        self.assertEqual(len(session["structuredContent"]["turns"]), 1)

    def test_json_rpc_handler(self):
        result = mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        self.assertEqual(result["jsonrpc"], "2.0")
        self.assertEqual(result["id"], 1)
        self.assertIn("tools", result["result"])

    def test_tool_outputs_match_output_schema_and_content(self):
        calls = [
            (
                "get_daily_work_report",
                {"date": "2026-06-15", "config": str(self.config)},
            ),
            (
                "get_work_trend",
                {"from": "2026-06-15", "to": "2026-06-15", "config": str(self.config)},
            ),
            (
                "search_work_records",
                {"query": "redis", "from": "2026-06-15", "to": "2026-06-15", "config": str(self.config)},
            ),
            (
                "get_git_activity",
                {"date": "2026-06-15", "config": str(self.config)},
            ),
            (
                "get_ai_session_details",
                {"date": "2026-06-15", "session_id": "s1", "config": str(self.config)},
            ),
        ]
        tools = {tool["name"]: tool for tool in mcp_server.list_tools()}
        for name, arguments in calls:
            result = mcp_server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": name,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            )
            structured = result["result"]["structuredContent"]
            assert_schema_matches(self, tools[name]["outputSchema"], structured)
            self.assertEqual(json.loads(result["result"]["content"][0]["text"]), structured)

    def test_argument_validation(self):
        bad_date = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_daily_work_report",
                    "arguments": {"date": "2026-99-99", "config": str(self.config)},
                },
            }
        )
        self.assertEqual(bad_date["error"]["code"], -32602)

        unknown = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_daily_work_report",
                    "arguments": {"date": "2026-06-15", "unexpected": "x"},
                },
            }
        )
        self.assertEqual(unknown["error"]["code"], -32602)

        bad_range = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "get_work_trend",
                    "arguments": {"from": "2026-06-20", "to": "2026-06-15", "config": str(self.config)},
                },
            }
        )
        self.assertEqual(bad_range["error"]["code"], -32602)

        bad_limit = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "search_work_records",
                    "arguments": {"query": "redis", "limit": 1000, "config": str(self.config)},
                },
            }
        )
        self.assertEqual(bad_limit["error"]["code"], -32602)

        bad_params = mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": "not-object"}
        )
        self.assertEqual(bad_params["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
