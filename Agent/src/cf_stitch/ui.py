"""Chinese offline research workspace; claims never become measurements."""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import ValidationError
import streamlit as st

from cf_stitch.config import load_settings
from cf_stitch.domain import ResearchTask
from cf_stitch.knowledge.seeds import SeedValidationError
from cf_stitch.experiments_ui import render_experiment_design, render_data_management
from cf_stitch.knowledge_ui import render_knowledge
from cf_stitch.rules_ui import render_rules
from cf_stitch.storage.database import Database, DatabaseVersionError, SeedConflictError


SCENARIOS = {
    "general_research": "通用研发",
    "v_splice": "V 形拼接",
    "j_beam": "J 型梁 R 角",
    "dual_layer_fixation": "双层叠层固定",
}
MECHANISMS = {
    "lockstitch": "锁式",
    "chainstitch": "链式",
    "tufting": "Tufting（簇绒）",
    "blind_stitch": "盲缝",
    "custom_unconfirmed": "无底线自锁（自定义待确认）",
    "unstitched": "未缝合对照",
}
PATHS = {
    "unspecified": "待选择",
    "straight": "直线",
    "parallel": "平行路径",
    "herringbone": "人字形",
    "custom": "自定义路径（待补充）",
}
PLATFORMS = {
    "unspecified": "待选择",
    "manual": "手动",
    "fixed_head": "固定缝合头",
    "gantry": "龙门平台",
    "robot": "机器人",
}
OBJECTIVE_DIRECTIONS = {"maximize": "提高", "minimize": "降低", "target": "达到目标值（阈值待补充）", "observe": "观察记录"}
ROLE_LABELS = {
    "design_variable": "研究变量",
    "context": "材料 / 构型背景",
    "measurement": "待实测字段",
    "equipment_claim": "设备来源声明",
    "equipment_setting": "设备设置来源声明",
    "quality_target": "设计目标",
}


def _numeric_input(raw: str, unit: str, not_applicable: bool = False) -> dict[str, Any]:
    """Keep blank values missing; never drop input for an unstitched task."""
    if not_applicable and raw.strip():
        raise ValueError("未缝合对照的针距 p 和行距 s 不适用。请清空这两个输入后保存；系统不会丢弃已填值。")
    value = None
    if raw.strip():
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise ValueError("工艺参数请输入数字，未知值请留空。") from exc
    return {
        "value": value,
        "unit": unit,
        "measurement_stage": "not_applicable" if not_applicable else "planned",
        "value_kind": "setting",
        "provenance": "user_provided",
        "source_note": "用户在本地任务工作台录入；尚未审查的候选设定",
        "missing_reason": ("not_applicable" if not_applicable else "not_provided") if value is None else None,
    }


