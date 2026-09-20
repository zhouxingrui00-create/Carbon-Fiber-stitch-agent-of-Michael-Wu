"""Local, read-only rule reviews and assumption-gated calculations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from cf_stitch.domain.schemas import ConfirmedEquipmentProfile
from cf_stitch.rules.calculations import (
    convert_unit,
    ideal_line_speed,
    j_density_to_pitch,
    matched_change,
    rectangular_density,
)
from cf_stitch.rules.results import RuleResult
from cf_stitch.rules.validation import validate_task
from cf_stitch.scenarios import get_scenario
from cf_stitch.storage.database import Database


STATUS_LABELS = {"PASS": "通过", "WARN": "警告", "BLOCK": "阻断", "UNKNOWN": "未知"}


def _optional_number(raw: str) -> float | None:
    if not raw.strip():
        return None
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ValueError("请输入数字；缺失数值请留空。") from exc


def _show_result(result: RuleResult) -> None:
    status = result.status.value
    text = f"{STATUS_LABELS[status]}（{status}）：{result.message}"
    display = {"PASS": st.success, "WARN": st.warning, "BLOCK": st.error, "UNKNOWN": st.info}[status]
    display(text)
    if result.value is not None:
        if isinstance(result.value, list):
            value = " – ".join(f"{item:.6g}" for item in result.value)
        else:
            value = f"{result.value:.8g}"
        st.metric("本次公式 / 换算结果", f"{value} {result.unit or ''}")
    st.json(result.model_dump(mode="json"), expanded=False)


def _scenario_cards(root: Path, labels: dict[str, str]) -> None:
    selected = st.selectbox("查看场景参数卡", list(labels), format_func=labels.get, key="rules_scenario")
    try:
        scenario = get_scenario(root, selected)
    except (OSError, ValueError) as exc:
        st.error("本次场景来源无法读取；未创建替代参数或推断设备能力。")
        st.text(str(exc))
        return
    st.subheader(scenario.label)
    st.warning("来源参数仍是初始窗口、设备设计声明或质量目标；没有自动成为本次设备硬限或实测能力。")
    st.caption("J 型针密的源记录可能带条件推导值，仅在均匀单排假设成立时有意义；不会自动写入任务。")
    if scenario.claims:
        st.dataframe([
            {
                "记录 ID": claim.get("record_id", ""),
                "参数": claim.get("label_zh", claim.get("field", "")),
                "字段": claim.get("field", ""),
                "下界": str(claim.get("min", "未提供")),
                "上界": str(claim.get("max", "未提供")),
                "单位": claim.get("unit", ""),
                "证据性质": claim.get("evidence_kind", ""),
                "适用场景": claim.get("scope", ""),
            }
            for claim in scenario.claims
        ], hide_index=True, width="stretch")
    for note in scenario.notes:
        st.write(note)
    if scenario.pending:
        st.subheader("本场景待确认")
        for pending in scenario.pending:
            st.text(str(pending))
    st.info("可达性：UNKNOWN；避碰：UNKNOWN。尚未进行 CAD、机构、TCP 与夹具验证，不生成机器人运动或设备执行程序。")
    with st.expander("场景完整参数卡、原文来源及未设定上下文"):
        st.json(scenario.to_dict())


def _calculations(mechanisms: dict[str, str], paths: dict[str, str]) -> None:
    st.caption("所有计算在本地执行，输入和结果不会自动写成实测数据，也不批准设备方案。适用假设需逐项确认。")
    operation = st.selectbox("计算类型", ["矩形阵列理论密度", "理想直线送料速度", "单位换算", "J 型针密条件换算", "匹配对照提升率 / 损失率"], key="rules_calculation")
    with st.form("rules_calculation_form"):
        if operation == "矩形阵列理论密度":
            st.code("N_A = 1,000,000 / (p × s)，p、s：mm，结果：点/m²", language="text")
            mechanism = st.selectbox("计算所用缝合机制", list(mechanisms), index=None, placeholder="请选择机制", format_func=mechanisms.get, key="density_mechanism")
            pattern = st.selectbox("计算所用路径", list(paths), index=None, placeholder="请选择路径", format_func=paths.get, key="density_path")
            pitch = st.text_input("针距 p（mm；未缝合组留空）", key="density_pitch")
            spacing = st.text_input("行距 s（mm；未缝合组留空）", key="density_spacing")
            rectangular = st.checkbox("本次是规则矩形阵列", key="density_rectangular")
            one_puncture = st.checkbox("每格恰好一个穿刺点", key="density_one_puncture")
            no_extras = st.checkbox("确认没有额外回针、双针和锁固穿刺；忽略有限边界效应", key="density_no_extras")
            st.caption("复杂路径必须按真实针位与实际面积计算；机械针位间距不能代替 p 或 s。未缝合时 p / s 不适用，不经除法明示密度 0。")
        elif operation == "理想直线送料速度":
            st.code("v = p × f / 1000，v：m/min，p：mm，f：针/min", language="text")
            pitch = st.text_input("实际每针前进量 p（mm）", key="speed_pitch")
            frequency = st.text_input("针频 f（针/min）", key="speed_frequency")
            straight = st.checkbox("理想直线、同步送料，无额外花样或回针", key="speed_straight")
            feed_advance = st.checkbox("p 是送料方向实际每针前进量", key="speed_feed_advance")
            one_cycle = st.checkbox("每周期一针", key="speed_one_cycle")
        elif operation == "单位换算":
            value = st.text_input("原始数值", key="unit_value")
            units = ["mm", "cm", "m", "N", "cN", "gf", "Hz", "stitches/min", "m/min", "mm/s"]
            unit_from = st.selectbox("原单位", units, index=None, placeholder="请选择原单位", key="unit_from")
            unit_to = st.selectbox("目标单位", units, index=None, placeholder="请选择目标单位", key="unit_to")
            one_cycle = st.checkbox("若涉及 Hz 与针/min，确认每周期一针", key="unit_one_cycle")
            st.caption("相同单位不代表相同测量对象；不会把供纱张力变成布面张力，也不直接把针频换成线速度。")
        elif operation == "J 型针密条件换算":
            st.write("D1 原文：5–15 针/cm。此项只展示该来源量的条件换算，不生成任务针距或优化范围。")
            single_row = st.checkbox("仅作均匀单排计数假设下的展示", key="j_density_single_row")
            st.caption("原单位、原范围、均匀单排假设和待确认状态一并保留；不与 D2 通用针距取交集。")
        else:
            stitched = st.text_input("缝合组均值", key="change_stitched")
            control = st.text_input("对照组均值", key="change_control")
            input_unit = st.text_input("两组共同单位", key="change_unit")
            change_mode = st.selectbox("结果含义", ["improvement", "loss"], format_func=lambda mode: "提升率（正值表示提高）" if mode == "improvement" else "损失率（正值表示损失）", key="change_mode")
            matched = st.checkbox("两组材料、成型 / 后处理、测试方法与状态匹配，单位相同", key="change_matched")
            st.caption("本页仅计算输入均值的比率；不建立实验记录。负损失率保留其提高含义，不截为零。")
        submitted = st.form_submit_button("计算并显示假设", key="rules_compute")
    if not submitted:
        return
    try:
        if operation == "矩形阵列理论密度":
            result = rectangular_density(_optional_number(pitch), _optional_number(spacing), mechanism=mechanism or "unspecified", path_pattern=pattern or "unspecified", regular_rectangular=rectangular, one_puncture_per_cell=one_puncture, extra_punctures=False if no_extras else None)
        elif operation == "理想直线送料速度":
            result = ideal_line_speed(_optional_number(pitch), _optional_number(frequency), straight_synchronized=straight, pitch_is_feed_advance=feed_advance, one_stitch_per_cycle=one_cycle)
        elif operation == "单位换算":
            result = convert_unit(_optional_number(value), unit_from or "", unit_to or "", stitches_per_cycle=1 if one_cycle else None)
        elif operation == "J 型针密条件换算":
            result = j_density_to_pitch(uniform_single_row=single_row)
        else:
            result = matched_change(_optional_number(stitched), _optional_number(control), matched_control=matched, mode=change_mode, unit=input_unit.strip() or None)
    except ValueError as exc:
        st.error(str(exc))
        return
    _show_result(result)


def _selected_confirmation(confirmation: dict[str, Any]) -> ConfirmedEquipmentProfile:
    """Adapt one explicitly selected record without combining or persisting it."""
    return ConfirmedEquipmentProfile.model_validate({
        "equipment_id": f"single-confirmation:{confirmation['confirmation_id']}",
        "equipment_name": confirmation["equipment_name"],
        "approved_by": confirmation["reviewer"],
        "approved_at": confirmation["confirmed_at"],
        "approval_reason": confirmation["reason"],
        "source_claim_ids": [confirmation["source_record_id"]],
        "limits": [{
            "field": confirmation["field"],
            "unit": confirmation["unit"],
            "min_value": confirmation["min_value"],
            "max_value": confirmation["max_value"],
            "applicability": confirmation["review_scope"],
            "evidence_note": f"单项人工确认 {confirmation['confirmation_id']}：{confirmation['reason']}",
            "source_refs": confirmation["source_refs"],
        }],
    })


def _task_review(db: Database, labels: dict[str, str]) -> None:
    tasks = db.list_tasks(namespace="real")
    if not tasks:
        st.info("尚无已保存任务；请先在任务工作台建立草案。没有自动生成样例任务。")
        return
    selected = st.selectbox("选择要审查的任务草案", list(range(len(tasks))), format_func=lambda index: f"{tasks[index]['title']} · {labels.get(tasks[index]['scenario'], tasks[index]['scenario'])} · {tasks[index]['task_id'][:8]}", key="review_task")
    task = tasks[selected]
    with st.expander("本次任务原始字段"):
        st.json(task)
    st.write("以下为只读审查。初始窗口外提示待审查；已确认设备限制外阻断候选。缺设备、机构或几何信息时仍是草案。")
    confirmation_records = db.list_parameter_confirmations(namespace="real")
    equipment = None
    applicable_fields: set[str] = set()
    if confirmation_records:
        choices = {item["confirmation_id"]: item for item in confirmation_records}
        confirmation_id = st.selectbox(
            "选择单项人工设备限制（可不选，不自动合并）", list(choices), index=None,
            placeholder="尚未选择人工限制",
            format_func=lambda key: f"{choices[key]['equipment_name']} · {choices[key]['field']} · {choices[key]['unit']} · {key[:8]}",
            key="review_confirmation",
        )
        if confirmation_id is not None:
            confirmation = choices[confirmation_id]
            st.text(f"字段：{confirmation['field']}；单位：{confirmation['unit']}；适用范围：{confirmation['review_scope']}")
            st.json(confirmation, expanded=False)
            applies = st.checkbox("确认这条限制适用于本次任务与设备版本", key=f"review_confirmation_applies_{task['task_id']}_{confirmation_id}")
            if applies:
                equipment = _selected_confirmation(confirmation)
                applicable_fields = {confirmation["field"]}
            st.caption("仅临时引用该条确认，保留原字段和引用；不存储整机配置，不解除来源待确认事项。")
    else:
        st.info("没有人工确认的设备限制。设备能力为未知，文档设计声明不会自动成为硬限。")
    through_thickness = st.checkbox("本次要求贯穿当前压实厚度", key=f"review_through_thickness_{task['task_id']}")
    tension_scope = st.selectbox("供纱张力窗口适用条件", ["unknown", "general_yarn_exploration", "fine_yarn_low_damage_exploration"], format_func=lambda key: {"unknown": "尚未确认适用窗口", "general_yarn_exploration": "一般供纱探索窗口", "fine_yarn_low_damage_exploration": "细纱低损伤探索窗口"}[key], key=f"review_tension_scope_{task['task_id']}")
    research_startup = None
    if task["scenario"] == "general_research":
        research_startup = True if st.checkbox("本次处于低速研发起步阶段", key=f"review_research_startup_{task['task_id']}") else None
    report = validate_task(task, db.list_parameters(), equipment=equipment, applicable_limit_fields=applicable_fields, tension_scope=None if tension_scope == "unknown" else tension_scope, through_thickness=True if through_thickness else None, research_startup=research_startup)
    status = report.status.value
    st.subheader(f"综合审查：{STATUS_LABELS[status]}（{status}）")
    st.caption(f"候选状态：{report.candidate_state}；设备可执行：否。本阶段不提供上机批准。")
    st.dataframe([
        {"状态": f"{STATUS_LABELS[item.status.value]} / {item.status.value}", "字段": item.field or "", "规则": item.code, "说明": item.message}
        for item in report.findings
    ], hide_index=True, width="stretch")
    with st.expander("完整审查结果、来源和适用假设", expanded=True):
        st.json(report.model_dump(mode="json"))


def render_rules(db: Database, root: Path, labels: dict[str, str], mechanisms: dict[str, str], paths: dict[str, str]) -> None:
    st.header("三场景与审查")
    st.caption("通过、警告、阻断、未知均说明规则与来源；计算值不等于实测值或预测值。")
    page = st.radio("场景与规则入口", ["场景参数卡", "条件化计算", "已存任务审查"], horizontal=True, key="rules_page")
    if page == "场景参数卡":
        _scenario_cards(root, labels)
    elif page == "条件化计算":
        _calculations(mechanisms, paths)
    else:
        _task_review(db, labels)
