"""Pure deterministic calculations; a computed number is never a measurement.

Each calculation retains its input, units, explicit assumptions and derivation.
These functions neither approve equipment nor read, write, or execute evidence.
"""

from __future__ import annotations

from decimal import Decimal, DecimalException, localcontext
import math
from numbers import Real
from typing import Any

from cf_stitch.domain.schemas import SourceReference
from cf_stitch.rules.results import RuleResult, Status


_D1_HASH = "f97ccd33c4afa780aa76ec441c5d8454e6229cc8b6addd4996f1b9812f04853b"
_D2_HASH = "b4e2b13767b3ca2c61fb07c751e3649b2d2043eb2ff62858bbdce62b483701c3"
_DENSITY_REF = SourceReference(
    document_id="D2", file_sha256=_D2_HASH,
    section="五、缝合密度与结构设计", block_id="D2:p0032",
    rendered_page_hint=7, excerpt="N_A = 10⁶ / (p × s)",
)
_J_REF = SourceReference(
    document_id="D1", file_sha256=_D1_HASH, section="9.3.3",
    block_id="D1:p0335", rendered_page_hint=63,
    excerpt="缝合针密：缝合处的针密可控，5~15针/cm，确保缝合部分的均匀平整。",
)


def _number(value: Any) -> Decimal | None:
    """Reject coercion (including bool), nonfinite values and oversized inputs."""
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return None
    try:
        as_float = float(value)
        if not math.isfinite(as_float):
            return None
        result = Decimal(str(value))
        if not result.is_finite() or (as_float == 0 and result != 0):
            return None
        return result
    except (ValueError, TypeError, OverflowError, DecimalException):
        return None


def _input(value: Any) -> Any:
    """Preserve invalid inputs as text rather than emit non-JSON NaN/Infinity."""
    if value is None or isinstance(value, (str, bool)):
        return value
    number = _number(value)
    if number is None:
        return repr(value)
    if isinstance(value, (int, float)):
        return value
    return str(value)


def _result(
    code: str, status: Status, message: str, *, value=None, unit=None,
    inputs=None, assumptions=None, source_refs=None, basis=None, **details,
) -> RuleResult:
    return RuleResult(
        code=code, status=status, message=message, value=value, unit=unit,
        provenance="formula_calculated",
        source_refs=[ref.model_copy(deep=True) for ref in source_refs or []],
        basis=basis or ["PROJECT_SPEC.md §6 数值计算"],
        assumptions=assumptions or [],
        details={"inputs": inputs or {}, "is_measurement": False,
                 "equipment_approval": False, **details},
    )


def _representable(value: Decimal) -> float | None:
    """Do not disguise numeric overflow/underflow as a usable infinity or zero."""
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(result) or (result == 0 and value != 0):
        return None
    return result


# Unit identity includes the counted object: mechanical cycles and stitches are
# distinct until their relationship is explicitly supplied. gf uses g0=9.80665.
_UNITS = {
    "mm": ("length", Decimal("1")),
    "cm": ("length", Decimal("10")),
    "m": ("length", Decimal("1000")),
    "um": ("length", Decimal("0.001")),
    "μm": ("length", Decimal("0.001")),
    "µm": ("length", Decimal("0.001")),
    "N": ("force", Decimal("1")),
    "cN": ("force", Decimal("0.01")),
    "mN": ("force", Decimal("0.001")),
    "gf": ("force", Decimal("0.00980665")),
    "Hz": ("cycle_frequency", Decimal("1")),
    "cycles/s": ("cycle_frequency", Decimal("1")),
    "cycles/min": ("cycle_frequency", Decimal(1) / Decimal(60)),
    "stitches/min": ("stitch_frequency", Decimal("1")),
    "针/min": ("stitch_frequency", Decimal("1")),
    "stitches/s": ("stitch_frequency", Decimal("60")),
    "针/s": ("stitch_frequency", Decimal("60")),
    "m/min": ("line_speed", Decimal("1")),
    "mm/min": ("line_speed", Decimal("0.001")),
    "mm/s": ("line_speed", Decimal("0.06")),
    "m/s": ("line_speed", Decimal("60")),
    "deg": ("angle", Decimal("1")),
    "%": ("ratio", Decimal("0.01")),
    "1": ("ratio", Decimal("1")),
    "stitches/cm": ("linear_stitch_density", Decimal("1")),
    "针/cm": ("linear_stitch_density", Decimal("1")),
    "stitches/mm": ("linear_stitch_density", Decimal("10")),
    "stitches/m": ("linear_stitch_density", Decimal("0.01")),
}