def _task_workbench(db: Database) -> None:
    st.header("任务工作台")
    st.write("先记录研发任务与候选设定。可保存缺参草案；保存不代表参数通过审查或设备可执行。")
    st.caption("机器人属于运动平台，人字形属于路径图案。三者独立记录。")
    with st.form("new_task", clear_on_submit=False):
        title = st.text_input("任务名称（必填）", key="task_title", max_chars=200)
        scenario = st.selectbox("工程场景", list(SCENARIOS), format_func=SCENARIOS.get, key="task_scenario")
        left, right = st.columns(2)
        with left:
            material_system = st.text_input("材料体系（未知可留空）", key="task_material")
        with right:
            configuration = st.text_input("构型描述（未知可留空）", key="task_configuration")
        with st.expander("厚度与测量条件（可选，独立于针深和抬脚）"):
            thickness = st.text_input("当前压实厚度（mm）", key="task_thickness")
            thickness_condition = st.text_input("厚度状态 / 测量条件", key="task_thickness_condition")
            st.caption("作为用户提供的材料背景保存；录入数值不会建立实验测量记录。未知厚度和测量条件请分别留空。")
        with st.expander("研究目标（可选，不是测量结果）"):
            objective_name = st.text_input("目标指标名称", key="task_objective")
            objective_direction = st.selectbox("目标方向", list(OBJECTIVE_DIRECTIONS), index=None, placeholder="请选择目标方向", format_func=OBJECTIVE_DIRECTIONS.get, key="task_objective_direction")
            objective_unit = st.text_input("目标指标单位", key="task_objective_unit")
            st.caption("如记录目标，请同时填写名称、方向和单位。目标阈值与试验方法本阶段保留待补充。")
        left, middle, right = st.columns(3)
        with left:
            mechanism = st.selectbox("缝合机制", list(MECHANISMS), index=None, placeholder="请选择缝合机制", format_func=MECHANISMS.get, key="task_mechanism")
        with middle:
            path_pattern = st.selectbox("路径图案", list(PATHS), format_func=PATHS.get, key="task_path")
        with right:
            platform = st.selectbox("运动平台", list(PLATFORMS), format_func=PLATFORMS.get, key="task_platform")
        st.caption("“无底线自锁”按原名保存为 custom_unconfirmed；未确认机构前不自动归类为常规锁式。")
        st.subheader("候选工艺设定")
        st.caption("以下均为用户录入的待审查设定，不是实测结果。没有依据的值请留空；空值与原因一并保存。")
        left, right = st.columns(2)
        with left:
            pitch = st.text_input("沿路径针距 p（mm）", key="task_pitch")
            tension = st.text_input("供纱张力设定（N）", key="task_tension")
            frequency = st.text_input("针频设定（针/min）", key="task_frequency")
        with right:
            spacing = st.text_input("相邻行距 s（mm）", key="task_spacing")
            diameter = st.text_input("针径（mm）", key="task_diameter")
        with st.expander("设备适配相关候选设定（可选）"):
            left, right = st.columns(2)
            with left:
                mechanical_spacing = st.text_input("机械针位间距（mm，不是 p / s）", key="task_mechanical_spacing")
                needle_depth = st.text_input("针刺深度（mm）", key="task_needle_depth")
                upper_web_tension = st.text_input("上层布面输送张力（N）", key="task_upper_web_tension")
            with right:
                foot_lift = st.text_input("压脚抬升高度（mm）", key="task_foot_lift")
                line_speed = st.text_input("线速度（m/min）", key="task_line_speed")
                lower_web_tension = st.text_input("下层布面输送张力（N）", key="task_lower_web_tension")
            st.caption("布面输送张力与供纱张力分开；针频与线速度分开。抬脚高度不能证明可稳定缝合厚度。")
        st.caption("未缝合对照的 p / s 必须留空。缺失的材料、构型及目标将保留待补充状态。")
        submitted = st.form_submit_button("保存任务草案", type="primary")
    if submitted:
        try:
            if not title.strip():
                raise ValueError("任务名称不能为空。")
            if mechanism is None:
                raise ValueError("请明确选择缝合机制。其他材料背景和工艺数值可留空，系统不会替你指定线迹。")
            objectives = []
            if objective_name.strip() or objective_direction is not None or objective_unit.strip():
                if not objective_name.strip() or objective_direction is None or not objective_unit.strip():
                    raise ValueError("目标信息尚未填完整：请同时提供指标名称、方向和单位，或清空全部目标字段。")
                objectives.append({"metric_name": objective_name.strip(), "direction": objective_direction, "unit": objective_unit.strip(), "missing_reason": "目标阈值尚未提供"})
            task = ResearchTask.model_validate({
                "title": title.strip(),
                "scenario": scenario,
                "material": {
                    "material_system": material_system.strip() or None,
                    "compacted_thickness_mm": {**_numeric_input(thickness, "mm"), "value_kind": "context", "source_note": "用户在本地录入的材料厚度背景；未建立实测记录"},
                    "thickness_condition": thickness_condition.strip() or None,
                    "missing_reasons": {"material_system": "not_provided"} if not material_system.strip() else {},
                },
                "geometry": {"configuration": configuration.strip() or None, "missing_reasons": {"configuration": "not_provided"} if not configuration.strip() else {}},
                "mechanism": {"kind": mechanism, "raw_mechanism_name": "无底线自锁" if mechanism == "custom_unconfirmed" else None},
                "path_pattern": path_pattern,
                "platform": platform,
                "objectives": objectives,
                "parameters": {
                    "pitch_mm": _numeric_input(pitch, "mm", mechanism == "unstitched"),
                    "row_spacing_mm": _numeric_input(spacing, "mm", mechanism == "unstitched"),
                    "yarn_feed_tension_N": _numeric_input(tension, "N"),
                    "needle_diameter_mm": _numeric_input(diameter, "mm"),
                    "stitch_frequency_spm": _numeric_input(frequency, "stitches/min"),
                    "mechanical_needle_spacing_mm": _numeric_input(mechanical_spacing, "mm"),
                    "needle_depth_mm": _numeric_input(needle_depth, "mm"),
                    "foot_lift_mm": _numeric_input(foot_lift, "mm"),
                    "line_speed_m_min": _numeric_input(line_speed, "m/min"),
                    "upper_fabric_web_tension_N": _numeric_input(upper_web_tension, "N"),
                    "lower_fabric_web_tension_N": _numeric_input(lower_web_tension, "N"),
                },
            })
            task_id = db.save_task(task)
        except ValidationError as exc:
            st.error("草案未保存：字段校验不通过。针距、行距和针径若填写必须大于 0；工艺值须为有限数字。")
            for error in exc.errors(include_url=False, include_context=False, include_input=False):
                st.text(f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}")
        except ValueError as exc:
            st.error(str(exc))
        except (OSError, sqlite3.DatabaseError) as exc:
            st.error("草案未保存：无法写入本地数据库。请检查数据目录权限、磁盘空间及数据库文件。")
            st.text(str(exc))
            st.stop()
        else:
            st.success("任务草案已保存到本地 SQLite。")
            st.text(f"任务 ID：{task_id}")
            st.info("当前仅建立草案；可到“三场景与审查”查看规则与设备适配结果，保存不批准上机。")
            for missing in task.missing_context():
                st.text(f"待补充：{missing}")
    st.divider()
    st.subheader("已保存的研发任务")
    tasks = db.list_tasks(namespace="real")
    if not tasks:
        st.info("尚无任务。填写上方表单即可建立第一个本地草案。")
        return
    st.dataframe([
        {"任务名称": task["title"], "场景": SCENARIOS.get(task["scenario"], task["scenario"]), "状态": "草案", "创建时间": task["created_at"], "任务 ID": task["task_id"]}
        for task in tasks
    ], hide_index=True, width="stretch")
    selected = st.selectbox("查看任务详情", list(range(len(tasks))), format_func=lambda index: f"{tasks[index]['title']} · {tasks[index]['task_id'][:8]}", key="saved_task")
    st.json(tasks[selected], expanded=False)


