"""Read local DOCX evidence without executing document text or relationships.

The DOCX hash identifies the actual bytes. An extracted-JSON fallback only declares
the old DOCX hash; its own bytes have a separate extraction_sha256. Neither reader
performs OCR, renders pages, follows external relationships, or infers measurements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_BLOCKS = 20000


class DocumentReadError(ValueError):
    """Evidence could not be safely and reliably read."""


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    block_id: str
    kind: str
    text: str
    section: str | None = None
    section_basis: str = "unknown"
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    cells: list[list[str]] = field(default_factory=list)
    image_count: int = 0
    unparsed_items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    document_id: str
    sha256: str
    expected_sha256: str
    byte_size: int
    filename: str
    source_path: str
    source_kind: str
    identity_status: str
    blocks: list[EvidenceBlock]
    limitations: list[str]
    image_count: int
    paragraph_count: int
    table_count: int
    extraction_sha256: str | None = None
    warnings: list[str] = field(default_factory=list)
    hash_basis: str = "actual_file"
    images_parsed: bool = False
    complete_content: bool = False
    image_count_basis: str = "body_drawing_elements"
    parser_version: str = "docx-xml-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_bytes(path: Path, limit: int) -> bytes:
    try:
        if path.stat().st_size > limit:
            raise DocumentReadError(f"文件超过读取大小限制：{path.name}")
        with path.open("rb") as handle:
            result = handle.read(limit + 1)
    except OSError as exc:
        raise DocumentReadError(f"无法读取文件：{path.name}（{exc.__class__.__name__}）") from exc
    if len(result) > limit:
        raise DocumentReadError(f"文件超过读取大小限制：{path.name}")
    return result


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DocumentReadError(f"JSON 存在重复字段：{key}")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    def invalid_constant(value: str) -> None:
        raise DocumentReadError(f"JSON 含非有限数值：{value}")

    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_json_object,
                          parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DocumentReadError("来源 JSON 无法解析") from exc


def _manifest(root: Path, document_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", document_id):
        raise DocumentReadError("非法文档 ID")
    records = _json(_read_bytes(root / "sources" / "manifest.json", MAX_JSON_BYTES))
    if not isinstance(records, list):
        raise DocumentReadError("来源清单必须是列表")
    matches = [entry for entry in records
               if isinstance(entry, dict) and entry.get("document_id") == document_id]
    if len(matches) != 1:
        raise DocumentReadError(f"来源清单没有唯一的文档：{document_id}")
    entry = matches[0]
    name = entry.get("original_filename")
    digest = entry.get("sha256")
    if (not isinstance(name, str) or not name or Path(name).name != name
            or "/" in name or "\\" in name):
        raise DocumentReadError("清单中的文件名必须是单个本地文件名")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise DocumentReadError("清单缺少有效 SHA-256")
    return entry


def _xml(raw: bytes, part_name: str) -> ET.Element:
    # ElementTree never follows external relationships. Explicitly reject DTDs
    # and entities as well, including UTF-16/32 XML with interleaved NUL bytes.
    security_scan = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in security_scan or b"<!ENTITY" in security_scan:
        raise DocumentReadError(f"拒绝含 DTD 或实体声明的 XML：{part_name}")
    try:
        return ET.fromstring(raw)
    except (ET.ParseError, RecursionError) as exc:
        raise DocumentReadError(f"DOCX XML 无法解析：{part_name}") from exc


def _part(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise DocumentReadError(f"DOCX XML 部件超过大小限制：{name}")
    with archive.open(info) as handle:
        raw = handle.read(MAX_XML_BYTES + 1)
    if len(raw) > MAX_XML_BYTES:
        raise DocumentReadError(f"DOCX XML 部件超过大小限制：{name}")
    return raw


def _validate_archive(archive: zipfile.ZipFile) -> set[str]:
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        raise DocumentReadError("DOCX ZIP 项目数量超过限制")
    names: set[str] = set()
    total = 0
    for entry in entries:
        name = entry.filename
        path = PurePosixPath(name)
        if (name in names or path.is_absolute() or ".." in path.parts
                or "\\" in entry.orig_filename or "\x00" in entry.orig_filename or ":" in name):
            raise DocumentReadError("DOCX ZIP 含重复或不安全的部件路径")
        names.add(name)
        if entry.flag_bits & 1:
            raise DocumentReadError("不支持加密 DOCX")
        total += entry.file_size
        if total > MAX_EXPANDED_BYTES:
            raise DocumentReadError("DOCX ZIP 展开大小超过限制")
        if entry.file_size > MAX_XML_BYTES and name.lower().endswith((".xml", ".rels")):
            raise DocumentReadError("DOCX XML 部件超过大小限制")
    if "word/document.xml" not in names:
        raise DocumentReadError("DOCX 缺少 word/document.xml")
    return names


def _paragraph_text(paragraph: ET.Element) -> str:
    fragments: list[str] = []

    def visit(node: ET.Element) -> None:
        # These are unsupported/hidden or non-body containers, not paragraphs
        # that the reader can safely present as ordinary source sentences.
        if node.tag in {W + "drawing", W + "pict", W + "object", W + "txbxContent",
                        W + "del", W + "moveFrom", MC + "AlternateContent"}:
            return
        if node.tag == W + "t":
            fragments.append(node.text or "")
        elif node.tag == W + "tab":
            fragments.append("\t")
        elif node.tag in {W + "br", W + "cr"}:
            if node.get(W + "type") not in {"page", "column"}:
                fragments.append("\n")
        elif node.tag == W + "noBreakHyphen":
            fragments.append("‑")
        else:
            for child in node:
                visit(child)

    visit(paragraph)
    return "".join(fragments)


def _unsupported(node: ET.Element) -> tuple[int, list[str]]:
    tags = [item.tag for item in node.iter()]
    image_count = tags.count(W + "drawing") + tags.count(W + "pict")
    items: list[str] = []
    if image_count:
        items.append(f"{image_count} 个图片/绘图对象未解析；图中文字、尺寸与图形需人工核验")
    labels = {
        W + "txbxContent": "文本框内容未解析",
        W + "object": "嵌入对象未解析",
        W + "altChunk": "外部格式正文块未解析",
        W + "del": "修订删除内容未纳入正文",
        W + "moveFrom": "修订移出内容未纳入正文",
        W + "ins": "修订插入文字按可见文本读取，修订状态未核验",
        W + "fldChar": "域字段仅保留已有显示文本；未计算或更新域",
        W + "fldSimple": "域字段仅保留已有显示文本；未计算或更新域",
        W + "instrText": "域指令未执行或解释",
        MC + "AlternateContent": "兼容性替代内容未解析",
        W + "footnoteReference": "脚注正文未解析",
        W + "endnoteReference": "尾注正文未解析",
    }
    for tag, label in labels.items():
        if tag in tags:
            items.append(label)
    return image_count, items


class _Sections:
    def __init__(self) -> None:
        self.entries: list[tuple[int, str, str]] = []
        self.chinese_major = False

    def add(self, text: str, outline_level: int | None = None) -> None:
        heading = text.strip()
        if not heading or len(heading) > 180 or "\n" in heading:
            return
        level: int | None = None
        basis = "inferred_numbered_heading"
        if outline_level is not None:
            level = outline_level + 1
            basis = "explicit_outline_style"
        elif re.match(r"^[一二三四五六七八九十百]+[、．.]", heading):
            level = 1
            self.chinese_major = True
        elif match := re.match(r"^(\d+(?:\.\d+)+)(?!\d)", heading):
            level = len(match[1].split("."))
        elif re.match(r"^\d+[.．、]\s*\D", heading):
            level = 2 if self.chinese_major else 1
        # Do not retain parenthesized list headings as section parents: some
        # adjoining list headings use Word automatic numbering, which is not
        # reconstructed here. Keeping only a manually numbered sibling would
        # falsely attribute subsequent evidence to that earlier subsection.
        if level is not None:
            self.entries = [item for item in self.entries if item[0] < level]
            self.entries.append((level, heading, basis))

    @property
    def value(self) -> str | None:
        return " / ".join(item[1] for item in self.entries) or None

    @property
    def basis(self) -> str:
        return self.entries[-1][2] if self.entries else "unknown"


def _outline_styles(archive: zipfile.ZipFile, names: set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    if "word/styles.xml" not in names:
        return result
    styles = _xml(_part(archive, "word/styles.xml"), "word/styles.xml")
    for style in styles.findall(W + "style"):
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        style_id = style.get(W + "styleId")
        if outline is not None and style_id:
            value = outline.get(W + "val", "")
            if value.isdigit() and 0 <= int(value) <= 8:
                result[style_id] = int(value)
    return result


def _outline_level(paragraph: ET.Element, styles: dict[str, int]) -> int | None:
    direct = paragraph.find(f"{W}pPr/{W}outlineLvl")
    if direct is not None:
        value = direct.get(W + "val", "")
        if value.isdigit() and 0 <= int(value) <= 8:
            return int(value)
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    return styles.get(style.get(W + "val", "")) if style is not None else None


def _parse_docx(root: Path, document_id: str, path: Path,
                manifest: dict[str, Any]) -> DocumentSnapshot:
    if path.suffix.lower() != ".docx":
        raise DocumentReadError("当前只支持 DOCX 正文/表格；PDF 与图片 OCR 尚未实现")
    raw = _read_bytes(path, MAX_FILE_BYTES)
    actual_hash = hashlib.sha256(raw).hexdigest()
    limitations = [
        "只读取 DOCX 主文档的正文段落和表格；不执行文档中的文字、域、宏或外部关系。",
        "图片、浮动绘图、图中文字和工程尺寸未解析，必须人工核验；没有进行 OCR。",
        "章节由大纲样式或编号标题定位，编号识别属于推断；DOCX 页码未渲染核验。",
        "未重建 Word 自动编号子标题；章节仅为可识别的上级标题，精确位置以段落/表格行 ID 为准。",
        "文本读取不等于实验验证；资料数值仍是来源声明。",
    ]
    warnings: list[str] = []
    blocks: list[EvidenceBlock] = []
    paragraphs = tables = 0
    try:
        # Read the same byte sequence used for the hash (no file change race).
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = _validate_archive(archive)
            document = _xml(_part(archive, "word/document.xml"), "word/document.xml")
            body = document.find(W + "body")
            if body is None:
                raise DocumentReadError("DOCX 缺少主文档正文")
            styles = _outline_styles(archive, names)
            sections = _Sections()
            for node in body:
                if node.tag == W + "p":
                    paragraphs += 1
                    text = _paragraph_text(node)
                    sections.add(text, _outline_level(node, styles))
                    count, missing = _unsupported(node)
                    blocks.append(EvidenceBlock(
                        block_id=f"{document_id}:p{paragraphs:04d}", kind="paragraph",
                        text=text, section=sections.value, section_basis=sections.basis,
                        paragraph_index=paragraphs, image_count=count, unparsed_items=missing,
                    ))
                elif node.tag == W + "tbl":
                    tables += 1
                    rows: list[list[str]] = []
                    row_blocks: list[EvidenceBlock] = []
                    for row_number, row in enumerate(node.findall(W + "tr"), 1):
                        cells = ["\n".join(_paragraph_text(p) for p in cell.findall(W + "p"))
                                 for cell in row.findall(W + "tc")]
                        rows.append(cells)
                        count, missing = _unsupported(row)
                        if row.findall(f".//{W}tbl"):
                            missing.append("嵌套表格未展开；当前行只含直接单元格段落")
                        row_blocks.append(EvidenceBlock(
                            block_id=f"{document_id}:t{tables:03d}:r{row_number:03d}",
                            kind="table_row", text="\t".join(cells),
                            section=sections.value, section_basis=sections.basis,
                            table_index=tables, row_index=row_number, cells=[cells],
                            image_count=count, unparsed_items=missing,
                        ))
                    count, missing = _unsupported(node)
                    if node.findall(f".//{W}tbl"):
                        missing.append("嵌套表格未展开")
                    if node.findall(f".//{W}gridSpan") or node.findall(f".//{W}vMerge"):
                        missing.append("表格含合并单元格；cells 保留物理 XML 单元格顺序，未填补合并副本")
                    blocks.append(EvidenceBlock(
                        block_id=f"{document_id}:t{tables:03d}", kind="table",
                        text="\n".join("\t".join(row) for row in rows),
                        section=sections.value, section_basis=sections.basis,
                        table_index=tables, cells=rows, image_count=count, unparsed_items=missing,
                    ))
                    blocks.extend(row_blocks)
                elif node.tag != W + "sectPr":
                    warnings.append(f"主文档非段落/表格块未解析：{node.tag.rsplit('}', 1)[-1]}")
                if len(blocks) > MAX_BLOCKS:
                    raise DocumentReadError("DOCX 正文块数量超过限制")
            image_count, unsupported = _unsupported(body)
            limitations.extend(unsupported)
            auxiliary = [name for name in names if re.fullmatch(
                r"word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name)]
            if auxiliary:
                limitations.append("未解析页眉/页脚/脚注/尾注/批注部件：" + "、".join(sorted(auxiliary)))
            external_count = 0
            for name in sorted(names):
                if name.endswith(".rels"):
                    relationships = _xml(_part(archive, name), name)
                    external_count += sum(rel.get("TargetMode") == "External"
                                          for rel in relationships.findall(REL + "Relationship"))
            if external_count:
                limitations.append(f"发现 {external_count} 个外部关系；只标记存在，不读取目标、不发网络请求")
            if any("vbaProject" in name or name.startswith("word/embeddings/") for name in names):
                limitations.append("存在宏/嵌入文件；未读取、执行或解包到文件系统")
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, RecursionError) as exc:
        raise DocumentReadError(f"DOCX 结构无法可靠读取：{path.name}") from exc
    expected = manifest["sha256"]
    if actual_hash != expected:
        warnings.append("实际原件 SHA-256 与来源清单不一致；作为独立文件版本保留，不能改写旧引用或认定图片一致")
    return DocumentSnapshot(
        document_id=document_id, sha256=actual_hash, expected_sha256=expected,
        byte_size=len(raw), filename=path.name, source_path=str(path.resolve()),
        source_kind="original_docx", identity_status="matched" if actual_hash == expected else "mismatch",
        blocks=blocks, limitations=list(dict.fromkeys(limitations)), image_count=image_count,
        paragraph_count=paragraphs, table_count=tables, warnings=warnings,
    )


def load_extracted_document(root: Path, document_id: str) -> DocumentSnapshot:
    """Read packaged text as limited evidence, never as a verified original file."""
    root = Path(root).resolve()
    manifest = _manifest(root, document_id)
    path = root / "sources" / "extracted" / f"{document_id}_text_blocks.json"
    raw = _read_bytes(path, MAX_JSON_BYTES)
    data = _json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict):
        raise DocumentReadError("抽取 JSON 缺少 metadata")
    metadata = data["metadata"]
    if (metadata.get("document_id") != document_id
            or metadata.get("sha256") != manifest["sha256"]):
        raise DocumentReadError("抽取 JSON 声明的文档身份与清单不一致")
    items = data.get("blocks")
    if not isinstance(items, list) or len(items) > MAX_BLOCKS:
        raise DocumentReadError("抽取 JSON 的 blocks 无效或过大")
    seen: set[str] = set()
    sections = _Sections()
    blocks: list[EvidenceBlock] = []
    paragraphs = tables = 0
    for item in items:
        if not isinstance(item, dict):
            raise DocumentReadError("抽取 JSON 正文块必须是对象")
        block_id = item.get("block_id")
        if not isinstance(block_id, str) or block_id in seen:
            raise DocumentReadError("抽取 JSON 的块 ID 缺失或重复")
        seen.add(block_id)
        if item.get("type") == "paragraph":
            match = re.fullmatch(re.escape(document_id) + r":p(\d{4,})", block_id)
            text = item.get("text")
            if not match or not isinstance(text, str):
                raise DocumentReadError("抽取 JSON 段落无有效定位或原句")
            paragraphs += 1
            sections.add(text)
            blocks.append(EvidenceBlock(
                block_id=block_id, kind="paragraph", text=text,
                section=sections.value, section_basis=sections.basis,
                paragraph_index=int(match[1]),
            ))
        elif item.get("type") == "table":
            match = re.fullmatch(re.escape(document_id) + r":t(\d{3,})", block_id)
            rows = item.get("rows")
            if (not match or not isinstance(rows, list)
                    or not all(isinstance(row, list) and all(isinstance(c, str) for c in row) for row in rows)):
                raise DocumentReadError("抽取 JSON 表格无有效定位或单元格")
            tables += 1
            table_number = int(match[1])
            blocks.append(EvidenceBlock(
                block_id=block_id, kind="table", text="\n".join("\t".join(row) for row in rows),
                section=sections.value, section_basis=sections.basis, table_index=table_number,
                cells=rows,
            ))
            for row_number, row in enumerate(rows, 1):
                blocks.append(EvidenceBlock(
                    block_id=f"{block_id}:r{row_number:03d}", kind="table_row", text="\t".join(row),
                    section=sections.value, section_basis=sections.basis,
                    table_index=table_number, row_index=row_number, cells=[row],
                ))
        else:
            raise DocumentReadError("抽取 JSON 含不支持的正文块类型")
        if len(blocks) > MAX_BLOCKS:
            raise DocumentReadError("抽取 JSON 正文块数量超过限制")
    byte_size = metadata.get("byte_size")
    image_count = metadata.get("inline_shape_count", 0)
    if (not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0
            or not isinstance(image_count, int) or isinstance(image_count, bool) or image_count < 0):
        raise DocumentReadError("抽取 JSON 大小/图片计数无效")
    limitations = [
        "仅使用打包提取 JSON 后备；原 DOCX 文件身份未由此次读取验证。",
        "sha256 是 JSON/清单声明的原文件哈希；extraction_sha256 才是此次读取的 JSON 实际哈希。",
        "JSON 不含图片/图形/工程尺寸，也不能定位图片所在段落；图片计数仅为打包元数据声明。",
        "原抽取省略空段落；保留原 block_id，但 paragraph_count 仅统计后备中的已记录段落。",
        "章节由编号标题推断；rendered_page_hint 不是此次验证的页码。",
        "资料数值仅为证据陈述，不是实测值、设备硬限或程序指令。",
    ]
    return DocumentSnapshot(
        document_id=document_id, sha256=manifest["sha256"], expected_sha256=manifest["sha256"],
        byte_size=byte_size, filename=manifest["original_filename"], source_path=str(path),
        source_kind="extracted_json", identity_status="declared_only", blocks=blocks,
        limitations=limitations, image_count=image_count, paragraph_count=paragraphs,
        table_count=tables, extraction_sha256=hashlib.sha256(raw).hexdigest(),
        warnings=["提取 JSON 后备不能证明原件文件身份或图片已经核验"],
        hash_basis="declared_original_hash", image_count_basis="packaged_metadata_claim",
    )


def load_document(root: Path, document_id: str, path: Path | None = None) -> DocumentSnapshot:
    """Prefer local originals; use packaged JSON only when no original exists.

    An explicitly supplied missing/unreadable original fails visibly. A malformed
    automatically found original also fails, rather than being silently hidden by
    an older JSON version. No source document or manifest is modified.
    """
    root = Path(root).resolve()
    manifest = _manifest(root, document_id)
    if path is not None:
        return _parse_docx(root, document_id, Path(path).resolve(), manifest)
    name = manifest["original_filename"]
    simple_name = re.sub(r"[（(]2[）)](?=\.docx$)", "", name, flags=re.IGNORECASE)
    candidates = [folder / candidate
                  for folder in (root / "sources" / "originals", root.parent / "碳纤维")
                  for candidate in dict.fromkeys((name, simple_name))]
    for candidate in candidates:
        if candidate.is_file():
            return _parse_docx(root, document_id, candidate, manifest)
    return load_extracted_document(root, document_id)


def load_documents(root: Path) -> list[DocumentSnapshot]:
    """Read the two user-designated sources, with no network or API dependency."""
    return [load_document(root, document_id) for document_id in ("D1", "D2")]
