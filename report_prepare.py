#!/usr/bin/env python3
"""Freshness-aware local report preparation for MCP reads."""

from __future__ import annotations

import getpass
import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from aiusage import (
    collect_turns,
    day_bounds,
    discover_claude_files,
    discover_codex_files,
)
from workreport import (
    atomic_write_text,
    build_daily_report,
    collect_git_activity,
    configured_projects,
    daily_report_path,
    json_dumps,
    load_config,
    load_daily_report,
    load_reflection,
    reports_root,
    run_git,
    write_daily_outputs,
)


CURRENT_DAY_CACHE_TTL_SECONDS = 300
REPORT_META_NAME = "report-meta.json"
GENERATOR_VERSION = "mcp-auto-1"
VALID_REFRESH_MODES = {"auto", "cache", "force"}

_day_locks_guard = threading.Lock()
_day_locks: dict[str, threading.Lock] = {}


@dataclass
class EnsureReportResult:
    day: str
    data_dir: Path
    report: dict[str, Any] | None
    warnings: list[str]
    source: str
    data_status: str
    generated_at: str | None = None
    error_code: str | None = None

    @property
    def refreshed(self) -> bool:
        return self.source == "refreshed"

    @property
    def generated(self) -> bool:
        return self.source == "generated"

    @property
    def cached(self) -> bool:
        return self.source == "cache"

    def freshness(self, mode: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "source": self.source,
            "generated_at": self.generated_at,
            "report_date": self.day,
            "data_status": self.data_status,
        }


def ensure_daily_report(day: str, config_path: str | None = None, refresh_mode: str = "auto") -> EnsureReportResult:
    if refresh_mode not in VALID_REFRESH_MODES:
        raise ValueError(f"refresh_mode 必须是 {'/'.join(sorted(VALID_REFRESH_MODES))}")
    config_path_obj, config, data_dir = resolve_report_config(config_path)
    lock = day_lock(config_path_obj, day)
    with lock:
        return _ensure_daily_report_locked(day, config_path_obj, config, data_dir, refresh_mode)


