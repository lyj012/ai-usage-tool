#!/usr/bin/env python3
"""Streamlit dashboard for local workday reports."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from workreport import (
    aggregate_project_distribution,
    aggregate_topic_trends,
    build_period_report,
    list_daily_report_dates,
    load_config,
    load_daily_reports_range,
    load_reflection,
    render_daily_markdown,
    render_period_markdown,
    save_reflection,
)


st.set_page_config(page_title="AI Usage Dashboard", layout="wide")


def resolve_data_dir(config_path: Path, config: dict[str, Any]) -> Path:
    data_dir = Path(str(config.get("data_dir") or "data")).expanduser()
    if data_dir.is_absolute():
        return data_dir
    return config_path.parent / data_dir


def resolve_config_path(config_text: str) -> Path:
    path = Path(config_text.strip() or "aiusage-config.json").expanduser()
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parent / path


def project_config_warnings(projects: list[Any]) -> list[str]:
    warnings = []
    for raw in projects:
        if not isinstance(raw, dict):
            warnings.append(f"项目配置格式错误: {raw}")
            continue
        name = str(raw.get("name") or "").strip() or "未命名项目"
        path_text = str(raw.get("path") or "").strip()
        if not path_text:
            warnings.append(f"{name}: 缺少本地 path，Git 采集不会使用 repo_url。")
            continue
        path = Path(path_text).expanduser()
        if not path.exists():
            warnings.append(f"{name}: 本地路径不存在: {path}")
        elif not (path / ".git").exists():
            warnings.append(f"{name}: path 不是 Git 仓库目录: {path}")
    return warnings


def read_daily_report(report_dir: Path) -> dict[str, Any] | None:
    path = report_dir / "daily-report.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def run_workday_export(config_path: Path, person: str, day: str, out_dir: Path) -> tuple[bool, str]:
    script = Path(__file__).with_name("aiusage.py")
    cmd = [
        sys.executable,
        str(script),
        "export-workday",
        "--person",
        person,
        "--date",
        day,
        "--config",
        str(config_path),
        "--out",
        str(out_dir),
        "--verbose",
    ]
    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(x for x in [proc.stdout.strip(), proc.stderr.strip()] if x)
    return proc.returncode == 0, output


def render_reflection_form(data_dir: Path, day: str) -> None:
    reflection = load_reflection(data_dir, day)
    with st.container():
        st.subheader("每日人工补充")
        most_important_goal = st.text_area("今天最重要的目标", value=str(reflection.get("most_important_goal") or ""), height=80)
        actual_result = st.text_area("今天实际完成的结果", value=str(reflection.get("actual_result") or ""), height=80)
        biggest_blocker = st.text_area("今天最大的阻塞或问题", value=str(reflection.get("biggest_blocker") or ""), height=80)
        c1, c2 = st.columns(2)
        accepted = c1.checkbox("是否完成验收", value=bool(reflection.get("accepted")))
        has_rework = c2.checkbox("是否发生返工", value=bool(reflection.get("has_rework")))
        other_work = st.text_area("其他无法从 Git 获取的工作", value=str(reflection.get("other_work") or ""), height=100)
        submitted = st.button("保存复盘")
    if submitted:
        save_reflection(
            data_dir,
            day,
            {
                "most_important_goal": most_important_goal,
                "actual_result": actual_result,
                "biggest_blocker": biggest_blocker,
                "accepted": accepted,
                "has_rework": has_rework,
                "other_work": other_work,
            },
        )
        st.success("复盘已保存。重新生成日报后会进入 daily-report。")


def render_v2_report(report: dict[str, Any]) -> None:
    overview = report.get("overview") or {}
    st.subheader(f"研发日报 {report.get('date')}")
    st.caption("AI 协作时长、人工审查时长、有效工作时间、返工比例和质量指标均为统计或规则估算，仅用于个人复盘。")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("AI 输入轮数", int(overview.get("ai_turn_count") or 0))
    c2.metric("AI 会话数", int(overview.get("ai_session_count") or 0))
    c3.metric("业务提交", int(overview.get("business_commit_count") or 0))
    c4.metric("修改文件", int(overview.get("files_changed") or 0))
    c5.metric("新增/删除", f"+{int(overview.get('insertions') or 0)} / -{int(overview.get('deletions') or 0)}")

    tabs = st.tabs(["概览", "Git 工作量", "AI 关联", "返工异常", "技术主题", "日报 Markdown", "原始 JSON"])
    with tabs[0]:
        warnings = report.get("warnings") or []
        for warning in warnings:
            st.info(warning)
        project_rows = report.get("project_distribution") or []
        if project_rows:
            st.dataframe(pd.DataFrame(project_rows), use_container_width=True, hide_index=True)
        else:
            st.info("暂无项目分布数据。可能是当天未扫描到 AI 输入和 Git 提交，或项目配置未命中。")
        st.markdown("#### 今日成果")
        st.write(report.get("today_outcome") or "-")
        st.markdown("#### 明日建议")
        for item in report.get("tomorrow_suggestions") or []:
            st.write(f"- {item}")
    with tabs[1]:
        git_workload = report.get("git_workload") or {}
        commits = git_workload.get("commits") or []
        file_changes = git_workload.get("file_changes") or []
        if commits:
            st.dataframe(pd.DataFrame(commits), use_container_width=True, hide_index=True)
        else:
            st.info("未采集到 Git 提交。可能是当天未提交、项目 path 配置错误，或本地仓库无当天提交。")
        if file_changes:
            st.dataframe(pd.DataFrame(file_changes), use_container_width=True, hide_index=True)
        else:
            st.info("暂无 Git 文件变更明细。")
    with tabs[2]:
        associations = report.get("associations") or []
        if associations:
            st.markdown("#### 已关联会话")
            st.dataframe(pd.DataFrame(associations), use_container_width=True, hide_index=True)
        else:
            st.info("AI-Git 关联为空。这只表示当前规则未匹配到会话和提交，不代表 AI 没有参与。")
        unmatched_sessions = report.get("unmatched_ai_sessions") or []
        if unmatched_sessions:
            st.markdown("#### 未关联 AI 会话")
            st.dataframe(pd.DataFrame(unmatched_sessions), use_container_width=True, hide_index=True)
        commit_summary = report.get("commit_association_summary") or {}
        if commit_summary:
            st.markdown("#### Commit 关联概览")
            c1, c2, c3 = st.columns(3)
            c1.metric("总提交", int(commit_summary.get("total_commits") or 0))
            c2.metric("已关联提交", int(commit_summary.get("associated_commit_count") or 0))
            c3.metric("未关联提交", int(commit_summary.get("unassociated_commit_count") or 0))
            unassociated_commits = commit_summary.get("unassociated_commits") or []
            if unassociated_commits:
                st.dataframe(pd.DataFrame(unassociated_commits), use_container_width=True, hide_index=True)
    with tabs[3]:
        rework_rows = report.get("rework_and_exceptions") or []
        if rework_rows:
            st.dataframe(pd.DataFrame(rework_rows), use_container_width=True, hide_index=True)
        else:
            st.info("暂未通过规则识别到明确返工信号，不代表绝对没有返工。")
    with tabs[4]:
        topic_rows = report.get("technical_topics") or []
        if topic_rows:
            st.dataframe(pd.DataFrame(topic_rows), use_container_width=True, hide_index=True)
        else:
            st.info("暂无技术主题信号。")
        quality = report.get("quality_metrics") or {}
        if quality.get("note"):
            st.caption(str(quality.get("note")))
        st.json(quality)
    with tabs[5]:
        markdown = render_daily_markdown(report)
        st.download_button("下载 daily-report.md", markdown.encode("utf-8"), "daily-report.md", mime="text/markdown")
        st.markdown(markdown)
    with tabs[6]:
        st.download_button(
            "下载 daily-report.json",
            json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            "daily-report.json",
            mime="application/json",
        )
        st.json(report)


def render_history_trends(data_dir: Path, person: str) -> None:
    st.divider()
    st.subheader("历史趋势")
    dates, warnings = list_daily_report_dates(data_dir)
    for warning in warnings:
        st.warning(warning)
    if not dates:
        st.info("暂无历史日报。先生成至少一份 daily-report.json 后再查看趋势。")
        return

    c1, c2 = st.columns(2)
    start_value = c1.date_input("趋势开始日期", value=date_from_text(dates[0]), key="trend_start")
    end_value = c2.date_input("趋势结束日期", value=date_from_text(dates[-1]), key="trend_end")
    start_day = start_value.isoformat()
    end_day = end_value.isoformat()
    reports, range_warnings = load_daily_reports_range(data_dir, start_day, end_day)
    for warning in range_warnings:
        st.warning(warning)
    if not reports:
        st.info("所选日期范围内没有可读取的日报。")
        return

    trend_report = build_period_report("range", f"{start_day}_{end_day}", person or "", reports, range_warnings)
    overview = trend_report.get("overview") or {}
    c3, c4, c5, c6 = st.columns(4)
    c3.metric("日报天数", int(overview.get("report_day_count") or 0))
    c4.metric("AI 输入", int(overview.get("ai_turn_count") or 0))
    c5.metric("业务提交", int(overview.get("business_commit_count") or 0))
    c6.metric("修改文件", int(overview.get("files_changed") or 0))

    tabs = st.tabs(["日报列表", "项目趋势", "技术主题", "返工趋势", "趋势 Markdown"])
    with tabs[0]:
        st.dataframe(pd.DataFrame(trend_report.get("daily_summaries") or []), use_container_width=True, hide_index=True)
    with tabs[1]:
        rows = aggregate_project_distribution(reports)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("暂无项目趋势数据。")
    with tabs[2]:
        rows = aggregate_topic_trends(reports)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("暂无技术主题趋势数据。")
    with tabs[3]:
        rows = trend_report.get("rework_trends") or []
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("所选日期范围内暂未通过规则识别到明确返工信号。")
    with tabs[4]:
        st.markdown(render_period_markdown(trend_report))


def date_from_text(value: str) -> Any:
    return pd.to_datetime(value).date()


def render_v2_dashboard() -> None:
    st.title("AI Usage Tool v2.0")
    st.caption("本地个人研发工作日报：AI 使用、Git 工作量、人工复盘、返工信号和技术主题。")

    with st.sidebar:
        st.header("v2 数据")
        config_text = st.text_input("配置文件", value="aiusage-config.json")
        person = st.text_input("人员", value="")
        day_value = st.date_input("日期")
        day = day_value.isoformat()

    config_path = resolve_config_path(config_text)
    if not config_path.exists():
        st.error("未找到 v2 配置文件，不能生成研发日报。")
        st.write("请先创建 `aiusage-config.json`，并确认当前打开的是 ai-usage-tool 仓库目录。")
        st.code(f"当前运行目录: {Path.cwd()}\n配置文件路径: {config_path}")
        st.code(
            'python .\\aiusage.py init-config --out .\\aiusage-config.json --project "ai-usage-tool=C:\\Users\\lenovo\\Desktop\\ai-usage-tool|https://github.com/lyj012/ai-usage-tool"',
            language="powershell",
        )
        return

    try:
        config = load_config(config_path)
    except Exception as exc:
        st.error(f"读取配置失败: {exc}")
        return

    data_dir = resolve_data_dir(config_path, config)
    report_dir = data_dir / "reports" / day
    projects = config.get("projects") or []
    warnings = project_config_warnings(projects)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("#### 已配置项目")
        st.dataframe(pd.DataFrame(projects), use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### 报告目录")
        st.code(str(report_dir))

    if not projects:
        st.warning("配置文件里没有 projects。请先配置本地 Git 仓库 path，否则 Git 工作量会是 0。")
        return
    for warning in warnings:
        st.warning(warning)

    render_reflection_form(data_dir, day)

    generate_disabled = not person.strip()
    if st.button("生成 / 刷新当天日报", disabled=generate_disabled):
        ok, output = run_workday_export(config_path, person.strip(), day, report_dir)
        if ok:
            st.success("日报已生成。")
        else:
            st.error("日报生成失败。")
        if output:
            st.code(output)

    report = read_daily_report(report_dir)
    if report is None:
        st.info("尚未生成当天日报。先保存复盘，再点击生成。")
    else:
        render_v2_report(report)
    render_history_trends(data_dir, person.strip())


def main() -> None:
    render_v2_dashboard()


if __name__ == "__main__":
    main()
