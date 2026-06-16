#!/usr/bin/env python3
"""
Export and summarize Codex / Claude Code usage as a tiny daily package.

Daily export package:
  inputs.jsonl  - one normalized record per real user input turn
  summary.md    - human-readable summary for quick review
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import zipfile
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


def log_progress(args: argparse.Namespace, message: str) -> None:
    if getattr(args, "verbose", False):
        print(message, flush=True)


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


def fmt_seconds(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    sec = int(round(float(seconds)))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if sec or not parts:
        parts.append(f"{sec}秒")
    return "".join(parts)


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


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


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


def aggregate(records: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        groups[tuple(rec.get(k) for k in keys)].append(rec)
    rows: list[dict[str, Any]] = []
    for key, items in sorted(groups.items(), key=lambda kv: kv[0]):
        row = {k: v for k, v in zip(keys, key)}
        row["session_count"] = len({x.get("session_id") for x in items if x.get("session_id")})
        row["turn_count"] = len(items)
        for field_name in (
            "input_tokens",
            "response_tokens",
            "cache_tokens",
            "reasoning_tokens",
            "total_tokens",
            "ai_active_seconds",
            "wallclock_seconds",
        ):
            row[field_name] = sum(float(x.get(field_name) or 0) for x in items)
        intervals = [float(x["input_interval_seconds"]) for x in items if x.get("input_interval_seconds") is not None]
        gaps = [float(x["after_done_gap_seconds"]) for x in items if x.get("after_done_gap_seconds") is not None]
        row["avg_input_interval_seconds"] = statistics.mean(intervals) if intervals else None
        row["median_input_interval_seconds"] = statistics.median(intervals) if intervals else None
        row["avg_after_done_gap_seconds"] = statistics.mean(gaps) if gaps else None
        row["median_after_done_gap_seconds"] = statistics.median(gaps) if gaps else None
        rows.append(row)
    return rows


def markdown_summary(records: list[dict[str, Any]], title: str) -> str:
    total = aggregate(records, [])[0] if records else {}
    by_day = aggregate(records, ["date"])
    by_project = aggregate(records, ["project"])
    by_tool = aggregate(records, ["tool"])
    high_tokens = sorted(records, key=lambda x: int(x.get("total_tokens") or 0), reverse=True)[:10]
    long_tasks = sorted(records, key=lambda x: float(x.get("ai_active_seconds") or 0), reverse=True)[:10]

    lines = [f"# {title}", ""]
    lines.extend(
        [
            "## 总览",
            "",
            f"- 输入轮数：{int(total.get('turn_count') or 0)}",
            f"- 会话数：{int(total.get('session_count') or 0)}",
            f"- AI执行时长：{fmt_seconds(total.get('ai_active_seconds') or 0)}",
            f"- 对话占用时长：{fmt_seconds(total.get('wallclock_seconds') or 0)}",
            f"- 输入token：{int(total.get('input_tokens') or 0):,}",
            f"- 响应token：{int(total.get('response_tokens') or 0):,}",
            f"- cache token：{int(total.get('cache_tokens') or 0):,}",
            f"- reasoning token：{int(total.get('reasoning_tokens') or 0):,}",
            f"- 总token：{int(total.get('total_tokens') or 0):,}",
            f"- 平均输入间隔：{fmt_seconds(total.get('avg_input_interval_seconds'))}",
            "",
        ]
    )
    lines.extend(workflow_section(records))
    lines.extend(table_section("按天", by_day, ["date"]))
    lines.extend(table_section("按工具", by_tool, ["tool"]))
    lines.extend(table_section("按项目", by_project, ["project"]))
    lines.extend(session_summary_section(records))
    lines.extend(detail_section("高token轮次", high_tokens, "total_tokens"))
    lines.extend(detail_section("长任务轮次", long_tasks, "ai_active_seconds"))
    return "\n".join(lines) + "\n"


def workflow_section(records: list[dict[str, Any]]) -> list[str]:
    samples: list[dict[str, float]] = []
    gap_values: list[float] = []
    for row in records:
        input_at = parse_dt(str(row.get("input_at") or ""))
        next_input_at = parse_dt(str(row.get("next_input_at") or ""))
        if input_at is None or next_input_at is None:
            continue
        interval = max(0.0, (next_input_at - input_at).total_seconds())
        ai_seconds = max(0.0, float(row.get("ai_active_seconds") or 0))
        ai_component = min(ai_seconds, interval)
        # For rhythm decomposition, keep the two parts inside the same
        # input-to-next-input window. Some Codex native events can start a
        # task a few seconds before the user_message event, so raw
        # after_done_gap_seconds is kept in inputs.jsonl but not used for
        # percentage decomposition.
        gap_seconds = max(0.0, interval - ai_component)
        samples.append(
            {
                "interval": interval,
                "ai": ai_component,
                "gap": min(gap_seconds, interval),
            }
        )
        gap_values.append(gap_seconds)

    lines = ["## 输入节奏拆解", ""]
    if not samples:
        return lines + ["有效样本不足。", ""]

    total_interval = sum(x["interval"] for x in samples)
    total_ai = sum(x["ai"] for x in samples)
    total_gap = sum(x["gap"] for x in samples)
    ai_ratio = (total_ai / total_interval * 100.0) if total_interval else 0.0
    gap_ratio = (total_gap / total_interval * 100.0) if total_interval else 0.0
    intervals = [x["interval"] for x in samples]
    ai_values = [x["ai"] for x in samples]

    lines.extend(
        [
            "> 这里的“接续/审查间隔”不是空闲时间，通常包含看 AI 结果、人工验证、切换项目、整理需求、处理并行会话等动作。",
            "",
            f"- 有效节奏样本：{len(samples)}",
            f"- 平均输入到下一输入：{fmt_seconds(statistics.mean(intervals))}",
            f"- 其中平均 AI 运行：{fmt_seconds(statistics.mean(ai_values))}",
            f"- 其中平均接续/审查：{fmt_seconds(statistics.mean(gap_values))}",
            f"- 按时间拆分：AI运行约 {ai_ratio:.1f}% / 接续审查约 {gap_ratio:.1f}%",
            "",
        ]
    )

    buckets = [
        ("1分钟内即时接续", 0, 60),
        ("1-5分钟快速审查", 60, 300),
        ("5-15分钟正常验证", 300, 900),
        ("15-30分钟深度处理", 900, 1800),
        ("30分钟以上跨任务/休息/并行", 1800, float("inf")),
    ]
    lines.append("| 接续/审查区间 | 轮数 | 占比 |")
    lines.append("|---|---:|---:|")
    for label, low, high in buckets:
        count = sum(1 for v in gap_values if low <= v < high)
        ratio = count / len(gap_values) * 100.0 if gap_values else 0.0
        lines.append(f"| {label} | {count} | {ratio:.1f}% |")
    lines.append("")
    return lines


def table_section(title: str, rows: list[dict[str, Any]], key_cols: list[str]) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        return lines + ["无数据", ""]
    cols = key_cols + ["turn_count", "ai_active_seconds", "input_tokens", "response_tokens", "cache_tokens", "total_tokens"]
    header = ["项目" if c == "project" else "工具" if c == "tool" else "日期" if c == "date" else c for c in cols]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for row in sorted(rows, key=lambda x: int(x.get("total_tokens") or 0), reverse=True):
        vals: list[str] = []
        for col in cols:
            val = row.get(col)
            if col.endswith("_seconds"):
                vals.append(fmt_seconds(val or 0))
            elif isinstance(val, (int, float)):
                vals.append(f"{int(val):,}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    return lines


def summarize_session_inputs(items: list[dict[str, Any]]) -> str:
    previews = [str(x.get("input_preview") or "").strip() for x in items if x.get("input_preview")]
    previews = [p for p in previews if p]
    if not previews:
        return "-"
    if len(previews) == 1:
        return previews[0]
    first = previews[0]
    rest = previews[1:]
    # Keep deterministic and privacy-light. This is intentionally a compact
    # rule summary; the full input text remains in inputs.jsonl for AI review.
    themes = []
    for p in rest[:3]:
        if p not in themes and p != first:
            themes.append(p)
    suffix = "；".join(themes)
    summary = first if not suffix else f"{first}；后续：{suffix}"
    return text_preview(summary, 180)


def session_summary_section(records: list[dict[str, Any]]) -> list[str]:
    lines = ["## 会话摘要", ""]
    if not records:
        return lines + ["无数据", ""]
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        key = (
            str(row.get("person") or ""),
            str(row.get("tool") or ""),
            str(row.get("project") or ""),
            str(row.get("session_id") or ""),
        )
        groups[key].append(row)

    rows: list[dict[str, Any]] = []
    for (person, tool, project, session_id), items in groups.items():
        items = sorted(items, key=lambda x: str(x.get("input_at") or ""))
        rows.append(
            {
                "person": person,
                "tool": tool,
                "project": project,
                "session_id": session_id,
                "start": items[0].get("input_at"),
                "end": items[-1].get("input_at"),
                "turn_count": len(items),
                "ai_active_seconds": sum(float(x.get("ai_active_seconds") or 0) for x in items),
                "total_tokens": sum(int(x.get("total_tokens") or 0) for x in items),
                "summary": summarize_session_inputs(items),
            }
        )
    rows.sort(key=lambda x: (str(x["start"]), str(x["project"])))

    lines.append("| 开始时间 | 工具 | 项目 | 轮数 | AI时长 | 总token | 输入内容摘要 |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for row in rows[:30]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["start"] or ""),
                    str(row["tool"] or ""),
                    str(row["project"] or ""),
                    str(row["turn_count"]),
                    fmt_seconds(row["ai_active_seconds"]),
                    f"{int(row['total_tokens']):,}",
                    str(row["summary"]).replace("|", "\\|"),
                ]
            )
            + " |"
        )
    if len(rows) > 30:
        lines.append(f"| ... | ... | ... | ... | ... | ... | 仅展示前30个会话，共{len(rows)}个 |")
    lines.append("")
    return lines


def detail_section(title: str, rows: list[dict[str, Any]], sort_field: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        return lines + ["无数据", ""]
    lines.append("| 时间 | 工具 | 项目 | AI时长 | 总token | 输入摘要 |")
    lines.append("|---|---|---|---:|---:|---|")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("input_at") or ""),
                    str(row.get("tool") or ""),
                    str(row.get("project") or ""),
                    fmt_seconds(row.get("ai_active_seconds") or 0),
                    f"{int(row.get('total_tokens') or 0):,}",
                    str(row.get("input_preview") or "").replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def write_export_zip(records: list[dict[str, Any]], out_dir: Path, person: str, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"ai-usage-{person}-{label}.zip"
    inputs_data = "\n".join(json_dumps(r) for r in records) + ("\n" if records else "")
    summary = markdown_summary(records, f"AI 使用统计 - {person} - {label}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("inputs.jsonl", inputs_data)
        z.writestr("summary.md", summary)
    return zip_path


def command_export(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.timezone)
    if args.command == "export-day":
        export_day = date.fromisoformat(args.date)
        start, end = day_bounds(export_day, tz)
        label = export_day.isoformat()
    else:
        start_day = date.fromisoformat(args.from_date)
        end_day = date.fromisoformat(args.to_date)
        start, _ = day_bounds(start_day, tz)
        _, end = day_bounds(end_day, tz)
        label = f"{start_day.isoformat()}_{end_day.isoformat()}"

    log_progress(args, f"导出范围: {start.isoformat()} -> {end.isoformat()}")
    turns = collect_turns(args, start, end, tz)
    records = [turn.to_record(tz) for turn in turns]
    if args.project:
        allowed_projects = set(args.project)
        before_count = len(records)
        records = [r for r in records if r.get("project") in allowed_projects]
        log_progress(args, f"项目过滤: {before_count} -> {len(records)}")
    log_progress(args, "写出 zip 包...")
    zip_path = write_export_zip(records, Path(args.out).expanduser(), args.person, label)
    print(f"Exported {len(records)} turns to {zip_path}")
    return 0


def read_inputs_from_zip_or_file(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            if "inputs.jsonl" not in z.namelist():
                return records
            with z.open("inputs.jsonl") as f:
                for raw in f:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        records.append(json.loads(line))
    elif path.name == "inputs.jsonl" or path.suffix == ".jsonl":
        for obj in read_jsonl(path):
            records.append(obj)
    return records


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def command_merge(args: argparse.Namespace) -> int:
    input_dir = Path(args.input).expanduser()
    out_dir = Path(args.out).expanduser()
    records: list[dict[str, Any]] = []
    for path in sorted(input_dir.glob("*")):
        if path.is_file() and (path.suffix in {".zip", ".jsonl"} or path.name == "inputs.jsonl"):
            log_progress(args, f"读取: {path}")
            records.extend(read_inputs_from_zip_or_file(path))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "team_inputs.jsonl").write_text(
        "\n".join(json_dumps(r) for r in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    write_csv(out_dir / "team_daily_summary.csv", aggregate(records, ["person", "date", "tool"]))
    write_csv(out_dir / "team_project_summary.csv", aggregate(records, ["person", "project", "tool"]))
    write_csv(out_dir / "team_person_summary.csv", aggregate(records, ["person"]))
    (out_dir / "summary.md").write_text(markdown_summary(records, "团队 AI 使用统计"), encoding="utf-8")
    print(f"Merged {len(records)} turns into {out_dir}")
    return 0


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
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("init-config", help="创建 v2 本地项目配置")
    p_config.add_argument("--out", default="aiusage-config.json", help="配置文件输出路径")
    p_config.add_argument("--project", action="append", default=[], help="项目配置，格式 name=path 或 name=path|repo_url，可重复传入")
    p_config.set_defaults(func=command_init_config)

    p_day = sub.add_parser("export-day", help="导出某一天的 inputs.jsonl + summary.md zip")
    add_common_export_args(p_day)
    p_day.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_day.set_defaults(func=command_export)

    p_workday = sub.add_parser("export-workday", help="导出 v2 个人研发工作日报")
    add_common_export_args(p_workday)
    p_workday.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_workday.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_workday.set_defaults(func=command_export_workday)

    p_list_reports = sub.add_parser("list-reports", help="列出已生成的 v2 日报日期")
    p_list_reports.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_list_reports.set_defaults(func=command_list_reports)

    p_show_report = sub.add_parser("show-report", help="读取指定日期的 v2 日报 JSON")
    p_show_report.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_show_report.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_show_report.set_defaults(func=command_show_report)

    p_topic_trends = sub.add_parser("topic-trends", help="按日期范围导出技术主题趋势")
    p_topic_trends.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    p_topic_trends.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    p_topic_trends.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_topic_trends.add_argument("--out", default=".", help="输出目录")
    p_topic_trends.set_defaults(func=command_topic_trends)

    p_week = sub.add_parser("export-week", help="基于日报导出 v2.1 周报")
    p_week.add_argument("--person", required=True, help="导出人，例如 lenovo")
    p_week.add_argument("--week", required=True, help="ISO 周，例如 2026-W25")
    p_week.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_week.add_argument("--out", default=".", help="输出目录")
    p_week.set_defaults(func=command_export_week)

    p_month = sub.add_parser("export-month", help="基于日报导出 v2.1 月报")
    p_month.add_argument("--person", required=True, help="导出人，例如 lenovo")
    p_month.add_argument("--month", required=True, help="月份，例如 2026-06")
    p_month.add_argument("--config", default="aiusage-config.json", help="v2 项目配置文件")
    p_month.add_argument("--out", default=".", help="输出目录")
    p_month.set_defaults(func=command_export_month)

    p_range = sub.add_parser("export-range", help="导出日期范围，起止日期均包含")
    add_common_export_args(p_range)
    p_range.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    p_range.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    p_range.set_defaults(func=command_export)

    p_merge = sub.add_parser("merge", help="合并多人每日 zip，生成团队汇总")
    p_merge.add_argument("--input", required=True, help="包含 ai-usage-*.zip 的目录")
    p_merge.add_argument("--out", required=True, help="团队报表输出目录")
    p_merge.add_argument("--verbose", action="store_true", help="显示读取进度")
    p_merge.set_defaults(func=command_merge)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
