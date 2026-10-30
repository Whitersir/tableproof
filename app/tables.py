"""CSV 输入边界。所有单元格始终保留为字符串，避免学号前导零丢失。"""
from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10000


class InputError(ValueError):
    pass


@dataclass
class Table:
    name: str
    headers: list[str]
    rows: list[dict]
    encoding: str = "utf-8-sig"

    def to_dict(self):
        return asdict(self)

    def describe(self):
        # 只向模型提供字段结构，不默认发送原始姓名、学号或样本行。
        return {"name": self.name, "headers": self.headers, "row_count": len(self.rows)}


def parse_csv(data: bytes, name: str) -> Table:
    if not name.lower().endswith(".csv"):
        raise InputError("首版只支持 CSV，请在 Excel 中另存为 CSV UTF-8。")
    if len(data) > MAX_BYTES:
        raise InputError("每个文件最多 2 MB。")
    text = None
    encoding = ""
    for candidate in ("utf-8-sig", "gb18030"):
        try:
            text, encoding = data.decode(candidate), candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None or "\x00" in text:
        raise InputError("无法识别文本编码，请另存为 CSV UTF-8。")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = [cell.strip() for cell in next(reader)]
        if not headers or len(headers) > 50 or any(not h for h in headers):
            raise InputError("表头不能为空，最多 50 列。")
        if len(set(headers)) != len(headers):
            raise InputError("存在重复列名，请先修改表头。")
        if any(len(h) > 100 for h in headers):
            raise InputError("列名最多 100 字。")
        rows = []
        for record, cells in enumerate(reader, start=2):
            if not cells or all(not c.strip() for c in cells):
                continue
            if len(cells) != len(headers):
                raise InputError(f"第 {record} 条 CSV 记录的列数与表头不一致。")
            if any(len(c) > 2000 for c in cells):
                raise InputError("列名最多 100 字，单元格最多 2000 字。")
            rows.append({"record": record, "values": dict(zip(headers, (c.strip() for c in cells)))})
            if len(rows) > MAX_ROWS:
                raise InputError("首版每张表最多 10000 条非空记录。")
    except StopIteration:
        raise InputError("文件为空，至少需要一行表头。") from None
    except csv.Error:
        raise InputError("CSV 格式错误，请检查引号、分隔符，或重新导出。") from None
    return Table(name, headers, rows, encoding)


def csv_text(headers: list[str], rows: list[list]) -> str:
    """给 Excel 使用的导出：BOM + 防止用户数据被当成公式。"""
    out = io.StringIO(newline="")
    writer = csv.writer(out)

    def safe(value):
        text = str(value) if value is not None else ""
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text

    writer.writerow([safe(h) for h in headers])
    writer.writerows([[safe(c) for c in row] for row in rows])
    return "\ufeff" + out.getvalue()