def _settings(settings: Any, db: Database) -> None:
    st.header("设置")
    st.write("CF-Stitch Agent · 阶段 4：实验关联、导入预览与待实验计划")
    st.success("基础功能为离线模式，无需 API Key；当前未接入 LLM、云端服务或真实设备控制。")
    for label, value in [("项目根目录", settings.project_root), ("数据目录", settings.data_dir), ("SQLite 文件", settings.db_path), ("默认监听地址", f"http://{settings.host}:{settings.port}"), ("数据库结构版本", db.schema_version())]:
        st.text(f"{label}：{value}")
    st.caption("界面不会读取或显示 API 密钥。默认本地访问配置由项目启动脚本使用。")
    st.subheader("当前可用范围")
    st.write("建立任务草案、读取DOCX和检索来源、参数词典、人工确认、三场景与计算审查、七组待实验计划、CSV/XLSX导入预览和关联实验记录。模型训练、优化与LLM编排等待后续阶段。")


def run() -> None:
    st.set_page_config(page_title="CF-Stitch Agent", page_icon="🧵", layout="wide")
    try:
        settings = load_settings()
        db = Database(settings.db_path)
        db.initialize()
    except (OSError, ValueError, DatabaseVersionError, sqlite3.DatabaseError) as exc:
        st.title("CF-Stitch Agent")
        st.error("本地工作区初始化失败，应用尚未就绪。请检查 CF_STITCH_DATA_DIR 配置、目录读写权限，以及 SQLite 数据库和来源种子的版本一致性。")
        st.text(str(exc))
        st.info("请保留现有数据库和来源文件供核查；配置说明见本项目 README。修正原因后重新启动，程序不会用空库替换现有文件。")
        st.stop()
    st.title("CF-Stitch Agent")
    st.caption("碳纤维预制体缝合工艺研发助手 · 本地离线 · 阶段 4")
    try:
        db.import_seed_bundle(settings.project_root)
    except (OSError, SeedValidationError, SeedConflictError) as exc:
        st.warning("来源种子本次未导入：文件缺失、校验失败或与已存版本不同。已有任务和参数声明保留；可继续建草案，但当前来源需核查。")
        st.text(str(exc))
    except sqlite3.DatabaseError as exc:
        st.error("来源写入本地数据库失败；请保留数据库并核查存储状态。")
        st.text(str(exc))
        st.stop()
    st.sidebar.title("研发工作区")
    page = st.sidebar.radio("功能入口", ["任务工作台", "资料参数", "三场景与审查", "实验设计", "数据管理", "设置"], key="navigation")
    st.sidebar.caption("来源声明 ≠ 实测结果\n\n任务草案 ≠ 已批准上机方案")
    actual_counts = db.counts(namespace="real")
    st.sidebar.caption(f"真实实验：{actual_counts['experiments']} · 已训练模型：{actual_counts['trained_models']}")
    if page == "任务工作台":
        _task_workbench(db)
    elif page == "资料参数":
        render_knowledge(db, settings.project_root, SCENARIOS, ROLE_LABELS)
    elif page == "三场景与审查":
        render_rules(db, settings.project_root, SCENARIOS, MECHANISMS, PATHS)
    elif page == "实验设计":
        render_experiment_design(db, settings.project_root, MECHANISMS)
    elif page == "数据管理":
        render_data_management(db, settings.project_root)
    else:
        _settings(settings, db)