def ensure_daily_reports_range(
    start_day: str,
    end_day: str,
    config_path: str | None = None,
    refresh_mode: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    reports: list[dict[str, Any]] = []
    warnings: list[str] = []
    processed_dates: list[str] = []
    generated_dates: list[str] = []
    refreshed_dates: list[str] = []
    cached_dates: list[str] = []
    failed_dates: list[str] = []
    current = start
    while current <= end:
        day = current.isoformat()
        processed_dates.append(day)
        result = ensure_daily_report(day, config_path, refresh_mode)
        warnings.extend(result.warnings)
        if result.report is not None:
            reports.append(result.report)
        if result.generated:
            generated_dates.append(day)
        elif result.refreshed:
            refreshed_dates.append(day)
        elif result.cached:
            cached_dates.append(day)
        if result.data_status == "failed":
            failed_dates.append(day)
        current += timedelta(days=1)

    meta = {
        "requested_date_range": {"from": start_day, "to": end_day},
        "effective_date_range": {"from": start_day, "to": end_day},
        "processed_dates": processed_dates,
        "generated_dates": generated_dates,
        "refreshed_dates": refreshed_dates,
        "cached_dates": cached_dates,
        "failed_dates": failed_dates,
        "report_day_count": len(processed_dates),
    }
    return reports, meta, unique_warnings(warnings)


def resolve_report_config(config_path: str | None) -> tuple[Path, dict[str, Any], Path]:
    config_path_obj = Path(config_path or "aiusage-config.json").expanduser()
    config = load_config(config_path_obj)
    base = config_path_obj.parent if config_path_obj.parent != Path("") else Path(".")
    data_dir = Path(str(config.get("data_dir") or "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = base / data_dir
    return config_path_obj, config, data_dir


def day_lock(config_path: Path, day: str) -> threading.Lock:
    key = f"{config_path.resolve()}::{day}"
    with _day_locks_guard:
        lock = _day_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _day_locks[key] = lock
        return lock


def _ensure_daily_report_locked(
    day: str,
    config_path: Path,
    config: dict[str, Any],
    data_dir: Path,
    refresh_mode: str,
) -> EnsureReportResult:
    report, load_warnings = load_daily_report(data_dir, day)
    report_path = daily_report_path(data_dir, day)
    meta = read_report_meta(data_dir, day)
    existing_status = report_data_status(report)
    if refresh_mode == "cache":
        if report is None:
            return EnsureReportResult(day, data_dir, None, load_warnings, "cache", "failed", error_code="REPORT_NOT_FOUND")
        return EnsureReportResult(day, data_dir, report, [], "cache", existing_status, meta.get("generated_at"))

    try:
        fingerprint = source_fingerprint(config, day)
        should_refresh, reason = should_refresh_report(day, report_path, report, meta, fingerprint, refresh_mode)
        if not should_refresh:
            return EnsureReportResult(day, data_dir, report, [], "cache", existing_status, meta.get("generated_at"))

        built = generate_daily_report(day, config, data_dir)
        generated_report = built["report"]
        generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        source = "refreshed" if report is not None else "generated"
        meta_payload = {
            "generated_at": generated_at,
            "refresh_reason": reason,
            "source_fingerprint": fingerprint,
            "codex_file_count": fingerprint.get("codex_file_count", 0),
            "claude_file_count": fingerprint.get("claude_file_count", 0),
            "git_heads": fingerprint.get("git_heads", {}),
            "generator_version": GENERATOR_VERSION,
            "data_status": report_data_status(generated_report),
        }
        write_daily_outputs(
            reports_root(data_dir) / day,
            generated_report,
            built["ai_records"],
            built["commits"],
            built["file_changes"],
            generated_report.get("associations") or [],
        )
        write_report_meta(data_dir, day, meta_payload)
        return EnsureReportResult(
            day,
            data_dir,
            generated_report,
            built["warnings"],
            source,
            report_data_status(generated_report),
            generated_at,
        )
    except Exception as exc:
        warning = f"{day}: 自动生成日报失败: {exc}"
        if report is not None:
            return EnsureReportResult(
                day,
                data_dir,
                report,
                [warning],
                "cache",
                existing_status,
                meta.get("generated_at"),
                error_code="REPORT_REFRESH_FAILED",
            )
        return EnsureReportResult(
            day,
            data_dir,
            None,
            [warning],
            "generated",
            "failed",
            error_code="REPORT_GENERATION_FAILED",
        )


def generate_daily_report(day: str, config: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    tz = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    target_day = date.fromisoformat(day)
    start, end = day_bounds(target_day, tz)
    person = str(config.get("person") or getpass.getuser() or "unknown")
    args = SimpleNamespace(
        person=person,
        timezone=str(config.get("timezone") or "Asia/Shanghai"),
        codex_root=[str(Path(x).expanduser()) for x in config.get("codex_roots") or [Path.home() / ".codex"]],
        claude_root=[str(Path(x).expanduser()) for x in config.get("claude_roots") or [Path.home() / ".claude"]],
        project_root=[str(Path(x).expanduser()) for x in configured_project_roots(config)],
        skip_project_root_scan=bool(config.get("skip_project_root_scan", False)),
        only=str(config.get("only") or "all"),
        project=list(config.get("project_filter") or []),
        include_subagents=bool(config.get("include_subagents", False)),
        max_estimated_turn_seconds=int(config.get("max_estimated_turn_seconds") or 1800),
        verbose=False,
    )
    turns = collect_turns(args, start, end, tz)
    ai_records = [turn.to_record(tz) for turn in turns]
    if args.project:
        allowed_projects = set(args.project)
        ai_records = [r for r in ai_records if r.get("project") in allowed_projects]
    commits, file_changes, git_errors = collect_git_activity(configured_projects(config), start, end)
    reflection = load_reflection(data_dir, day)
    report = build_daily_report(day, person, ai_records, commits, file_changes, reflection, git_errors)
    report["data_status"] = report_data_status(report)
    return {
        "report": report,
        "ai_records": ai_records,
        "commits": commits,
        "file_changes": file_changes,
        "warnings": git_errors,
    }


def configured_project_roots(config: dict[str, Any]) -> list[Path]:
    if "project_roots" in config:
        return [Path(x).expanduser() for x in config.get("project_roots") or []]
    default = Path.home() / "2027"
    return [default] if default.exists() else []


def source_fingerprint(config: dict[str, Any], day: str) -> dict[str, Any]:
    project_roots = [] if bool(config.get("skip_project_root_scan", False)) else configured_project_roots(config)
    codex_roots = [Path(x).expanduser() for x in config.get("codex_roots") or [Path.home() / ".codex"]]
    claude_roots = [Path(x).expanduser() for x in config.get("claude_roots") or [Path.home() / ".claude"]]
    include_subagents = bool(config.get("include_subagents", False))
    codex_files = discover_codex_files(codex_roots, project_roots)
    claude_files = discover_claude_files(claude_roots, project_roots, include_subagents)
    source_files = codex_files + claude_files
    mtimes = [safe_mtime(path) for path in source_files]
    git_heads = git_head_fingerprint(configured_projects(config))
    return {
        "day": day,
        "codex_file_count": len(codex_files),
        "claude_file_count": len(claude_files),
        "source_file_count": len(source_files),
        "source_max_mtime": max(mtimes) if mtimes else 0,
        "git_heads": git_heads,
    }


def git_head_fingerprint(projects: list[Any]) -> dict[str, str]:
    heads: dict[str, str] = {}
    for project in projects:
        if not project.path.exists():
            heads[project.name] = "missing"
            continue
        proc = run_git(project.path, ["rev-parse", "HEAD"])
        heads[project.name] = proc.stdout.strip()[:12] if proc.returncode == 0 else "unavailable"
    return heads


def should_refresh_report(
    day: str,
    report_path: Path,
    report: dict[str, Any] | None,
    meta: dict[str, Any],
    fingerprint: dict[str, Any],
    refresh_mode: str,
) -> tuple[bool, str]:
    if refresh_mode == "force":
        return True, "force"
    if report is None or not report_path.exists():
        return True, "missing"
    if is_today(day):
        age = datetime.now(timezone.utc).timestamp() - report_path.stat().st_mtime
        if age > CURRENT_DAY_CACHE_TTL_SECONDS:
            return True, "current_day_ttl"
    if not meta:
        if float(fingerprint.get("source_max_mtime") or 0) > report_path.stat().st_mtime:
            return True, "source_newer_than_legacy_report"
        return False, "legacy_cache_without_meta"
    previous = meta.get("source_fingerprint") or {}
    if previous.get("source_file_count") != fingerprint.get("source_file_count"):
        return True, "source_file_count_changed"
    if float(previous.get("source_max_mtime") or 0) > report_path.stat().st_mtime:
        return True, "source_newer_than_report"
    if previous.get("source_max_mtime") != fingerprint.get("source_max_mtime"):
        return True, "source_mtime_changed"
    if previous.get("git_heads") != fingerprint.get("git_heads"):
        return True, "git_head_changed"
    return False, "cache_valid"


def report_data_status(report: dict[str, Any] | None) -> str:
    if report is None:
        return "failed"
    explicit = str(report.get("data_status") or "")
    if explicit in {"available", "no_activity", "failed"}:
        return explicit
    overview = report.get("overview") or {}
    has_activity = any(
        int(overview.get(field) or 0) > 0
        for field in ("ai_turn_count", "ai_session_count", "commit_count", "files_changed")
    )
    return "available" if has_activity else "no_activity"


def read_report_meta(data_dir: Path, day: str) -> dict[str, Any]:
    path = reports_root(data_dir) / day / REPORT_META_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_report_meta(data_dir: Path, day: str, meta: dict[str, Any]) -> None:
    path = reports_root(data_dir) / day / REPORT_META_NAME
    atomic_write_text(path, json_dumps(meta) + "\n")


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def is_today(day: str) -> bool:
    return date.fromisoformat(day) == datetime.now().astimezone().date()


def unique_warnings(warnings: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in warnings:
        text = str(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
