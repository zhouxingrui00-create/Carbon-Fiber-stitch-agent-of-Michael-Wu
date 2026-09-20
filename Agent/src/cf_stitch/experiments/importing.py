"""Offline tabular ingestion: source bytes -> explicit mapping -> review -> transaction.

Only static CSV and XLSX cells are accepted. Nothing in a file is evaluated, and
numeric fields are not inferred from headings, formatting, or the value's unit.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from cf_stitch.experiments.models import ObservationRow, validate_rows


IGNORE_FIELD = "__ignore__"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ROWS = 20000
MAX_COLUMNS = 256
MAX_CELL_CHARS = 100000
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NUMERIC = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_CELL = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
_INTERNAL_FIELDS = {"namespace", "raw_row", "raw_file_sha256", "sheet_name", "row_number"}
CANONICAL_FIELDS = tuple(name for name in ObservationRow.model_fields if name not in _INTERNAL_FIELDS)
_FIELD_SCHEMAS = ObservationRow.model_json_schema().get("properties", {})
_NUMBER_TYPES = {name: next((option["type"] for option in [schema, *schema.get("anyOf", [])]
                             if option.get("type") in ("number", "integer")), None)
                 for name, schema in _FIELD_SCHEMAS.items()}


class TabularError(ValueError):
    """The file cannot be represented as a trustworthy static input table."""


class ImportPreviewError(ValueError):
    """An invalid, changed, or unreviewed import cannot be committed."""


@dataclass(frozen=True)
class TabularData:
    filename: str
    file_sha256: str
    headers: list[str]
    rows: list[dict[str, Any]]
    row_numbers: list[int]
    sheets: list[str]
    sheet_name: str | None
    encoding: str
    raw_bytes: bytes = field(repr=False)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportIssue:
    row: int | None
    field: str | None
    severity: str
    message: str


@dataclass(frozen=True)
class ImportPreview:
    rows: list[ObservationRow]
    issues: list[ImportIssue]
    duplicates: list[Any]
    fingerprint: str
    metadata: dict[str, Any]
    table: TabularData = field(repr=False)
    mapping: dict[str, str]
    namespace: str

    @property
    def can_commit(self) -> bool:
        return bool(self.rows) and not any(issue.severity == "error" for issue in self.issues)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _nonempty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _table(filename: str, data: bytes, grid: list[tuple[int, list[Any]]], *,
           sheets: list[str], sheet_name: str | None, encoding: str,
           warnings: list[str] | None = None) -> TabularData:
    populated = [(number, cells) for number, cells in grid if any(_nonempty(v) for v in cells)]
    if not populated:
        raise TabularError("文件没有表头；空白模板应至少保留字段表头。")
    _, raw_headers = populated[0]
    headers: list[str] = []
    for value in raw_headers:
        if not isinstance(value, str) or not value.strip():
            raise TabularError("表头必须是非空文本；不允许空列名。")
        if value != value.strip():
            raise TabularError("表头存在首尾空白，请显式清理后再导入。")
        if len(value) > MAX_CELL_CHARS:
            raise TabularError("表头过长。")
        headers.append(value)
    if len(set(headers)) != len(headers):
        raise TabularError("表头重复，无法进行唯一字段映射。")
    if len(headers) > MAX_COLUMNS:
        raise TabularError(f"最多支持 {MAX_COLUMNS} 列。")
    rows, row_numbers = [], []
    for number, cells in populated[1:]:
        if len(cells) > len(headers):
            raise TabularError(f"第 {number} 行包含超出表头的单元格，不能静默丢弃。")
        values = cells + [None] * (len(headers) - len(cells))
        if any(isinstance(v, str) and len(v) > MAX_CELL_CHARS for v in values):
            raise TabularError(f"第 {number} 行单元格过长。")
        rows.append(dict(zip(headers, values)))
        row_numbers.append(number)
    if len(rows) > MAX_ROWS:
        raise TabularError(f"单次最多导入 {MAX_ROWS} 行。")
    return TabularData(filename, hashlib.sha256(data).hexdigest(), headers, rows,
                       row_numbers, sheets, sheet_name, encoding, data, warnings or [])


def _xml(data: bytes, name: str) -> ET.Element:
    # Removing NULs detects both UTF-8 and UTF-16/32 declarations before parsing.
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise TabularError(f"XML 实体或 DTD 不允许：{name}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise TabularError(f"XML 格式损坏：{name}") from exc


def _column_index(letters: str) -> int:
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def _xlsx(data: bytes, filename: str, sheet_name: str | None, *,
          list_only: bool = False) -> TabularData | list[str]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TabularError("XLSX 不是有效的 ZIP 工作簿。") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 1000 or sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
            raise TabularError("XLSX 展开大小或部件数量超限。")
        names = [item.filename for item in infos]
        if len(set(names)) != len(names):
            raise TabularError("XLSX 存在重复 ZIP 部件。")
        roots: dict[str, ET.Element] = {}
        for item in infos:
            name = item.filename
            lower = name.lower()
            if (name.startswith(("/", "\\")) or "\\" in name or
                    ".." in PurePosixPath(name).parts or ":" in name):
                raise TabularError("XLSX 部件路径非法。")
            if item.flag_bits & 1 or (item.file_size > 1000000 and
                    item.file_size / max(item.compress_size, 1) > 1000):
                raise TabularError("XLSX 含加密部件或异常压缩比。")
            if any(marker in lower for marker in ("vba", "activex", "externallinks/", "embeddings/")):
                raise TabularError("不支持宏、嵌入对象或外部链接。请提供静态 XLSX。")
            if lower.endswith((".xml", ".rels")):
                try:
                    member_bytes = archive.read(item)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                    raise TabularError(f"XLSX 部件损坏或压缩方法不支持：{name}") from exc
                root = _xml(member_bytes, name)
                roots[name] = root
                if lower.endswith(".rels"):
                    for relation in root:
                        if relation.get("TargetMode", "").lower() == "external":
                            raise TabularError("XLSX 含外部关系，不会访问外部文件或网络。")
                if root.find(f".//{_NS}f") is not None:
                    raise TabularError("工作簿含公式；缓存值不能冒充测量。请另存为静态值后重新预览。")
                if root.find(f".//{_NS}mergeCell") is not None:
                    raise TabularError("工作簿含合并单元格；请显式展开为普通表格，避免伪造重复值。")
        workbook = roots.get("xl/workbook.xml")
        relations = roots.get("xl/_rels/workbook.xml.rels")
        if workbook is None or relations is None:
            raise TabularError("缺少 XLSX 工作簿或工作表关系。")
        if "[Content_Types].xml" in roots:
            for content in roots["[Content_Types].xml"]:
                if any(word in content.get("ContentType", "").lower()
                       for word in ("macroenabled", "vba", "macrosheet")):
                    raise TabularError("不支持含宏工作簿。")
        targets = {rel.get("Id"): rel.get("Target") for rel in relations}
        sheet_elements = workbook.findall(f"{_NS}sheets/{_NS}sheet")
        sheets = [sheet.get("name", "") for sheet in sheet_elements]
        if not sheets or any(not name for name in sheets) or len(set(sheets)) != len(sheets):
            raise TabularError("工作表名称缺失或重复。")
        if list_only:
            return sheets
        selected = sheet_name if sheet_name is not None else sheets[0]
        if selected not in sheets:
            raise TabularError(f"工作表不存在：{selected}")
        target = targets.get(sheet_elements[sheets.index(selected)].get(f"{_REL}id"))
        if not target or ":" in target or "\\" in target:
            raise TabularError("工作表关系缺失或路径非法。")
        part = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        if not part.startswith("xl/worksheets/") or part not in roots:
            raise TabularError("只支持本工作簿内的普通工作表。")
        shared = roots.get("xl/sharedStrings.xml")
        strings = [] if shared is None else ["".join(t.text or "" for t in item.iter(f"{_NS}t"))
                                            for item in shared.findall(f"{_NS}si")]
        grid = []
        seen_rows = set()
        for row in roots[part].findall(f"{_NS}sheetData/{_NS}row"):
            try:
                number = int(row.get("r", ""))
            except ValueError as exc:
                raise TabularError("工作表行号非法。") from exc
            if number < 1 or number > 1048576 or number in seen_rows:
                raise TabularError("工作表行号重复或越界。")
            seen_rows.add(number)
            cells: dict[int, Any] = {}
            for cell in row.findall(f"{_NS}c"):
                address = cell.get("r", "")
                match = _CELL.fullmatch(address)
                if match is None or int(match[2]) != number:
                    raise TabularError(f"单元格地址非法：{address}")
                column = _column_index(match[1])
                if column >= MAX_COLUMNS or column in cells:
                    raise TabularError("单元格列越界或重复。")
                kind = cell.get("t", "n")
                element = cell.find(f"{_NS}v")
                value = None if element is None else element.text
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(f"{_NS}t"))
                elif kind == "s":
                    try:
                        index = int(value or "")
                        if index < 0:
                            raise ValueError
                        value = strings[index]
                    except (ValueError, IndexError) as exc:
                        raise TabularError(f"共享字符串索引非法：{address}") from exc
                elif kind == "b":
                    if value not in ("0", "1"):
                        raise TabularError(f"布尔值非法：{address}")
                    value = value == "1"
                elif kind == "e":
                    raise TabularError(f"单元格含 Excel 错误：{address} {value}")
                elif kind not in ("n", "str", "d"):
                    raise TabularError(f"不支持的单元格类型：{address} {kind}")
                cells[column] = value
            last = max(cells, default=-1)
            # Excel may persist trailing styled blank cells. They carry no data.
            while last >= 0 and not _nonempty(cells.get(last)):
                last -= 1
            grid.append((number, [cells.get(index) for index in range(last + 1)]))
            if len(grid) > MAX_ROWS + 1:
                raise TabularError(f"单次最多解析 {MAX_ROWS} 条记录。")
        grid.sort(key=lambda item: item[0])
        warnings = ["Excel 仅读取存储的单元格值；不重算公式、不按显示格式推断单位或日期。"]
        if len(sheets) > 1:
            warnings.append(f"本次只读取工作表 {selected}；其他工作表未作为数据导入。")
        return _table(filename, data, grid, sheets=sheets, sheet_name=selected,
                      encoding="xlsx-static-xml", warnings=warnings)


def _validate_file(data: bytes, filename: str) -> str:
    if not isinstance(data, bytes) or not data or len(data) > MAX_FILE_BYTES:
        raise TabularError("文件为空、类型非法或超过 20 MiB。")
    if not isinstance(filename, str) or not filename or any(char in filename for char in ("/", "\\", "\x00")):
        raise TabularError("请提供不含路径的原始文件名。")
    return PurePosixPath(filename).suffix.lower()


def list_xlsx_sheets(data: bytes, filename: str) -> list[str]:
    """List sheets after workbook security checks, without assuming sheet 1 is data."""
    if _validate_file(data, filename) != ".xlsx":
        raise TabularError("仅 XLSX 文件具有可选择的工作表。")
    result = _xlsx(data, filename, None, list_only=True)
    assert isinstance(result, list)
    return result


def read_tabular(data: bytes, filename: str, *, encoding: str = "utf-8-sig",
                 sheet_name: str | None = None) -> TabularData:
    """Parse bytes without executing formulas or silently guessing CSV encoding."""
    suffix = _validate_file(data, filename)
    if suffix == ".xlsx":
        result = _xlsx(data, filename, sheet_name)
        assert isinstance(result, TabularData)
        return result
    if suffix != ".csv":
        raise TabularError("仅支持 .csv 与静态 .xlsx；不支持 .xls/.xlsm。")
    if sheet_name is not None:
        raise TabularError("CSV 没有工作表。")
    if encoding not in ("utf-8-sig", "utf-8", "gb18030"):
        raise TabularError("CSV 编码仅支持显式选择 UTF-8 或 GB18030。")
    try:
        content = data.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise TabularError("CSV 编码不匹配，请显式选择正确编码。") from exc
    if "\x00" in content:
        raise TabularError("CSV 包含 NUL，不能作为文本表格读取。")
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    grid = []
    try:
        previous_line = 0
        for cells in reader:
            grid.append((previous_line + 1, cells))
            previous_line = reader.line_num
            if len(grid) > MAX_ROWS + 1:
                raise TabularError(f"单次最多解析 {MAX_ROWS} 条记录。")
    except csv.Error as exc:
        raise TabularError(f"CSV 格式错误：{exc}") from exc
    return _table(filename, data, grid, sheets=[], sheet_name=None, encoding=encoding)


def _convert(value: Any, target: str) -> Any:
    if not _nonempty(value):
        return None
    if _NUMBER_TYPES.get(target):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("数字字段不能使用布尔或复合值。")
        if isinstance(value, str) and _NUMERIC.fullmatch(value.strip()) is None:
            raise ValueError("需要明确的有限数字；不自动去单位、填补缺值或解释 NA。")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("数字必须有限，不能使用 NaN/Infinity。")
        if _NUMBER_TYPES[target] == "integer":
            if not numeric.is_integer() or abs(numeric) > 2**53:
                raise ValueError("计数必须是可精确表示的整数。")
            return int(numeric)
        return numeric
    return value


def _fingerprint(table: TabularData, mapping: dict[str, str], namespace: str,
                 rows: list[ObservationRow]) -> str:
    payload = {"file_sha256": table.file_sha256, "filename": table.filename,
               "encoding": table.encoding, "sheet_name": table.sheet_name,
               "mapping": mapping, "namespace": namespace,
               "rows": [row.model_dump(mode="json") for row in rows]}
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def preview_import(table: TabularData, mapping: dict[str, str], *, namespace: str = "real",
                   store: Any = None) -> ImportPreview:
    """Validate explicit mappings, record provenance, and check store relations read-only."""
    issues = [ImportIssue(None, None, "warning", message) for message in table.warnings]
    rows: list[ObservationRow] = []
    accepted_row_numbers: list[int] = []
    duplicates: list[Any] = []
    if namespace not in ("real", "demo"):
        issues.append(ImportIssue(None, "namespace", "error", "只能选择 real 或 demo 命名空间。"))
    if not isinstance(mapping, dict):
        raise ImportPreviewError("字段映射必须是显式字典。")
    targets = [target for target in mapping.values() if target != IGNORE_FIELD]
    if len(targets) != len(set(targets)):
        issues.append(ImportIssue(None, None, "error", "多个源列不能映射到同一个规范字段。"))
    for header, target in mapping.items():
        if header not in table.headers:
            issues.append(ImportIssue(None, header, "error", "映射列不在当前文件中。"))
        if target != IGNORE_FIELD and target not in CANONICAL_FIELDS:
            issues.append(ImportIssue(None, header, "error", "未知规范字段，不能推测其含义。"))
    for header in table.headers:
        has_data = any(_nonempty(row.get(header)) for row in table.rows)
        if header not in mapping and has_data:
            issues.append(ImportIssue(None, header, "error", "非空列尚未映射；如需舍弃请显式选择忽略。"))
        elif mapping.get(header) == IGNORE_FIELD and has_data:
            issues.append(ImportIssue(None, header, "warning", "该非空列已显式忽略；原始文件仍保留。"))
    if not table.rows:
        issues.append(ImportIssue(None, None, "warning", "当前为仅含表头的空白模板，没有测量记录可导入。"))
    mapping_valid = not any(issue.severity == "error" for issue in issues)
    if mapping_valid:
        for number, raw in zip(table.row_numbers, table.rows):
            payload: dict[str, Any] = {"namespace": namespace}
            invalid = False
            for source, target in mapping.items():
                if target == IGNORE_FIELD:
                    continue
                try:
                    payload[target] = _convert(raw.get(source), target)
                except ValueError as exc:
                    invalid = True
                    issues.append(ImportIssue(number, target, "error", str(exc)))
            if invalid:
                continue
            for field_name, value in (("raw_row", raw), ("raw_file_sha256", table.file_sha256),
                                      ("sheet_name", table.sheet_name), ("row_number", number)):
                if field_name in ObservationRow.model_fields:
                    payload[field_name] = value
            try:
                rows.append(ObservationRow.model_validate(payload))
                accepted_row_numbers.append(number)
            except ValidationError as exc:
                for error in exc.errors(include_url=False, include_input=False):
                    issues.append(ImportIssue(number, ".".join(map(str, error["loc"])) or None,
                                              "error", error["msg"]))
    if rows:
        result = store.preview_rows(rows, namespace=namespace) if store is not None else validate_rows(rows, namespace)
        duplicates = result.get("duplicates", [])
        for severity, key in (("error", "errors"), ("warning", "warnings")):
            for issue in result.get(key, []):
                if isinstance(issue, str):
                    issues.append(ImportIssue(None, None, severity, issue))
                else:
                    index = issue.get("row")
                    source_number = accepted_row_numbers[index - 1] if isinstance(index, int) and 1 <= index <= len(accepted_row_numbers) else None
                    issues.append(ImportIssue(source_number, issue.get("field"), severity, issue["message"]))
    if store is None:
        seen = set()
        for row in rows:
            identifier = row.measurement_id
            if identifier in seen:
                issues.append(ImportIssue(None, "measurement_id", "error", f"文件内记录 ID 重复：{identifier}"))
            seen.add(identifier)
    metadata = {"filename": table.filename, "file_sha256": table.file_sha256,
                "sheet_name": table.sheet_name, "encoding": table.encoding,
                "row_numbers": list(table.row_numbers), "original_rows": table.rows,
                "units_inferred": False, "measurements_generated": False,
                "mapping": dict(mapping), "namespace": namespace}
    return ImportPreview(rows, issues, duplicates, _fingerprint(table, mapping, namespace, rows),
                         metadata, table, dict(mapping), namespace)


def commit_preview(preview: ImportPreview, store: Any) -> dict[str, Any]:
    """Re-read source bytes and revalidate the preview before the store transaction."""
    if not isinstance(preview, ImportPreview) or not preview.can_commit:
        raise ImportPreviewError("必须先取得无阻断问题的非空预览。")
    current = read_tabular(preview.table.raw_bytes, preview.table.filename,
                           encoding=preview.table.encoding if preview.table.sheet_name is None else "utf-8-sig",
                           sheet_name=preview.table.sheet_name)
    fresh = preview_import(current, preview.mapping, namespace=preview.namespace, store=store)
    if (fresh.fingerprint != preview.fingerprint or
            _fingerprint(preview.table, preview.mapping, preview.namespace, preview.rows) != preview.fingerprint):
        raise ImportPreviewError("文件、映射、命名空间或预览内容已改变，请重新预览。")
    if not fresh.can_commit:
        raise ImportPreviewError("重新校验发现问题；请重新预览检查关系和重复记录。")
    return store.commit_import(fresh.rows, raw_bytes=current.raw_bytes,
                               filename=current.filename, mapping=preview.mapping,
                               sheet_name=current.sheet_name, namespace=preview.namespace,
                               row_numbers=current.row_numbers, encoding=current.encoding)


def blank_csv_template() -> bytes:
    """A header-only template; never synthesize rows, specimens, or measurements."""
    output = io.StringIO(newline="")
    csv.writer(output).writerow(CANONICAL_FIELDS)
    return output.getvalue().encode("utf-8-sig")
