import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import mcp_server
import report_prepare


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
            json.dumps(
                {
                    "projects": [],
                    "data_dir": str(self.data_dir),
                    "codex_roots": [str(self.root / "missing-codex")],
                    "claude_roots": [str(self.root / "missing-claude")],
                    "project_roots": [],
                    "skip_project_root_scan": True,
                },
                ensure_ascii=False,
            ),
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
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["report"]["date"], "2026-06-16")
        self.assertEqual(result["structuredContent"]["report"]["data_status"], "no_activity")
        self.assertEqual(result["structuredContent"]["data_freshness"]["source"], "generated")

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

    def test_stdio_remote_safe_startup_uses_remote_schema(self):
        process = subprocess.Popen(
            [sys.executable, "mcp_server.py", "--remote-safe", "--config", str(self.config)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            assert process.stdin is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            assert process.stdout is not None
            line = process.stdout.readline()
        finally:
            process.terminate()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        payload = json.loads(line)
        tools = payload["result"]["tools"]
        for tool in tools:
            self.assertNotIn("config", tool["inputSchema"]["properties"])
            self.assertNotIn("refresh_mode", tool["inputSchema"]["properties"])
        session_tool = next(tool for tool in tools if tool["name"] == "get_ai_session_details")
        self.assertNotIn("session_id", session_tool["inputSchema"]["properties"])
        self.assertIn("session_ref", session_tool["inputSchema"]["properties"])

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

        bad_refresh = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "get_daily_work_report",
                    "arguments": {"date": "2026-06-15", "refresh_mode": "bad", "config": str(self.config)},
                },
            }
        )
        self.assertEqual(bad_refresh["error"]["code"], -32602)

        bad_params = mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": "not-object"}
        )
        self.assertEqual(bad_params["error"]["code"], -32602)


class McpAutoPrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.codex_root = self.root / "codex"
        self.claude_root = self.root / "claude"
        self.config = self.root / "aiusage-config.json"
        self.config.write_text(
            json.dumps(
                {
                    "person": "tester",
                    "data_dir": str(self.data_dir),
                    "projects": [],
                    "codex_roots": [str(self.codex_root)],
                    "claude_roots": [str(self.claude_root)],
                    "project_roots": [],
                    "skip_project_root_scan": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_codex_turn(self, day="2026-06-19", text="work on mcp auto report"):
        self.codex_root.mkdir(parents=True, exist_ok=True)
        path = self.codex_root / "codex-session.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "turn_context", "payload": {"cwd": str(self.root / "demo")}}),
                    json.dumps(
                        {
                            "timestamp": f"{day}T09:00:00+08:00",
                            "type": "event_msg",
                            "payload": {"type": "user_message", "message": text},
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "timestamp": f"{day}T09:01:00+08:00",
                            "type": "event_msg",
                            "payload": {"type": "task_complete"},
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_claude_turn(self, day="2026-06-20", text="claude work item"):
        self.claude_root.mkdir(parents=True, exist_ok=True)
        path = self.claude_root / "claude-session.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": f"{day}T10:00:00+08:00",
                            "sessionId": "claude-s1",
                            "cwd": str(self.root / "demo"),
                            "message": {"content": text},
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": f"{day}T10:02:00+08:00",
                            "sessionId": "claude-s1",
                            "message": {"usage": {"input_tokens": 3, "output_tokens": 4}},
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_daily_report_auto_generates_from_codex(self):
        self.write_codex_turn()
        result = mcp_server.get_daily_work_report({"date": "2026-06-19", "config": str(self.config)})
        self.assertFalse(result["isError"])
        content = result["structuredContent"]
        self.assertEqual(content["data_freshness"]["source"], "generated")
        self.assertEqual(content["report"]["overview"]["ai_turn_count"], 1)
        self.assertEqual(content["report"]["data_status"], "available")
        self.assertTrue((self.data_dir / "reports" / "2026-06-19" / "daily-report.json").exists())
        self.assertTrue((self.data_dir / "reports" / "2026-06-19" / "report-meta.json").exists())

    def test_daily_report_auto_generates_from_claude(self):
        self.write_claude_turn()
        result = mcp_server.get_ai_session_details({"date": "2026-06-20", "config": str(self.config)})
        self.assertFalse(result["isError"])
        self.assertEqual(len(result["structuredContent"]["turns"]), 1)
        self.assertEqual(result["structuredContent"]["data_freshness"]["source"], "generated")

    def test_git_activity_auto_generates_from_configured_repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        env = {
            **dict(),
            "GIT_AUTHOR_DATE": "2026-06-19T09:00:00+08:00",
            "GIT_COMMITTER_DATE": "2026-06-19T09:00:00+08:00",
        }
        subprocess.run(
            ["git", "commit", "-m", "feat: add report docs"],
            cwd=repo,
            check=True,
            env={**os.environ, **env},
            stdout=subprocess.DEVNULL,
        )
        self.config.write_text(
            json.dumps(
                {
                    "person": "tester",
                    "data_dir": str(self.data_dir),
                    "projects": [{"name": "repo", "path": str(repo)}],
                    "codex_roots": [str(self.codex_root)],
                    "claude_roots": [str(self.claude_root)],
                    "project_roots": [],
                    "skip_project_root_scan": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = mcp_server.get_git_activity({"date": "2026-06-19", "config": str(self.config)})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["overview"]["commit_count"], 1)
        self.assertEqual(result["structuredContent"]["data_freshness"]["source"], "generated")

    def test_empty_day_generates_no_activity_report(self):
        result = mcp_server.get_daily_work_report({"date": "2026-06-18", "config": str(self.config)})
        self.assertFalse(result["isError"])
        report = result["structuredContent"]["report"]
        self.assertEqual(report["overview"]["ai_turn_count"], 0)
        self.assertEqual(report["overview"]["commit_count"], 0)
        self.assertEqual(report["data_status"], "no_activity")

    def test_range_query_auto_prepares_all_days(self):
        self.write_codex_turn(day="2026-06-19")
        result = mcp_server.get_work_trend(
            {"from": "2026-06-18", "to": "2026-06-20", "config": str(self.config)}
        )
        self.assertFalse(result["isError"])
        content = result["structuredContent"]
        self.assertEqual(content["requested_date_range"], {"from": "2026-06-18", "to": "2026-06-20"})
        self.assertEqual(content["report_day_count"], 3)
        self.assertEqual(content["trend"]["date_range"], {"from": "2026-06-18", "to": "2026-06-20"})
        self.assertEqual(len(content["processed_dates"]), 3)

    def test_cache_hit_does_not_regenerate_valid_historical_report(self):
        self.write_codex_turn(day="2026-06-19")
        first = mcp_server.get_daily_work_report({"date": "2026-06-19", "config": str(self.config)})
        self.assertFalse(first["isError"])
        second = mcp_server.get_daily_work_report({"date": "2026-06-19", "config": str(self.config)})
        self.assertFalse(second["isError"])
        self.assertEqual(second["structuredContent"]["data_freshness"]["source"], "cache")

    def test_source_mtime_refreshes_report(self):
        source = self.write_codex_turn(day="2026-06-19", text="first mcp task")
        first = mcp_server.get_daily_work_report({"date": "2026-06-19", "config": str(self.config)})
        self.assertFalse(first["isError"])
        time.sleep(1.1)
        source.write_text(source.read_text(encoding="utf-8").replace("first mcp task", "second mcp task"), encoding="utf-8")
        refreshed = mcp_server.get_daily_work_report({"date": "2026-06-19", "config": str(self.config)})
        self.assertFalse(refreshed["isError"])
        self.assertEqual(refreshed["structuredContent"]["data_freshness"]["source"], "refreshed")

    def test_partial_failure_keeps_range_query_alive(self):
        self.write_codex_turn(day="2026-06-19")
        original = report_prepare.generate_daily_report

        def fail_one_day(day, config, data_dir):
            if day == "2026-06-18":
                raise RuntimeError("boom")
            return original(day, config, data_dir)

        with mock.patch("report_prepare.generate_daily_report", side_effect=fail_one_day):
            result = mcp_server.get_work_trend(
                {"from": "2026-06-18", "to": "2026-06-19", "config": str(self.config), "refresh_mode": "force"}
            )
        self.assertFalse(result["isError"])
        self.assertIn("2026-06-18", result["structuredContent"]["failed_dates"])
        self.assertIn("2026-06-19", result["structuredContent"]["processed_dates"])

    def test_concurrent_same_day_generates_once(self):
        self.write_codex_turn(day="2026-06-19")
        original = report_prepare.generate_daily_report
        calls = 0
        calls_lock = threading.Lock()

        def counted(day, config, data_dir):
            nonlocal calls
            with calls_lock:
                calls += 1
            return original(day, config, data_dir)

        results = []
        with mock.patch("report_prepare.generate_daily_report", side_effect=counted):
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        mcp_server.get_daily_work_report({"date": "2026-06-19", "config": str(self.config)})
                    )
                )
                for _ in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(calls, 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(not item["isError"] for item in results))
        json.loads((self.data_dir / "reports" / "2026-06-19" / "daily-report.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
