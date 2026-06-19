#!/usr/bin/env python3
"""Minimal stdio MCP server for reading local AI Usage Tool reports.

This server intentionally has no third-party dependency. It implements the
small JSON-RPC surface needed for MCP initialize, tools/list, and tools/call.
"""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import date
from hashlib import sha256
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
PRODUCT_VERSION = "0.3.0"
MAX_RANGE_DAYS = 31
MAX_SEARCH_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20
MAX_QUERY_CHARS = 120
MAX_SESSION_ID_CHARS = 128
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOCAL_PATH_RE = re.compile(
    r"(?i)([a-z]:\\[^,;，。；\r\n]+|\\\\[^,;，。；\r\n]+|/(?:users|home)/[^,;，。；\r\n]+)"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_RE = re.compile(r"(?i)\b(?:https?://|ssh://|git@)[^\s]+")
SECRET_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_-]{10,}|"
    r"gh[pousr]_[A-Za-z0-9_]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{12,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{10,}|"
    r"(?:api[_-]?key|token|password|passwd|secret)\s*[:=]\s*[^,\s;，。；]+"
    r")\b"
)
REMOTE_REMOVED_KEYS = {
    "input_text",
    "input_preview",
    "input_summary",
    "source_file",
    "project_cwd",
    "repo_path",
    "repo_url",
    "author_email",
    "email",
    "turn_id",
    "hash",
    "path",
}


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


def parse_day(value: str, field_name: str) -> date:
    if not DATE_RE.fullmatch(value):
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field_name} 不是有效日期")


def require_date(arguments: dict[str, Any], key: str) -> str:
    value = require_string(arguments, key)
    return parse_day(value, key).isoformat()


def require_date_range(arguments: dict[str, Any]) -> tuple[str, str]:
    start_day = require_date(arguments, "from")
    end_day = require_date(arguments, "to")
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    if start > end:
        raise ValueError("from 不能晚于 to")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"日期范围不能超过 {MAX_RANGE_DAYS} 天")
    return start_day, end_day


def optional_date(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        return ""
    return parse_day(value, key).isoformat()


def optional_string(arguments: dict[str, Any], key: str, max_chars: int) -> str:
    value = str(arguments.get(key) or "").strip()
    if len(value) > max_chars:
        raise ValueError(f"{key} 不能超过 {max_chars} 个字符")
    return value


def search_limit(arguments: dict[str, Any]) -> int:
    raw = arguments.get("limit", DEFAULT_SEARCH_LIMIT)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("limit 必须是整数")
    if raw < 1 or raw > MAX_SEARCH_LIMIT:
        raise ValueError(f"limit 必须在 1 到 {MAX_SEARCH_LIMIT} 之间")
    return raw


def tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json_dumps(data)}],
        "structuredContent": data,
        "isError": is_error,
    }


def get_daily_work_report(arguments: dict[str, Any]) -> dict[str, Any]:
    day = require_date(arguments, "date")
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    report, report_warnings = load_daily_report(data_dir, day)
    warnings.extend(report_warnings)
    if report is None:
        return tool_result({"date": day, "warnings": warnings}, is_error=True)
    return tool_result({"report": report, "warnings": warnings})


