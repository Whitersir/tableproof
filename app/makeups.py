"""人工补签只作用于一份已完成的核对结果，不改写上传的原始记录。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .reconcile import percentage, summarize_groups
from .tables import InputError


class MakeupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: list[str] = Field(min_length=1, max_length=3)
    reason: str = Field(min_length=1, max_length=500)
    signed_at: str = Field(min_length=1, max_length=50)
    confirmed_attendance: bool = False
    operation_id: str = Field(min_length=36, max_length=36)

    @field_validator("key")
    @classmethod
    def valid_key(cls, value):
        if any(not item.strip() or len(item) > 2000 for item in value):
            raise ValueError("匹配标识不能为空。")
        return [item.strip() for item in value]

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        if not value.strip():
            raise ValueError("请填写补签原因。")
        return value.strip()

    @field_validator("operation_id")
    @classmethod
    def valid_operation(cls, value):
        return str(uuid.UUID(value))

    @field_validator("signed_at")
    @classmethod
    def valid_time(cls, value):
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("签到时间格式不正确。") from None
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("签到时间必须包含时区。")
        if stamp > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("实际签到时间不能晚于当前时间。")
        return stamp.astimezone(timezone.utc).isoformat()


def apply_makeup(result, request: MakeupRequest, recorded_at: str):
    if not request.confirmed_attendance:
        raise InputError("请先确认该人员实际参加了本次统计范围内的活动。")
    updated = deepcopy(result)
    person = next((d for d in updated["details"] if d["key"] == request.key), None)
    if person is None:
        raise InputError("当前有效报名名单中没有该标识；空标识、冲突或范围外人员不能直接补签。")
    if person["status"] != "未到场":
        raise InputError("该人员已到场或已经补签，无需重复操作。")
    event = {"id": request.operation_id, "key": request.key, "reason": request.reason,
             "signed_at": request.signed_at, "recorded_at": recorded_at,
             "registration_records": person["registration_records"]}
    person["status"] = "已到场"
    person["makeup"] = event
    updated.setdefault("makeups", []).append(event)
    totals = updated["totals"]
    totals["arrived"] = sum(d["status"] == "已到场" for d in updated["details"])
    totals["absent"] = totals["valid_registered"] - totals["arrived"]
    totals["attendance_rate"] = percentage(totals["arrived"], totals["valid_registered"])
    totals["makeup_people"] = len(updated["makeups"])
    updated["groups"] = summarize_groups(updated)
    rule = "人工补签计入当前核对任务的到场人数，单独保留时间与原因；不增加原始签到条数。"
    if rule not in updated["rules"]:
        updated["rules"].append(rule)
    return updated
