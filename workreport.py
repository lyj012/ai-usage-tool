#!/usr/bin/env python3
"""Local v2 work report helpers for AI usage, Git activity, and reflection."""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
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
    if "test" in path or "spec" in path:
        return "test"
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
            commit_text = str(commit.get("message") or "") + " " + " ".join(
                str(x.get("path") or "") for x in commit.get("file_summaries") or []
            )
            overlap = session_tokens.intersection(tokenize(commit_text))
            if overlap:
                score += min(20, len(overlap) * 4)
                evidence.append("输入内容与提交信息/文件路径存在关键词重合: " + ", ".join(sorted(overlap)[:5]))
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
            signals.append(
                {
                    "type": "fix_or_revert_commit",
                    "project": commit.get("project"),
                    "target": commit.get("short_hash"),
                    "confidence": "low" if "fix" in hits or "修复" in hits else "medium",
                    "evidence": [f"{commit.get('committed_at')} {commit.get('short_hash')} {commit.get('message')}"],
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
    return signals


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
    rework = detect_rework(ai_records, commits, file_changes, associations)
    topics = detect_topics(ai_records, commits, file_changes)
    business_commits = [c for c in commits if not c.get("is_merge")]
    project_distribution = build_project_distribution(ai_records, commits, file_changes)
    ai_seconds = sum(float(x.get("ai_active_seconds") or 0) for x in ai_records)
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
        "manual_reflection": reflection,
        "main_completed_items": infer_completed_items(reflection, commits, associations),
        "work_focus": infer_work_focus(reflection, ai_records, commits),
        "rework_and_exceptions": rework,
        "technical_topics": topics,
        "quality_metrics": build_quality_metrics(ai_records, commits, associations, rework),
        "today_outcome": reflection.get("actual_result") or infer_outcome(commits, associations),
        "tomorrow_suggestions": build_tomorrow_suggestions(reflection, rework, topics),
        "warnings": git_errors,
    }
    return report


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
        "note": "以下指标为规则估算，不代表绝对准确工时或绩效结论。",
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
    lines.extend(markdown_table("## 2. 项目分布", report.get("project_distribution") or [], ["project", "ai_turn_count", "commit_count", "business_commit_count", "files_changed", "insertions", "deletions"]))
    ai_usage = report.get("ai_usage") or {}
    lines.extend(["## 3. AI 使用情况", "", f"- 按工具：{ai_usage.get('by_tool') or {}}", ""])
    git_workload = report.get("git_workload") or {}
    lines.extend(["## 4. Git 工作量", "", f"- 文件分类：{git_workload.get('by_category') or {}}", f"- 主要模块：{git_workload.get('top_modules') or {}}", ""])
    lines.extend(markdown_list("## 5. 主要完成事项", report.get("main_completed_items") or []))
    lines.extend(markdown_list("## 6. 工作重点", report.get("work_focus") or []))
    lines.extend(markdown_rework(report.get("rework_and_exceptions") or []))
    lines.extend(markdown_topics(report.get("technical_topics") or []))
    lines.extend(["## 9. 今日成果", "", str(report.get("today_outcome") or "-"), ""])
    lines.extend(markdown_list("## 10. 明日建议", report.get("tomorrow_suggestions") or []))
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(markdown_list("## 采集警告", warnings))
    return "\n".join(lines).rstrip() + "\n"


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


def markdown_rework(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["## 7. 返工和异常", ""]
    if not rows:
        return lines + ["- 暂未识别到明确返工信号。", ""]
    for row in rows[:20]:
        lines.append(f"- {row.get('type')} / {row.get('project')} / {row.get('target')} / 置信度：{row.get('confidence')}")
        for evidence in row.get("evidence") or []:
            lines.append(f"  - 依据：{evidence}")
    lines.append("")
    return lines


def markdown_topics(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["## 8. 技术主题", ""]
    if not rows:
        return lines + ["- 无数据", ""]
    for row in rows:
        learn = "，建议专项学习" if row.get("worth_learning") else ""
        lines.append(f"- {row.get('topic')}：相关信号 {row.get('related_task_count')} 条{learn}")
    lines.append("")
    return lines
