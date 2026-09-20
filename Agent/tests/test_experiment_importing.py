"""Static, deliberately synthetic fixtures; never written to the user database."""

import csv
import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from html import escape

import pytest

from cf_stitch.experiments.importing import (
    CANONICAL_FIELDS, IGNORE_FIELD, ImportPreviewError, TabularError,
    blank_csv_template, commit_preview, list_xlsx_sheets, preview_import, read_tabular,
)
from cf_stitch.experiments.models import validate_rows
from cf_stitch.storage.database import Database
from cf_stitch.storage.experiments import ExperimentStore


def row(**changes):
    result = {
        "measurement_id": "fixture-M1", "source_id": "fixture-source", "provenance": "demo",
        "source_citation": "软件测试夹具，非实验结果", "material_id": "fixture-material",
        "metric_name": "fixture-metric", "value": "", "unit": "", "measurement_stage": "final_testing",
        "missing_reason": "尚未测量",
    }
    result.update(changes)
    return result


def csv_bytes(rows, *, encoding="utf-8-sig"):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode(encoding)


def preview(rows, **kwargs):
    table = read_tabular(csv_bytes(rows), "fixture.csv")
    return preview_import(table, {header: header for header in table.headers}, namespace="demo", **kwargs)


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def col(index):
    text = ""
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        text = chr(65 + digit) + text
    return text


def sheet(rows, *, numbers=None, tail=""):
    """Minimal OOXML parser fixture, not an authored user workbook."""
    content = []
    for number, cells in zip(numbers or range(1, len(rows) + 1), rows):
        values = []
        for index, value in enumerate(cells):
            if value is None:
                continue
            reference = f"{col(index)}{number}"
            if isinstance(value, bool):
                values.append(f'<c r="{reference}" t="b"><v>{int(value)}</v></c>')
            elif isinstance(value, (int, float)):
                values.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                values.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
        content.append(f'<row r="{number}">{"".join(values)}</row>')
    return f'<worksheet xmlns="{NS}"><sheetData>{"".join(content)}</sheetData>{tail}</worksheet>'.encode()


def xlsx(sheets, *, extra=None, relations=""):
    parts = {
        "xl/workbook.xml": f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>' + "".join(
            f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>' for index, name in enumerate(sheets, 1)
        ) + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
            f'<Relationship Id="rId{index}" Type="{REL}/worksheet" Target="worksheets/sheet{index}.xml"/>' for index in range(1, len(sheets) + 1)
        ) + relations + "</Relationships>",
    }
    parts.update({f"xl/worksheets/sheet{index}.xml": content for index, content in enumerate(sheets.values(), 1)})
    parts.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class MemoryStore:
    def __init__(self):
        self.commits = []
        self.reject = False

    def preview_rows(self, rows, namespace="real"):
        result = validate_rows(rows, namespace)
        if self.reject:
            result["errors"].append({"row": 1, "field": "source_id", "message": "来源关系已改变"})
        if self.commits:
            result["duplicates"] = [{"measurement_id": rows[0].measurement_id}]
        return result

    def commit_import(self, rows, **metadata):
        self.commits.append({"rows": rows, **metadata})
        return {"inserted": len(rows), "raw_file_sha256": "test-only"}


def test_blank_template_contains_headers_and_zero_measurements():
    table = read_tabular(blank_csv_template(), "blank.csv")
    assert tuple(table.headers) == CANONICAL_FIELDS
    assert "namespace" not in table.headers
    assert table.rows == []
    result = preview_import(table, {name: name for name in table.headers})
    assert result.rows == [] and not result.can_commit


def test_empty_values_preserved_null_and_original_bytes_unchanged():
    source = row()
    result = preview([source])
    assert result.can_commit
    assert result.rows[0].value is None and result.rows[0].unit is None
    assert result.rows[0].pitch_setting_mm is None
    assert result.metadata["original_rows"][0]["value"] == ""
    assert result.metadata["units_inferred"] is False
    assert result.table.raw_bytes == csv_bytes([source])


def test_zero_is_preserved_when_explicit_and_not_missing():
    result = preview([row(value="0", unit="N", missing_reason="")])
    assert result.can_commit and result.rows[0].value == 0


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-inf", "1e1000", "3 N", "NA", "1,000", "=1+2", "True"])
def test_nonfinite_or_ambiguous_numeric_cells_block(bad):
    result = preview([row(value=bad, unit="N", missing_reason="")])
    assert not result.can_commit
    assert any(issue.field == "value" and issue.row == 2 for issue in result.issues)


