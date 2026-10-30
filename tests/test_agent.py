"""所有模型测试均使用 httpx.MockTransport，没有真实模型请求或付费调用。"""
import json

import httpx
import pytest

from app.agent import run_agent
from app.model_client import ModelClient, ModelConfig, ModelError
from app.storage import Store


def call(name, args, call_id="call-1"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def scripted(messages):
    queue = list(messages)
    received = []
    def handler(request):
        received.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        message = queue.pop(0)
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    client = ModelClient(ModelConfig("https://mock.invalid/v1", "test-model", "test-secret"), httpx.MockTransport(handler))
    return client, received


def prepared(tmp_path, demo_tables):
    store = Store(tmp_path / "agent-data")
    dataset_id = store.save_dataset(*demo_tables, (b"test", b"test"))
    return store, store.create_run(dataset_id, "agent", "按学号核对，按班级统计")


MATCH = {"registration_keys": ["学号"], "checkin_keys": ["学生编号"], "group_by": "班级"}
END = {"role": "assistant", "content": "已完成"}


def test_real_loop_with_mocked_http_executes_tools_and_preserves_usage(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, received = scripted([call("inspect_tables", {}), call("reconcile_attendance", MATCH), END])
    run_agent(store, run, model)
    assert run["status"] == "completed"
    assert run["result"]["totals"]["arrived"] == 4
    assert run["usage"] == {"prompt_tokens": 300, "completion_tokens": 60}
    assert received[2]["messages"][-1]["role"] == "tool"
    # 模型只收到表结构与聚合统计，不含原始个人名单。
    assert "林一" not in json.dumps(received, ensure_ascii=False)
    assert "test-secret" not in json.dumps(run, ensure_ascii=False)


def test_ask_user_pause_and_resume(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    first, _ = scripted([call("inspect_tables", {}), call("ask_user", {"question": "请确认用学号对应学生编号？"})])
    run_agent(store, run, first)
    assert run["status"] == "needs_input"
    second, received = scripted([call("reconcile_attendance", MATCH), END])
    run_agent(store, store.run(run["id"]), second, "确认使用学号")
    assert store.run(run["id"])["status"] == "completed"
    assert any(m.get("content") == "确认使用学号" for m in received[0]["messages"])


def test_unknown_tool_does_not_execute_and_model_can_recover(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, received = scripted([call("execute_python", {"code": "raise SystemExit"}), call("inspect_tables", {}), call("reconcile_attendance", MATCH), END])
    run_agent(store, run, model)
    assert run["status"] == "completed"
    assert any(e["status"] == "error" for e in run["trace"])
    assert "error" in received[1]["messages"][-1]["content"]


def test_invalid_column_can_be_corrected(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, _ = scripted([call("inspect_tables", {}), call("reconcile_attendance", {**MATCH, "registration_keys": ["不存在"]}), call("reconcile_attendance", MATCH), END])
    run_agent(store, run, model)
    assert run["status"] == "completed"
    assert len([e for e in run["trace"] if e["status"] == "error"]) == 1


def test_model_cannot_set_name_matching_consent(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, _ = scripted([call("inspect_tables", {}), call("reconcile_attendance", {**MATCH, "allow_name_only": True}), END])
    run_agent(store, run, model)
    assert run["status"] == "failed"
    assert run["result"] is None


def test_model_cannot_finish_without_actually_calculating(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, _ = scripted([END])
    run_agent(store, run, model)
    assert run["status"] == "failed"
    assert "未执行核对" in run["error"]


def test_grouping_is_a_separate_tool(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, _ = scripted([call("inspect_tables", {}), call("reconcile_attendance", {**MATCH, "group_by": None}), call("summarize_by_group", {"column": "班级"}), END])
    run_agent(store, run, model)
    assert len(run["result"]["groups"]) == 3


def test_loop_stops_at_limit(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    model, _ = scripted([call("inspect_tables", {})] * 10)
    run_agent(store, run, model)
    assert run["status"] == "failed"
    assert run["rounds"] == 10
    assert "上限" in run["error"]


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 302])
def test_proxy_errors_do_not_leak_response_body_or_key(status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="secret-from-provider test-secret"))
    model = ModelClient(ModelConfig("https://mock.invalid/v1", "test-model", "test-secret"), transport)
    with pytest.raises(ModelError) as exc:
        model.complete([], [])
    assert "test-secret" not in str(exc.value)
    assert "secret-from-provider" not in str(exc.value)


def test_timeout_is_a_controlled_failure(tmp_path, demo_tables):
    store, run = prepared(tmp_path, demo_tables)
    def timeout(request):
        raise httpx.ReadTimeout("test-secret must never be logged")
    model = ModelClient(ModelConfig("https://mock.invalid/v1", "m", "test-secret"), httpx.MockTransport(timeout))
    run_agent(store, run, model)
    assert run["status"] == "failed"
    assert "超时" in run["error"]
    assert "test-secret" not in json.dumps(run, ensure_ascii=False)


def test_malformed_model_response_is_controlled():
    model = ModelClient(ModelConfig("https://mock.invalid/v1", "m", "x"), httpx.MockTransport(lambda r: httpx.Response(200, json={"x": 1})))
    with pytest.raises(ModelError, match="响应格式"):
        model.complete([], [])


def test_background_api_uses_same_agent_loop(client):
    model, _ = scripted([call("inspect_tables", {}), call("reconcile_attendance", MATCH), call("reconcile_attendance", MATCH), END])
    client.app.state.client_factory = lambda: model
    dataset_id = client.post("/api/demo").json()["id"]
    response = client.post("/api/runs/agent", json={"dataset_id": dataset_id, "task": "核对学号并按班级统计", "consent_to_send": True})
    assert response.status_code == 202
    assert client.get(f'/api/runs/{response.json()["id"]}').json()["status"] == "needs_review"
    review = client.post(f'/api/runs/{response.json()["id"]}/review', json={"approved": True, "consent_to_send": True})
    assert review.status_code == 202
    result = client.get(f'/api/runs/{response.json()["id"]}').json()
    assert result["status"] == "completed"
    assert result["result"]["totals"]["attendance_rate"] == "66.67"


def test_model_config_reads_only_project_env_and_env_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('TABLE_AGENT_BASE_URL=https://mock.invalid/v1\nTABLE_AGENT_MODEL=m\nTABLE_AGENT_API_KEY=private-test\n', encoding="utf-8")
    monkeypatch.setenv("TABLE_AGENT_MODEL", "override")
    cfg = ModelConfig.load(tmp_path)
    assert cfg.ready and cfg.model == "override" and cfg.api_key == "private-test"


def test_extra_options_cannot_override_messages():
    model = ModelClient(ModelConfig("https://mock.invalid/v1", "m", "x", '{"messages":[]}'))
    with pytest.raises(ModelError, match="EXTRA_JSON"):
        model.complete([], [])
