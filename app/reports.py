"""报告内容由确定性结果生成；模型不能直接改写核对数字。"""
from .tables import csv_text


def markdown_report(run):
    result = run["result"]
    t = result["totals"]
    rate = f'{t["attendance_rate"]}%' if t["attendance_rate"] is not None else "不适用（分母为零）"
    # 报告使用纯文本列表，防止源数据中的竖线破坏 Markdown 表格。
    lines = ["# 报名与签到核对报告", "", f'任务编号：{run["id"]}', f'生成时间（UTC）：{run["updated_at"]}',
             f'运行方式：{"本地规则核对" if run["mode"] == "local" else "模型工具调用"}', "",
             "## 统计结果", "", f'- 有效报名：{t["valid_registered"]} 人', f'- 已到场：{t["arrived"]} 人',
             f'- 未到场：{t["absent"]} 人', f'- 到场率：{rate}',
             f'- 待确认并排除的报名记录：{t["excluded_registration_rows"]} 条',
             f'- 重复签到：{t["duplicate_checkin_rows"]} 条', f'- 未报名签到：{t["unregistered_people"]} 个标识',
             "", "## 分组统计", ""]
    for group in result["groups"]:
        lines.append(f'- {group["group"]}：报名 {group["registered"]}，到场 {group["arrived"]}，到场率 {group["attendance_rate"]}%')
    lines += ["", "## 输入与匹配口径", "", f'- 报名文件：{result["sources"]["registration"]["name"]}',
              f'- 签到文件：{result["sources"]["checkin"]["name"]}',
              f'- 报名匹配字段：{", ".join(result["config"]["registration_keys"])}',
              f'- 签到匹配字段：{", ".join(result["config"]["checkin_keys"])}',
              f'- 报名筛选：{result["config"]["registration_filters"] or "无"}',
              f'- 签到筛选：{result["config"]["checkin_filters"] or "无"}', ""]
    lines.extend("- " + rule for rule in result["rules"])
    lines += ["", "## 异常记录", "", f'共 {len(result["issues"])} 条异常标记；同一记录可能有多种标记。完整名单见异常 CSV。', "",
              "## 结果边界", "", "本报告使用程序核对结果生成。待确认项需要人工检查；不是对真实出席身份的认证。", ""]
    return "\n".join(lines)


def export_csv(result, kind):
    if kind == "details":
        headers = result["sources"]["registration"]["headers"]
        return csv_text([*headers, "核对状态", "报名源记录号", "签到源记录号"], [
            [*[d["values"][h] for h in headers], d["status"], ";".join(map(str, d["registration_records"])),
             ";".join(map(str, d["checkin_records"]))] for d in result["details"]])
    if kind == "groups":
        return csv_text(["分组", "有效报名", "已到场", "未到场", "到场率（%）"], [
            [g["group"], g["registered"], g["arrived"], g["absent"], g["attendance_rate"]] for g in result["groups"]])
    import json
    return csv_text(["异常类型", "来源文件", "源记录号", "说明", "原始字段"], [
        [i["type"], i["source"], i["record"], i["detail"], json.dumps(i["values"], ensure_ascii=False)] for i in result["issues"]])
