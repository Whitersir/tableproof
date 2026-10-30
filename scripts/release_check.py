"""检查准备提交的源码；只报告文件名，不回显任何疑似秘密内容。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"data", ".venv", "test-results", "__pycache__", ".pytest_cache", "dist"}


def main():
    result = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if result.returncode:
        print("请先 git init 并添加准备发布的文件，再执行发布检查。")
        return 1
    names = [n.decode("utf-8") for n in result.stdout.split(b"\0") if n]
    failures = []
    key_pattern = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
    for name in names:
        path = Path(name)
        if any(p in FORBIDDEN for p in path.parts) or (path.name.startswith(".env") and path.name != ".env.example") or path.suffix in (".sqlite3", ".log", ".pyc"):
            failures.append((name, "禁止提交的本地数据/配置"))
            continue
        if path.suffix in (".png", ".jpg", ".svg"):
            continue
        content = (ROOT / path).read_text(encoding="utf-8-sig", errors="replace")
        if key_pattern.search(content):
            failures.append((name, "疑似凭据，请在本地检查"))
    for name, reason in failures:
        print(f"FAIL {name}: {reason}")
    print(f"发布检查：{len(names)} 个跟踪文件，{len(failures)} 个问题。")
    return int(bool(failures) or not names)


if __name__ == "__main__":
    raise SystemExit(main())
