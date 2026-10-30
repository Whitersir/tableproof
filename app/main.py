from __future__ import annotations

import os
import threading
import json
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import run_agent, trace
from .model_client import ModelClient, ModelConfig, ModelError
from .settings import ModelSettings, SettingsRequest, ProbeRequest, connection_probe
from .reconcile import ClarificationNeeded, MatchConfig, reconcile
from .reports import export_csv, markdown_report
from .storage import Store, public_run
from .tables import MAX_BYTES, InputError, parse_csv

ROOT = Path(__file__).resolve().parents[1]


class LocalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dataset_id: str
    config: MatchConfig


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dataset_id: str
    task: str = Field(min_length=1, max_length=3000)
    consent_to_send: bool = False
    allow_name_only: bool = False


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(default="", max_length=3000)
    consent_to_send: bool = False
    allow_name_only: bool = False


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approved: bool
    note: str = Field(default="", max_length=3000)
    consent_to_send: bool = False


def create_app(data_root: Path | None = None, config_root: Path | None = None):
    app = FastAPI(title="核对台 API", version="0.2.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    store = Store(data_root or Path(os.environ.get("TABLE_AGENT_DATA_DIR", ROOT / "data")))
    store.recover_interrupted()
    app.state.store = store
    app.state.agent_lock = threading.Lock()
    app.state.probe_lock = threading.Lock()
    app.state.settings = ModelSettings(config_root or ROOT, store.root)
    app.state.probe_factory = lambda config: ModelClient(config)
    # 不导入、扫描用户其他软件的凭据。每次 Agent 任务重新读本项目配置。
    app.state.client_factory = lambda: ModelClient(app.state.settings.effective())

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.method in ("POST", "PUT", "DELETE"):
            if request.headers.get("x-requested-with") != "table-report-agent":
                return JSONResponse({"detail": "请通过本机核对台页面操作。"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin not in (f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}"):
                return JSONResponse({"detail": "不接受其他站点发起的请求。"}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 2 * MAX_BYTES + 65536:
                    return JSONResponse({"detail": "上传总大小超过限制，每张表最多 2 MB。"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "请求长度不合法。"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(InputError)
    async def input_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ModelError)
    async def model_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI 默认会返回 input；配置接口中这可能是密钥，统一去掉原始输入。
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    def dataset_info(dataset_id):
        registration, checkin = store.dataset(dataset_id)
        return {"id": dataset_id, "registration": {**registration.describe(), "preview": registration.rows[:5]},
                "checkin": {**checkin.describe(), "preview": checkin.rows[:5]}}

    @app.get("/api/health")
    def health():
        config = app.state.settings.effective()
        return {"status": "ok", "model_configured": config.ready, "version": "0.2.0"}

    @app.get("/api/settings/model")
    def get_settings():
        return app.state.settings.public()

    @app.post("/api/settings/model")
    def save_settings(request: SettingsRequest):
        return app.state.settings.save(request)

    @app.delete("/api/settings/model")
    def clear_settings():
        return app.state.settings.clear()

    @app.post("/api/settings/model/test")
    def test_settings(request: ProbeRequest):
        if not request.consent_to_test:
            raise HTTPException(422, "连接测试将发起一次可能计费的模型请求，请先勾选确认。")
        if not app.state.probe_lock.acquire(blocking=False):
            raise HTTPException(409, "正在测试连接，请等待完成。")
        try:
            config = app.state.settings.candidate(request)
            return connection_probe(app.state.probe_factory(config))
        finally:
            app.state.probe_lock.release()

    @app.post("/api/datasets")
    def upload(registration: UploadFile = File(...), checkin: UploadFile = File(...)):
        rb, cb = registration.file.read(MAX_BYTES + 1), checkin.file.read(MAX_BYTES + 1)
        rn, cn = Path(registration.filename or "registration.csv").name, Path(checkin.filename or "checkin.csv").name
        r, c = parse_csv(rb, rn), parse_csv(cb, cn)
        return dataset_info(store.save_dataset(r, c, (rb, cb)))

    @app.post("/api/demo")
    def demo():
        rb = (ROOT / "samples" / "registration.csv").read_bytes()
        cb = (ROOT / "samples" / "checkin.csv").read_bytes()
        info = dataset_info(store.save_dataset(parse_csv(rb, "示例报名.csv"), parse_csv(cb, "示例签到.csv"), (rb, cb)))
        info["is_demo"] = True
        return info

    @app.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str):
        return dataset_info(dataset_id)

    @app.get("/api/runs")
    def runs():
        return store.recent_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return public_run(store.run(run_id))

    @app.post("/api/runs/local")
    def local(request: LocalRequest):
        r, c = store.dataset(request.dataset_id)
        run = store.create_run(request.dataset_id, "local")
        trace(run, "读取数据", "ok", f"报名 {len(r.rows)} 条，签到 {len(c.rows)} 条。")
        try:
            run["result"] = reconcile(r, c, request.config)
            run["status"] = "completed"
            trace(run, "核对与统计", "ok", "按用户选择的精确匹配字段和筛选条件执行。")
            trace(run, "生成报告", "ok", "已生成明细、异常和分组统计，可下载。")
        except ClarificationNeeded as exc:
            run["status"], run["question"] = "needs_input", str(exc)
            trace(run, "匹配确认", "waiting", str(exc))
        except InputError as exc:
            run["status"], run["error"] = "failed", str(exc)
            trace(run, "核对与统计", "error", str(exc))
        store.save_run(run)
        return public_run(run)

    def background_agent(run, client, answer=None, allow_name_only=False):
        try:
            run_agent(store, run, client, answer, allow_name_only)
        finally:
            app.state.agent_lock.release()

    def check_model(consent):
        if not consent:
            raise HTTPException(422, "请确认允许向所配置的中转站发送任务、字段结构和聚合统计。")
        client = app.state.client_factory()
        if not client.config.ready:
            raise HTTPException(409, "尚未配置中转站 API。先使用本地核对；配置方法见 README。")
        return client

    @app.post("/api/runs/agent", status_code=202)
    def agent(request: AgentRequest, background: BackgroundTasks):
        client = check_model(request.consent_to_send)
        store.dataset(request.dataset_id)
        if not app.state.agent_lock.acquire(blocking=False):
            raise HTTPException(409, "已有 Agent 任务执行中，请等待其结束。")
        try:
            run = store.create_run(request.dataset_id, "agent", request.task)
            run["review_required"] = True
            store.save_run(run)
            background.add_task(background_agent, run, client, None, request.allow_name_only)
            return public_run(run)
        except Exception:
            app.state.agent_lock.release()
            raise

    @app.post("/api/runs/{run_id}/continue", status_code=202)
    def continue_run(run_id: str, request: ContinueRequest, background: BackgroundTasks):
        client = check_model(request.consent_to_send)
        if not app.state.agent_lock.acquire(blocking=False):
            raise HTTPException(409, "已有 Agent 任务执行中，请稍后重试。")
        try:
            run = store.run(run_id)
            if run.get("interrupted"):
                raise HTTPException(409, "上次进程中的任务已中断，请新建任务。")
            if run["mode"] != "agent" or run["status"] not in ("needs_input", "failed"):
                raise HTTPException(409, "只有等待回答或失败的 Agent 任务可以继续。")
            if run["status"] == "needs_input" and not request.answer.strip():
                raise HTTPException(422, "请输入你的补充说明。")
            if run["rounds"] >= 10:
                raise HTTPException(409, "已达到调用上限，请新建任务。")
            run["status"] = "running"
            store.save_run(run)
            background.add_task(background_agent, run, client, request.answer, request.allow_name_only)
            return public_run(run)
        except Exception:
            app.state.agent_lock.release()
            raise

    @app.post("/api/runs/{run_id}/review", status_code=202)
    def review_run(run_id: str, request: ReviewRequest, background: BackgroundTasks):
        client = check_model(request.consent_to_send)
        if not app.state.agent_lock.acquire(blocking=False):
            raise HTTPException(409, "已有 Agent 任务执行中，请稍后重试。")
        try:
            run = store.run(run_id)
            if run["status"] != "needs_review" or not run.get("pending_config"):
                raise HTTPException(409, "当前没有等待确认的核对方案。")
            if not request.approved and not request.note.strip():
                raise HTTPException(422, "请说明希望修改的匹配字段或统计范围。")
            config = run.pop("pending_config")
            run["calculation_current"] = False
            if request.approved:
                run["approved_config"] = config
                # allow_name_only 是服务器控制字段，不应由模型作为参数传回。
                safe = dict(config)
                safe.pop("allow_name_only", None)
                answer = "用户确认执行以下方案。请用完全相同的参数再次调用 reconcile_attendance，再根据结果继续：" + json.dumps(safe, ensure_ascii=False)
                trace(run, "用户审阅", "ok", "用户确认了匹配字段、分组和筛选范围。")
            else:
                run["approved_config"] = None
                answer = "用户要求修改方案后重新提交确认：" + request.note.strip()
                trace(run, "用户审阅", "waiting", "用户要求修改方案，尚未执行计算。")
            run["status"] = "running"
            store.save_run(run)
            background.add_task(background_agent, run, client, answer)
            return public_run(run)
        except Exception:
            app.state.agent_lock.release()
            raise

    @app.get("/api/runs/{run_id}/export/{kind}")
    def download(run_id: str, kind: str):
        run = store.run(run_id)
        if run["status"] != "completed" or not run["result"]:
            raise HTTPException(409, "任务尚未完成，不能下载最终报告。")
        if kind == "report":
            return Response(markdown_report(run), media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="attendance-report.md"'})
        if kind not in ("details", "issues", "groups"):
            raise HTTPException(404, "不支持的导出格式。")
        return Response(export_csv(run["result"], kind), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="attendance-{kind}.csv"'})

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    app.mount("/samples", StaticFiles(directory=ROOT / "samples"), name="samples")
    return app