def convert_unit(
    value: float | None, from_unit: str, to_unit: str, *,
    stitches_per_cycle: float | None = None,
) -> RuleResult:
    """Convert only compatible units; never reinterpret the physical field."""
    inputs = {"value": _input(value), "from_unit": _input(from_unit),
              "to_unit": _input(to_unit), "stitches_per_cycle": _input(stitches_per_cycle)}
    common = dict(inputs=inputs, unit=to_unit if isinstance(to_unit, str) else None,
                  basis=["spec/domain_parameters.yaml unit_rules",
                         "SI 换算定义：1 min=60 s，1 cm=10 mm；gf 使用标准重力 9.80665 m/s²"],
                  field_semantics="只换单位，不改变针距/行距/机械间距或供纱/布面张力等字段身份")
    number = _number(value)
    if number is None:
        status = Status.UNKNOWN if value is None else Status.BLOCK
        return _result("unit_input", status, "数值缺失或不是有限数，不能换算。", **common)
    if not isinstance(from_unit, str) or not isinstance(to_unit, str) or from_unit not in _UNITS or to_unit not in _UNITS:
        return _result("unit_unsupported", Status.UNKNOWN, "单位未支持；保留原值，不猜测单位。", **common)
    source_dim, source_factor = _UNITS[from_unit]
    target_dim, target_factor = _UNITS[to_unit]
    assumptions = []
    with localcontext() as context:
        context.prec = 50
        converted = number * source_factor / target_factor
        if source_dim != target_dim:
            if {source_dim, target_dim} != {"cycle_frequency", "stitch_frequency"}:
                return _result("unit_dimension", Status.BLOCK, "物理量不同，不能直接换算；针频与线速度必须分别记录。", **common)
            per_cycle = _number(stitches_per_cycle)
            if stitches_per_cycle is None:
                return _result("unit_cycle_unknown", Status.UNKNOWN, "Hz 与针频换算需要明确每周期针数。", **common)
            if per_cycle is None or per_cycle <= 0:
                return _result("unit_cycle_invalid", Status.BLOCK, "每周期针数必须为有限正数。", **common)
            assumptions = [f"每周期 {per_cycle} 针，由调用者显式确认；此换算不确认设备能力"]
            converted = (converted * 60 * per_cycle if source_dim == "cycle_frequency"
                         else converted / (60 * per_cycle))
        result = _representable(converted)
    if result is None:
        return _result("unit_numeric_range", Status.BLOCK, "换算超出可表示的数值范围，不返回溢出或下溢结果。", assumptions=assumptions, **common)
    return _result("unit_converted", Status.PASS, "单位换算完成；来源性质与物理字段保持不变。", value=result, assumptions=assumptions, **common)