def get_work_trend(arguments: dict[str, Any]) -> dict[str, Any]:
    start_day, end_day = require_date_range(arguments)
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
    query = require_string(arguments, "query")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query 不能超过 {MAX_QUERY_CHARS} 个字符")
    query = query.lower()
    limit = search_limit(arguments)
    data_dir, warnings = resolve_data_dir(arguments.get("config"))
    start_day = optional_date(arguments, "from")
    end_day = optional_date(arguments, "to")
    if bool(start_day) != bool(end_day):
        raise ValueError("from 和 to 必须同时提供")
    if start_day and end_day:
        require_date_range({"from": start_day, "to": end_day})
        reports, report_warnings = load_daily_reports_range(data_dir, start_day, end_day)
        warnings.extend(report_warnings)
    else:
        dates, date_warnings = list_daily_report_dates(data_dir)
        warnings.extend(date_warnings)
        dates = dates[-MAX_RANGE_DAYS:]
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
    day = require_date(arguments, "date")
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
    day = require_date(arguments, "date")
    session_id = optional_string(arguments, "session_id", MAX_SESSION_ID_CHARS)
    session_ref = optional_string(arguments, "session_ref", MAX_SESSION_ID_CHARS)
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
    if session_ref:
        turns = [row for row in turns if session_reference(row.get("session_id")) == session_ref]
        associations = [row for row in associations if session_reference(row.get("session_id")) == session_ref]
        unmatched = [row for row in unmatched if session_reference(row.get("session_id")) == session_ref]
    if (session_id or session_ref) and not (turns or associations or unmatched):
        return tool_result({"date": day, "session_ref": session_ref or None, "warnings": ["未找到指定 AI 会话。"]}, is_error=True)
    return tool_result(
        {
            "date": day,
            "session_id": session_id or None,
            "session_ref": session_ref or None,
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


def validate_arguments(name: str, arguments: dict[str, Any]) -> None:
    validate_arguments_with_schema(arguments, TOOLS[name][1])


def validate_arguments_with_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    properties = schema.get("properties") or {}
    allowed = set(properties.keys())
    extra = sorted(set(arguments.keys()) - allowed)
    if extra:
        raise ValueError(f"未知参数: {', '.join(extra)}")
    for key in schema.get("required") or []:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"缺少必填参数: {key}")
    for key, value in arguments.items():
        prop = properties.get(key) or {}
        if prop.get("type") == "string":
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须是字符串")
            if "minLength" in prop and len(value.strip()) < int(prop["minLength"]):
                raise ValueError(f"{key} 不能为空")
            if "maxLength" in prop and len(value) > int(prop["maxLength"]):
                raise ValueError(f"{key} 不能超过 {prop['maxLength']} 个字符")
            pattern = prop.get("pattern")
            if pattern and value and not re.fullmatch(str(pattern), value):
                raise ValueError(f"{key} 格式不正确")
        if prop.get("type") == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须是整数")
            if "minimum" in prop and value < int(prop["minimum"]):
                raise ValueError(f"{key} 不能小于 {prop['minimum']}")
            if "maximum" in prop and value > int(prop["maximum"]):
                raise ValueError(f"{key} 不能大于 {prop['maximum']}")


def tool_annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def string_array_schema() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def loose_object_schema() -> dict[str, Any]:
    return {"type": "object"}


OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_daily_work_report": object_schema(
        {
            "date": {"type": "string"},
            "report": loose_object_schema(),
            "warnings": string_array_schema(),
        },
        ["warnings"],
    ),
    "get_work_trend": object_schema(
        {
            "trend": loose_object_schema(),
            "warnings": string_array_schema(),
        },
        ["trend", "warnings"],
    ),
    "search_work_records": object_schema(
        {
            "query": {"type": "string"},
            "matches": {"type": "array", "items": loose_object_schema()},
            "warnings": string_array_schema(),
        },
        ["query", "matches", "warnings"],
    ),
    "get_git_activity": object_schema(
        {
            "date": {"type": "string"},
            "git_workload": loose_object_schema(),
            "overview": loose_object_schema(),
            "warnings": string_array_schema(),
        },
        ["date", "git_workload", "overview", "warnings"],
    ),
    "get_ai_session_details": object_schema(
        {
            "date": {"type": "string"},
            "session_id": {"type": ["string", "null"]},
            "session_ref": {"type": ["string", "null"]},
            "turns": {"type": "array", "items": loose_object_schema()},
            "associations": {"type": "array", "items": loose_object_schema()},
            "unmatched_ai_sessions": {"type": "array", "items": loose_object_schema()},
            "warnings": string_array_schema(),
        },
        ["date", "turns", "associations", "unmatched_ai_sessions", "warnings"],
    ),
}


def remote_config_arguments(arguments: dict[str, Any], config_path: str | None) -> dict[str, Any]:
    result = dict(arguments)
    result.pop("config", None)
    result["config"] = config_path or "aiusage-config.json"
    return result


def remote_input_schema(name: str) -> dict[str, Any]:
    schema = deepcopy(TOOLS[name][1])
    properties = schema.get("properties") or {}
    properties.pop("config", None)
    if name == "get_ai_session_details":
        properties.pop("session_id", None)
        properties["session_ref"] = {
            "type": "string",
            "maxLength": MAX_SESSION_ID_CHARS,
            "description": "Remote HTTP 模式返回的稳定会话引用。不要传原始 session_id。",
        }
    required = [key for key in schema.get("required") or [] if key != "config"]
    schema["properties"] = properties
    schema["required"] = required
    return schema


def input_schema_for(name: str, remote: bool) -> dict[str, Any]:
    return remote_input_schema(name) if remote else deepcopy(TOOLS[name][1])


def output_schema_for(name: str) -> dict[str, Any]:
    return deepcopy(OUTPUT_SCHEMAS[name])


def remote_safe_response(response_data: dict[str, Any]) -> dict[str, Any]:
    if "result" not in response_data:
        return response_data
    result = response_data.get("result")
    if not isinstance(result, dict):
        return response_data
    structured = remote_safe_value(result.get("structuredContent") or {})
    safe_result = dict(result)
    safe_result["structuredContent"] = structured
    safe_result["content"] = [{"type": "text", "text": json_dumps(structured)}]
    return {**response_data, "result": safe_result}


def remote_safe_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            normalized = str(child_key)
            if normalized in REMOTE_REMOVED_KEYS:
                continue
            if normalized == "session_id":
                result["session_ref"] = session_reference(child_value)
                continue
            if normalized == "commit_hash":
                result["commit_ref"] = stable_ref(child_value, "commit")
                continue
            result[normalized] = remote_safe_value(child_value, normalized)
        return result
    if isinstance(value, list):
        return [remote_safe_value(item, key) for item in value[:MAX_SEARCH_LIMIT]]
    if isinstance(value, str):
        text = EMAIL_RE.sub("[redacted-email]", value)
        text = LOCAL_PATH_RE.sub("[local-path]", text)
        text = URL_RE.sub("[redacted-url]", text)
        text = SECRET_RE.sub("[redacted-secret]", text)
        if key in {"text", "input_preview", "input_summary"} and len(text) > 240:
            text = text[:239] + "..."
        return text
    return value


