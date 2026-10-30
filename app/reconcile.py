"""不依赖模型的业务规则；这里是建议你最先阅读和修改的代码。"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tables import InputError, Table


class ExactFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    column: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=2000)


class MatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    registration_keys: list[str] = Field(min_length=1, max_length=3)
    checkin_keys: list[str] = Field(min_length=1, max_length=3)
    group_by: str | None = None
    registration_filters: list[ExactFilter] = Field(default_factory=list, max_length=3)
    checkin_filters: list[ExactFilter] = Field(default_factory=list, max_length=3)
    allow_name_only: bool = False

    @model_validator(mode="after")
    def same_length(self):
        if len(self.registration_keys) != len(self.checkin_keys):
            raise ValueError("两张表的匹配字段数量必须一致。")
        if len(set(self.registration_keys)) != len(self.registration_keys) or len(set(self.checkin_keys)) != len(self.checkin_keys):
            raise ValueError("匹配字段不能重复。")
        return self


class ClarificationNeeded(InputError):
    pass


def percentage(part: int, total: int):
    if total == 0:
        return None
    return str((Decimal(part) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_match_config(registration: Table, checkin: Table, config: MatchConfig):
    """检查方案的字段和姓名匹配风险，不读取或计算人员结果。"""
    for table, keys, filters in (
        (registration, config.registration_keys, config.registration_filters),
        (checkin, config.checkin_keys, config.checkin_filters),
    ):
        needed = keys + [f.column for f in filters]
        if table is registration and config.group_by:
            needed.append(config.group_by)
        if any(k not in table.headers for k in needed):
            raise InputError(f"{table.name} 缺少所选字段，请重新选择。")
    name_aliases = {"姓名", "名字", "name", "姓名/名称", "学生姓名"}
    name_only = len(config.registration_keys) == 1 and (
        config.registration_keys[0].lower() in name_aliases or config.checkin_keys[0].lower() in name_aliases
    )
    if name_only and not config.allow_name_only:
        raise ClarificationNeeded("当前只用姓名匹配。请优先选择学号或姓名+班级；确认接受风险后才能仅用姓名匹配。")
    return name_only


def reconcile(registration: Table, checkin: Table, config: MatchConfig) -> dict:
    """只做精确匹配。空标识、冲突标识不进入分母；不会通过姓名猜测身份。"""
    name_only = validate_match_config(registration, checkin, config)

    def key(row, fields):
        values = tuple(row["values"][field] for field in fields)
        return values if all(values) else None

    def selected(row, filters):
        return all(row["values"][f.column] == f.value for f in filters)

    issues = []

    def issue(kind, table, row, detail):
        issues.append({"type": kind, "source": table.name, "record": row["record"], "detail": detail, "values": row["values"]})

    all_registration_keys = {key(r, config.registration_keys) for r in registration.rows}
    reg_rows = [r for r in registration.rows if selected(r, config.registration_filters)]
    check_rows = [r for r in checkin.rows if selected(r, config.checkin_filters)]
    buckets = defaultdict(list)
    for row in reg_rows:
        k = key(row, config.registration_keys)
        if k is None:
            issue("报名标识为空", registration, row, "待确认，未计入有效报名人数")
        else:
            buckets[k].append(row)

    eligible, ambiguous = {}, set()
    for k, rows in buckets.items():
        signatures = {tuple(sorted(r["values"].items())) for r in rows}
        if len(rows) > 1 and (name_only or len(signatures) > 1):
            ambiguous.add(k)
            for row in rows:
                issue("报名标识冲突", registration, row, "同一标识存在歧义，待确认，未计入分母")
        else:
            eligible[k] = rows
            for row in rows[1:]:
                issue("重复报名", registration, row, "完全相同的报名记录，仅计一人")

    check_buckets = defaultdict(list)
    for row in check_rows:
        k = key(row, config.checkin_keys)
        if k is None:
            issue("签到标识为空", checkin, row, "无法匹配，待确认")
            continue
        check_buckets[k].append(row)
        if k in ambiguous:
            issue("签到匹配待确认", checkin, row, "对应报名标识有冲突")
        elif k not in buckets:
            if k in all_registration_keys:
                issue("范围外签到", checkin, row, "报名存在，但不在本次报名筛选范围")
            else:
                issue("未报名签到", checkin, row, "在完整报名表中未找到该标识")
    for rows in check_buckets.values():
        for row in rows[1:]:
            issue("重复签到", checkin, row, "同一标识仅计一次到场")

    details = []
    for k, rows in eligible.items():
        checks = check_buckets.get(k, [])
        details.append({
            "key": list(k), "values": rows[0]["values"],
            "registration_records": [r["record"] for r in rows],
            "checkin_records": [r["record"] for r in checks],
            "status": "已到场" if checks else "未到场",
            "group": rows[0]["values"].get(config.group_by, "") if config.group_by else "全部",
        })
    arrived = sum(d["status"] == "已到场" for d in details)
    totals = {
        "registration_rows": len(reg_rows), "checkin_rows": len(check_rows),
        "valid_registered": len(details), "arrived": arrived, "absent": len(details) - arrived,
        "attendance_rate": percentage(arrived, len(details)),
        "excluded_registration_rows": sum(i["type"] in ("报名标识为空", "报名标识冲突") for i in issues),
        "duplicate_checkin_rows": sum(i["type"] == "重复签到" for i in issues),
        "unregistered_people": sum(k not in all_registration_keys for k in check_buckets),
    }
    result = {
        "config": config.model_dump(), "totals": totals, "details": details, "issues": issues,
        "sources": {"registration": registration.describe(), "checkin": checkin.describe()},
        "rules": [
            "按所选标识去除首尾空白后精确匹配，保留前导零，区分大小写。",
            "有效报名人数为筛选后标识完整且无冲突的去重人数。",
            "到场率 = 已匹配的有效报名人数 / 有效报名人数；分母为零时不计算。",
            "重复签到仅计一次；未报名签到、空标识和冲突标识不计入到场人数。",
            "记录号按 CSV 记录计数：表头为第 1 条；含换行单元格时不等于文本行号。",
        ],
    }
    result["groups"] = summarize_groups(result)
    return result


def summarize_groups(result: dict) -> list[dict]:
    groups = defaultdict(lambda: {"registered": 0, "arrived": 0})
    for row in result["details"]:
        g = groups[row["group"] or "（空分组）"]
        g["registered"] += 1
        g["arrived"] += row["status"] == "已到场"
    return [{"group": k, **v, "absent": v["registered"] - v["arrived"],
             "attendance_rate": percentage(v["arrived"], v["registered"])} for k, v in groups.items()]
