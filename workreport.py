#!/usr/bin/env python3
"""Local v2 work report helpers for AI usage, Git activity, and reflection."""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "projects": [],
    "data_dir": "data",
}

REWORK_WORDS = ("继续修改", "还是不对", "恢复", "回滚", "不生效", "报错", "再改", "fix", "revert", "rollback")

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "Java": (".java", "java"),
    "Spring Boot": ("spring", "controller", "service", "mapper", "repository"),
    "MySQL": (".sql", "mysql", "sql", "表", "索引"),
    "Redis": ("redis",),
    "Vue": (".vue", "vue", "vite", "pinia"),
    "Git": ("git", "commit", "merge", "rebase", "cherry-pick"),
    "支付": ("支付", "payment", "pay", "订单", "回调"),
    "权限": ("权限", "permission", "auth", "role", "登录", "认证"),
    "状态机": ("状态机", "status", "state"),
    "幂等": ("幂等", "idempotent", "重复提交"),
    "OBS": ("obs",),
    "OCR": ("ocr",),
    "Kafka": ("kafka",),
    "Elasticsearch": ("elasticsearch", "elastic", "es"),
    "RAG": ("rag",),
    "MCP": ("mcp",),
}


@dataclass
class ProjectConfig:
    name: str
    path: Path
    repo_url: str | None = None


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def compact_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


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


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误: {path}")
    data.setdefault("projects", [])
    data.setdefault("data_dir", "data")
    return data


def write_default_config(path: Path, projects: list[str]) -> Path:
    parsed_projects = []
    for item in projects:
        if "=" not in item:
            raise ValueError("--project 格式应为 name=path 或 name=path|repo_url")
        name, raw_path = item.split("=", 1)
        local_path, repo_url = split_project_target(raw_path)
        row = {"name": name.strip(), "path": str(Path(local_path).expanduser())}
        if repo_url:
            row["repo_url"] = repo_url
        parsed_projects.append(row)
    data = dict(DEFAULT_CONFIG)
    data["projects"] = parsed_projects
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(data) + "\n", encoding="utf-8")
    return path


def configured_projects(config: dict[str, Any]) -> list[ProjectConfig]:
    projects: list[ProjectConfig] = []
    for raw in config.get("projects") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        path_text = str(raw.get("path") or "").strip()
        repo_url = str(raw.get("repo_url") or "").strip() or None
        if not name or not path_text:
            continue
        projects.append(ProjectConfig(name=name, path=Path(path_text).expanduser(), repo_url=repo_url))
    return projects


