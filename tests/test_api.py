import csv
import io

from app.main import create_app
from app.model_client import ModelClient, ModelConfig
from app.storage import Store


def make_run(client):
    dataset = client.post("/api/demo").json()
    response = client.post("/api/runs/local", json={"dataset_id": dataset["id"], "config": {
        "registration_keys": ["学号"], "checkin_keys": ["学生编号"], "group_by": "班级"}})
    assert response.status_code == 200
    return response.json()


def test_end_to_end_demo_and_all_downloads(client):
    run = make_run(client)
    assert run["status"] == "completed"
    assert "messages" not in run
    report = client.get(f'/api/runs/{run["id"]}/export/report')
    assert report.status_code == 200
    assert "66.67%" in report.text
    assert "attachment" in report.headers["content-disposition"]
    for kind, count in [("details", 6), ("issues", 5), ("groups", 3)]:
        response = client.get(f'/api/runs/{run["id"]}/export/{kind}')
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert len(rows) - 1 == count


def test_original_uploads_and_history_persist(client):
    run = make_run(client)
    store = Store(client.app.state.store.root)
    assert store.run(run["id"])["result"]["totals"]["arrived"] == 4
    assert (store.root / "uploads" / run["dataset_id"] / "registration.csv").exists()
    assert client.get("/api/runs").json()[0]["id"] == run["id"]


def test_upload_custom_csv_then_match(client):
    response = client.post("/api/datasets", files={"registration": ("r.csv", b"id,team\n001,A\n002,B\n"), "checkin": ("c.csv", b"number\n001\n")})
    assert response.status_code == 200
    dataset = response.json()
    run = client.post("/api/runs/local", json={"dataset_id": dataset["id"], "config": {"registration_keys": ["id"], "checkin_keys": ["number"]}}).json()
    assert run["result"]["totals"]["attendance_rate"] == "50.00"


def test_invalid_upload_does_not_create_dataset(client):
    response = client.post("/api/datasets", files={"registration": ("r.xlsx", b"x"), "checkin": ("c.csv", b"id\n1")})
    assert response.status_code == 422
    assert "CSV" in response.json()["detail"]


def test_missing_dataset_is_404(client):
    response = client.post("/api/runs/local", json={"dataset_id": "missing", "config": {"registration_keys": ["id"], "checkin_keys": ["id"]}})
    assert response.status_code == 404


def test_missing_api_configuration_is_explicit_not_fake_ai(client):
    client.app.state.client_factory = lambda: ModelClient(ModelConfig())
    dataset = client.post("/api/demo").json()
    response = client.post("/api/runs/agent", json={"dataset_id": dataset["id"], "task": "核对", "consent_to_send": True})
    assert response.status_code == 409
    assert "尚未配置" in response.json()["detail"]
    assert client.get("/api/runs").json() == []


def test_model_requires_explicit_data_transfer_consent(client):
    response = client.post("/api/runs/agent", json={"dataset_id": "x", "task": "核对"})
    assert response.status_code == 422
    assert "发送" in response.json()["detail"]


def test_cross_origin_and_non_app_posts_are_blocked(client):
    assert client.post("/api/demo", headers={"X-Requested-With": ""}).status_code == 403
    assert client.post("/api/demo", headers={"Origin": "https://example.com"}).status_code == 403


def test_preview_cap_does_not_truncate_export(client):
    r = "id\n" + "\n".join(str(n) for n in range(250))
    d = client.post("/api/datasets", files={"registration": ("r.csv", r.encode()), "checkin": ("c.csv", b"id\n0\n")}).json()
    run = client.post("/api/runs/local", json={"dataset_id": d["id"], "config": {"registration_keys": ["id"], "checkin_keys": ["id"]}}).json()
    assert len(run["result"]["details"]) == 200
    assert run["result"]["preview_counts"]["details"] == 250
    export = client.get(f'/api/runs/{run["id"]}/export/details')
    assert len(list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))) == 251


def test_index_and_static_files_have_browser_boundary(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/.env").status_code == 404
