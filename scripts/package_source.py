"""从干净 Git 提交生成源码 ZIP 和 SHA-256 文件，不打包本机运行数据。"""
import argparse
import hashlib
import re
import subprocess
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    result = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError("Git 打包失败，请检查仓库和提交状态。")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="例如 0.2.1")
    version = parser.parse_args().version
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?", version):
        parser.error("版本号格式不正确。")
    if git("status", "--porcelain").strip():
        raise RuntimeError("工作区有未提交修改，请先检查并提交后再打包。")
    directory = ROOT / "dist"
    directory.mkdir(exist_ok=True)
    archive = directory / f"TableProof-v{version}-source.zip"
    git("archive", "--format=zip", f"--prefix=TableProof-v{version}/", f"--output={archive}", "HEAD")
    with ZipFile(archive) as zipped:
        for name in zipped.namelist():
            path = Path(name)
            if any(p in {"data", ".venv", "test-results", ".git", "dist", "__pycache__"} for p in path.parts):
                raise RuntimeError("源码包包含禁止的本机目录，已停止发布。")
            if path.name.startswith(".env") and path.name != ".env.example":
                raise RuntimeError("源码包包含私密环境文件，已停止发布。")
        if zipped.testzip():
            raise RuntimeError("ZIP 完整性检查失败。")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"{archive.name} ({archive.stat().st_size} bytes)")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
