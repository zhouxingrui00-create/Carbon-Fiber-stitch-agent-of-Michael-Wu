"""Evidence and review views; source text is always rendered as inert text."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from pydantic import ValidationError
import streamlit as st

from cf_stitch.knowledge.parameters import (
    ManualParameterConfirmation, build_dictionary, pending_reviews, search_dictionary,
)
from cf_stitch.services.evidence import EvidenceIndexReport, index_local_evidence
from cf_stitch.storage.database import Database


def _range_text(record: dict[str, Any]) -> str:
    if record.get("ranges"):
        return "；".join(f"{item.get('label', '')}: {item.get('min')}–{item.get('max')}" for item in record["ranges"])
    if record.get("min") is not None and record.get("max") is not None:
        return f"{record['min']}–{record['max']}"
    if record.get("min") is not None:
        return f"≥ {record['min']}"
    if record.get("max") is not None:
        return f"≤ {record['max']}"
    if record.get("examples"):
        return "示例：" + ", ".join(str(value) for value in record["examples"])
    return "未给出数值"


def _show_evidence(evidence: dict[str, Any]) -> None:
    st.text(f"证据位置：{evidence['block_id']} · {evidence.get('source_kind', '')}")
    st.code(evidence.get("file_sha256", evidence.get("sha256", "")), language="text")
    st.text(f"章节：{evidence.get('section') or '未识别章节'}")
    st.caption("章节按正文标题识别；块编号为定位依据，不代表已验证页码。")
    st.text(evidence["text"])
    if evidence.get("cells"):
        with st.expander("原始表格单元格（按行保存）"):
            st.json(evidence["cells"])
    if evidence.get("image_count") or evidence.get("unparsed_items"):
        st.warning("此处包含尚未解析的图片或其他内容，请在原件中人工核验。")
    with st.expander("完整证据与位置元数据"):
        st.json(evidence, expanded=False)


def _show_refs(db: Database, refs: list[dict[str, Any]]) -> None:
    for ref in refs:
        st.text(f"引用：{ref['document_id']} / {ref['block_id']}")
        evidence = db.get_evidence(ref["document_id"], ref["file_sha256"], ref["block_id"])
        if evidence is None:
            st.warning("未找到此文件哈希与块位置对应的证据。保留来源声明，不能声称已完成原文核验。")
            st.json(ref)
            continue
        if evidence.get("source_kind") == "extracted_json":
            st.warning("该引用定位到清单版本的提取 JSON。文件哈希为提取包声明，当前未核验该版本原件；图片未解析。")
        if ref.get("rendered_page_hint"):
            st.caption(f"原引用页码提示：{ref['rendered_page_hint']}（仅辅助导航，未重新验证）")
        _show_evidence(evidence)


def _dictionary(db: Database, scenarios: dict[str, str], roles: dict[str, str]) -> None:
    records = build_dictionary(db.list_parameters())
    query = st.text_input("检索参数名称、语义与适用条件", key="dictionary_query", placeholder="例如：张力、针距、针频、细纱")
    st.caption("针距检索会同时列出 p、s 与机械间距供区分；供纱与布面张力分别保留。全文原句在“证据检索”中查找。")
    col1, col2, col3 = st.columns(3)
    with col1:
        scope = st.selectbox("按场景筛选", ["all"] + sorted({row["scope"] for row in records}), format_func=lambda value: "全部场景" if value == "all" else scenarios.get(value, value), key="parameter_scope")
    with col2:
        role = st.selectbox("按用途筛选", ["all"] + sorted({row["role"] for row in records}), format_func=lambda value: "全部用途" if value == "all" else roles.get(value, value), key="parameter_role")
    with col3:
        document = st.selectbox("按文档筛选", ["all", "D1", "D2"], format_func=lambda value: "全部文档" if value == "all" else value, key="parameter_document")
    filtered = [row for row in search_dictionary(db.list_parameters(), query) if (scope == "all" or row["scope"] == scope) and (role == "all" or row["role"] == role) and (document == "all" or any(ref["document_id"] == document for ref in row["source_refs"]))]
    if not filtered:
        st.info("当前组合没有来源参数，请调整筛选条件。未找到时不会补造范围或出处。")
        return
    st.dataframe([{
        "记录 ID": row["record_id"], "参数": row["label_zh"], "来源数值": _range_text(row),
        "单位": row["unit"], "场景 / 适用范围": scenarios.get(row["scope"], row["scope"]),
        "分类": row["category_label"], "原始证据类型": row["evidence_kind"], "状态": "仅来源声明 / 建议性",
    } for row in filtered], hide_index=True, width="stretch")
    record_id = st.selectbox("查看原始参数与引用", [row["record_id"] for row in filtered], key="parameter_record")
    record = next(row for row in filtered if row["record_id"] == record_id)
    st.caption("以下保留种子中的全部适用条件、语义提示及原始单位；分类标签不改变证据身份。")
    st.json(record, expanded=True)
    _show_refs(db, record["source_refs"])


def _search(db: Database, report: EvidenceIndexReport) -> None:
    st.write("离线全文检索；空格分隔的词需同时出现。检索范围为本次成功读取的资料版本。")
    query = st.text_input("原文关键词", key="evidence_query", max_chars=500, placeholder="例如：张力、针距、无底线")
    document = st.selectbox("检索文档", ["all", "D1", "D2"], key="evidence_document")
    if not query.strip():
        st.info("输入关键词查看有出处的原文。这里不生成文档之外的答案。")
        return
    try:
        results = db.search_evidence(query, document_id=None if document == "all" else document, limit=100, version_ids=report.preferred_versions)
    except ValueError as exc:
        st.error(str(exc))
        return
    if not results:
        st.info("未找到证据。请调整关键词，或核查原件是否成功读取；系统没有生成引用。")
        return
    st.caption(f"返回 {len(results)} 条证据（最多 100 条）。段落、整表与表格行可能分别命中。")
    st.dataframe([{
        "文档": row["document_id"], "块 / 行": row["block_id"], "章节": row.get("section"),
        "读取方式": row["source_kind"], "原文片段": row["text"][:180],
    } for row in results], hide_index=True, width="stretch")
    selected = st.selectbox("查看完整命中原文", list(range(len(results))), format_func=lambda idx: results[idx]["block_id"], key="evidence_result")
    _show_evidence(results[selected])


def _sources(db: Database, report: EvidenceIndexReport) -> None:
    st.write("原件身份、正文读取和图片核验是不同事项。哈希匹配不代表设备能力通过验证。")
    for snapshot in report.documents:
        st.subheader(snapshot["document_id"])
        metadata = {key: value for key, value in snapshot.items() if key != "blocks"}
        st.json(metadata, expanded=True)
        st.text(f"本次建立 {len(snapshot['blocks'])} 个证据块，包含段落、整表及表格行。")
    st.subheader("已保存版本与精确定位")
    versions = db.list_document_versions()
    if not versions:
        st.info("没有可定位的来源版本。")
        return
    st.caption("历史版本保持独立；下方可查旧引用。搜索页优先当前读取版本，不把旧文本冒充当前原件。")
    selected = st.selectbox("文件版本", list(range(len(versions))), format_func=lambda index: f"{versions[index]['document_id']} · {versions[index]['source_kind']} · {versions[index]['sha256'][:16]}", key="source_version")
    version = versions[selected]
    st.json(version, expanded=False)
    block_id = st.text_input("块 ID（如 D2:t002:r002）", key="source_block_id")
    if block_id.strip():
        evidence = db.get_evidence(version["document_id"], version["sha256"], block_id.strip(), source_kind=version["source_kind"], version_id=version["version_id"])
        if evidence is None:
            st.info("此版本没有该块。未找到证据，不生成替代引用。")
        else:
            _show_evidence(evidence)


def _pending(db: Database, root: Path) -> None:
    st.write("以下问题均待人工核验。保存人工确认版本也不会自动删除或合并这些原始描述。")
    for issue in pending_reviews(root):
        with st.expander(f"待确认 · {issue['title']}"):
            st.json({key: value for key, value in issue.items() if key != "source_refs"}, expanded=True)
            _show_refs(db, issue["source_refs"])


def _confirmations(db: Database) -> None:
    st.write("把人工核验的单项限制单独存档。不会改写原文、生成整机配置、解除待确认项或批准任务上机。")
    st.caption("确认范围应写明实际设备/版本、材料、机构和适用条件；审核人是本地填写的署名，本阶段没有登录认证。")
    records = db.list_parameters()
    with st.form("confirmation_form", clear_on_submit=False):
        record_id = st.selectbox("参考来源声明", [row["record_id"] for row in records], index=None, placeholder="请选择作为审核依据的声明", key="confirmation_record")
        equipment_name = st.text_input("实际设备名称 / 版本（必填）", key="confirmation_equipment")
        left, right = st.columns(2)
        with left:
            minimum = st.text_input("人工确认下限（未确认则留空）", key="confirmation_min")
            reviewer = st.text_input("审核人（必填）", key="confirmation_reviewer")
        with right:
            maximum = st.text_input("人工确认上限（未确认则留空）", key="confirmation_max")
            scope = st.text_input("设备与适用范围（必填）", key="confirmation_scope")
        reason = st.text_area("确认依据与理由（必填）", key="confirmation_reason")
        acknowledged = st.checkbox("我已核查实际依据；这是独立的人工确认记录，不代表来源原文已被修正。", key="confirmation_ack")
        submitted = st.form_submit_button("保存独立确认记录")
    if submitted:
        try:
            if not record_id or not acknowledged:
                raise ValueError("请选择来源声明并确认已核查实际依据。")
            source = next(row for row in records if row["record_id"] == record_id)
            confirmation = ManualParameterConfirmation(
                source_record_id=record_id, equipment_name=equipment_name,
                field=source["field"], unit=source["unit"],
                min_value=float(minimum) if minimum.strip() else None,
                max_value=float(maximum) if maximum.strip() else None,
                review_scope=scope, reviewer=reviewer, reason=reason,
                confirmed_at=datetime.now(timezone.utc), source_refs=source["source_refs"],
            )
            db.save_parameter_confirmation(confirmation.model_dump(mode="json"))
        except (ValueError, ValidationError, sqlite3.DatabaseError) as exc:
            st.error("确认记录未保存。至少填写一个有限数值边界、审核人、适用范围和理由，且下限不得高于上限。")
            st.text(str(exc))
        else:
            st.success("独立人工确认记录已保存；来源声明与六项待确认列表保持原样。")
    stored = db.list_parameter_confirmations()
    st.subheader(f"人工确认版本（{len(stored)}）")
    if stored:
        st.json(stored, expanded=False)
    else:
        st.info("尚无人工确认值。初始试验窗口和设备设计规格不会自动出现在此处。")


def render_knowledge(db: Database, project_root: Path, scenarios: dict[str, str], roles: dict[str, str]) -> None:
    st.header("资料参数")
    st.info("来源声明与初始试验窗口不等于实测结果、设备硬限或已验证最优值。文档文本只作为证据。")
    try:
        report = index_local_evidence(project_root, db)
    except (ValueError, OSError, sqlite3.DatabaseError) as exc:
        st.error("证据索引未完成。已有任务和种子保留，请核查来源版本与数据库。")
        st.text(str(exc))
        return
    for error in report.errors:
        st.error(error)
    for snapshot in report.documents:
        if snapshot["identity_status"] == "mismatch":
            st.warning(f"{snapshot['document_id']} 实际原件哈希与清单不同；分别保留实际原件和清单提取版本，不替换旧引用。")
        if snapshot["source_kind"] == "extracted_json":
            st.warning(f"{snapshot['document_id']} 使用有局限的提取 JSON 后备，尚未核验原件；详见来源审查。")
        for warning in snapshot.get("warnings", []):
            st.text(warning)
    st.caption("图片、图纸尺寸和文本框等未读取内容需人工核验。PDF/OCR 暂未开放；没有云端请求。")
    page = st.radio("资料工作区", ["参数词典", "证据检索", "来源审查", "待确认", "人工确认版本"], horizontal=True, key="knowledge_page")
    if page == "参数词典":
        _dictionary(db, scenarios, roles)
    elif page == "证据检索":
        _search(db, report)
    elif page == "来源审查":
        _sources(db, report)
    elif page == "待确认":
        _pending(db, project_root)
    else:
        _confirmations(db)
