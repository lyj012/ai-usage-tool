#!/usr/bin/env python3
"""Minimal stdio MCP server for reading local AI Usage Tool reports.

This server intentionally has no third-party dependency. It implements the
small JSON-RPC surface needed for MCP initialize, tools/list, and tools/call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from workreport import (
    build_period_report,
    list_daily_report_dates,
    load_config,
    load_daily_report,
    load_daily_reports_range,
)


PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "ai-usage-tool"
SERVER_VERSION = "3.0.0"


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def resolve_data_dir(config_path_text: str | None = None) -> tuple[Path, list[str]]:
    config_path = Path(config_path_text or "aiusage-config.json").expanduser()
    warnings: list[str] = []
    config = load_config(config_path)
    config_base = config_path.parent if config_path.parent != Path("") else Path(".")
    data_dir = Path(str(config.get("data_dir") or "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = config_base / data_dir
    return data_dir, warnings


def tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json_dumps(data)}],
        "structuredContent": data,
        "isError": is_error,
    }


def get_daily_work_report(arguments: dict[str, Any]) -> dict[str, Any]:
    day = require_string(arguments, "date")
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    report, report_warnings = load_daily_report(data_dir, day)
    warnings.extend(report_warnings)
    if report is None:
        return tool_result({"date": day, "warnings": warnings}, is_error=True)
    return tool_result({"report": report, "warnings": warnings})


def get_work_trend(arguments: dict[str, Any]) -> dict[str, Any]:
    start_day = require_string(arguments, "from")
    end_day = require_string(arguments, "to")
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    reports, report_warnings = load_daily_reports_range(data_dir, start_day, end_day)
    warnings.extend(report_warnings)
    trend = build_period_report(
        "range",
        f"{start_day}_{end_day}",
        str(arguments.get("person") or ""),
        reports,
        warnings,
    )
    return tool_result({"trend": trend, "warnings": warnings})


def search_work_records(arguments: dict[str, Any]) -> dict[str, Any]:
    query = require_string(arguments, "query").lower()
    limit = int(arguments.get("limit") or 20)
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    start_day = str(arguments.get("from") or "")
    end_day = str(arguments.get("to") or "")
    if start_day and end_day:
        reports, report_warnings = load_daily_reports_range(data_dir, start_day, end_day)
        warnings.extend(report_warnings)
    else:
        dates, date_warnings = list_daily_report_dates(data_dir)
        warnings.extend(date_warnings)
        reports = []
        for day in dates:
            report, report_warnings = load_daily_report(data_dir, day)
            warnings.extend(report_warnings)
            if report is not None:
                reports.append(report)

    matches: list[dict[str, Any]] = []
    for report in reports:
        day = str(report.get("date") or "")
        add_match(matches, query, limit, day, "today_outcome", report.get("today_outcome"))
        for field_name in ("main_completed_items", "work_focus", "tomorrow_suggestions", "warnings"):
            for item in report.get(field_name) or []:
                add_match(matches, query, limit, day, field_name, item)
        for row in report.get("technical_topics") or []:
            add_match(matches, query, limit, day, "technical_topics", row)
        for row in report.get("rework_and_exceptions") or []:
            add_match(matches, query, limit, day, "rework_and_exceptions", row)
        for row in (report.get("git_workload") or {}).get("commits") or []:
            add_match(matches, query, limit, day, "git_commit", row)
        for row in report.get("associations") or []:
            add_match(matches, query, limit, day, "association", row)
        if len(matches) >= limit:
            break
    return tool_result({"query": query, "matches": matches[:limit], "warnings": warnings})


def get_git_activity(arguments: dict[str, Any]) -> dict[str, Any]:
    day = require_string(arguments, "date")
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    report, report_warnings = load_daily_report(data_dir, day)
    warnings.extend(report_warnings)
    if report is None:
        return tool_result({"date": day, "warnings": warnings}, is_error=True)
    return tool_result(
        {
            "date": day,
            "git_workload": report.get("git_workload") or {},
            "overview": report.get("overview") or {},
            "warnings": warnings,
        }
    )


def get_ai_session_details(arguments: dict[str, Any]) -> dict[str, Any]:
    day = require_string(arguments, "date")
    session_id = str(arguments.get("session_id") or "")
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    report, report_warnings = load_daily_report(data_dir, day)
    warnings.extend(report_warnings)
    if report is None:
        return tool_result({"date": day, "warnings": warnings}, is_error=True)
    turns = (report.get("ai_usage") or {}).get("turns") or []
    associations = report.get("associations") or []
    unmatched = report.get("unmatched_ai_sessions") or []
    if session_id:
        turns = [row for row in turns if str(row.get("session_id") or "") == session_id]
        associations = [row for row in associations if str(row.get("session_id") or "") == session_id]
        unmatched = [row for row in unmatched if str(row.get("session_id") or "") == session_id]
    return tool_result(
        {
            "date": day,
            "session_id": session_id or None,
            "turns": turns,
            "associations": associations,
            "unmatched_ai_sessions": unmatched,
            "warnings": warnings,
        }
    )


def add_match(matches: list[dict[str, Any]], query: str, limit: int, day: str, source: str, value: Any) -> None:
    if len(matches) >= limit:
        return
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    if query in text.lower():
        matches.append({"date": day, "source": source, "text": text[:800]})


def require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少必填参数: {key}")
    return value.strip()


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

TOOLS: dict[str, tuple[str, dict[str, Any], ToolHandler]] = {
    "get_daily_work_report": (
        "读取指定日期的本地 daily-report.json。",
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD。"},
                "config": {"type": "string", "description": "可选配置文件路径，默认 aiusage-config.json。"},
            },
            "required": ["date"],
        },
        get_daily_work_report,
    ),
    "get_work_trend": (
        "按日期范围读取日报并返回趋势聚合。",
        {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "开始日期 YYYY-MM-DD。"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD。"},
                "person": {"type": "string", "description": "可选人员名。"},
                "config": {"type": "string", "description": "可选配置文件路径。"},
            },
            "required": ["from", "to"],
        },
        get_work_trend,
    ),
    "search_work_records": (
        "在本地日报中搜索成果、主题、返工、commit 和 AI-Git 关联。",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词。"},
                "from": {"type": "string", "description": "可选开始日期 YYYY-MM-DD。"},
                "to": {"type": "string", "description": "可选结束日期 YYYY-MM-DD。"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 20。"},
                "config": {"type": "string", "description": "可选配置文件路径。"},
            },
            "required": ["query"],
        },
        search_work_records,
    ),
    "get_git_activity": (
        "读取指定日期日报中的 Git 工作量明细。",
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD。"},
                "config": {"type": "string", "description": "可选配置文件路径。"},
            },
            "required": ["date"],
        },
        get_git_activity,
    ),
    "get_ai_session_details": (
        "读取指定日期的 AI 会话摘要、关联和未关联原因，可按 session_id 过滤。",
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD。"},
                "session_id": {"type": "string", "description": "可选 AI 会话 ID。"},
                "config": {"type": "string", "description": "可选配置文件路径。"},
            },
            "required": ["date"],
        },
        get_ai_session_details,
    ),
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "title": name.replace("_", " ").title(),
            "description": description,
            "inputSchema": input_schema,
        }
        for name, (description, input_schema, _) in TOOLS.items()
    ]


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            return response(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "tools/list":
            return response(request_id, {"tools": list_tools()})
        if method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in TOOLS:
                return error_response(request_id, -32602, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return error_response(request_id, -32602, "Tool arguments must be an object")
            handler = TOOLS[name][2]
            return response(request_id, handler(arguments))
        return error_response(request_id, -32601, f"Method not found: {method}")
    except ValueError as exc:
        return error_response(request_id, -32602, str(exc))
    except Exception as exc:
        return response(request_id, tool_result({"error": str(exc)}, is_error=True))


def response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio() -> int:
    configure_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            result = handle_request(request)
        except Exception as exc:
            result = error_response(None, -32700, f"Parse error: {exc}")
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve_stdio())
