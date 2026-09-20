"""Local experimental plans, import preview and immutable linked records."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import streamlit as st

from cf_stitch.experiments.importing import CANONICAL_FIELDS, read_tabular, list_xlsx_sheets, preview_import, commit_preview
from cf_stitch.experiments.models import ObservationRow
from cf_stitch.experiments.plans import build_trial_plan, review_plan
from cf_stitch.knowledge.seeds import load_experiment_template
from cf_stitch.rules_ui import _selected_confirmation
from cf_stitch.storage.experiments import ExperimentStore


PROVENANCE_LABELS = {
    "experimental_measured": "实验实测", "literature_measured": "文献实测",
    "simulation": "仿真", "demo": "演示", "prediction": "预测",
}


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    return value


def _downloads(root: Path) -> None:
    st.caption("模板不含真实实验或测量。CSV以UTF-8编码；Excel读取.xlsx，不执行公式或宏。")
    st.write("日常简化版：26列中文表头，适合单试样、单指标日常记录；完整录入模板：58列，保留详细工艺及其他来源类型。")
    st.caption("简化版内含“日常记录”和“填写说明”。导入时选择“日常记录”，按“填写说明”中的对照设置字段映射；没有结果时留空并填写原因。")
    for name, label, mime in (
        ("daily_measurements.xlsx", "下载日常简化版 Excel（26列中文）", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("blank_measurements.csv", "下载空白 CSV 录入模板", "text/csv"),
        ("blank_measurements.xlsx", "下载空白 Excel 录入模板", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("seven_group_plan.json", "下载七组待实验计划 JSON", "application/json"),
        ("seven_group_plan.csv", "下载七组实验清单 CSV", "text/csv"),
    ):
        path = root / "templates" / "stage4" / name
        if path.is_file():
            st.download_button(label, path.read_bytes(), file_name=name, mime=mime, key=f"download_{name}")


def render_experiment_design(db, root: Path, mechanisms: dict[str, str]) -> None:
    st.header("实验设计")
    store = ExperimentStore(db)
    try:
        template = load_experiment_template(root)
    except (OSError, ValueError) as exc:
        st.error("来源试验模板当前不可读取；没有创建或补造试验。")
        st.text(str(exc))
        return
    st.warning(template["warning"])
    st.info("背景为约20–25 mm厚预制体。缝线、材料/铺层、压实、成型和后处理保持一致，匹配未缝合对照；重复数、独立制样数、针径、张力、速度和测试方案待确认。")
    st.dataframe([{
        "来源组别": group["source_group_label"],
        "机制": mechanisms.get(group["stitch_mechanism"], group["stitch_mechanism"]),
        "针距 p（mm）": "不适用" if group["pitch_mm"] is None else str(group["pitch_mm"]),
        "行距 s（mm）": "不适用" if group["row_spacing_mm"] is None else str(group["row_spacing_mm"]),
        "目的": group["purpose_zh"], "状态": "待实验方案", "已记录测量数": len(group["measurements"]),
    } for group in template["groups"]], hide_index=True, width="stretch")
    with st.expander("完整原始模板、适用条件与引用"):
        st.json(template)
    st.write("建议先进行低速穿刺与针线匹配筛查；明确其余条件后，再研究机制与p/s组合。七个组别不是七条独立实测样本。")
    with st.form("create_trial_plan"):
        title = st.text_input("待实验计划名称", key="plan_title")
        namespace = st.selectbox("计划数据空间", ["real", "demo"], format_func=lambda item: "研究计划（不含测量）" if item == "real" else "演示计划（独立空间）", key="plan_namespace")
        submitted = st.form_submit_button("保存七组待实验计划", key="save_trial_plan")
    if submitted:
        try:
            if not title.strip():
                raise ValueError("请填写计划名称。")
            plan = build_trial_plan(root, title.strip(), namespace=namespace)
            saved = store.save_plan(plan)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            st.error(f"计划未保存：{exc}")
        else:
            st.success("已保存待实验计划；没有创建运行、独立试样或测量结果。")
            st.text(f"计划 ID：{saved}")
    st.subheader("已有计划与设备条件审查")
    view_namespace = st.selectbox("查看计划空间", ["real", "demo"], key="plan_view_namespace")
    plans = store.list_plans(namespace=view_namespace)
    if plans:
        index = st.selectbox("选择已保存计划", list(range(len(plans))), format_func=lambda i: f"{plans[i]['title']} · {plans[i]['plan_id']}", key="plan_review_selection")
        plan = plans[index]
        st.json(plan, expanded=False)
        equipment = None
        applicable_fields = set()
        confirmations = {item["confirmation_id"]: item for item in db.list_parameter_confirmations(namespace=view_namespace)}
        if confirmations:
            chosen = st.selectbox("计划适配参考的人工确认限制", list(confirmations), index=None, placeholder="不自动选用", format_func=lambda key: f"{confirmations[key]['equipment_name']} · {confirmations[key]['field']}", key="plan_equipment_confirmation")
            if chosen:
                st.json(confirmations[chosen], expanded=False)
                if st.checkbox("确认此限制适用于该计划的材料、机构和测量条件", key=f"plan_limit_applies_{plan['plan_id']}_{chosen}"):
                    equipment = _selected_confirmation(confirmations[chosen])
                    applicable_fields = {confirmations[chosen]["field"]}
        report = review_plan(plan, equipment=equipment, applicable_limit_fields=applicable_fields)
        st.write(f"审查状态：{report.status.value}；计划仍为待实验方案。")
        st.dataframe([{"状态": item.status.value, "检查": item.message} for item in report.findings], hide_index=True, width="stretch")
        with st.expander("完整审查依据与来源"):
            st.json(_plain(report), expanded=False)
        st.caption("仅离线条件审查。缺设备能力为未知，超出适用确认限制则阻断；此处没有批准执行或设备写入。")
    else:
        st.info("本空间尚无已保存计划。上方来源模板保留，不自动写成实验。")
    with st.expander("下载待实验清单与空白录入模板"):
        _downloads(root)


def _import_data(store: ExperimentStore, namespace: str, root: Path) -> None:
    st.write("一行对应一条测量记录。设定值使用 `*_setting_*` 字段；测量值独立填写 `metric_name / value / unit / measurement_stage`。没有测量时 value 留空并说明原因。")
    with st.expander("导入字段说明与空白模板"):
        _downloads(root)
        st.json(ObservationRow.model_json_schema(), expanded=False)
    uploaded = st.file_uploader("选择本地 CSV 或 Excel 文件", type=["csv", "xlsx"], max_upload_size=20, key="experiment_upload")
    if uploaded is None:
        st.session_state.pop("experiment_preview", None)
        st.info("尚未选择文件。上传只在本地预览，点击确认前不写入数据库。")
        return
    data = uploaded.getvalue()
    name = uploaded.name
    encoding = st.selectbox("CSV编码（不猜测）", ["utf-8-sig", "gb18030"], key="experiment_encoding") if name.lower().endswith(".csv") else "utf-8-sig"
    try:
        if name.lower().endswith(".xlsx"):
            sheets = list_xlsx_sheets(data, name)
            sheet = st.selectbox("选择Excel数据工作表", sheets, key="experiment_sheet")
            table = read_tabular(data, name, sheet_name=sheet)
        else:
            table = read_tabular(data, name, encoding=encoding)
    except (ValueError, OSError) as exc:
        st.session_state.pop("experiment_preview", None)
        st.error(f"文件未导入：{exc}")
        return
    st.caption(f"原文件 SHA-256：{table.file_sha256}；数据行数：{len(table.rows)}；原件字节在确认导入时只读保存。")
    st.subheader("原始内容预览")
    st.dataframe(table.rows[:50], hide_index=True, width="stretch")
    st.subheader("字段映射")
    st.caption("同名字段可按名称匹配。其他列必须明确映射或选择忽略；单位不会自动猜测或换算。")
    fields = list(CANONICAL_FIELDS)
    mapping = {}
    options = ["__unmapped__", "__ignore__", *fields]
    for header in table.headers:
        mapped = st.selectbox(header, options,
                              index=options.index(header) if header in fields else 0,
                              format_func=lambda item: {"__unmapped__": "尚未映射", "__ignore__": "明确忽略此列"}.get(item, item),
                              key=f"map_{table.file_sha256}_{table.sheet_name}_{header}")
        if mapped != "__unmapped__":
            mapping[header] = mapped
    fingerprint = sha256(json.dumps({"file": table.file_sha256, "sheet": table.sheet_name, "encoding": encoding, "mapping": mapping, "namespace": namespace}, sort_keys=True).encode()).hexdigest()
    previous = st.session_state.get("experiment_preview")
    if previous and previous["selection"] != fingerprint:
        st.session_state.pop("experiment_preview", None)
    if st.button("检查映射并生成导入预览", key="preview_experiment_import"):
        try:
            preview = preview_import(table, mapping, namespace=namespace, store=store)
            st.session_state["experiment_preview"] = {"selection": fingerprint, "preview": preview}
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            st.session_state.pop("experiment_preview", None)
            st.error(f"预览失败，数据库未修改：{exc}")
    current = st.session_state.get("experiment_preview")
    if not current:
        return
    preview = current["preview"]
    st.subheader("校验与规范化预览")
    if preview.issues:
        st.dataframe([_plain(issue) for issue in preview.issues], hide_index=True, width="stretch")
    if preview.duplicates:
        st.write("已识别的重复项")
        st.json(preview.duplicates, expanded=False)
    if preview.rows:
        st.dataframe([_plain(row) for row in preview.rows[:50]], hide_index=True, width="stretch")
    st.json(preview.metadata, expanded=False)
    if not preview.can_commit:
        st.error("当前预览不能提交。请修正错误；不会部分导入正确行。")
        return
    st.info("预览可提交。提交时再次校验、整批事务写入；同标识内容冲突会拒绝并回滚。")
    acknowledged = st.checkbox("已核对来源类别、映射、单位及缺失项，确认整批导入", key=f"ack_import_{fingerprint}")
    if st.button("确认导入本地数据库", disabled=not acknowledged, key="commit_experiment_import"):
        try:
            result = commit_preview(preview, store)
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            st.error(f"导入未完成，整批事务已回滚：{exc}")
        else:
            st.success("本地导入已完成；重复内容不会新增测量。")
            st.json(_plain(result))
        st.session_state.pop("experiment_preview", None)


def render_data_management(db, root: Path) -> None:
    st.header("数据管理")
    store = ExperimentStore(db)
    namespace = st.selectbox("实验数据空间", ["real", "demo"], format_func=lambda value: "研究来源（按来源类别分别统计）" if value == "real" else "演示数据（独立隔离）", key="experiment_namespace")
    page = st.radio("实验数据入口", ["来源统计", "导入预览", "关联记录"], horizontal=True, key="experiment_data_page")
    if page == "导入预览":
        _import_data(store, namespace, root)
    elif page == "关联记录":
        provenance = st.selectbox("记录来源类别", list(PROVENANCE_LABELS), format_func=PROVENANCE_LABELS.get, key="experiment_provenance")
        rows = store.list_measurements(namespace=namespace, provenance=provenance)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.info("所选空间与来源类别暂无记录。")
        with st.expander("共享父预制体、运行、卷材及对照依赖"):
            st.json(store.dependency_groups(namespace=namespace, provenance=provenance))
            st.caption("这是后续分组验证的依赖信息；没有训练模型，也不把文献均值扩增成独立试样。")
        st.caption("下方关联实体显示本空间全部来源类别；每条记录保留 provenance，未按上方测量类别筛选合并。")
        entities = {"source": "数据来源", "material": "材料与批次", "parent_preform": "父预制体", "equipment": "设备标识（非确认能力）", "run": "运行与设定", "specimen": "试样", "control": "对照关系", "raw_file": "原始导入文件", "import_batch": "导入与映射版本", "import_rows": "原文件行与测量关联"}
        entity = st.selectbox("查看关联实体", list(entities), format_func=entities.get, key="experiment_entity")
        records = store.list_entities(entity, namespace=namespace)
        if records:
            st.json(records, expanded=False)
        else:
            st.info("所选实体尚无记录。")
    else:
        counts = store.counts(namespace=namespace)
        labels = {"sources": "数据来源", "materials": "材料", "parent_preforms": "父预制体", "equipment": "设备标识", "runs": "运行记录", "specimens": "试样", "controls": "对照关系", "measurements": "测量记录（含待测空值）", "labeled_measurements": "已有数值的记录（各来源合计）", "import_batches": "导入版本", "raw_files": "原始文件", "plans": "待实验计划", **PROVENANCE_LABELS}
        st.dataframe([{"对象或来源": labels.get(key, key), "实际记录数": value} for key, value in counts.items()], hide_index=True, width="stretch")
        st.caption("实验实测、文献实测、仿真、演示和预测分别统计。空测量不计作有效实测标签；七组待实验计划不计作实验结果。")
    st.subheader("模型与约束优化状态")
    st.code("预测：not_ready\n约束优化：not_ready", language="text")
    st.write("尚未实现训练或优化；导入成功不代表数据已通过建模适用性验证。")
