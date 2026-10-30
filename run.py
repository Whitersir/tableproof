"""从项目根目录或双击启动脚本运行，不依赖 PowerShell 激活虚拟环境。"""
import argparse
import uvicorn

from app.main import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动本地核对台")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"核对台已准备启动，请打开 http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