def split_project_target(value: str) -> tuple[str, str | None]:
    if "|" not in value:
        return value.strip(), None
    local_path, repo_url = value.split("|", 1)
    return local_path.strip(), repo_url.strip() or None


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git_commit_records(project: ProjectConfig, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not project.path.exists():
        hint = f"，远程仓库: {project.repo_url}" if project.repo_url else ""
        return [], [], [f"{project.name}: 本地项目目录不存在: {project.path}{hint}。请先 git clone 到该路径，日报采集读取本地 Git 历史。"]
    inside = run_git(project.path, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        hint = f"，远程仓库: {project.repo_url}" if project.repo_url else ""
        return [], [], [f"{project.name}: 不是 Git 仓库: {project.path}{hint}。请确认 path 指向 git clone 后的目录。"]

    proc = run_git(
        project.path,
        [
            "log",
            f"--since={start.isoformat()}",
            f"--until={end.isoformat()}",
            "--date=iso-strict",
            "--pretty=format:@@COMMIT@@%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%s",
            "--numstat",
        ],
    )
    if proc.returncode != 0:
        return [], [], [f"{project.name}: git log 失败: {proc.stderr.strip()}"]

    commits: list[dict[str, Any]] = []
    file_changes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_files: list[dict[str, Any]] = []

    def finish_current() -> None:
        nonlocal current, current_files
        if current is None:
            return
        current["files_changed"] = len(current_files)
        current["insertions"] = sum(int(x.get("insertions") or 0) for x in current_files)
        current["deletions"] = sum(int(x.get("deletions") or 0) for x in current_files)
        current["categories"] = sorted({x["category"] for x in current_files})
        current["modules"] = sorted({x["module"] for x in current_files if x.get("module")})
        current["file_summaries"] = [
            {
                "path": x["path"],
                "category": x["category"],
                "module": x["module"],
                "insertions": x["insertions"],
                "deletions": x["deletions"],
            }
            for x in current_files[:30]
        ]
        commits.append(current)
        file_changes.extend(current_files)
        current = None
        current_files = []

    for raw_line in proc.stdout.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("@@COMMIT@@"):
            finish_current()
            parts = line[len("@@COMMIT@@") :].split("\x1f")
            if len(parts) < 6:
                continue
            commit_hash, parents, author, email, committed_at, message = parts[:6]
            current = {
                "project": project.name,
                "repo_path": str(project.path),
                "repo_url": project.repo_url,
                "hash": commit_hash,
                "short_hash": commit_hash[:8],
                "parents": parents.split() if parents else [],
                "author": author,
                "author_email": email,
                "committed_at": committed_at,
                "message": message,
                "is_merge": len(parents.split()) > 1,
            }
            continue
        if current is None or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        add_text, del_text, path_text = cols[0], cols[1], cols[2]
        is_binary = add_text == "-" or del_text == "-"
        insertions = 0 if is_binary else int(add_text or 0)
        deletions = 0 if is_binary else int(del_text or 0)
        category = classify_path(path_text)
        module = infer_module(path_text)
        current_files.append(
            {
                "project": project.name,
                "repo_path": str(project.path),
                "repo_url": project.repo_url,
                "commit_hash": current["hash"],
                "short_hash": current["short_hash"],
                "committed_at": current["committed_at"],
                "path": path_text,
                "category": category,
                "module": module,
                "insertions": insertions,
                "deletions": deletions,
                "is_binary": is_binary,
            }
        )
    finish_current()
    return commits, file_changes, errors


def collect_git_activity(projects: list[ProjectConfig], start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    commits: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    for project in projects:
        project_commits, project_files, project_errors = git_commit_records(project, start, end)
        commits.extend(project_commits)
        files.extend(project_files)
        errors.extend(project_errors)
    commits.sort(key=lambda x: str(x.get("committed_at") or ""))
    files.sort(key=lambda x: (str(x.get("committed_at") or ""), str(x.get("path") or "")))
    return commits, files, errors


def classify_path(path_text: str) -> str:
    path = path_text.lower()
    suffix = Path(path).suffix
    if "test" in path or "spec" in path:
        return "test"
    if suffix == ".sql":
        return "sql"
    if suffix in {".md", ".txt", ".rst", ".docx"}:
        return "doc"
    if suffix in {".vue", ".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".less", ".html"}:
        return "frontend"
    if suffix in {".java", ".py", ".go", ".kt", ".cs", ".php", ".rb"}:
        return "backend"
    if suffix in {".yml", ".yaml", ".json", ".toml", ".ini", ".env", ".properties", ".xml"}:
        return "config"
    return "other"


def infer_module(path_text: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", path_text) if p]
    ignored = {
        "src",
        "main",
        "test",
        "java",
        "resources",
        "components",
        "views",
        "pages",
        "api",
        "assets",
        "utils",
        "common",
    }
    for part in parts:
        normalized = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "", part)
        if normalized and normalized.lower() not in ignored and "." not in normalized:
            return normalized
    return parts[0] if parts else ""


def load_reflection(data_dir: Path, day: str) -> dict[str, Any]:
    path = reflection_path(data_dir, day)
    if not path.exists():
        return {
            "date": day,
            "most_important_goal": "",
            "actual_result": "",
            "biggest_blocker": "",
            "accepted": False,
            "has_rework": False,
            "other_work": "",
            "updated_at": None,
        }
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"复盘文件格式错误: {path}")
    return data


def save_reflection(data_dir: Path, day: str, reflection: dict[str, Any]) -> Path:
    data = dict(reflection)
    data["date"] = day
    data["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    path = reflection_path(data_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(data) + "\n", encoding="utf-8")
    return path


def reflection_path(data_dir: Path, day: str) -> Path:
    return data_dir / "reflections" / f"{day}.json"


def project_matches(ai_row: dict[str, Any], commit: dict[str, Any]) -> bool:
    ai_project = str(ai_row.get("project") or "").lower()
    commit_project = str(commit.get("project") or "").lower()
    if ai_project and commit_project and ai_project == commit_project:
        return True
    cwd = str(ai_row.get("project_cwd") or "").lower().replace("\\", "/")
    repo = str(commit.get("repo_path") or "").lower().replace("\\", "/")
    return bool(cwd and repo and (cwd.startswith(repo) or repo.startswith(cwd)))


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[\w\u4e00-\u9fff]{2,}", text.lower())
    return {w for w in words if len(w) >= 2}


def associate_ai_git(ai_records: list[dict[str, Any]], commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sessions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ai_records:
        sessions[(str(row.get("project") or ""), str(row.get("session_id") or ""))].append(row)

    associations: list[dict[str, Any]] = []
    for (_, session_id), items in sessions.items():
        items = sorted(items, key=lambda x: str(x.get("input_at") or ""))
        if not session_id or not items:
            continue
        session_text = " ".join(str(x.get("input_text") or x.get("input_preview") or "") for x in items)
        session_tokens = tokenize(session_text)
        first = parse_iso(str(items[0].get("input_at") or ""))
        last = parse_iso(str(items[-1].get("task_finished_at") or items[-1].get("input_at") or ""))

        scored: list[dict[str, Any]] = []
        for commit in commits:
            score = 0
            evidence: list[str] = []
            if project_matches(items[0], commit):
                score += 40
                evidence.append("项目路径或项目名匹配")
            committed_at = parse_iso(str(commit.get("committed_at") or ""))
            if first and last and committed_at:
                hours_after_start = (committed_at - first).total_seconds() / 3600
                hours_after_end = (committed_at - last).total_seconds() / 3600
                if -1 <= hours_after_start and hours_after_end <= 8:
                    score += 25
                    evidence.append("提交时间靠近 AI 会话")
                elif -6 <= hours_after_start and hours_after_end <= 24:
                    score += 10
                    evidence.append("提交时间在同日邻近窗口")
            message_overlap = session_tokens.intersection(tokenize(str(commit.get("message") or "")))
            if message_overlap:
                score += min(16, len(message_overlap) * 4)
                evidence.append("输入内容与提交信息存在关键词重合: " + ", ".join(sorted(message_overlap)[:5]))
            path_text = " ".join(str(x.get("path") or "") for x in commit.get("file_summaries") or [])
            path_overlap = session_tokens.intersection(tokenize(path_text))
            if path_overlap:
                score += min(20, len(path_overlap) * 5)
                evidence.append("输入内容与文件路径存在关键词重合: " + ", ".join(sorted(path_overlap)[:5]))
            if score >= 30:
                scored.append(
                    {
                        "commit_hash": commit.get("hash"),
                        "short_hash": commit.get("short_hash"),
                        "message": commit.get("message"),
                        "score": score,
                        "confidence": confidence_label(score),
                        "evidence": evidence,
                    }
                )
        scored.sort(key=lambda x: int(x["score"]), reverse=True)
        if scored:
            associations.append(
                {
                    "session_id": session_id,
                    "project": items[0].get("project"),
                    "start_at": items[0].get("input_at"),
                    "end_at": items[-1].get("task_finished_at") or items[-1].get("input_at"),
                    "turn_count": len(items),
                    "input_summary": summarize_texts([str(x.get("input_preview") or "") for x in items]),
                    "matched_commits": scored[:5],
                    "best_confidence": scored[0]["confidence"],
                    "best_score": scored[0]["score"],
                }
            )
    associations.sort(key=lambda x: str(x.get("start_at") or ""))
    return associations


def session_groups(ai_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ai_records:
        groups[(str(row.get("project") or ""), str(row.get("session_id") or ""))].append(row)
    sessions = []
    for (project, session_id), items in groups.items():
        items = sorted(items, key=lambda x: str(x.get("input_at") or ""))
        if not session_id or not items:
            continue
        sessions.append(
            {
                "project": project,
                "session_id": session_id,
                "start_at": items[0].get("input_at"),
                "end_at": items[-1].get("task_finished_at") or items[-1].get("input_at"),
                "turn_count": len(items),
                "input_summary": summarize_texts([str(x.get("input_preview") or "") for x in items]),
                "items": items,
            }
        )
    sessions.sort(key=lambda x: str(x.get("start_at") or ""))
    return sessions


def time_window_match(start: datetime | None, end: datetime | None, committed_at: datetime | None) -> tuple[bool, bool]:
    if not start or not end or not committed_at:
        return False, False
    hours_after_start = (committed_at - start).total_seconds() / 3600
    hours_after_end = (committed_at - end).total_seconds() / 3600
    near = -1 <= hours_after_start and hours_after_end <= 8
    same_day_window = -6 <= hours_after_start and hours_after_end <= 24
    return near, same_day_window


def score_ai_commit_candidate(items: list[dict[str, Any]], commit: dict[str, Any]) -> dict[str, Any]:
    session_text = " ".join(str(x.get("input_text") or x.get("input_preview") or "") for x in items)
    session_tokens = tokenize(session_text)
    first = parse_iso(str(items[0].get("input_at") or "")) if items else None
    last = parse_iso(str(items[-1].get("task_finished_at") or items[-1].get("input_at") or "")) if items else None
    committed_at = parse_iso(str(commit.get("committed_at") or ""))
    project_match = project_matches(items[0], commit) if items else False
    near_time, same_day_window = time_window_match(first, last, committed_at)
    message_overlap = session_tokens.intersection(tokenize(str(commit.get("message") or "")))
    path_text = " ".join(str(x.get("path") or "") for x in commit.get("file_summaries") or [])
    path_overlap = session_tokens.intersection(tokenize(path_text))
    score = 0
    if project_match:
        score += 40
    if near_time:
        score += 25
    elif same_day_window:
        score += 10
    if message_overlap:
        score += min(16, len(message_overlap) * 4)
    if path_overlap:
        score += min(20, len(path_overlap) * 5)
    return {
        "score": score,
        "project_match": project_match,
        "near_time": near_time,
        "same_day_window": same_day_window,
        "message_overlap": sorted(message_overlap),
        "path_overlap": sorted(path_overlap),
    }


def build_unmatched_ai_sessions(
    ai_records: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    associations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched_session_ids = {str(x.get("session_id") or "") for x in associations}
    rows = []
    for session in session_groups(ai_records):
        session_id = str(session.get("session_id") or "")
        if session_id in matched_session_ids:
            continue
        items = session.get("items") or []
        candidates = [score_ai_commit_candidate(items, commit) | {"commit": commit} for commit in commits]
        candidates.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
        rows.append(
            {
                "session_id": session_id,
                "project": session.get("project"),
                "start_at": session.get("start_at"),
                "end_at": session.get("end_at"),
                "turn_count": session.get("turn_count"),
                "input_summary": session.get("input_summary"),
                "reason": unmatched_session_reason(candidates, bool(commits)),
                "best_candidate": format_best_commit_candidate(candidates[0]) if candidates else None,
            }
        )
    return rows


def unmatched_session_reason(candidates: list[dict[str, Any]], has_commits: bool) -> str:
    if not has_commits:
        return "当天没有 Git 提交可关联。"
    if not candidates:
        return "没有可比较的 Git 提交。"
    best = candidates[0]
    if int(best.get("score") or 0) > 0:
        return f"最高规则分 {best.get('score')}，未达到 30 分关联阈值。"
    if any(x.get("project_match") for x in candidates):
        return "存在同项目提交，但提交时间窗口和关键词均未匹配。"
    if any(x.get("near_time") or x.get("same_day_window") for x in candidates):
        return "存在同日时间窗口提交，但项目路径或项目名不匹配。"
    return "项目、时间窗口和关键词均未匹配。"


def format_best_commit_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    commit = candidate.get("commit") or {}
    return {
        "short_hash": commit.get("short_hash"),
        "message": commit.get("message"),
        "score": candidate.get("score"),
        "project_match": candidate.get("project_match"),
        "near_time": candidate.get("near_time"),
        "same_day_window": candidate.get("same_day_window"),
        "message_overlap": (candidate.get("message_overlap") or [])[:5],
        "path_overlap": (candidate.get("path_overlap") or [])[:5],
    }


def build_commit_association_summary(
    ai_records: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    associations: list[dict[str, Any]],
) -> dict[str, Any]:
    associated_hashes = {
        str(match.get("commit_hash") or "")
        for assoc in associations
        for match in assoc.get("matched_commits") or []
        if match.get("commit_hash")
    }
    sessions = session_groups(ai_records)
    associated_commits = []
    unassociated_commits = []
    for commit in commits:
        row = {
            "hash": commit.get("hash"),
            "short_hash": commit.get("short_hash"),
            "project": commit.get("project"),
            "committed_at": commit.get("committed_at"),
            "message": commit.get("message"),
        }
        if str(commit.get("hash") or "") in associated_hashes:
            associated_commits.append(row)
        else:
            unassociated_commits.append(row | {"reason": unassociated_commit_reason(commit, sessions, bool(ai_records))})
    return {
        "total_commits": len(commits),
        "associated_commit_count": len(associated_commits),
        "unassociated_commit_count": len(unassociated_commits),
        "associated_commits": associated_commits,
        "unassociated_commits": unassociated_commits,
    }


def unassociated_commit_reason(commit: dict[str, Any], sessions: list[dict[str, Any]], has_ai_records: bool) -> str:
    if not has_ai_records:
        return "当天没有 AI 输入记录可关联。"
    candidates = []
    for session in sessions:
        items = session.get("items") or []
        candidates.append(score_ai_commit_candidate(items, commit))
    if not candidates:
        return "没有可比较的 AI 会话。"
    candidates.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
    best = candidates[0]
    if int(best.get("score") or 0) > 0:
        return f"最高规则分 {best.get('score')}，未达到 30 分关联阈值。"
    if any(x.get("project_match") for x in candidates):
        return "存在同项目 AI 会话，但时间窗口和关键词均未匹配。"
    if any(x.get("near_time") or x.get("same_day_window") for x in candidates):
        return "存在时间接近的 AI 会话，但项目路径或项目名不匹配。"
    return "项目、时间窗口和关键词均未匹配。"


def confidence_label(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def summarize_texts(texts: list[str], limit: int = 220) -> str:
    clean = "；".join(t.strip() for t in texts if t.strip())
    clean = " ".join(clean.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "..."


def detect_rework(ai_records: list[dict[str, Any]], commits: list[dict[str, Any]], file_changes: list[dict[str, Any]], associations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    by_file: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in file_changes:
        by_file[(str(row.get("project") or ""), str(row.get("path") or ""))].append(row)
    for (project, path_text), rows in by_file.items():
        if len({x.get("commit_hash") for x in rows}) >= 3:
            signals.append(
                {
                    "type": "same_file_repeated_changes",
                    "project": project,
                    "target": path_text,
                    "confidence": "medium",
                    "evidence": [
                        f"{len({x.get('commit_hash') for x in rows})} 个提交修改同一文件",
                        *[
                            f"{x.get('committed_at')} {x.get('short_hash')} +{x.get('insertions')} -{x.get('deletions')}"
                            for x in rows[:5]
                        ],
                    ],
                }
            )

    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ai_records:
        session_id = str(row.get("session_id") or "")
        if session_id:
            by_session[session_id].append(row)
    for session_id, rows in by_session.items():
        rows = sorted(rows, key=lambda x: str(x.get("input_at") or ""))
        similar_pairs = []
        for i in range(len(rows)):
            for j in range(i + 1, min(len(rows), i + 4)):
                left = tokenize(str(rows[i].get("input_text") or rows[i].get("input_preview") or ""))
                right = tokenize(str(rows[j].get("input_text") or rows[j].get("input_preview") or ""))
                similarity = token_similarity(left, right)
                if similarity >= 0.55 and len(left.intersection(right)) >= 3:
                    similar_pairs.append(
                        f"{rows[i].get('input_at')} 与 {rows[j].get('input_at')} 输入相似度 {similarity:.2f}"
                    )
        if similar_pairs:
            signals.append(
                {
                    "type": "similar_inputs_same_session",
                    "project": rows[0].get("project"),
                    "target": session_id,
                    "confidence": "medium",
                    "evidence": [
                        f"同一 AI 会话内出现 {len(similar_pairs)} 组相似输入，可能是在反复澄清或修正同一问题。",
                        *similar_pairs[:5],
                    ],
                }
            )

    for row in ai_records:
        text = str(row.get("input_text") or row.get("input_preview") or "").lower()
        hits = [word for word in REWORK_WORDS if word.lower() in text]
        if hits:
            signals.append(
                {
                    "type": "ai_rework_words",
                    "project": row.get("project"),
                    "target": row.get("session_id"),
                    "confidence": "low",
                    "evidence": [
                        f"{row.get('input_at')} 输入包含返工词: {', '.join(hits)}",
                        str(row.get("input_preview") or "")[:180],
                    ],
                }
            )

    for commit in commits:
        message = str(commit.get("message") or "").lower()
        hits = [word for word in ("fix", "bugfix", "修复", "revert", "rollback", "回滚") if word in message]
        if hits:
            related_files = commit.get("file_summaries") or []
            signals.append(
                {
                    "type": "fix_or_revert_commit",
                    "project": commit.get("project"),
                    "target": commit.get("short_hash"),
                    "confidence": "medium" if any(x in hits for x in ("revert", "rollback", "回滚")) else "low",
                    "evidence": [
                        f"{commit.get('committed_at')} {commit.get('short_hash')} {commit.get('message')}",
                        f"涉及文件 {len(related_files)} 个，新增/删除 +{commit.get('insertions', 0)} / -{commit.get('deletions', 0)}",
                        *[
                            f"{x.get('path')} +{x.get('insertions')} -{x.get('deletions')}"
                            for x in related_files[:5]
                        ],
                    ],
                }
            )

    module_sessions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for assoc in associations:
        project = str(assoc.get("project") or "")
        session_id = str(assoc.get("session_id") or "")
        for match in assoc.get("matched_commits") or []:
            commit_hash = match.get("commit_hash")
            for commit in commits:
                if commit.get("hash") == commit_hash:
                    for module in commit.get("modules") or []:
                        module_sessions[(project, module)].add(session_id)
    for (project, module), session_ids in module_sessions.items():
        if len(session_ids) >= 2:
            signals.append(
                {
                    "type": "multiple_sessions_same_module",
                    "project": project,
                    "target": module,
                    "confidence": "medium",
                    "evidence": [f"{len(session_ids)} 个 AI 会话关联到同一模块: {module}"],
                }
            )
    module_files: dict[tuple[str, str], set[str]] = defaultdict(set)
    module_commits: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in file_changes:
        key = (str(row.get("project") or ""), str(row.get("module") or "unknown"))
        module_files[key].add(str(row.get("path") or ""))
        module_commits[key].add(str(row.get("commit_hash") or ""))
    for (project, module), paths in module_files.items():
        if len(paths) >= 5 or len(module_commits[(project, module)]) >= 3:
            signals.append(
                {
                    "type": "module_repeated_changes",
                    "project": project,
                    "target": module,
                    "confidence": "medium",
                    "evidence": [
                        f"模块 {module} 当天涉及 {len(paths)} 个文件、{len(module_commits[(project, module)])} 个提交。",
                        *sorted(paths)[:8],
                    ],
                }
            )
    return signals


def token_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


def detect_topics(ai_records: list[dict[str, Any]], commits: list[dict[str, Any]], file_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    corpus_by_topic: dict[str, list[str]] = defaultdict(list)
    texts = []
    texts.extend(str(x.get("input_text") or x.get("input_preview") or "") for x in ai_records)
    texts.extend(str(x.get("message") or "") for x in commits)
    texts.extend(str(x.get("path") or "") for x in file_changes)
    lower_texts = [x.lower() for x in texts if x]
    for topic, needles in TOPIC_RULES.items():
        evidence = []
        for text in lower_texts:
            if any(needle.lower() in text for needle in needles):
                evidence.append(text[:160])
        if evidence:
            corpus_by_topic[topic] = evidence[:8]
    rows = []
    for topic, evidence in sorted(corpus_by_topic.items()):
        rows.append(
            {
                "topic": topic,
                "related_task_count": len(evidence),
                "evidence": evidence,
                "appeared_today": True,
                "worth_learning": len(evidence) >= 3,
            }
        )
    return rows


def build_daily_report(
    day: str,
    person: str,
    ai_records: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    file_changes: list[dict[str, Any]],
    reflection: dict[str, Any],
    git_errors: list[str],
) -> dict[str, Any]:
    associations = associate_ai_git(ai_records, commits)
    unmatched_ai_sessions = build_unmatched_ai_sessions(ai_records, commits, associations)
    commit_association_summary = build_commit_association_summary(ai_records, commits, associations)
    rework = detect_rework(ai_records, commits, file_changes, associations)
    topics = detect_topics(ai_records, commits, file_changes)
    business_commits = [c for c in commits if not c.get("is_merge")]
    project_distribution = build_project_distribution(ai_records, commits, file_changes)
    ai_seconds = sum(float(x.get("ai_active_seconds") or 0) for x in ai_records)
    warnings = build_report_warnings(ai_records, commits, associations, git_errors)
    report = {
        "schema_version": "2.0",
        "date": day,
        "person": person,
        "overview": {
            "ai_turn_count": len(ai_records),
            "ai_session_count": len({x.get("session_id") for x in ai_records if x.get("session_id")}),
            "commit_count": len(commits),
            "business_commit_count": len(business_commits),
            "merge_commit_count": len(commits) - len(business_commits),
            "files_changed": len({(x.get("project"), x.get("path")) for x in file_changes}),
            "insertions": sum(int(x.get("insertions") or 0) for x in file_changes),
            "deletions": sum(int(x.get("deletions") or 0) for x in file_changes),
            "ai_active_seconds": ai_seconds,
            "project_switch_count": estimate_project_switch_count(ai_records),
        },
        "project_distribution": project_distribution,
        "ai_usage": build_ai_usage(ai_records),
        "git_workload": build_git_workload(commits, file_changes),
        "associations": associations,
        "unmatched_ai_sessions": unmatched_ai_sessions,
        "commit_association_summary": commit_association_summary,
        "manual_reflection": reflection,
        "main_completed_items": infer_completed_items(reflection, commits, associations),
        "work_focus": infer_work_focus(reflection, ai_records, commits),
        "rework_and_exceptions": rework,
        "technical_topics": topics,
        "quality_metrics": build_quality_metrics(ai_records, commits, associations, rework),
        "today_outcome": reflection.get("actual_result") or infer_outcome(commits, associations),
        "tomorrow_suggestions": build_tomorrow_suggestions(reflection, rework, topics),
        "warnings": warnings,
    }
    return report


def build_report_warnings(
    ai_records: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    associations: list[dict[str, Any]],
    git_errors: list[str],
) -> list[str]:
    warnings = list(git_errors)
    if not ai_records:
        warnings.append(
            "未扫描到当天 AI 输入记录。可能原因：当天确实未使用已支持工具、日期或时区不匹配、Codex/Claude 记录路径未覆盖。"
        )
    if not commits:
        warnings.append(
            "未采集到当天 Git 提交。可能原因：当天未提交、本地项目 path 配置错误、path 不是 Git 仓库，或提交时间不在所选日期内。"
        )
    if ai_records and commits and not associations:
        warnings.append(
            "AI-Git 关联为空。这只表示当前本地规则未匹配到会话和提交，不代表 AI 没有参与研发工作。"
        )
    return warnings


def build_project_distribution(ai_records: list[dict[str, Any]], commits: list[dict[str, Any]], file_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects = sorted(
        {
            *(str(x.get("project") or "") for x in ai_records),
            *(str(x.get("project") or "") for x in commits),
        }
        - {""}
    )
    rows = []
    for project in projects:
        project_ai = [x for x in ai_records if x.get("project") == project]
        project_commits = [x for x in commits if x.get("project") == project]
        project_files = [x for x in file_changes if x.get("project") == project]
        rows.append(
            {
                "project": project,
                "ai_turn_count": len(project_ai),
                "ai_session_count": len({x.get("session_id") for x in project_ai if x.get("session_id")}),
                "commit_count": len(project_commits),
                "business_commit_count": len([x for x in project_commits if not x.get("is_merge")]),
                "files_changed": len({x.get("path") for x in project_files}),
                "insertions": sum(int(x.get("insertions") or 0) for x in project_files),
                "deletions": sum(int(x.get("deletions") or 0) for x in project_files),
            }
        )
    return rows


def build_ai_usage(ai_records: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool = Counter(str(x.get("tool") or "unknown") for x in ai_records)
    return {
        "by_tool": dict(by_tool),
        "turns": [
            {
                "tool": x.get("tool"),
                "project": x.get("project"),
                "session_id": x.get("session_id"),
                "input_at": x.get("input_at"),
                "ai_active_seconds": x.get("ai_active_seconds"),
                "total_tokens": x.get("total_tokens"),
                "input_preview": x.get("input_preview"),
            }
            for x in ai_records
        ],
    }


def build_git_workload(commits: list[dict[str, Any]], file_changes: list[dict[str, Any]]) -> dict[str, Any]:
    by_category = Counter(str(x.get("category") or "other") for x in file_changes)
    by_module = Counter(str(x.get("module") or "unknown") for x in file_changes)
    return {
        "commits": commits,
        "file_changes": file_changes,
        "by_category": dict(by_category),
        "top_modules": dict(by_module.most_common(20)),
    }


def estimate_project_switch_count(ai_records: list[dict[str, Any]]) -> int:
    ordered = sorted(ai_records, key=lambda x: str(x.get("input_at") or ""))
    count = 0
    previous = None
    for row in ordered:
        project = row.get("project")
        if previous is not None and project != previous:
            count += 1
        previous = project
    return count


def infer_completed_items(reflection: dict[str, Any], commits: list[dict[str, Any]], associations: list[dict[str, Any]]) -> list[str]:
    items = []
    actual = str(reflection.get("actual_result") or "").strip()
    if actual:
        items.append(actual)
    for commit in commits:
        if not commit.get("is_merge"):
            items.append(f"{commit.get('project')}: {commit.get('message')}")
    for assoc in associations[:5]:
        if assoc.get("best_confidence") in {"high", "medium"}:
            items.append(f"AI 会话关联产出: {assoc.get('input_summary')}")
    return unique_texts(items)[:20]


def infer_work_focus(reflection: dict[str, Any], ai_records: list[dict[str, Any]], commits: list[dict[str, Any]]) -> list[str]:
    focus = []
    goal = str(reflection.get("most_important_goal") or "").strip()
    if goal:
        focus.append(goal)
    focus.extend(str(x.get("message") or "") for x in commits[:8] if x.get("message"))
    focus.extend(str(x.get("input_preview") or "") for x in ai_records[:8] if x.get("input_preview"))
    return unique_texts(focus)[:10]


def infer_outcome(commits: list[dict[str, Any]], associations: list[dict[str, Any]]) -> str:
    if commits:
        return f"完成 {len([x for x in commits if not x.get('is_merge')])} 个业务提交，涉及 {len(associations)} 个 AI 会话关联。"
    if associations:
        return f"形成 {len(associations)} 个 AI 会话与代码工作的关联记录。"
    return "暂无可从 Git 或 AI 记录确认的成果，请补充人工复盘。"


def build_quality_metrics(ai_records: list[dict[str, Any]], commits: list[dict[str, Any]], associations: list[dict[str, Any]], rework: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = defaultdict(list)
    for row in ai_records:
        sessions[str(row.get("session_id") or "")].append(row)
    high_value = [a for a in associations if a.get("best_confidence") in {"high", "medium"}]
    low_output = []
    commit_hashes = {match.get("commit_hash") for assoc in associations for match in assoc.get("matched_commits") or []}
    if ai_records and not commit_hashes:
        low_output.append("当天存在 AI 输入记录，但未关联到 Git 提交。")
    return {
        "note": "以下指标为本地规则统计或估算，只用于个人复盘参考，不代表绝对工时、产出价值或绩效结论。",
        "ai_collaboration_seconds_estimate": sum(float(x.get("ai_active_seconds") or 0) for x in ai_records),
        "manual_review_seconds_estimate": estimate_manual_review_seconds(ai_records),
        "effective_work_seconds_estimate": estimate_effective_work_seconds(ai_records),
        "rework_ratio_estimate": round(min(1.0, len(rework) / max(1, len(sessions) + len(commits))), 3),
        "project_switch_count": estimate_project_switch_count(ai_records),
        "session_repeat_rate_estimate": estimate_session_repeat_rate(associations),
        "avg_turns_per_task": round(sum(len(v) for v in sessions.values()) / max(1, len(sessions)), 2),
        "high_consumption_low_output_tasks": low_output,
        "high_value_tasks": [x.get("input_summary") for x in high_value[:10]],
    }


def estimate_manual_review_seconds(ai_records: list[dict[str, Any]]) -> float:
    total = 0.0
    for row in ai_records:
        gap = row.get("after_done_gap_seconds")
        if gap is not None:
            total += min(float(gap or 0), 1800.0)
    return total


def estimate_effective_work_seconds(ai_records: list[dict[str, Any]]) -> float:
    return sum(float(x.get("ai_active_seconds") or 0) for x in ai_records) + estimate_manual_review_seconds(ai_records)


def estimate_session_repeat_rate(associations: list[dict[str, Any]]) -> float:
    commit_counts = Counter()
    for assoc in associations:
        for match in assoc.get("matched_commits") or []:
            commit_counts[str(match.get("commit_hash") or "")] += 1
    repeated = sum(1 for _, count in commit_counts.items() if count > 1)
    return round(repeated / max(1, len(commit_counts)), 3)


def build_tomorrow_suggestions(reflection: dict[str, Any], rework: list[dict[str, Any]], topics: list[dict[str, Any]]) -> list[str]:
    suggestions = []
    if not reflection.get("accepted"):
        suggestions.append("优先补齐今天未完成验收的事项，避免明日继续扩大返工面。")
    if rework:
        suggestions.append("针对返工信号最多的文件或模块先做一次小范围复盘，确认问题根因和验收口径。")
    for topic in topics:
        if topic.get("worth_learning"):
            suggestions.append(f"技术主题 {topic.get('topic')} 多次出现，可整理一个专项笔记或检查清单。")
            break
    if not suggestions:
        suggestions.append("明日可延续当前主线，优先把已完成工作做验收和归档。")
    return suggestions


def unique_texts(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        clean = " ".join(str(item).split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def reports_root(data_dir: Path) -> Path:
    return data_dir / "reports"


def daily_report_path(data_dir: Path, day: str) -> Path:
    return reports_root(data_dir) / day / "daily-report.json"


def read_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"文件不存在: {path}"
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return None, f"JSON 损坏: {path}: {exc}"
    except OSError as exc:
        return None, f"读取失败: {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"日报格式错误: {path}"
    return data, None


def list_daily_report_dates(data_dir: Path) -> tuple[list[str], list[str]]:
    root = reports_root(data_dir)
    if not root.exists():
        return [], [f"报告目录不存在: {root}"]
    dates = []
    warnings = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        report_path = child / "daily-report.json"
        if report_path.exists():
            dates.append(child.name)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name):
            warnings.append(f"日报目录缺少 daily-report.json: {child}")
    return dates, warnings


def load_daily_report(data_dir: Path, day: str) -> tuple[dict[str, Any] | None, list[str]]:
    report, warning = read_json_file(daily_report_path(data_dir, day))
    return report, [warning] if warning else []


def iter_days(start_day: date, end_day: date) -> list[str]:
    days = []
    current = start_day
    while current <= end_day:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def load_daily_reports_range(data_dir: Path, start_day: str, end_day: str) -> tuple[list[dict[str, Any]], list[str]]:
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    reports = []
    warnings = []
    for day in iter_days(start, end):
        report, report_warnings = load_daily_report(data_dir, day)
        if report is None:
            warnings.extend(report_warnings)
            continue
        reports.append(report)
    return reports, warnings


def aggregate_topic_trends(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_topic: dict[str, dict[str, Any]] = {}
    for report in reports:
        day = str(report.get("date") or "")
        projects = {str(x.get("project") or "") for x in report.get("project_distribution") or [] if x.get("project")}
        for row in report.get("technical_topics") or []:
            topic = str(row.get("topic") or "").strip()
            if not topic:
                continue
            target = by_topic.setdefault(
                topic,
                {
                    "topic": topic,
                    "appeared_days": [],
                    "related_task_count": 0,
                    "projects": set(),
                    "evidence": [],
                    "worth_learning_days": 0,
                },
            )
            if day and day not in target["appeared_days"]:
                target["appeared_days"].append(day)
            target["related_task_count"] += int(row.get("related_task_count") or 0)
            target["projects"].update(projects)
            target["evidence"].extend(str(x) for x in (row.get("evidence") or [])[:3])
            if row.get("worth_learning"):
                target["worth_learning_days"] += 1
    result = []
    for row in by_topic.values():
        appeared_days = sorted(row["appeared_days"])
        result.append(
            {
                "topic": row["topic"],
                "appeared_day_count": len(appeared_days),
                "appeared_days": appeared_days,
                "related_task_count": row["related_task_count"],
                "projects": sorted(row["projects"]),
                "repeated": len(appeared_days) >= 2,
                "worth_learning": row["worth_learning_days"] > 0 or len(appeared_days) >= 2 or row["related_task_count"] >= 5,
                "evidence": unique_texts(row["evidence"])[:10],
            }
        )
    result.sort(key=lambda x: (int(x.get("appeared_day_count") or 0), int(x.get("related_task_count") or 0)), reverse=True)
    return result


def aggregate_project_distribution(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for report in reports:
        for item in report.get("project_distribution") or []:
            project = str(item.get("project") or "unknown")
            target = rows.setdefault(
                project,
                {
                    "project": project,
                    "ai_turn_count": 0,
                    "ai_session_count": 0,
                    "commit_count": 0,
                    "business_commit_count": 0,
                    "files_changed": 0,
                    "insertions": 0,
                    "deletions": 0,
                    "days": set(),
                },
            )
            target["days"].add(str(report.get("date") or ""))
            for field_name in ("ai_turn_count", "ai_session_count", "commit_count", "business_commit_count", "files_changed", "insertions", "deletions"):
                target[field_name] += int(item.get(field_name) or 0)
    result = []
    for row in rows.values():
        result.append({k: (len(v) if k == "days" else v) for k, v in row.items()})
    result.sort(key=lambda x: (int(x.get("commit_count") or 0), int(x.get("ai_turn_count") or 0)), reverse=True)
    return result


def aggregate_overview(reports: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "ai_turn_count",
        "ai_session_count",
        "commit_count",
        "business_commit_count",
        "merge_commit_count",
        "files_changed",
        "insertions",
        "deletions",
        "ai_active_seconds",
        "project_switch_count",
    )
    result = {field_name: 0 for field_name in fields}
    for report in reports:
        overview = report.get("overview") or {}
        for field_name in fields:
            result[field_name] += float(overview.get(field_name) or 0) if field_name.endswith("_seconds") else int(overview.get(field_name) or 0)
    result["report_day_count"] = len(reports)
    return result


def build_period_report(
    period_type: str,
    label: str,
    person: str,
    reports: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    reports = sorted(reports, key=lambda x: str(x.get("date") or ""))
    rework = []
    for report in reports:
        for row in report.get("rework_and_exceptions") or []:
            item = dict(row)
            item["date"] = report.get("date")
            rework.append(item)
    topic_trends = aggregate_topic_trends(reports)
    return {
        "schema_version": "2.1",
        "report_type": period_type,
        "label": label,
        "person": person,
        "date_range": {
            "from": reports[0].get("date") if reports else None,
            "to": reports[-1].get("date") if reports else None,
        },
        "overview": aggregate_overview(reports),
        "daily_summaries": [
            {
                "date": report.get("date"),
                "today_outcome": report.get("today_outcome"),
                "overview": report.get("overview") or {},
                "warnings": report.get("warnings") or [],
            }
            for report in reports
        ],
        "project_distribution": aggregate_project_distribution(reports),
        "topic_trends": topic_trends,
        "rework_trends": rework,
        "suggestions": build_period_suggestions(rework, topic_trends, warnings),
        "warnings": warnings,
    }


def build_period_suggestions(rework: list[dict[str, Any]], topic_trends: list[dict[str, Any]], warnings: list[str]) -> list[str]:
    suggestions = []
    if warnings:
        suggestions.append("先补齐缺失或损坏的日报，再做趋势结论。")
    if rework:
        suggestions.append("优先复盘出现返工信号最多的模块或文件，确认验收口径和修改边界。")
    repeated_topics = [x for x in topic_trends if x.get("worth_learning")]
    if repeated_topics:
        suggestions.append(f"技术主题 {repeated_topics[0].get('topic')} 重复出现，可整理专项笔记或检查清单。")
    if not suggestions:
        suggestions.append("本周期数据较平稳，可延续当前工作节奏并补充人工复盘。")
    return suggestions


def iso_week_bounds(week_text: str) -> tuple[date, date]:
    year_text, week_num_text = week_text.split("-W", 1)
    start = date.fromisocalendar(int(year_text), int(week_num_text), 1)
    return start, start + timedelta(days=6)


def month_bounds(month_text: str) -> tuple[date, date]:
    year, month = [int(x) for x in month_text.split("-", 1)]
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def render_period_markdown(report: dict[str, Any]) -> str:
    overview = report.get("overview") or {}
    title = "周报" if report.get("report_type") == "week" else "月报" if report.get("report_type") == "month" else "趋势报告"
    lines = [f"# 研发{title} - {report.get('label')}", ""]
    lines.extend(
        [
            f"- 报告天数：{overview.get('report_day_count', 0)}",
            f"- AI 输入轮数：{overview.get('ai_turn_count', 0)}",
            f"- Git 提交数：{overview.get('commit_count', 0)}",
            f"- 业务提交数：{overview.get('business_commit_count', 0)}",
            f"- 修改文件数：{overview.get('files_changed', 0)}",
            f"- 新增/删除：+{int(overview.get('insertions') or 0)} / -{int(overview.get('deletions') or 0)}",
            f"- AI 协作时长（统计/估算）：{fmt_seconds(overview.get('ai_active_seconds') or 0)}",
            "",
        ]
    )
    lines.extend(markdown_table("## 项目分布", report.get("project_distribution") or [], ["project", "days", "ai_turn_count", "commit_count", "business_commit_count", "files_changed", "insertions", "deletions"]))
    lines.extend(markdown_table("## 技术主题趋势", report.get("topic_trends") or [], ["topic", "appeared_day_count", "related_task_count", "repeated", "worth_learning"]))
    lines.extend(markdown_rework(report.get("rework_trends") or []))
    lines.extend(markdown_list("## 建议", report.get("suggestions") or []))
    if report.get("warnings"):
        lines.extend(markdown_list("## 数据提示", report.get("warnings") or []))
    return "\n".join(lines).rstrip() + "\n"


def write_period_outputs(out_dir: Path, report: dict[str, Any], prefix: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{prefix}-report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")
    (out_dir / f"{prefix}-report.md").write_text(render_period_markdown(report), encoding="utf-8")


def write_daily_outputs(out_dir: Path, report: dict[str, Any], ai_records: list[dict[str, Any]], commits: list[dict[str, Any]], file_changes: list[dict[str, Any]], associations: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ai-inputs.jsonl").write_text(
        "\n".join(compact_json_dumps(x) for x in ai_records) + ("\n" if ai_records else ""),
        encoding="utf-8",
    )
    (out_dir / "git-commits.jsonl").write_text(
        "\n".join(compact_json_dumps(x) for x in commits) + ("\n" if commits else ""),
        encoding="utf-8",
    )
    (out_dir / "git-file-changes.jsonl").write_text(
        "\n".join(compact_json_dumps(x) for x in file_changes) + ("\n" if file_changes else ""),
        encoding="utf-8",
    )
    (out_dir / "associations.jsonl").write_text(
        "\n".join(compact_json_dumps(x) for x in associations) + ("\n" if associations else ""),
        encoding="utf-8",
    )
    (out_dir / "daily-report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")
    (out_dir / "daily-report.md").write_text(render_daily_markdown(report), encoding="utf-8")


def render_daily_markdown(report: dict[str, Any]) -> str:
    overview = report.get("overview") or {}
    lines = [f"# 研发日报 - {report.get('date')}", ""]
    lines.extend(
        [
            "> 口径说明：本报告混合使用事实采集字段和规则估算字段。AI 协作时长、人工审查时长、有效工作时间、返工比例和质量指标均只用于个人复盘，不是绝对工时或绩效结论。",
            "",
        ]
    )
    lines.extend(
        [
            "## 1. 今日概览",
            "",
            f"- AI 输入轮数：{overview.get('ai_turn_count', 0)}",
            f"- AI 会话数：{overview.get('ai_session_count', 0)}",
            f"- Git 提交数：{overview.get('commit_count', 0)}",
            f"- 排除 merge 后业务提交数：{overview.get('business_commit_count', 0)}",
            f"- 修改文件数：{overview.get('files_changed', 0)}",
            f"- 新增/删除代码行：+{overview.get('insertions', 0)} / -{overview.get('deletions', 0)}",
            f"- 项目切换次数估算：{overview.get('project_switch_count', 0)}",
            "",
        ]
    )
    lines.extend(markdown_data_notices(report))
    lines.extend(markdown_table("## 2. 项目分布", report.get("project_distribution") or [], ["project", "ai_turn_count", "commit_count", "business_commit_count", "files_changed", "insertions", "deletions"]))
    ai_usage = report.get("ai_usage") or {}
    lines.extend(["## 3. AI 使用情况", "", f"- 按工具：{ai_usage.get('by_tool') or {}}"])
    if not (ai_usage.get("turns") or []):
        lines.append("- 未扫描到 AI 输入记录。请确认日期、时区、工具记录路径和项目根目录扫描范围。")
    lines.append("")
    git_workload = report.get("git_workload") or {}
    lines.extend(["## 4. Git 工作量", "", f"- 文件分类：{git_workload.get('by_category') or {}}", f"- 主要模块：{git_workload.get('top_modules') or {}}"])
    if not (git_workload.get("commits") or []):
        lines.append("- 未采集到 Git 提交。请确认当天是否已提交、项目 path 是否指向本地 Git 仓库。")
    lines.append("")
    lines.extend(markdown_list("## 5. 主要完成事项", report.get("main_completed_items") or []))
    lines.extend(markdown_list("## 6. 工作重点", report.get("work_focus") or []))
    lines.extend(
        markdown_associations(
            report.get("associations") or [],
            report.get("unmatched_ai_sessions") or [],
            report.get("commit_association_summary") or {},
        )
    )
    lines.extend(markdown_rework(report.get("rework_and_exceptions") or []))
    lines.extend(markdown_topics(report.get("technical_topics") or []))
    lines.extend(markdown_quality_metrics(report.get("quality_metrics") or {}))
    lines.extend(["## 11. 今日成果", "", str(report.get("today_outcome") or "-"), ""])
    lines.extend(markdown_list("## 12. 明日建议", report.get("tomorrow_suggestions") or []))
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(markdown_list("## 采集提示", warnings))
    return "\n".join(lines).rstrip() + "\n"


def markdown_data_notices(report: dict[str, Any]) -> list[str]:
    overview = report.get("overview") or {}
    notices = []
    if int(overview.get("ai_turn_count") or 0) == 0:
        notices.append("AI 输入为 0：可能是未扫描到记录、日期不对、时区不匹配，或工具记录路径未覆盖。")
    if int(overview.get("commit_count") or 0) == 0:
        notices.append("Git 提交为 0：可能是当天未提交、项目 path 配置错误，或本地仓库无当天提交。")
    if int(overview.get("ai_turn_count") or 0) > 0 and int(overview.get("commit_count") or 0) > 0 and not (report.get("associations") or []):
        notices.append("AI-Git 关联为空：只表示规则未匹配，不代表 AI 没有参与；具体原因见 AI-Git 关联章节。")
    if not notices:
        return []
    return ["## 数据提示", "", *[f"- {item}" for item in notices], ""]


def markdown_table(title: str, rows: list[dict[str, Any]], cols: list[str]) -> list[str]:
    lines = [title, ""]
    if not rows:
        return lines + ["无数据", ""]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")).replace("|", "\\|") for col in cols) + " |")
    lines.append("")
    return lines


def markdown_list(title: str, rows: list[Any]) -> list[str]:
    lines = [title, ""]
    if not rows:
        return lines + ["- 无数据", ""]
    lines.extend(f"- {str(row)}" for row in rows)
    lines.append("")
    return lines


def markdown_associations(rows: list[dict[str, Any]], unmatched_sessions: list[dict[str, Any]] | None = None, commit_summary: dict[str, Any] | None = None) -> list[str]:
    lines = ["## 7. AI-Git 关联", ""]
    unmatched_sessions = unmatched_sessions or []
    commit_summary = commit_summary or {}
    if not rows:
        lines.append("- 未形成 AI 会话与 Git 提交的规则关联。该结果只表示当前规则未匹配，不代表 AI 没有参与。")
    else:
        lines.append("### 已关联会话")
        lines.append("")
        for row in rows[:20]:
            lines.append(
                f"- {row.get('project')} / {row.get('session_id')} / 置信度：{row.get('best_confidence')} / 分数：{row.get('best_score')}"
            )
            summary = str(row.get("input_summary") or "").strip()
            if summary:
                lines.append(f"  - 输入摘要：{summary}")
            for match in (row.get("matched_commits") or [])[:3]:
                lines.append(f"  - 关联提交：{match.get('short_hash')} {match.get('message')} / {match.get('confidence')}")
    if unmatched_sessions:
        lines.extend(["", "### 未关联 AI 会话", ""])
        for row in unmatched_sessions[:20]:
            lines.append(f"- {row.get('project')} / {row.get('session_id')}：{row.get('reason')}")
            best = row.get("best_candidate") or {}
            if best:
                lines.append(f"  - 最接近提交：{best.get('short_hash')} {best.get('message')} / 分数：{best.get('score')}")
    if commit_summary:
        lines.extend(["", "### Commit 关联概览", ""])
        lines.append(
            f"- 总提交：{commit_summary.get('total_commits', 0)}，已关联：{commit_summary.get('associated_commit_count', 0)}，未关联：{commit_summary.get('unassociated_commit_count', 0)}"
        )
        unassociated = commit_summary.get("unassociated_commits") or []
        for row in unassociated[:20]:
            lines.append(f"- 未关联提交 {row.get('short_hash')} {row.get('message')}：{row.get('reason')}")
    lines.append("")
    return lines


def markdown_rework(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["## 8. 返工和异常", ""]
    if not rows:
        return lines + ["- 暂未通过规则识别到明确返工信号，不代表绝对没有返工。", ""]
    for row in rows[:20]:
        lines.append(f"- {row.get('type')} / {row.get('project')} / {row.get('target')} / 置信度：{row.get('confidence')}")
        for evidence in row.get("evidence") or []:
            lines.append(f"  - 依据：{evidence}")
    lines.append("")
    return lines


def markdown_topics(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["## 9. 技术主题", ""]
    if not rows:
        return lines + ["- 无数据", ""]
    for row in rows:
        learn = "，建议专项学习" if row.get("worth_learning") else ""
        lines.append(f"- {row.get('topic')}：相关信号 {row.get('related_task_count')} 条{learn}")
    lines.append("")
    return lines


def markdown_quality_metrics(metrics: dict[str, Any]) -> list[str]:
    lines = ["## 10. 工作质量指标", ""]
    if not metrics:
        return lines + ["- 无数据", ""]
    note = str(metrics.get("note") or "以下指标为规则估算，只用于个人复盘参考。")
    lines.extend(
        [
            f"- 说明：{note}",
            f"- AI 协作时长（统计/估算）：{fmt_seconds(metrics.get('ai_collaboration_seconds_estimate') or 0)}",
            f"- 人工审查时长（估算）：{fmt_seconds(metrics.get('manual_review_seconds_estimate') or 0)}",
            f"- 有效工作时间（估算）：{fmt_seconds(metrics.get('effective_work_seconds_estimate') or 0)}",
            f"- 返工比例（规则估算）：{metrics.get('rework_ratio_estimate', 0)}",
            f"- 项目切换次数（估算）：{metrics.get('project_switch_count', 0)}",
            f"- 会话重复率（规则估算）：{metrics.get('session_repeat_rate_estimate', 0)}",
            f"- 平均每任务轮次（统计/估算）：{metrics.get('avg_turns_per_task', 0)}",
            "",
        ]
    )
    low_output = metrics.get("high_consumption_low_output_tasks") or []
    if low_output:
        lines.append("- 高消耗低产出提示：")
        lines.extend(f"  - {item}" for item in low_output)
        lines.append("")
    return lines