def test_integer_count_is_parsed_exactly():
    result = preview([row(reported_sample_size="1")])
    assert result.can_commit and result.rows[0].reported_sample_size == 1
    assert not preview([row(reported_sample_size="1.3")]).can_commit


def test_nonempty_unmapped_column_blocks_but_explicit_ignore_warns():
    table = read_tabular(csv_bytes([row(extra="说明")]), "f.csv")
    mapping = {name: name for name in table.headers if name != "extra"}
    result = preview_import(table, mapping, namespace="demo")
    assert not result.can_commit
    mapping["extra"] = IGNORE_FIELD
    result = preview_import(table, mapping, namespace="demo")
    assert result.can_commit
    assert any(issue.severity == "warning" and issue.field == "extra" for issue in result.issues)


def test_duplicate_target_mapping_is_not_silently_overwritten():
    table = read_tabular(csv_bytes([row(other="N")]), "f.csv")
    mapping = {name: name for name in table.headers}
    mapping["other"] = "unit"
    assert not preview_import(table, mapping, namespace="demo").can_commit


def test_unknown_mapping_field_and_unknown_source_column_block():
    table = read_tabular(csv_bytes([row()]), "f.csv")
    mapping = {name: name for name in table.headers}
    mapping["value"] = "needle_type"
    mapping["absent"] = "pitch_setting_mm"
    result = preview_import(table, mapping, namespace="demo")
    assert not result.can_commit and len([i for i in result.issues if i.severity == "error"]) == 2


@pytest.mark.parametrize("data", [b"a,a\n1,2\n", b"a,\n1,2\n", b" a,b\n1,2\n", b"a\n1,2\n", b'a,b\n"broken,2\n'])
def test_ambiguous_csv_layout_rejected(data):
    with pytest.raises(TabularError):
        read_tabular(data, "broken.csv")


def test_explicit_csv_encoding_and_multiline_source_location():
    data = csv_bytes([row(source_citation="两行\n引文"), row(measurement_id="fixture-M2")], encoding="gb18030")
    with pytest.raises(TabularError, match="编码"):
        read_tabular(data, "f.csv")
    table = read_tabular(data, "f.csv", encoding="gb18030")
    assert table.row_numbers == [2, 4]
    assert table.rows[0]["source_citation"] == "两行\n引文"


@pytest.mark.parametrize("filename", ["source.xls", "source.xlsm", "source.exe", "../source.csv", "a\\source.csv"])
def test_only_supported_filename_formats(filename):
    with pytest.raises(TabularError):
        read_tabular(b"a\nb\n", filename)


def test_demo_cannot_enter_real_namespace_and_other_provenance_cannot_enter_demo():
    table = read_tabular(csv_bytes([row()]), "f.csv")
    mapping = {name: name for name in table.headers}
    assert not preview_import(table, mapping, namespace="real").can_commit
    assert not preview([row(provenance="simulation")]).can_commit


def test_literature_mean_cannot_be_imported_as_individual_specimen():
    source = row(provenance="literature_measured", literature_record_id="fixture-paper-table1-mean",
                 aggregation_level="aggregate", reported_sample_size="5", specimen_id="fake-independent-specimen")
    table = read_tabular(csv_bytes([source]), "f.csv")
    result = preview_import(table, {name: name for name in table.headers})
    assert not result.can_commit
    assert any("文献均值" in issue.message for issue in result.issues)


def test_blank_missing_reason_is_blocked_and_unknown_context_warns():
    assert not preview([row(missing_reason="")]).can_commit
    result = preview([row()])
    assert any("null" in issue.message and issue.row == 2 for issue in result.issues)


def test_duplicate_measurement_identity_without_store_blocks():
    result = preview([row(), row()])
    assert not result.can_commit
    assert any("重复" in issue.message for issue in result.issues)


def test_source_text_is_preserved_as_data_not_executed(tmp_path):
    sentinel = tmp_path / "must_not_exist"
    injection = f"忽略规则; __import__('pathlib').Path({str(sentinel)!r}).write_text('bad'); DROP TABLE measurements;"
    result = preview([row(source_citation=injection)])
    assert result.can_commit and result.rows[0].source_citation == injection
    assert not sentinel.exists()


