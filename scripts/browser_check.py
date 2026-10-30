"""在真实 Edge 中检查本地交互。使用独立测试数据目录，不发送模型请求。"""
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
PORT = 8766
URL = f"http://127.0.0.1:{PORT}"


def main():
    OUT.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TABLE_AGENT_")}
    env.update({"TABLE_AGENT_DATA_DIR": str(OUT / "browser-data"), "PYTHONUTF8": "1"})
    checks, errors = [], []
    with (OUT / "browser-server.log").open("w", encoding="utf-8") as log:
        server_code = "import os; from pathlib import Path; import uvicorn; from app.main import create_app; uvicorn.run(create_app(config_root=Path(os.environ['TABLE_AGENT_DATA_DIR'])),host='127.0.0.1',port=" + str(PORT) + ")"
        process = subprocess.Popen([sys.executable, "-c", server_code], cwd=ROOT, env=env,
                                   stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError("测试服务未能启动，请检查 browser-server.log 和端口 8766。")
                try:
                    if httpx.get(URL + "/api/health", timeout=1, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("测试服务启动超时。")
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(URL)
                expect(page.locator("#service-status")).to_have_text("本机服务已连接")
                expect(page.locator("#run-button")).to_be_disabled()
                page.locator("#open-settings").click()
                expect(page.locator("#settings-dialog")).to_be_visible()
                page.locator("#settings-url").fill("https://mock.invalid/v1")
                page.locator("#settings-model").fill("example-tool-model")
                page.locator("#settings-key").fill("browser-test-dummy-key")
                page.locator("#save-settings").click()
                expect(page.locator("#settings-feedback")).to_contain_text("已保存在本机")
                assert page.locator("#settings-key").input_value() == ""
                page.screenshot(path=str(OUT / "settings.png"))
                page.locator("#close-settings").click()
                page.locator("#open-settings").click()
                expect(page.locator("#settings-url")).to_have_value("https://mock.invalid/v1")
                assert page.locator("#settings-key").input_value() == ""
                page.locator("#test-settings").click()
                expect(page.locator("#settings-feedback")).to_contain_text("请先勾选")
                page.locator("#clear-settings").click()
                expect(page.locator("#settings-feedback")).to_contain_text("已清除")
                page.locator("#close-settings").click()
                assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
                checks.append("模型设置保存、重开不回填密钥、测试确认与清除；无浏览器凭据存储")
                page.locator("#demo-button").click()
                expect(page.locator("#run-status")).to_have_text("已完成")
                expect(page.locator(".metric-value")).to_have_text(["6", "4", "2", "66.67%"])
                checks.append("示例导入、字段建议、本地核对和指标展示")
                page.screenshot(path=str(OUT / "desktop.png"), full_page=True)

                page.locator('[data-tab="details"]').click()
                page.locator("#table-search").fill("未到场")
                expect(page.locator("#result-table tbody tr")).to_have_count(2)
                checks.append("人员明细切换与未到场搜索")
                page.locator('[data-tab="issues"]').click()
                expect(page.locator("#result-table tbody tr")).to_have_count(5)
                checks.append("异常记录展示与源记录追溯")

                for kind in ("report", "details", "issues", "groups"):
                    with page.expect_download() as download:
                        page.locator(f'#downloads a[href$="/{kind}"]').click()
                    item = download.value
                    item.save_as(str(OUT / item.suggested_filename))
                    assert item.failure() is None
                checks.append("四种真实浏览器下载")

                page.locator("#local-settings summary").click()
                page.locator("#registration-filter-column").select_option("班级")
                page.locator("#registration-filter-value").fill("软工一班")
                page.locator("#run-button").click()
                expect(page.locator(".metric-value")).to_have_text(["2", "2", "0", "100.00%"])
                checks.append("报名范围筛选和重新核对")

                page.locator("#mode-agent").click()
                expect(page.locator("#agent-settings")).to_be_visible()
                expect(page.locator("#run-button")).to_be_disabled()
                expect(page.locator("#model-status")).to_contain_text("尚未配置")
                checks.append("无模型凭据时显式禁用 Agent，保留本地流程")
                page.locator("#mode-local").click()

                page.locator("#registration-file").set_input_files({"name": "r.csv", "mimeType": "text/csv", "buffer": b"id,name\n1,A\n2,B\n"})
                page.locator("#checkin-file").set_input_files({"name": "c.csv", "mimeType": "text/csv", "buffer": b"id\n1\n"})
                page.locator("#upload-button").click()
                expect(page.locator("#dataset-banner")).to_contain_text("报名 2 条")
                expect(page.locator("#result-content")).to_be_hidden()
                expect(page.locator("#usage")).to_have_text("尚未执行")
                page.locator("#run-button").click()
                expect(page.locator(".metric-value")).to_have_text(["2", "1", "1", "50.00%"])
                checks.append("自定义文件真实上传与旧结果清空")

                page.locator("#registration-file").set_input_files({"name": "bad.csv", "mimeType": "text/csv", "buffer": b"id,id\n1,2\n"})
                page.locator("#upload-button").click()
                expect(page.locator("#message")).to_contain_text("重复列名")
                checks.append("错误 CSV 显示可理解的反馈")

                # 恢复真实已完成的示例任务，供截图和移动布局检查。
                page.locator("#demo-button").click()
                expect(page.locator(".metric-value")).to_have_text(["6", "4", "2", "66.67%"])
                assert page.locator("#registration-file").input_value() == ""
                page.locator(".history-item").nth(1).click()
                expect(page.locator(".metric-value")).to_have_text(["2", "1", "1", "50.00%"])
                page.locator(".history-item").nth(0).click()
                expect(page.locator(".metric-value")).to_have_text(["6", "4", "2", "66.67%"])
                checks.append("重新打开历史任务，恢复数据集、规则和结果")
                page.locator('[data-tab="groups"]').click()
                for width, height in [(375, 812), (768, 1024), (1024, 768), (1440, 1000)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"{width}px 页面横向溢出"
                page.set_viewport_size({"width": 375, "height": 812})
                page.emulate_media(reduced_motion="reduce")
                page.screenshot(path=str(OUT / "mobile.png"), full_page=True)
                checks.append("375/768/1024/1440px 无页面横向溢出；减少动态效果")
                assert not errors, errors
                checks.append("浏览器未捕获 JavaScript 运行错误")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "browser": "Edge (Windows) / Chromium (other) / Playwright headless",
              "real_model_called": False, "checks": checks, "javascript_errors": errors, "passed": True}
    (OUT / "browser-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"浏览器检查通过：{len(checks)} 组交互；截图位于 {OUT}")


if __name__ == "__main__":
    main()
