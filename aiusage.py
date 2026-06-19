#!/usr/bin/env python3
"""Generate local personal workday reports from Codex / Claude Code usage and Git activity."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from workreport import (
    aggregate_topic_trends,
    build_daily_report,
    build_period_report,
    collect_git_activity,
    configured_projects,
    iso_week_bounds,
    list_daily_report_dates,
    load_config,
    load_daily_report,
    load_daily_reports_range,
    load_reflection,
    month_bounds,
    reports_root,
    write_daily_outputs,
    write_default_config,
    write_period_outputs,
)

try:
    import tiktoken  # type: ignore

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - optional dependency
    _ENCODER = None


ISO_Z = "%Y-%m-%dT%H:%M:%S%z"
PRODUCT_VERSION = "0.3.0"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def valid_date_arg(value: str) -> str:
    if not DATE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("日期必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("日期无效")
    return value


def valid_week_arg(value: str) -> str:
    if not WEEK_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("ISO 周必须是 YYYY-Www，例如 2026-W25")
    try:
        year_text, week_text = value.split("-W", 1)
        date.fromisocalendar(int(year_text), int(week_text), 1)
    except ValueError:
        raise argparse.ArgumentTypeError("ISO 周无效")
    return value


def valid_month_arg(value: str) -> str:
    if not MONTH_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("月份必须是 YYYY-MM")
    year_text, month_text = value.split("-", 1)
    month = int(month_text)
    if month < 1 or month > 12:
        raise argparse.ArgumentTypeError("月份无效")
    int(year_text)
    return value


def log_progress(args: argparse.Namespace, message: str) -> None:
    if getattr(args, "verbose", False):
        print(message, flush=True)


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_tz(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def iso(dt: datetime | None, tz: ZoneInfo) -> str | None:
    local = to_tz(dt, tz)
    if local is None:
        return None
    return local.isoformat(timespec="seconds")


def epoch_seconds(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except Exception:
        return None


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def in_range(dt: datetime | None, start: datetime, end: datetime, tz: ZoneInfo) -> bool:
    local = to_tz(dt, tz)
    return local is not None and start <= local < end


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    if _ENCODER is not None:
        return len(_ENCODER.encode(text))
    # Mixed Chinese/code heuristic when tiktoken is unavailable.
    return max(1, int(len(text) / 2.2))


def text_preview(text: str, limit: int = 120) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "..."


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") in {"image", "image_url"}:
                    parts.append("[Image]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def is_real_user_input(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    ignored_prefixes = (
        "<environment_context>",
        "<permissions instructions>",
        "<app-context>",
        "<system-reminder>",
        "[Request interrupted",
    )
    if stripped.startswith(ignored_prefixes):
        return False
    if "工具结果" in stripped and "<summary>" in stripped:
        return False
    return True


def project_key(cwd: str | None) -> str:
    if not cwd:
        return "unknown"
    p = Path(cwd)
    name = p.name or str(p)
    # Prefer a stable repo-ish name, but keep it simple for daily summaries.
    return name.replace(" ", "_")


@dataclass
class Turn:
    person: str
    tool: str
    project_cwd: str | None
    session_id: str
    turn_id: str | None
    turn_index: int
    input_at: datetime
    input_text: str
    source_file: str
    task_started_at: datetime | None = None
    task_finished_at: datetime | None = None
    next_input_at: datetime | None = None
    input_interval_seconds: float | None = None
    ai_active_seconds: float | None = None
    after_done_gap_seconds: float | None = None
    wallclock_seconds: float | None = None
    input_tokens: int = 0
    response_tokens: int = 0
    cache_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    token_source: str = "estimated"
    duration_source: str = "estimated"
    is_estimated: bool = True
    is_interrupted: bool = False

    def normalize_tokens(self) -> None:
        if self.input_tokens <= 0:
            self.input_tokens = estimate_tokens(self.input_text)
        if self.total_tokens <= 0:
            self.total_tokens = (
                self.input_tokens
                + self.response_tokens
                + self.cache_tokens
                + self.reasoning_tokens
            )

    def to_record(self, tz: ZoneInfo) -> dict[str, Any]:
        self.normalize_tokens()
        local_input = to_tz(self.input_at, tz)
        assert local_input is not None
        return {
            "person": self.person,
            "tool": self.tool,
            "date": local_input.date().isoformat(),
            "project": project_key(self.project_cwd),
            "project_cwd": self.project_cwd,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "input_at": iso(self.input_at, tz),
            "task_started_at": iso(self.task_started_at, tz),
            "task_finished_at": iso(self.task_finished_at, tz),
            "next_input_at": iso(self.next_input_at, tz),
            "input_interval_seconds": round(self.input_interval_seconds, 3)
            if self.input_interval_seconds is not None
            else None,
            "ai_active_seconds": round(self.ai_active_seconds, 3)
            if self.ai_active_seconds is not None
            else None,
            "wallclock_seconds": round(self.wallclock_seconds, 3)
            if self.wallclock_seconds is not None
            else None,
            "after_done_gap_seconds": round(self.after_done_gap_seconds, 3)
            if self.after_done_gap_seconds is not None
            else None,
            "input_text": self.input_text,
            "input_preview": text_preview(self.input_text),
            "input_chars": len(self.input_text),
            "input_tokens": int(self.input_tokens),
            "response_tokens": int(self.response_tokens),
            "cache_tokens": int(self.cache_tokens),
            "reasoning_tokens": int(self.reasoning_tokens),
            "total_tokens": int(self.total_tokens),
            "token_source": self.token_source,
            "duration_source": self.duration_source,
            "is_estimated": self.is_estimated,
            "is_interrupted": self.is_interrupted,
            "source_file": self.source_file,
        }


def discover_codex_files(codex_roots: list[Path], project_roots: list[Path]) -> list[Path]:
    files: set[Path] = set()
    candidate_roots: list[Path] = []
    for root in codex_roots:
        if root.exists():
            candidate_roots.append(root)
    for project_root in project_roots:
        if not project_root.exists():
            continue
        for name in (".codex", ".codex-ui-dev"):
            candidate_roots.extend(project_root.glob(f"**/{name}"))
    for root in candidate_roots:
        for pattern in ("sessions/**/*.jsonl", "archived_sessions/*.jsonl", "*.jsonl"):
            files.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(files)


def discover_claude_files(claude_roots: list[Path], project_roots: list[Path], include_subagents: bool) -> list[Path]:
    files: set[Path] = set()
    candidate_roots: list[Path] = []
    for root in claude_roots:
        if root.exists():
            candidate_roots.append(root)
    for project_root in project_roots:
        if not project_root.exists():
            continue
        candidate_roots.extend(project_root.glob("**/.claude"))
    for root in candidate_roots:
        for path in root.glob("**/*.jsonl"):
            if not include_subagents and "subagents" in path.parts:
                continue
            files.add(path)
    return sorted(files)


def iter_with_progress(
    args: argparse.Namespace,
    files: list[Path],
    label: str,
    every: int = 25,
) -> Iterable[tuple[int, Path]]:
    total = len(files)
    if total == 0:
        log_progress(args, f"{label}: 未发现文件")
        return
    log_progress(args, f"{label}: 发现 {total} 个文件，开始解析")
    for index, path in enumerate(files, 1):
        if getattr(args, "verbose", False) and (index == 1 or index == total or index % every == 0):
            print(f"{label}: {index}/{total} {path}", flush=True)
        yield index, path


def parse_codex_file(path: Path, person: str, tz: ZoneInfo, start: datetime, end: datetime) -> list[Turn]:
    session_id = path.stem
    cwd: str | None = None
    turns: list[Turn] = []
    current: Turn | None = None
    turn_index = 0

    def finish_current(finished_at: datetime | None = None, interrupted: bool = False) -> None:
        nonlocal current
        if current is None:
            return
        if finished_at is not None:
            current.task_finished_at = finished_at
        if current.task_finished_at is None:
            current.task_finished_at = current.task_started_at
        if current.task_started_at and current.task_finished_at:
            current.ai_active_seconds = max(
                0.0, (current.task_finished_at - current.task_started_at).total_seconds()
            )
            current.wallclock_seconds = current.ai_active_seconds
        current.is_interrupted = interrupted
        current.duration_source = "native"
        current.is_estimated = current.token_source != "native"
        if in_range(current.input_at, start, end, tz):
            turns.append(current)
        current = None

    for obj in read_jsonl(path):
        ts = parse_dt(obj.get("timestamp"))
        typ = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if typ == "session_meta":
            meta = payload
            session_id = str(meta.get("id") or session_id)
            cwd = meta.get("cwd") or cwd
            continue
        if typ == "turn_context":
            cwd = payload.get("cwd") or cwd
            continue
        if typ != "event_msg":
            continue
        event_type = payload.get("type")
        if event_type == "task_started":
            finish_current()
            current = None
            turn_index += 1
            started = epoch_seconds(payload.get("started_at")) or ts
            current = Turn(
                person=person,
                tool="codex",
                project_cwd=cwd,
                session_id=session_id,
                turn_id=str(payload.get("turn_id") or ""),
                turn_index=turn_index,
                input_at=started or datetime.now(timezone.utc),
                task_started_at=started,
                input_text="",
                source_file=str(path),
            )
        elif event_type == "user_message":
            message = payload.get("message")
            text = message if isinstance(message, str) else extract_text_from_content(message)
            if not is_real_user_input(text):
                continue
            if current is None:
                turn_index += 1
                current = Turn(
                    person=person,
                    tool="codex",
                    project_cwd=cwd,
                    session_id=session_id,
                    turn_id=None,
                    turn_index=turn_index,
                    input_at=ts or datetime.now(timezone.utc),
                    task_started_at=ts,
                    input_text=text,
                    source_file=str(path),
                )
            else:
                current.input_at = ts or current.input_at
                current.input_text = text
                current.project_cwd = current.project_cwd or cwd
        elif event_type == "token_count" and current is not None:
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
            current.input_tokens += int(usage.get("input_tokens") or 0)
            current.response_tokens += int(usage.get("output_tokens") or 0)
            current.cache_tokens += int(usage.get("cached_input_tokens") or 0)
            current.reasoning_tokens += int(usage.get("reasoning_output_tokens") or 0)
            current.total_tokens += int(usage.get("total_tokens") or 0)
            current.token_source = "native"
        elif event_type in {"task_complete", "turn_aborted"} and current is not None:
            completed = epoch_seconds(payload.get("completed_at")) or ts
            duration_ms = payload.get("duration_ms")
            if duration_ms is not None:
                try:
                    current.ai_active_seconds = max(0.0, float(duration_ms) / 1000.0)
                    current.wallclock_seconds = current.ai_active_seconds
                except Exception:
                    pass
            finish_current(completed, interrupted=event_type == "turn_aborted")

    finish_current()
    finalize_intervals(turns)
    return turns


def claude_usage(message: dict[str, Any]) -> dict[str, int]:
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    nested = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    cache_creation += int(nested.get("ephemeral_5m_input_tokens") or 0)
    cache_creation += int(nested.get("ephemeral_1h_input_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    input_tokens = int(usage.get("input_tokens") or 0)
    reasoning = int(usage.get("reasoning_output_tokens") or 0)
    cache = cache_creation + cache_read
    return {
        "input_tokens": input_tokens,
        "response_tokens": output,
        "cache_tokens": cache,
        "reasoning_tokens": reasoning,
        "total_tokens": input_tokens + output + cache + reasoning,
    }


def parse_claude_file(
    path: Path,
    person: str,
    tz: ZoneInfo,
    start: datetime,
    end: datetime,
    include_subagents: bool,
    max_estimated_turn_seconds: int,
) -> list[Turn]:
    events: list[dict[str, Any]] = []
    for obj in read_jsonl(path):
        if not include_subagents and obj.get("isSidechain") is True:
            continue
        typ = obj.get("type")
        if typ not in {"user", "assistant"}:
            continue
        ts = parse_dt(obj.get("timestamp"))
        if ts is None:
            continue
        events.append(obj)
    events.sort(key=lambda o: parse_dt(o.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))

    turns: list[Turn] = []
    current: Turn | None = None
    turn_index = 0
    session_id = path.stem
    cwd: str | None = None

    for obj in events:
        typ = obj.get("type")
        ts = parse_dt(obj.get("timestamp"))
        if ts is None:
            continue
        cwd = obj.get("cwd") or cwd
        session_id = str(obj.get("sessionId") or session_id)
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        if typ == "user":
            text = extract_text_from_content(msg.get("content"))
            if not is_real_user_input(text):
                continue
            if current is not None:
                current.task_finished_at = current.task_finished_at or ts
                finalize_estimated_duration(current, max_estimated_turn_seconds)
                if in_range(current.input_at, start, end, tz):
                    turns.append(current)
            turn_index += 1
            current = Turn(
                person=person,
                tool="claude",
                project_cwd=cwd,
                session_id=session_id,
                turn_id=str(obj.get("promptId") or obj.get("uuid") or ""),
                turn_index=turn_index,
                input_at=ts,
                task_started_at=ts,
                input_text=text,
                source_file=str(path),
            )
        elif typ == "assistant" and current is not None:
            current.task_finished_at = ts
            usage = claude_usage(msg)
            if usage["total_tokens"] > 0:
                current.input_tokens += usage["input_tokens"]
                current.response_tokens += usage["response_tokens"]
                current.cache_tokens += usage["cache_tokens"]
                current.reasoning_tokens += usage["reasoning_tokens"]
                current.total_tokens += usage["total_tokens"]
                current.token_source = "native"

    if current is not None:
        finalize_estimated_duration(current, max_estimated_turn_seconds)
        if in_range(current.input_at, start, end, tz):
            turns.append(current)

    finalize_intervals(turns)
    return turns


def finalize_estimated_duration(turn: Turn, max_seconds: int) -> None:
    if turn.task_finished_at is None:
        turn.task_finished_at = turn.task_started_at
    if turn.task_started_at and turn.task_finished_at:
        raw = max(0.0, (turn.task_finished_at - turn.task_started_at).total_seconds())
        turn.wallclock_seconds = raw
        turn.ai_active_seconds = min(raw, float(max_seconds))
    turn.duration_source = "estimated"
    turn.is_estimated = True


def finalize_intervals(turns: list[Turn]) -> None:
    by_session: dict[str, list[Turn]] = defaultdict(list)
    for turn in turns:
        by_session[turn.session_id].append(turn)
    for session_turns in by_session.values():
        session_turns.sort(key=lambda t: t.input_at)
        prev: Turn | None = None
        for i, turn in enumerate(session_turns):
            if prev is not None:
                turn.input_interval_seconds = max(0.0, (turn.input_at - prev.input_at).total_seconds())
            if i + 1 < len(session_turns):
                nxt = session_turns[i + 1]
                turn.next_input_at = nxt.input_at
                if turn.task_finished_at:
                    turn.after_done_gap_seconds = max(
                        0.0, (nxt.input_at - turn.task_finished_at).total_seconds()
                    )
            prev = turn


def collect_turns(args: argparse.Namespace, start: datetime, end: datetime, tz: ZoneInfo) -> list[Turn]:
    project_roots = [] if args.skip_project_root_scan else [Path(p).expanduser() for p in args.project_root or []]
    codex_roots = [Path(p).expanduser() for p in args.codex_root or []]
    claude_roots = [Path(p).expanduser() for p in args.claude_root or []]

    turns: list[Turn] = []
    if project_roots:
        log_progress(
            args,
            "会深扫项目目录中的 .codex/.codex-ui-dev/.claude；项目很多时这一步可能较慢。",
        )
    if not args.only or args.only in {"codex", "all"}:
        log_progress(args, "扫描 Codex 会话文件...")
        codex_files = discover_codex_files(codex_roots, project_roots)
        codex_turns = 0
        for _, path in iter_with_progress(args, codex_files, "Codex"):
            parsed = parse_codex_file(path, args.person, tz, start, end)
            codex_turns += len(parsed)
            turns.extend(parsed)
        log_progress(args, f"Codex: 解析完成，命中 {codex_turns} 轮")
    if not args.only or args.only in {"claude", "all"}:
        log_progress(args, "扫描 Claude Code 会话文件...")
        claude_files = discover_claude_files(claude_roots, project_roots, args.include_subagents)
        claude_turns = 0
        for _, path in iter_with_progress(args, claude_files, "Claude"):
            parsed = parse_claude_file(
                path,
                args.person,
                tz,
                start,
                end,
                args.include_subagents,
                args.max_estimated_turn_seconds,
            )
            claude_turns += len(parsed)
            turns.extend(parsed)
        log_progress(args, f"Claude: 解析完成，命中 {claude_turns} 轮")
    turns.sort(key=lambda t: (t.input_at, t.tool, t.session_id, t.turn_index))
    finalize_intervals(turns)
    log_progress(args, f"总计命中 {len(turns)} 轮，准备写出")
    return turns


def command_init_config(args: argparse.Namespace) -> int:
    path = Path(args.out).expanduser()
    write_default_config(path, args.project or [])
    print(f"Wrote config to {path}")
    return 0


def command_export_workday(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.timezone)
    export_day = date.fromisoformat(args.date)
    start, end = day_bounds(export_day, tz)
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    projects = configured_projects(config)
    config_base = config_path.parent if config_path.parent != Path("") else Path(".")
    data_dir = Path(str(config.get("data_dir") or "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = config_base / data_dir

    log_progress(args, f"导出 v2 工作日报范围: {start.isoformat()} -> {end.isoformat()}")
    turns = collect_turns(args, start, end, tz)
    ai_records = [turn.to_record(tz) for turn in turns]
    if args.project:
        allowed_projects = set(args.project)
        before_count = len(ai_records)
        ai_records = [r for r in ai_records if r.get("project") in allowed_projects]
        log_progress(args, f"AI 项目过滤: {before_count} -> {len(ai_records)}")

    commits, file_changes, git_errors = collect_git_activity(projects, start, end)
    reflection = load_reflection(data_dir, export_day.isoformat())
    report = build_daily_report(
        export_day.isoformat(),
        args.person,
        ai_records,
        commits,
        file_changes,
        reflection,
        git_errors,
    )
    out_dir = Path(args.out).expanduser()
    if str(out_dir) == ".":
        out_dir = data_dir / "reports" / export_day.isoformat()
    write_daily_outputs(out_dir, report, ai_records, commits, file_changes, report.get("associations") or [])
    print(f"Exported v2 work report to {out_dir}")
    if git_errors:
        print("Git warnings:")
        for item in git_errors:
            print(f"- {item}")
    return 0


def resolve_report_data_dir(config_path: Path, config: dict[str, Any]) -> Path:
    config_base = config_path.parent if config_path.parent != Path("") else Path(".")
    data_dir = Path(str(config.get("data_dir") or "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = config_base / data_dir
    return data_dir


def command_list_reports(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    data_dir = resolve_report_data_dir(config_path, config)
    dates, warnings = list_daily_report_dates(data_dir)
    result = {"data_dir": str(data_dir), "dates": dates, "warnings": warnings}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_show_report(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    data_dir = resolve_report_data_dir(config_path, config)
    report, warnings = load_daily_report(data_dir, args.date)
    if report is None:
        print(json.dumps({"date": args.date, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"report": report, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0


def command_topic_trends(args: argparse.Namespace) -> int:
    if date.fromisoformat(args.from_date) > date.fromisoformat(args.to_date):
        raise SystemExit("--from 不能晚于 --to")
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    data_dir = resolve_report_data_dir(config_path, config)
    reports, warnings = load_daily_reports_range(data_dir, args.from_date, args.to_date)
    trends = aggregate_topic_trends(reports)
    result = {
        "schema_version": "2.1",
        "date_range": {"from": args.from_date, "to": args.to_date},
        "topic_trends": trends,
        "warnings": warnings,
    }
    out_dir = Path(args.out).expanduser()
    if str(out_dir) == ".":
        out_dir = reports_root(data_dir) / f"{args.from_date}_{args.to_date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "topic-trends.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported topic trends to {out_path}")
    if warnings:
        print("Warnings:")
        for item in warnings:
            print(f"- {item}")
    return 0


def command_export_week(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    data_dir = resolve_report_data_dir(config_path, config)
    start, end = iso_week_bounds(args.week)
    reports, warnings = load_daily_reports_range(data_dir, start.isoformat(), end.isoformat())
    report = build_period_report("week", args.week, args.person, reports, warnings)
    out_dir = Path(args.out).expanduser()
    if str(out_dir) == ".":
        out_dir = reports_root(data_dir) / args.week
    write_period_outputs(out_dir, report, "weekly")
    print(f"Exported weekly report to {out_dir}")
    if warnings:
        print("Warnings:")
        for item in warnings:
            print(f"- {item}")
    return 0


def command_export_month(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    data_dir = resolve_report_data_dir(config_path, config)
    start, end = month_bounds(args.month)
    reports, warnings = load_daily_reports_range(data_dir, start.isoformat(), end.isoformat())
    report = build_period_report("month", args.month, args.person, reports, warnings)
    out_dir = Path(args.out).expanduser()
    if str(out_dir) == ".":
        out_dir = reports_root(data_dir) / args.month
    write_period_outputs(out_dir, report, "monthly")
    print(f"Exported monthly report to {out_dir}")
    if warnings:
        print("Warnings:")
        for item in warnings:
            print(f"- {item}")
    return 0


def add_common_export_args(parser: argparse.ArgumentParser) -> None:
    home = Path.home()
    default_project_roots = [str(home / "2027")] if (home / "2027").exists() else []
    parser.add_argument("--person", required=True, help="导出人，例如 zac")
    parser.add_argument("--out", default=".", help="输出目录")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--codex-root", action="append", default=[str(home / ".codex")])
    parser.add_argument("--claude-root", action="append", default=[str(home / ".claude")])
    parser.add_argument("--project-root", action="append", default=default_project_roots)
    parser.add_argument("--skip-project-root-scan", action="store_true", help="只扫描 ~/.codex 和 ~/.claude")
    parser.add_argument("--only", choices=["all", "codex", "claude"], default="all")
    parser.add_argument("--project", action="append", default=[], help="只导出指定项目名，可重复传入")
    parser.add_argument("--include-subagents", action="store_true", help="包含 Claude subagents/sidechain")
    parser.add_argument("--max-estimated-turn-seconds", type=int, default=1800)
    parser.add_argument("--verbose", action="store_true", help="显示扫描和解析进度")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aiusage", description="Codex / Claude Code usage exporter")
    parser.add_argument("--version", action="version", version=f"%(prog)s {PRODUCT_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("init-config", help="创建 v2 本地项目配置")
    p_config.add_argument("--out", default="aiusage-config.json", help="配置文件输出路径")
    p_config.add_argument("--project", action="append", default=[], help="项目配置，格式 name=path 或 name=path|repo_url，可重复传入")
    p_config.set_defaults(func=command_init_config)

    p_workday = sub.add_parser("export-workday", help="导出 v2 个人研发工作日报")
    add_common_export_args(p_workday)
    p_workday.add_argument("--date", required=True, type=valid_date_arg, help="YYYY-MM-DD")
    p_workday.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_workday.set_defaults(func=command_export_workday)

    p_list_reports = sub.add_parser("list-reports", help="列出已生成的 v2 日报日期")
    p_list_reports.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_list_reports.set_defaults(func=command_list_reports)

    p_show_report = sub.add_parser("show-report", help="读取指定日期的 v2 日报 JSON")
    p_show_report.add_argument("--date", required=True, type=valid_date_arg, help="YYYY-MM-DD")
    p_show_report.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_show_report.set_defaults(func=command_show_report)

    p_topic_trends = sub.add_parser("topic-trends", help="按日期范围导出技术主题趋势")
    p_topic_trends.add_argument("--from", dest="from_date", required=True, type=valid_date_arg, help="YYYY-MM-DD")
    p_topic_trends.add_argument("--to", dest="to_date", required=True, type=valid_date_arg, help="YYYY-MM-DD")
    p_topic_trends.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_topic_trends.add_argument("--out", default=".", help="输出目录")
    p_topic_trends.set_defaults(func=command_topic_trends)

    p_week = sub.add_parser("export-week", help="基于日报导出 v2.1 周报")
    p_week.add_argument("--person", required=True, help="导出人，例如 lenovo")
    p_week.add_argument("--week", required=True, type=valid_week_arg, help="ISO 周，例如 2026-W25")
    p_week.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_week.add_argument("--out", default=".", help="输出目录")
    p_week.set_defaults(func=command_export_week)

    p_month = sub.add_parser("export-month", help="基于日报导出 v2.1 月报")
    p_month.add_argument("--person", required=True, help="导出人，例如 lenovo")
    p_month.add_argument("--month", required=True, type=valid_month_arg, help="月份，例如 2026-06")
    p_month.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_month.add_argument("--out", default=".", help="输出目录")
    p_month.set_defaults(func=command_export_month)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