def rectangular_density(
    pitch_mm: float | None, row_spacing_mm: float | None, *,
    mechanism: str = "lockstitch", path_pattern: str = "parallel",
    regular_rectangular: bool = False, one_puncture_per_cell: bool = False,
    extra_punctures: bool | None = False,
) -> RuleResult:
    """Theoretical area density only for an explicitly confirmed simple array."""
    inputs = {"pitch_mm": _input(pitch_mm), "row_spacing_mm": _input(row_spacing_mm),
              "mechanism": _input(mechanism), "path_pattern": _input(path_pattern),
              "regular_rectangular": _input(regular_rectangular),
              "one_puncture_per_cell": _input(one_puncture_per_cell),
              "extra_punctures": _input(extra_punctures)}
    assumptions = ["规则矩形阵列", "每单元一个穿刺点", "不含额外回针、双针、锁固和有限边界效应",
                   "p 为沿路径针距，s 为相邻缝合行距；机械针位间距不得代入"]
    common = dict(inputs=inputs, unit="points/m^2", source_refs=[_DENSITY_REF],
                  assumptions=assumptions, formula="N_A = 10^6 / (p * s)")
    if mechanism == "unstitched":
        if pitch_mm is not None or row_spacing_mm is not None:
            return _result("density_unstitched_invalid", Status.BLOCK, "未缝合组 p/s 必须为空，不能用 0 或任意间距代入。", **common)
        common["assumptions"] = ["未缝合状态；p/s 不适用，不进行除法运算"]
        return _result("density_unstitched", Status.PASS, "未缝合组理论穿刺密度为 0；未执行 p/s 除法。", value=0, **common)
    pitch, spacing = _number(pitch_mm), _number(row_spacing_mm)
    if pitch_mm is None or row_spacing_mm is None:
        return _result("density_missing", Status.UNKNOWN, "针距或行距缺失，不能计算密度。", **common)
    if pitch is None or spacing is None or pitch <= 0 or spacing <= 0:
        return _result("density_invalid", Status.BLOCK, "针距和行距必须是有限正数；不允许除零。", **common)
    if mechanism not in ("lockstitch", "chainstitch", "tufting") or path_pattern not in ("straight", "parallel"):
        return _result("density_unsupported", Status.UNKNOWN, "复杂路径或未确认机构不支持该简化公式；需实际针位与面积。", **common)
    if extra_punctures is not False:
        return _result("density_extra_punctures", Status.UNKNOWN, "存在或尚未排除额外穿刺、双针、回针；需实际针位计算。", **common)
    if regular_rectangular is not True or one_puncture_per_cell is not True:
        return _result("density_assumptions", Status.UNKNOWN, "规则矩形阵列及每格一个穿刺点的假设尚未明确。", **common)
    with localcontext() as context:
        context.prec = 50
        value = _representable(Decimal(1_000_000) / (pitch * spacing))
    if value is None:
        return _result("density_numeric_range", Status.BLOCK, "密度超出可表示的数值范围，不返回溢出或下溢结果。", **common)
    return _result("density_calculated", Status.PASS, "已计算理论阵列密度；不代表有限试件针数或实测质量。", value=value, **common)


def ideal_line_speed(
    pitch_mm: float | None, frequency_spm: float | None, *,
    straight_synchronized: bool = False, pitch_is_feed_advance: bool = False,
    one_stitch_per_cycle: bool = False,
) -> RuleResult:
    """Ideal synchronized straight feed only; neither actual speed nor cycle time."""
    common = dict(
        inputs={"pitch_mm": _input(pitch_mm), "frequency_spm": _input(frequency_spm),
                "straight_synchronized": _input(straight_synchronized),
                "pitch_is_feed_advance": _input(pitch_is_feed_advance),
                "one_stitch_per_cycle": _input(one_stitch_per_cycle)},
        unit="m/min", formula="v = p * f / 1000",
        assumptions=["理想直线同步送料", "p 是送料方向实际每针前进量", "每周期一针，f 单位为针/min",
                     "不含摆动、曲线、回针、停顿及加减速；不代表设备实际节拍"],
    )
    pitch, frequency = _number(pitch_mm), _number(frequency_spm)
    if pitch_mm is None or frequency_spm is None:
        return _result("speed_missing", Status.UNKNOWN, "缺少针距或针频，不能计算。", **common)
    if pitch is None or frequency is None or pitch <= 0 or frequency <= 0:
        return _result("speed_invalid", Status.BLOCK, "针距与针频必须是有限正数。", **common)
    if any(flag is not True for flag in (straight_synchronized, pitch_is_feed_advance, one_stitch_per_cycle)):
        return _result("speed_assumptions", Status.UNKNOWN, "直线同步送料、实际送料针距和每周期一针尚未全部确认。", **common)
    with localcontext() as context:
        context.prec = 50
        value = _representable(pitch * frequency / 1000)
    if value is None:
        return _result("speed_numeric_range", Status.BLOCK, "理想速度超出可表示的数值范围。", **common)
    return _result("speed_calculated", Status.PASS, "理想直线送料速度计算完成；不是机器实测速度或可执行指令。", value=value, **common)


