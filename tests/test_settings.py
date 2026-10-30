import json

import httpx
import pytest

from app.model_client import ModelClient, ModelConfig
from test_agent import call, scripted, MATCH, END

PAYLOAD = {"base_url": "https://mock.invalid/v1", "model": "mock-tools", "api_key": "unit-test-private-key", "extra_json": "{}"}


def test_settings_save_persist_never_return_key(client):
    response = client.post("/api/settings/model", json=PAYLOAD)
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["has_api_key"] is True
    assert PAYLOAD["api_key"] not in response.text
    assert PAYLOAD["api_key"] not in client.get("/api/settings/model").text
    private = client.app.state.settings.path
    assert json.loads(private.read_text(encoding="utf-8"))["TABLE_AGENT_API_KEY"] == PAYLOAD["api_key"]
    client.post("/api/settings/model", json={**PAYLOAD, "api_key": "", "model": "another-model"})
    assert client.app.state.settings.effective().api_key == PAYLOAD["api_key"]


def test_saved_key_not_reused_for_new_host(client):
    client.post("/api/settings/model", json=PAYLOAD)
    response = client.post("/api/settings/model", json={**PAYLOAD, "base_url": "https://different.invalid/v1", "api_key": ""})
    assert response.status_code == 422
    assert "重新输入" in response.json()["detail"]
    assert client.app.state.settings.effective().base_url == PAYLOAD["base_url"]


def test_clear_removes_private_file(client):
    client.post("/api/settings/model", json=PAYLOAD)
    response = client.delete("/api/settings/model")
    assert response.status_code == 200
    assert not response.json()["configured"]
    assert not client.app.state.settings.path.exists()


def test_clear_does_not_remove_env_config(client, monkeypatch):
    client.post("/api/settings/model", json=PAYLOAD)
    monkeypatch.setenv("TABLE_AGENT_BASE_URL", "https://env.invalid/v1")
    monkeypatch.setenv("TABLE_AGENT_MODEL", "env-model")
    monkeypatch.setenv("TABLE_AGENT_API_KEY", "env-private-key")
    response = client.delete("/api/settings/model")
    assert response.json()["configured"]
    assert "API_KEY" in response.json()["environment_overrides"]
    assert "env-private-key" not in response.text


def test_settings_validation_never_echoes_key(client):
    secret = "do-not-echo-this-key" * 300
    response = client.post("/api/settings/model", json={**PAYLOAD, "api_key": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert "input" not in response.text


@pytest.mark.parametrize("url", ["http://remote.invalid/v1", "https://mock.invalid/v1?api_key=secret", "https://user:password@mock.invalid/v1", "https://mock.invalid/v1/chat/completions"])
def test_invalid_settings_not_saved(client, url):
    response = client.post("/api/settings/model", json={**PAYLOAD, "base_url": url})
    assert response.status_code == 422
    assert not client.app.state.settings.path.exists()


def test_probe_requires_consent_without_network(client):
    response = client.post("/api/settings/model/test", json=PAYLOAD)
    assert response.status_code == 422
    assert "计费" in response.json()["detail"]


def test_probe_verifies_tool_call_and_does_not_save(client):
    def factory(config):
        def handler(request):
            body = json.loads(request.content)
            assert body["tool_choice"]["function"]["name"] == "connection_probe"
            assert "registration" not in request.content.decode()
            return httpx.Response(200, json={"choices": [{"message": call("connection_probe", {"value": "ok"})}], "usage": {"prompt_tokens": 10}})
        return ModelClient(config, httpx.MockTransport(handler))
    client.app.state.probe_factory = factory
    response = client.post("/api/settings/model/test", json={**PAYLOAD, "consent_to_test": True})
    assert response.status_code == 200
    assert response.json()["tool_call_verified"] is True
    assert not client.app.state.settings.path.exists()
    assert PAYLOAD["api_key"] not in response.text


def test_text_only_probe_is_not_claimed_tool_success(client):
    client.app.state.probe_factory = lambda c: ModelClient(c, httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": END}]})))
    response = client.post("/api/settings/model/test", json={**PAYLOAD, "consent_to_test": True})
    assert response.json()["reachable"] is True
    assert response.json()["tool_call_verified"] is False


def test_repr_hides_key():
    assert "secret-value" not in repr(ModelConfig("https://mock.invalid", "m", "secret-value"))


def test_review_revision_cannot_silently_run_old_plan(client):
    changed = {**MATCH, "registration_filters": [{"column": "班级", "value": "软工一班"}]}
    model, _ = scripted([call("inspect_tables", {}), call("reconcile_attendance", MATCH),
                         call("reconcile_attendance", changed), call("reconcile_attendance", changed), END])
    client.app.state.client_factory = lambda: model
    dataset_id = client.post("/api/demo").json()["id"]
    run_id = client.post("/api/runs/agent", json={"dataset_id": dataset_id, "task": "核对报名与签到", "consent_to_send": True}).json()["id"]
    first = client.get(f"/api/runs/{run_id}").json()
    assert first["status"] == "needs_review" and first["result"] is None
    assert client.get(f"/api/runs/{run_id}/export/report").status_code == 409
    client.post(f"/api/runs/{run_id}/review", json={"approved": False, "note": "只看软工一班", "consent_to_send": True})
    second = client.get(f"/api/runs/{run_id}").json()
    assert second["status"] == "needs_review" and second["result"] is None
    assert second["pending_config"]["registration_filters"] == changed["registration_filters"]
    client.post(f"/api/runs/{run_id}/review", json={"approved": True, "consent_to_send": True})
    finished = client.get(f"/api/runs/{run_id}").json()
    assert finished["status"] == "completed"
    assert finished["result"]["totals"]["valid_registered"] == 2


def test_wrong_review_state_rejected(client):
    model, _ = scripted([])
    client.app.state.client_factory = lambda: model
    d = client.post("/api/demo").json()["id"]
    run = client.post("/api/runs/local", json={"dataset_id": d, "config": MATCH}).json()
    assert client.post(f'/api/runs/{run["id"]}/review', json={"approved": True, "consent_to_send": True}).status_code == 409


def test_model_cannot_change_group_after_approval_without_new_review(client):
    model, _ = scripted([call("inspect_tables", {}), call("reconcile_attendance", MATCH),
                         call("reconcile_attendance", MATCH), call("summarize_by_group", {"column": "姓名"})])
    client.app.state.client_factory = lambda: model
    dataset_id = client.post("/api/demo").json()["id"]
    run_id = client.post("/api/runs/agent", json={"dataset_id": dataset_id, "task": "按班级统计", "consent_to_send": True}).json()["id"]
    client.post(f"/api/runs/{run_id}/review", json={"approved": True, "consent_to_send": True})
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "needs_review"
    assert run["pending_config"]["group_by"] == "姓名"
    assert run["result"]["config"]["group_by"] == "班级"
    assert run["calculation_current"] is False
