"""Read packaged declarations without interpreting them as measured capabilities.

Hashes describe the files in this handover package. They do not establish the
identity or engineering validity of an original DOCX held outside the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

import yaml


class SeedValidationError(ValueError):
    """A source package is malformed or silently changes source semantics."""


SOURCE_EVIDENCE_KINDS = frozenset({
    "initial_trial_window", "descriptive_ranges", "example_values",
    "qualitative_guidance", "must_measure", "design_parameter",
    "design_specification", "design_target", "design_requirement",
})


class _UniqueSafeLoader(yaml.SafeLoader):
    """Do not let duplicate YAML keys silently overwrite a source declaration."""


def _unique_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise SeedValidationError("种子 YAML 的键必须是字符串。")
        if key in result:
            raise SeedValidationError(f"种子 YAML 含重复键：{key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SeedValidationError("种子只能包含有效 JSON 数据，不能包含 NaN 或日期对象。") from exc


def _read_yaml(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    try:
        payload = yaml.load(raw.decode("utf-8-sig"), Loader=_UniqueSafeLoader)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise SeedValidationError(f"无法读取种子：{path.name}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise SeedValidationError(f"不支持的种子结构版本：{path.name}")
    _json(payload)
    return payload, raw


def load_parameter_seed(project_root: Path) -> dict:
    """Return a fresh original YAML payload; do not derive values or limits."""
    payload, _ = _read_yaml(Path(project_root) / "spec" / "domain_parameters.yaml")
    policy = payload.get("policy")
    required_policy = (
        "all_records_are_real_measurements", "default_promote_to_hard_limits",
        "default_execute_on_equipment", "research_window_is_safety_guarantee",
        "unknown_is_zero", "source_images_transcribed",
    )
    if not isinstance(policy, dict) or any(policy.get(key) is not False for key in required_policy):
        raise SeedValidationError("参数种子必须保持来源声明、非实测、非设备硬限语义。")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise SeedValidationError("参数种子缺少声明列表。")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise SeedValidationError("每条参数声明必须是对象。")
        for key in ("record_id", "field", "label_zh", "unit", "role", "scope", "evidence_kind"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise SeedValidationError(f"参数声明缺少文本字段 {key}。")
        if record["record_id"] in seen:
            raise SeedValidationError(f"参数声明编号重复：{record['record_id']}")
        seen.add(record["record_id"])
        if record.get("verification_status") != "source_only" or record.get("enforcement") != "advisory":
            raise SeedValidationError("种子不能自动升级为实测、确认能力或设备硬限。")
        if record["evidence_kind"] not in SOURCE_EVIDENCE_KINDS:
            raise SeedValidationError("参数种子只能包含来源声明类别，不能包含实测值或确认设备硬限。")
        if not isinstance(record.get("source_refs"), list) or not record["source_refs"]:
            raise SeedValidationError("参数声明缺少来源。")
    return payload


def load_experiment_template(project_root: Path) -> dict:
    """Read plans for display only. This function never creates experiments."""
    payload, _ = _read_yaml(Path(project_root) / "spec" / "experiment_templates.yaml")
    if not isinstance(payload.get("groups"), list) or not payload["groups"]:
        raise SeedValidationError("试验模板缺少方案组。")
    for group in payload["groups"]:
        if not isinstance(group, dict) or group.get("measurements") != [] or group.get("status") != "planned":
            raise SeedValidationError("只读模板必须是待实验方案，且不含测量结果。")
    return payload


@dataclass(frozen=True)
class SeedBundle:
    payload: dict
    sources: list[dict]
    raw_yaml: str
    raw_manifest: str
    identity: dict


def load_seed_bundle(project_root: Path) -> SeedBundle:
    """Check package consistency, not the authenticity of adjacent originals."""
    root = Path(project_root)
    payload = load_parameter_seed(root)
    parameter_raw = (root / "spec" / "domain_parameters.yaml").read_bytes()
    # Reparse the exact byte snapshot being hashed; changed-on-read files fail.
    if yaml.load(parameter_raw.decode("utf-8-sig"), Loader=_UniqueSafeLoader) != payload:
        raise SeedValidationError("读取过程中参数种子发生变化，请重试。")
    manifest_raw = (root / "sources" / "manifest.json").read_bytes()
    try:
        sources = json.loads(manifest_raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SeedValidationError("来源清单不是有效 JSON。") from exc
    if not isinstance(sources, list) or not sources:
        raise SeedValidationError("来源清单必须是非空列表。")
    source_map = {}
    extracted_hashes = {}
    block_ids = {}
    for source in sources:
        if not isinstance(source, dict):
            raise SeedValidationError("来源清单条目必须是对象。")
        document_id = source.get("document_id")
        source_hash = source.get("sha256")
        if not isinstance(document_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
            raise SeedValidationError("来源编号无效。")
        if document_id in source_map:
            raise SeedValidationError(f"来源编号重复：{document_id}")
        if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise SeedValidationError(f"来源哈希无效：{document_id}")
        source_map[document_id] = source_hash
        extracted_path = root / "sources" / "extracted" / f"{document_id}_text_blocks.json"
        extracted_raw = extracted_path.read_bytes()
        try:
            extracted = json.loads(extracted_raw.decode("utf-8-sig"))
            metadata = extracted["metadata"]
            if metadata["document_id"] != document_id or metadata["sha256"] != source_hash:
                raise SeedValidationError(f"清单与包内提取内容身份不同：{document_id}")
            block_ids[document_id] = {block["block_id"] for block in extracted["blocks"]}
        except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SeedValidationError(f"提取内容结构错误：{document_id}") from exc
        extracted_hashes[document_id] = sha256(extracted_raw).hexdigest()
    for record in payload["records"]:
        for ref in record["source_refs"]:
            if not isinstance(ref, dict):
                raise SeedValidationError("来源引用必须是对象。")
            document_id = ref.get("document_id")
            if document_id not in source_map or ref.get("file_sha256") != source_map[document_id]:
                raise SeedValidationError(f"声明来源与包内清单不一致：{record['record_id']}")
            if ref.get("block_id") not in block_ids[document_id]:
                raise SeedValidationError(f"包内找不到引用块：{record['record_id']}")
    _json(sources)
    identity = {
        "schema_version": payload["schema_version"],
        "parameter_sha256": sha256(parameter_raw).hexdigest(),
        "manifest_sha256": sha256(manifest_raw).hexdigest(),
        "extracted_sha256": extracted_hashes,
        "verification_scope": "packaged_manifest_and_extracted_only",
    }
    return SeedBundle(payload, sources, parameter_raw.decode("utf-8-sig"), manifest_raw.decode("utf-8-sig"), identity)