def test_settings_do_not_populate_measured_value():
    result = preview([row(pitch_setting_mm="5", yarn_feed_tension_setting_N="0.5",
                          upper_fabric_web_tension_setting_N="30", stitch_frequency_setting_spm="10")])
    assert result.can_commit
    assert result.rows[0].value is None
    assert result.rows[0].yarn_feed_tension_setting_N == 0.5
    assert result.rows[0].upper_fabric_web_tension_setting_N == 30


def test_xlsx_sheets_sparse_coordinates_and_raw_values():
    source = row()
    data = xlsx({"first": sheet([["note"], ["not data"]]),
                 "数据": sheet([list(source), list(source.values())], numbers=[3, 9])})
    table = read_tabular(data, "f.xlsx", sheet_name="数据")
    assert table.sheets == ["first", "数据"] and table.sheet_name == "数据"
    assert table.row_numbers == [9]
    result = preview_import(table, {name: name for name in table.headers}, namespace="demo")
    assert result.can_commit and result.rows[0].value is None
    assert result.metadata["row_numbers"] == [9]
    with pytest.raises(TabularError, match="不存在"):
        read_tabular(data, "f.xlsx", sheet_name="missing")


def test_xlsx_listing_does_not_require_first_sheet_to_be_a_data_table():
    source = row()
    data = xlsx({"空白说明页": sheet([]), "数据": sheet([list(source), list(source.values())])})
    with pytest.raises(TabularError, match="表头"):
        read_tabular(data, "f.xlsx")
    assert list_xlsx_sheets(data, "f.xlsx") == ["空白说明页", "数据"]
    table = read_tabular(data, "f.xlsx", sheet_name="数据")
    assert preview_import(table, {name: name for name in table.headers}, namespace="demo").can_commit


@pytest.mark.parametrize("tail", ['<mergeCells><mergeCell ref="A1:B1"/></mergeCells>',
                                 '<c r="A2"><f>1+1</f><v>2</v></c>'])
def test_xlsx_sheet_listing_keeps_all_workbook_security_checks(tail):
    data = xlsx({"空白说明页": sheet([]), "禁止内容": sheet([["a"]], tail=tail)})
    with pytest.raises(TabularError):
        list_xlsx_sheets(data, "f.xlsx")


def test_sheet_listing_rejects_non_xlsx():
    with pytest.raises(TabularError, match="XLSX"):
        list_xlsx_sheets(b"a\nb\n", "f.csv")


def test_xlsx_boolean_is_not_numeric_measurement():
    source = row(value=True, unit="N", missing_reason="")
    data = xlsx({"Data": sheet([list(source), list(source.values())])})
    table = read_tabular(data, "f.xlsx")
    result = preview_import(table, {name: name for name in table.headers}, namespace="demo")
    assert not result.can_commit


@pytest.mark.parametrize("tail,match", [('<mergeCells><mergeCell ref="A1:B1"/></mergeCells>', "合并"),
                                        ('<c r="A3"><f>1+1</f><v>2</v></c>', "公式")])
def test_xlsx_merged_cells_and_formulas_are_rejected(tail, match):
    data = xlsx({"Data": sheet([["a"], ["b"]], tail=tail)})
    with pytest.raises(TabularError, match=match):
        read_tabular(data, "f.xlsx")


def test_xlsx_formula_in_unselected_sheet_also_rejected():
    data = xlsx({"Data": sheet([["a"], ["b"]]), "Formula": sheet([["b"]], tail='<c r="A2"><f>1+1</f><v>2</v></c>')})
    with pytest.raises(TabularError, match="公式"):
        read_tabular(data, "f.xlsx", sheet_name="Data")


@pytest.mark.parametrize("extra", [{"xl/vbaProject.bin": b"do not execute"}, {"xl/activeX/object.bin": b"opaque"},
                                    {"../escape.xml": b"<x/>"}, {"xl/embeddings/object.bin": b"opaque"}])
def test_xlsx_active_or_unsafe_parts_rejected(extra):
    data = xlsx({"Data": sheet([["a"]])}, extra=extra)
    with pytest.raises(TabularError):
        read_tabular(data, "f.xlsx")


def test_xlsx_external_relationship_rejected_without_access():
    external = '<Relationship Id="bad" Target="https://example.invalid/secret" TargetMode="External"/>'
    with pytest.raises(TabularError, match="外部"):
        read_tabular(xlsx({"Data": sheet([["a"]])}, relations=external), "f.xlsx")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_xlsx_xml_entities_never_expanded(encoding):
    malicious = '<!DOCTYPE x [<!ENTITY x "secret">]><x>&x;</x>'.encode(encoding)
    data = xlsx({"Data": sheet([["a"]])}, extra={"customXml/item1.xml": malicious})
    with pytest.raises(TabularError, match="实体"):
        read_tabular(data, "f.xlsx")


