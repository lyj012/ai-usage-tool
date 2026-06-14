#!/usr/bin/env python3
"""Streamlit dashboard for aiusage export packages."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from aiusage import aggregate, fmt_seconds, json_dumps, markdown_summary, read_inputs_from_zip_or_file
from workreport import load_config, load_reflection, render_daily_markdown, save_reflection


st.set_page_config(page_title="AI Usage Dashboard", layout="wide")


def read_inputs_from_uploaded_zip(uploaded_file: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    data = uploaded_file.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "inputs.jsonl" not in z.namelist():
            return records
        with z.open("inputs.jsonl") as f:
            for raw in f:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    records.append(json.loads(line))
    return records


def load_records_from_uploads(files: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for file in files:
        try:
            records.extend(read_inputs_from_uploaded_zip(file))
        except Exception as exc:
            st.warning(f"读取 {file.name} 失败: {exc}")
    return records


def load_records_from_dir(path_text: str) -> list[dict[str, Any]]:
    if not path_text.strip():
        return []
    path = Path(path_text).expanduser()
    if not path.exists():
        st.warning(f"目录不存在: {path}")
        return []
    records: list[dict[str, Any]] = []
    for file in sorted(path.glob("*")):
        if file.is_file() and (file.suffix == ".zip" or file.name == "inputs.jsonl" or file.suffix == ".jsonl"):
            try:
                records.extend(read_inputs_from_zip_or_file(file))
            except Exception as exc:
                st.warning(f"读取 {file} 失败: {exc}")
    return records


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def seconds_columns_to_human(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for col in result.columns:
        if col.endswith("_seconds"):
            result[col + "_human"] = result[col].apply(lambda x: fmt_seconds(x) if pd.notnull(x) else "")
    return result


def make_csv_download(df: pd.DataFrame, label: str, file_name: str) -> None:
    csv_data = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(label, csv_data, file_name=file_name, mime="text/csv")


def render_kpis(records: list[dict[str, Any]]) -> None:
    total = aggregate(records, [])[0] if records else {}
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("输入轮数", f"{int(total.get('turn_count') or 0):,}")
    c2.metric("会话数", f"{int(total.get('session_count') or 0):,}")
    c3.metric("AI执行时长", fmt_seconds(total.get("ai_active_seconds") or 0))
    c4.metric("总 token", f"{int(total.get('total_tokens') or 0):,}")
    c5.metric("平均输入间隔", fmt_seconds(total.get("avg_input_interval_seconds")))

    c6, c7, c8, c9 = st.columns(4)
    c6.metric("输入 token", f"{int(total.get('input_tokens') or 0):,}")
    c7.metric("响应 token", f"{int(total.get('response_tokens') or 0):,}")
    c8.metric("cache token", f"{int(total.get('cache_tokens') or 0):,}")
    c9.metric("reasoning token", f"{int(total.get('reasoning_tokens') or 0):,}")


def render_summary_tables(records: list[dict[str, Any]]) -> None:
    tabs = st.tabs(["按人", "按天", "按项目", "按工具", "会话摘要", "高消耗轮次", "长任务轮次", "原始明细"])

    table_specs = [
        (tabs[0], ["person"], "person_summary.csv"),
        (tabs[1], ["person", "date", "tool"], "daily_summary.csv"),
        (tabs[2], ["person", "project", "tool"], "project_summary.csv"),
        (tabs[3], ["tool"], "tool_summary.csv"),
    ]
    for tab, keys, filename in table_specs:
        with tab:
            rows = aggregate(records, keys)
            df = seconds_columns_to_human(to_frame(rows))
            st.dataframe(df, use_container_width=True, hide_index=True)
            make_csv_download(df, "下载 CSV", filename)

    with tabs[4]:
        session_df = build_session_summary(records)
        st.dataframe(session_df, use_container_width=True, hide_index=True)
        make_csv_download(session_df, "下载 CSV", "session_summary.csv")

    with tabs[5]:
        df = to_frame(sorted(records, key=lambda x: int(x.get("total_tokens") or 0), reverse=True)[:100])
        st.dataframe(select_detail_cols(df), use_container_width=True, hide_index=True)
        make_csv_download(df, "下载 CSV", "high_token_turns.csv")

    with tabs[6]:
        df = to_frame(sorted(records, key=lambda x: float(x.get("ai_active_seconds") or 0), reverse=True)[:100])
        st.dataframe(select_detail_cols(df), use_container_width=True, hide_index=True)
        make_csv_download(df, "下载 CSV", "long_task_turns.csv")

    with tabs[7]:
        df = to_frame(records)
        st.dataframe(df, use_container_width=True, hide_index=True)
        make_csv_download(df, "下载全部明细 CSV", "team_inputs.csv")


def select_detail_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cols = [
        "person",
        "date",
        "tool",
        "project",
        "input_at",
        "ai_active_seconds",
        "total_tokens",
        "input_tokens",
        "response_tokens",
        "cache_tokens",
        "input_preview",
    ]
    available = [col for col in cols if col in df.columns]
    result = df[available].copy()
    if "ai_active_seconds" in result.columns:
        result["ai_active_human"] = result["ai_active_seconds"].apply(fmt_seconds)
    return result


def build_session_summary(records: list[dict[str, Any]]) -> pd.DataFrame:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in records:
        key = (
            str(row.get("person") or ""),
            str(row.get("date") or ""),
            str(row.get("tool") or ""),
            str(row.get("project") or ""),
            str(row.get("session_id") or ""),
        )
        groups.setdefault(key, []).append(row)

    rows: list[dict[str, Any]] = []
    for (person, day, tool, project, session_id), items in groups.items():
        items = sorted(items, key=lambda x: str(x.get("input_at") or ""))
        previews = [str(x.get("input_preview") or "").strip() for x in items if x.get("input_preview")]
        summary = previews[0] if previews else ""
        if len(previews) > 1:
            summary += "；后续：" + "；".join(previews[1:4])
        rows.append(
            {
                "person": person,
                "date": day,
                "tool": tool,
                "project": project,
                "start": items[0].get("input_at"),
                "turn_count": len(items),
                "ai_active_seconds": sum(float(x.get("ai_active_seconds") or 0) for x in items),
                "ai_active_human": fmt_seconds(sum(float(x.get("ai_active_seconds") or 0) for x in items)),
                "total_tokens": sum(int(x.get("total_tokens") or 0) for x in items),
                "summary": summary[:240],
                "session_id": session_id,
            }
        )
    return pd.DataFrame(sorted(rows, key=lambda x: str(x.get("start") or "")))


def render_download_report(records: list[dict[str, Any]]) -> None:
    summary = markdown_summary(records, "团队 AI 使用统计")
    st.download_button("下载 summary.md", summary.encode("utf-8"), "summary.md", mime="text/markdown")
    inputs_jsonl = "\n".join(json_dumps(r) for r in records) + ("\n" if records else "")
    st.download_button("下载 team_inputs.jsonl", inputs_jsonl.encode("utf-8"), "team_inputs.jsonl", mime="application/jsonl")

    with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
        with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("summary.md", summary)
            z.writestr("team_inputs.jsonl", inputs_jsonl)
            z.writestr("team_person_summary.csv", to_frame(aggregate(records, ["person"])).to_csv(index=False))
            z.writestr("team_daily_summary.csv", to_frame(aggregate(records, ["person", "date", "tool"])).to_csv(index=False))
            z.writestr("team_project_summary.csv", to_frame(aggregate(records, ["person", "project", "tool"])).to_csv(index=False))
        zip_data = Path(tmp.name).read_bytes()
    st.download_button("下载完整团队报表 zip", zip_data, "team_aiusage_report.zip", mime="application/zip")


def resolve_data_dir(config_path: Path, config: dict[str, Any]) -> Path:
    data_dir = Path(str(config.get("data_dir") or "data")).expanduser()
    if data_dir.is_absolute():
        return data_dir
    return config_path.parent / data_dir


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
    with st.form("daily_reflection"):
        st.subheader("每日人工补充")
        most_important_goal = st.text_area("今天最重要的目标", value=str(reflection.get("most_important_goal") or ""), height=80)
        actual_result = st.text_area("今天实际完成的结果", value=str(reflection.get("actual_result") or ""), height=80)
        biggest_blocker = st.text_area("今天最大的阻塞或问题", value=str(reflection.get("biggest_blocker") or ""), height=80)
        c1, c2 = st.columns(2)
        accepted = c1.checkbox("是否完成验收", value=bool(reflection.get("accepted")))
        has_rework = c2.checkbox("是否发生返工", value=bool(reflection.get("has_rework")))
        other_work = st.text_area("其他无法从 Git 获取的工作", value=str(reflection.get("other_work") or ""), height=100)
        submitted = st.form_submit_button("保存复盘")
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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("AI 输入轮数", int(overview.get("ai_turn_count") or 0))
    c2.metric("AI 会话数", int(overview.get("ai_session_count") or 0))
    c3.metric("业务提交", int(overview.get("business_commit_count") or 0))
    c4.metric("修改文件", int(overview.get("files_changed") or 0))
    c5.metric("新增/删除", f"+{int(overview.get('insertions') or 0)} / -{int(overview.get('deletions') or 0)}")

    tabs = st.tabs(["概览", "Git 工作量", "AI 关联", "返工异常", "技术主题", "日报 Markdown", "原始 JSON"])
    with tabs[0]:
        st.dataframe(pd.DataFrame(report.get("project_distribution") or []), use_container_width=True, hide_index=True)
        st.markdown("#### 今日成果")
        st.write(report.get("today_outcome") or "-")
        st.markdown("#### 明日建议")
        for item in report.get("tomorrow_suggestions") or []:
            st.write(f"- {item}")
    with tabs[1]:
        git_workload = report.get("git_workload") or {}
        st.dataframe(pd.DataFrame(git_workload.get("commits") or []), use_container_width=True, hide_index=True)
        st.dataframe(pd.DataFrame(git_workload.get("file_changes") or []), use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(pd.DataFrame(report.get("associations") or []), use_container_width=True, hide_index=True)
    with tabs[3]:
        st.dataframe(pd.DataFrame(report.get("rework_and_exceptions") or []), use_container_width=True, hide_index=True)
    with tabs[4]:
        st.dataframe(pd.DataFrame(report.get("technical_topics") or []), use_container_width=True, hide_index=True)
        quality = report.get("quality_metrics") or {}
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


def render_v2_dashboard() -> None:
    st.title("AI Usage Tool v2.0")
    st.caption("本地个人研发工作日报：AI 使用、Git 工作量、人工复盘、返工信号和技术主题。")

    with st.sidebar:
        st.header("v2 数据")
        config_text = st.text_input("配置文件", value="aiusage-config.json")
        person = st.text_input("人员", value="")
        day_value = st.date_input("日期")
        day = day_value.isoformat()

    config_path = Path(config_text).expanduser()
    try:
        config = load_config(config_path)
    except Exception as exc:
        st.error(f"读取配置失败: {exc}")
        return

    data_dir = resolve_data_dir(config_path, config)
    report_dir = data_dir / "reports" / day
    projects = config.get("projects") or []

    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("#### 已配置项目")
        st.dataframe(pd.DataFrame(projects), use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### 报告目录")
        st.code(str(report_dir))

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
        return
    render_v2_report(report)


def main() -> None:
    with st.sidebar:
        mode = st.radio("视图", ["v2 个人日报", "v1 团队统计"], index=0)

    if mode == "v2 个人日报":
        render_v2_dashboard()
        return

    st.title("AI Usage Dashboard")
    st.caption("上传每天导出的 ai-usage-*.zip，或填写本地目录，查看团队 AI 使用统计。")

    with st.sidebar:
        st.header("数据来源")
        uploaded = st.file_uploader("上传 ai-usage-*.zip", type=["zip"], accept_multiple_files=True)
        input_dir = st.text_input("或读取本地目录", value="")
        st.divider()
        st.header("筛选")

    records = []
    records.extend(load_records_from_uploads(uploaded or []))
    records.extend(load_records_from_dir(input_dir))

    if not records:
        st.info("请上传导出的 zip，或填写包含 ai-usage-*.zip 的本地目录。")
        return

    df = to_frame(records)
    with st.sidebar:
        people = sorted(df["person"].dropna().unique().tolist()) if "person" in df else []
        projects = sorted(df["project"].dropna().unique().tolist()) if "project" in df else []
        tools = sorted(df["tool"].dropna().unique().tolist()) if "tool" in df else []
        selected_people = st.multiselect("人员", people, default=people)
        selected_projects = st.multiselect("项目", projects, default=projects)
        selected_tools = st.multiselect("工具", tools, default=tools)

    filtered = records
    if selected_people:
        filtered = [r for r in filtered if r.get("person") in selected_people]
    if selected_projects:
        filtered = [r for r in filtered if r.get("project") in selected_projects]
    if selected_tools:
        filtered = [r for r in filtered if r.get("tool") in selected_tools]

    render_kpis(filtered)
    st.divider()
    render_summary_tables(filtered)
    st.divider()
    render_download_report(filtered)


if __name__ == "__main__":
    main()