def session_reference(value: Any) -> str:
    return stable_ref(value, "session")


def stable_ref(value: Any, prefix: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    digest = sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

TOOLS: dict[str, tuple[str, dict[str, Any], ToolHandler]] = {
    "get_daily_work_report": (
        "只读读取指定日期的本地 daily-report.json；不访问互联网、不写文件、不删除数据。Remote HTTP 模式会返回脱敏视图。",
        object_schema(
            {
                "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "日期，格式 YYYY-MM-DD。"},
                "config": {"type": "string", "maxLength": 500, "description": "本地 stdio 模式可选配置文件路径；Remote HTTP 模式会忽略调用方传入值。"},
            },
            ["date"],
        ),
        get_daily_work_report,
    ),
    "get_work_trend": (
        "只读按日期范围读取本地日报并返回趋势聚合；日期范围最多 31 天，不访问互联网、不写文件。",
        object_schema(
            {
                "from": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "开始日期 YYYY-MM-DD。"},
                "to": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "结束日期 YYYY-MM-DD。"},
                "person": {"type": "string", "maxLength": 80, "description": "可选人员名。"},
                "config": {"type": "string", "maxLength": 500, "description": "本地 stdio 模式可选配置文件路径；Remote HTTP 模式会忽略调用方传入值。"},
            },
            ["from", "to"],
        ),
        get_work_trend,
    ),
    "search_work_records": (
        "只读搜索本地日报中的成果、主题、返工、commit 和 AI-Git 关联；不会访问互联网或修改数据。",
        object_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS, "description": "搜索关键词。"},
                "from": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "可选开始日期 YYYY-MM-DD。"},
                "to": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "可选结束日期 YYYY-MM-DD。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_LIMIT, "description": "最多返回条数，默认 20。"},
                "config": {"type": "string", "maxLength": 500, "description": "本地 stdio 模式可选配置文件路径；Remote HTTP 模式会忽略调用方传入值。"},
            },
            ["query"],
        ),
        search_work_records,
    ),
    "get_git_activity": (
        "只读读取指定日期日报中的 Git 工作量明细；Remote HTTP 模式会移除本地路径、邮箱和完整 hash。",
        object_schema(
            {
                "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "日期，格式 YYYY-MM-DD。"},
                "config": {"type": "string", "maxLength": 500, "description": "本地 stdio 模式可选配置文件路径；Remote HTTP 模式会忽略调用方传入值。"},
            },
            ["date"],
        ),
        get_git_activity,
    ),
    "get_ai_session_details": (
        "只读读取指定日期的 AI 会话摘要、关联和未关联原因；Remote HTTP 模式会移除完整输入文本和本地路径。",
        object_schema(
            {
                "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "日期，格式 YYYY-MM-DD。"},
                "session_id": {"type": "string", "maxLength": MAX_SESSION_ID_CHARS, "description": "可选 AI 会话 ID。"},
                "config": {"type": "string", "maxLength": 500, "description": "本地 stdio 模式可选配置文件路径；Remote HTTP 模式会忽略调用方传入值。"},
            },
            ["date"],
        ),
        get_ai_session_details,
    ),
}


def list_tools(remote: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "title": name.replace("_", " ").title(),
            "description": description,
            "inputSchema": input_schema_for(name, remote),
            "outputSchema": output_schema_for(name),
            "annotations": tool_annotations(),
        }
        for name, (description, _, _) in TOOLS.items()
    ]


def handle_request(request: dict[str, Any], *, remote: bool = False, remote_config: str | None = None) -> dict[str, Any] | None:
    if not isinstance(request, dict):
        return error_response(None, -32600, "Request must be an object")
    method = request.get("method")
    request_id = request.get("id")
    if request_id is not None and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
        return error_response(None, -32600, "Request id must be a string, integer, or null")
    if request_id is None:
        return None
    if request.get("jsonrpc") != "2.0":
        return error_response(request_id, -32600, "jsonrpc must be 2.0")
    if not isinstance(method, str) or not method:
        return error_response(request_id, -32600, "method is required")
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
            return response(request_id, {"tools": list_tools(remote=remote)})
        if method == "tools/call":
            params = request.get("params") or {}
            if not isinstance(params, dict):
                return error_response(request_id, -32602, "Params must be an object")
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in TOOLS:
                return error_response(request_id, -32602, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return error_response(request_id, -32602, "Tool arguments must be an object")
            validate_arguments_with_schema(arguments, input_schema_for(name, remote))
            arguments = remote_config_arguments(arguments, remote_config) if remote else deepcopy(arguments)
            handler = TOOLS[name][2]
            result = response(request_id, handler(arguments))
            return remote_safe_response(result) if remote else result
        return error_response(request_id, -32601, f"Method not found: {method}")
    except ValueError as exc:
        return error_response(request_id, -32602, str(exc))
    except Exception as exc:
        result = response(request_id, tool_result({"error": str(exc)}, is_error=True))
        return remote_safe_response(result) if remote else result


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