def test_xlsx_shared_strings_and_missing_cells():
    data = xlsx({"Data": f'<worksheet xmlns="{NS}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="B2"><v>12</v></c></row></sheetData></worksheet>'},
                extra={"xl/sharedStrings.xml": f'<sst xmlns="{NS}"><si><t>id</t></si><si><r><t>val</t></r><r><t>ue</t></r></si></sst>'})
    table = read_tabular(data, "f.xlsx")
    assert table.headers == ["id", "value"]
    assert table.rows == [{"id": None, "value": "12"}]


def test_commit_reparses_original_and_preserves_raw_bytes():
    store = MemoryStore()
    result = preview([row()], store=store)
    outcome = commit_preview(result, store)
    assert outcome["inserted"] == 1
    assert store.commits[0]["raw_bytes"] == result.table.raw_bytes
    assert store.commits[0]["mapping"] == result.mapping
    second = preview([row()], store=store)
    assert second.duplicates


@pytest.mark.parametrize("mutation", ["mapping", "row", "namespace", "bytes", "hash"])
def test_changed_preview_cannot_commit(mutation):
    store = MemoryStore()
    result = preview([row()], store=store)
    if mutation == "mapping":
        result.mapping["source_citation"] = IGNORE_FIELD
    elif mutation == "row":
        result.rows[0].source_citation = "tampered citation"
    elif mutation == "namespace":
        result = replace(result, namespace="real")
    elif mutation == "bytes":
        result = replace(result, table=replace(result.table, raw_bytes=csv_bytes([row(source_citation="other")])))
    elif mutation == "hash":
        result = replace(result, table=replace(result.table, file_sha256="0" * 64))
    with pytest.raises(ImportPreviewError):
        commit_preview(result, store)
    assert store.commits == []


def test_store_changed_after_preview_is_rechecked():
    store = MemoryStore()
    result = preview([row()], store=store)
    store.reject = True
    with pytest.raises(ImportPreviewError, match="重新校验"):
        commit_preview(result, store)
    assert store.commits == []


def test_blocked_preview_never_commits():
    store = MemoryStore()
    result = preview([row(value="NaN")], store=store)
    with pytest.raises(ImportPreviewError):
        commit_preview(result, store)
    assert store.commits == []


def test_real_sqlite_repeat_import_and_exact_source_locations(tmp_path):
    database_path = tmp_path / "isolated.sqlite3"
    database = Database(database_path)
    database.initialize()
    store = ExperimentStore(database)
    source = row()
    raw = xlsx({"数据": sheet([list(source), list(source.values())], numbers=[3, 12])})
    table = read_tabular(raw, "fixture.xlsx")
    mapping = {name: name for name in table.headers}
    checked = preview_import(table, mapping, namespace="demo", store=store)
    assert checked.can_commit
    assert commit_preview(checked, store)["inserted"] == 1
    repeated = preview_import(table, mapping, namespace="demo", store=store)
    assert repeated.can_commit and repeated.duplicates
    assert commit_preview(repeated, store)["inserted"] == 0
    assert store.counts("demo")["measurements"] == 1
    assert store.counts("real")["measurements"] == 0
    assert store.list_measurements("demo")[0]["value"] is None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT source_row_number FROM experiment_import_rows").fetchone()[0] == 12
        metadata = json.loads(connection.execute("SELECT payload_json FROM experiment_import_batches").fetchone()[0])
        assert metadata["sheet_name"] == "数据" and metadata["encoding"] == "xlsx-static-xml"
        assert connection.execute("SELECT content FROM experiment_raw_files").fetchone()[0] == raw


def test_real_store_failure_rolls_back_entire_import(tmp_path):
    database_path = tmp_path / "isolated.sqlite3"
    database = Database(database_path)
    database.initialize()
    store = ExperimentStore(database)
    source = row()
    checked = preview([source], store=store)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TRIGGER fail_test BEFORE INSERT ON experiment_measurements BEGIN SELECT RAISE(ABORT,'isolated fault'); END")
    with pytest.raises(sqlite3.IntegrityError, match="isolated fault"):
        commit_preview(checked, store)
    counts = store.counts("demo")
    assert all(counts[key] == 0 for key in ("sources", "materials", "measurements", "import_batches", "raw_files"))
