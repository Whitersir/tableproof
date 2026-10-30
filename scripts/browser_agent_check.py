"""浏览器→后端→本机固定响应接口的集成检查；真实模型调用数为零。"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test-results"


def main():
    OUT.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TABLE_AGENT_")}
    env.update({"TABLE_AGENT_DATA_DIR": str(OUT / "browser-agent-data"), "PYTHONUTF8": "1"})
    code = "import os; from pathlib import Path; import uvicorn; from app.main import create_app; uvicorn.run(create_app(config_root=Path(os.environ['TABLE_AGENT_DATA_DIR'])),host='127.0.0.1',port=8767,log_level='warning')"
    processes = []
    with (OUT / "browser-agent.log").open("w", encoding="utf-8") as log:
        try:
            for args in ([sys.executable, "-c", code], [sys.executable, "scripts/mock_provider.py"]):
                processes.append(subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
            for url in ("http://127.0.0.1:8767/api/health", "http://127.0.0.1:8768/health"):
                for _ in range(50):
                    if any(p.poll() is not None for p in processes):
                        raise RuntimeError("模拟集成服务启动失败，请检查端口和日志。")
                    try:
                        if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("模拟集成服务启动超时。")
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://127.0.0.1:8767")
                expect(page.locator("#service-status")).to_have_text("本机服务已连接")
                page.locator("#open-settings").click()
                page.locator("#settings-url").fill("http://127.0.0.1:8768/v1")
                page.locator("#settings-model").fill("scripted-mock-not-a-real-model")
                page.locator("#settings-key").fill("dummy-local-test-key")
                page.locator("#test-consent").check()
                page.locator("#test-settings").click()
                expect(page.locator("#settings-feedback")).to_contain_text("已收到有效工具调用", timeout=10000)
                page.locator("#save-settings").click()
                expect(page.locator("#settings-feedback")).to_contain_text("已保存在本机")
                page.locator("#close-settings").click()
                page.locator("#demo-button").click()
                expect(page.locator("#run-status")).to_have_text("已完成")
                page.locator("#mode-agent").click()
                page.locator("#consent").check()
                page.locator("#run-button").click()
                expect(page.locator("#run-status")).to_have_text("等待审阅", timeout=15000)
                expect(page.locator("#plan-fields")).to_contain_text("学号 ↔ 学生编号")
                expect(page.locator("#result-content")).to_be_hidden()
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(OUT / "agent-review-mock.png"))
                page.locator("#approve-plan").click()
                expect(page.locator("#run-status")).to_have_text("已完成", timeout=15000)
                expect(page.locator(".metric-value")).to_have_text(["6", "4", "2", "66.67%"])
                expect(page.locator("#usage")).to_contain_text("4 次请求")
                assert not errors, errors
                result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "kind": "browser_to_backend_to_scripted_mock_http",
                          "real_model_called": False, "passed": True,
                          "checks": ["用户填写配置", "真实本机 HTTP 工具格式探测", "方案审阅前未计算", "确认后续轮执行与报告", "用量与操作记录"]}
                (OUT / "browser-agent-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                browser.close()
        finally:
            for process in processes:
                process.terminate()
                process.wait(timeout=10)
    print("浏览器 Agent 集成检查通过：使用本机固定响应服务，未调用真实模型。")


if __name__ == "__main__":
    main()
