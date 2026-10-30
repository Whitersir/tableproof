import csv
import io

import pytest
from pydantic import ValidationError

from app.reconcile import ClarificationNeeded, ExactFilter, MatchConfig, reconcile
from app.tables import InputError, csv_text, parse_csv


def table(text, name="table.csv"):
    return parse_csv(text.encode("utf-8"), name)


def test_demo_has_manually_checked_answer(demo_tables, config):
    result = reconcile(*demo_tables, config)
    assert result["totals"] == {
        "registration_rows": 8, "checkin_rows": 7, "valid_registered": 6, "arrived": 4,
        "absent": 2, "attendance_rate": "66.67", "excluded_registration_rows": 1,
        "duplicate_checkin_rows": 1, "unregistered_people": 1,
    }
    assert {d["key"][0] for d in result["details"] if d["status"] == "未到场"} == {"004", "006"}
    assert len(result["issues"]) == 5
    assert result["groups"][0] == {"group": "软工一班", "registered": 2, "arrived": 2, "absent": 0, "attendance_rate": "100.00"}


def test_provenance_covers_all_duplicate_source_records(demo_tables, config):
    result = reconcile(*demo_tables, config)
    person = next(d for d in result["details"] if d["key"] == ["003"])
    assert person["registration_records"] == [4, 8]
    assert person["checkin_records"] == [4, 5]


def test_leading_zero_identifiers_are_distinct():
    result = reconcile(table("id\n001\n1\n"), table("id\n1\n"), MatchConfig(registration_keys=["id"], checkin_keys=["id"]))
    assert result["totals"]["valid_registered"] == 2
    assert result["totals"]["arrived"] == 1


def test_conflicting_identity_is_not_counted_as_attended():
    result = reconcile(table("id,姓名\n01,甲\n01,乙\n02,丙\n"), table("id\n01\n02\n"), MatchConfig(registration_keys=["id"], checkin_keys=["id"]))
    assert result["totals"]["valid_registered"] == 1
    assert result["totals"]["arrived"] == 1
    assert result["totals"]["excluded_registration_rows"] == 2
    assert "签到匹配待确认" in {i["type"] for i in result["issues"]}


def test_name_only_requires_confirmation():
    with pytest.raises(ClarificationNeeded):
        reconcile(table("姓名\n甲\n"), table("姓名\n甲\n"), MatchConfig(registration_keys=["姓名"], checkin_keys=["姓名"]))


def test_name_duplicates_stay_ambiguous_even_after_confirmation():
    result = reconcile(table("姓名,班级\n甲,一班\n甲,二班\n"), table("姓名\n甲\n"),
                       MatchConfig(registration_keys=["姓名"], checkin_keys=["姓名"], allow_name_only=True))
    assert result["totals"]["valid_registered"] == 0
    assert result["totals"]["attendance_rate"] is None


def test_composite_key_disambiguates_names():
    result = reconcile(table("姓名,班级\n甲,一班\n甲,二班\n"), table("姓名,班级\n甲,二班\n"),
                       MatchConfig(registration_keys=["姓名", "班级"], checkin_keys=["姓名", "班级"]))
    assert result["totals"]["arrived"] == 1
    assert next(d for d in result["details"] if d["status"] == "已到场")["key"] == ["甲", "二班"]


def test_registration_filter_does_not_label_other_groups_unregistered(demo_tables, config):
    config.registration_filters = [ExactFilter(column="班级", value="软工一班")]
    result = reconcile(*demo_tables, config)
    assert result["totals"]["valid_registered"] == 2
    assert result["totals"]["attendance_rate"] == "100.00"
    assert result["totals"]["unregistered_people"] == 1
    assert sum(i["type"] == "范围外签到" for i in result["issues"]) == 3


def test_checkin_filter_changes_numerator_not_registration_denominator(demo_tables, config):
    config.checkin_filters = [ExactFilter(column="日期", value="2099-01-01")]
    result = reconcile(*demo_tables, config)
    assert result["totals"]["valid_registered"] == 6
    assert result["totals"]["arrived"] == 0


def test_missing_keys_do_not_match_each_other():
    result = reconcile(table("id,姓名\n,甲\n"), table("id,姓名\n,甲\n"), MatchConfig(registration_keys=["id"], checkin_keys=["id"]))
    assert result["totals"]["arrived"] == 0
    assert result["totals"]["attendance_rate"] is None
    assert len(result["issues"]) == 2


def test_empty_checkin_table_means_absent(demo_tables, config):
    result = reconcile(demo_tables[0], table("学生编号,姓名,日期\n"), config)
    assert result["totals"]["absent"] == 6


def test_unknown_column_is_an_error(demo_tables, config):
    config.group_by = "不存在"
    with pytest.raises(InputError):
        reconcile(*demo_tables, config)


@pytest.mark.parametrize("text", ["", "a,a\n1,2\n", "a,\n1,2\n", "a,b\n1\n", 'a,b\n"broken,x\n'])
def test_rejects_malformed_csv(text):
    with pytest.raises(InputError):
        table(text)


def test_gb18030_and_whitespace_are_supported():
    t = parse_csv(" 学号 ,姓名\n 001 , 甲 \n".encode("gb18030"), "旧编码.csv")
    assert t.rows[0]["values"] == {"学号": "001", "姓名": "甲"}
    assert t.encoding == "gb18030"


def test_multiline_cells_preserve_record_numbers():
    t = table('id,备注\n1,"第一行\n第二行"\n\n2,正常\n')
    assert [r["record"] for r in t.rows] == [2, 4]


def test_rejects_oversize_file():
    with pytest.raises(InputError):
        parse_csv(b"x" * (2 * 1024 * 1024 + 1), "x.csv")


@pytest.mark.parametrize("value", ["=1+1", "+SUM(A1)", "-1+2", "@SUM(A1)", "  =cmd()"])
def test_csv_formula_cells_are_escaped(value):
    exported = list(csv.reader(io.StringIO(csv_text(["列"], [[value]]).lstrip("\ufeff"))))
    assert exported[1][0] == "'" + value


def test_model_parameters_are_strict():
    with pytest.raises(ValidationError):
        MatchConfig(registration_keys=["id"], checkin_keys=["id"], allow_name_only="true")
    with pytest.raises(ValidationError):
        MatchConfig(registration_keys=["id"], checkin_keys=["id"], execute_python="print(1)")
