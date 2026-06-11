#!/usr/bin/env python3
"""Streamlit dashboard for aiusage export packages."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from aiusage import aggregate, fmt_seconds, json_dumps, markdown_summary, read_inputs_from_zip_or_file


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


def main() -> None:
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