def matched_change(
    stitched_mean: float | None, control_mean: float | None, *,
    matched_control: bool = False, mode: str = "improvement", unit: str | None = None,
) -> RuleResult:
    """Calculate a relative change only after the caller confirms comparability."""
    common = dict(
        inputs={"stitched_mean": _input(stitched_mean), "control_mean": _input(control_mean),
                "matched_control": _input(matched_control), "mode": _input(mode),
                "original_unit": _input(unit)},
        unit="%", assumptions=["缝合组与对照组使用同一指标、单位、材料状态和测试方法",
                                "匹配对照由调用者明确确认；本函数不验证原始测量或独立性"],
        shared_control_note="共用对照和重复测量依赖必须在后续验证分组中保留",
    )
    if mode not in ("improvement", "loss"):
        return _result("change_mode", Status.BLOCK, "仅支持 improvement（提升率）或 loss（损失率）。", **common)
    if matched_control is not True:
        return _result("change_unmatched", Status.UNKNOWN, "对照尚未确认匹配，不输出提升率或损失率。", **common)
    stitched, control = _number(stitched_mean), _number(control_mean)
    if stitched_mean is None or control_mean is None:
        return _result("change_missing", Status.UNKNOWN, "组均值或对照均值缺失。", **common)
    if stitched is None or control is None or control == 0:
        return _result("change_invalid", Status.BLOCK, "均值必须有限且对照不能为零。", **common)
    with localcontext() as context:
        context.prec = 50
        change = (stitched - control) / control * 100
        value = _representable(-change if mode == "loss" else change)
    if value is None:
        return _result("change_numeric_range", Status.BLOCK, "相对变化超出可表示的数值范围。", **common)
    message = ("损失率已计算：正值表示损失，负值表示提高；未截断负值。" if mode == "loss"
               else "提升率已计算：正值表示提高，负值表示降低。")
    status = Status.PASS
    if control < 0:
        status = Status.WARN
        message = "已按带符号对照计算；对照为负，提升或损失的方向须由指标定义另行解释。"
    return _result("change_calculated", status, message, value=value,
                   formula=("(control - stitched) / control * 100" if mode == "loss"
                            else "(stitched - control) / control * 100"), **common)


def j_density_to_pitch(
    min_stitches_cm: float = 5, max_stitches_cm: float = 15, *,
    uniform_single_row: bool = False,
) -> RuleResult:
    """Conditional display for the unresolved D1 J-beam density requirement."""
    common = dict(
        inputs={"min_stitches_cm": _input(min_stitches_cm), "max_stitches_cm": _input(max_stitches_cm),
                "original_unit": "stitches/cm", "uniform_single_row": _input(uniform_single_row)},
        unit="mm", source_refs=[_J_REF],
        basis=["PROJECT_SPEC.md §5.2 J 型梁 R 角", "docs/SOURCE_AUDIT.md A. J型梁针密与通用针距"],
        assumptions=["沿同一计数方向的均匀单排针密", "p_mm = 10 / density_per_cm；倒数变换反转上下界"],
        formula="[p_min, p_max] = [10 / n_max, 10 / n_min]",
        review_status="pending", use_as_default=False,
        source_identity_note="引用旧提取 JSON 声明哈希；D1 当前原件哈希不同，未认定同版",
        source_range_stitches_cm=[5, 15],
        input_scope="来源原量" if min_stitches_cm == 5 and max_stitches_cm == 15 else "调用者输入；不是 D1 原范围",
    )
    lower, upper = _number(min_stitches_cm), _number(max_stitches_cm)
    if min_stitches_cm is None or max_stitches_cm is None:
        return _result("j_density_missing", Status.UNKNOWN, "针密范围缺失。", **common)
    if lower is None or upper is None or lower <= 0 or upper < lower:
        return _result("j_density_invalid", Status.BLOCK, "针密范围必须是有序有限正数。", **common)
    if uniform_single_row is not True:
        return _result("j_density_assumptions", Status.UNKNOWN, "未确认均匀单排及计数方向，不显示换算针距。", **common)
    with localcontext() as context:
        context.prec = 50
        minimum, maximum = _representable(Decimal(10) / upper), _representable(Decimal(10) / lower)
    if minimum is None or maximum is None:
        return _result("j_density_numeric_range", Status.BLOCK, "换算针距超出可表示的数值范围。", **common)
    return _result("j_density_pending", Status.WARN,
                   "仅在均匀单排假设下展示派生针距；定义仍待确认，不覆盖通用窗口或自动纳入优化。",
                   value=[minimum, maximum], **common)
