import csv
import io
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.storage import Store
from app.makeups import MakeupRequest
from test_api import make_run


def payload(key=None, **extra):
    return {"key": key or ["004"], "reason": "签到设备故障，已人工核实", "signed_at": "2020-01-01T09:30:00+08:00",
            "confirmed_attendance": True, "operation_id": str(uuid.uuid4()), **extra}


def test_makeup_updates_totals_groups_exports_and_preserves_original(client):
    source = make_run(client)
    raw = (client.app.state.store.root / "uploads" / source["dataset_id"] / "checkin.csv").read_bytes()
    response = client.post(f'/api/runs/{source["id"]}/makeups', json=payload())
    assert response.status_code == 200
    run = response.json()
    assert run["id"] != source["id"] and run["parent_run_id"] == source["id"]
    assert run["mode"] == "local" and run["requests"] == 0
    totals = run["result"]["totals"]
    assert (totals["arrived"], totals["absent"], totals["attendance_rate"], totals["makeup_people"]) == (5, 1, "83.33", 1)
    assert totals["checkin_rows"] == 7
    assert next(g for g in run["result"]["groups"] if g["group"] == "软工二班")["arrived"] == 2
    original = client.get(f'/api/runs/{source["id"]}').json()
    assert original["result"]["totals"]["arrived"] == 4
    assert (client.app.state.store.root / "uploads" / source["dataset_id"] / "checkin.csv").read_bytes() == raw
    report = client.get(f'/api/runs/{run["id"]}/export/report').text
    assert "83.33%" in report and "其中人工补签：1 人" in report and "签到设备故障" in report
    exported = client.get(f'/api/runs/{run["id"]}/export/details')
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    person = next(r for r in rows if r["学号"] == "004")
    assert person["签到方式"] == "人工补签" and person["签到源记录号"] == ""
    assert person["实际签到时间"] == "2020-01-01T01:30:00+00:00"


def test_idempotent_retry_only_creates_one_snapshot(client):
    source = make_run(client)
    request = payload()
    one = client.post(f'/api/runs/{source["id"]}/makeups', json=request).json()
    two = client.post(f'/api/runs/{source["id"]}/makeups', json=request).json()
    assert one["id"] == two["id"]
    assert len(client.get("/api/runs").json()) == 2
    conflict = client.post(f'/api/runs/{source["id"]}/makeups', json={**request, "reason": "修改后的内容"})
    assert conflict.status_code == 409


def test_concurrent_retries_are_atomic(client):
    source = make_run(client)
    request = MakeupRequest.model_validate(payload())
    store = client.app.state.store
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: store.makeup(source["id"], request), range(2)))
    assert runs[0]["id"] == runs[1]["id"]
    assert len(store.recent_runs()) == 2


def test_multiple_makeups_and_duplicate_protection(client):
    source = make_run(client)
    first = client.post(f'/api/runs/{source["id"]}/makeups', json=payload()).json()
    assert client.post(f'/api/runs/{first["id"]}/makeups', json=payload()).status_code == 409
    second = client.post(f'/api/runs/{first["id"]}/makeups', json=payload(["006"])).json()
    assert second["result"]["totals"]["makeup_people"] == 2
    assert second["result"]["totals"]["attendance_rate"] == "100.00"
    records = client.get(f'/api/runs/{second["id"]}/export/makeups')
    assert len(list(csv.reader(io.StringIO(records.content.decode("utf-8-sig"))))) == 3
    assert client.get(f'/api/runs/{second["id"]}/makeup-candidates').json()["total"] == 0


@pytest.mark.parametrize("key", [["001"], ["missing"], [""]])
def test_cannot_makeup_arrived_unknown_or_blank_identifier(client, key):
    source = make_run(client)
    assert client.post(f'/api/runs/{source["id"]}/makeups', json=payload(key)).status_code in (409, 422)


@pytest.mark.parametrize("change", [{"reason": "   "}, {"signed_at": "bad"}, {"signed_at": "2020-01-01T09:00:00"},
                                     {"signed_at": "2099-01-01T09:00:00+08:00"}, {"operation_id": "x" * 36}, {"confirmed_attendance": False}])
def test_makeup_requires_valid_audit_fields(client, change):
    source = make_run(client)
    response = client.post(f'/api/runs/{source["id"]}/makeups', json=payload(**change))
    assert response.status_code in (409, 422)
    assert len(client.get("/api/runs").json()) == 1


def test_candidates_search_complete_result_beyond_preview(client):
    r = "id,name\n" + "\n".join(f"{i},person{i}" for i in range(250))
    dataset = client.post("/api/datasets", files={"registration": ("r.csv", r.encode()), "checkin": ("c.csv", b"id\n")}).json()
    source = client.post("/api/runs/local", json={"dataset_id": dataset["id"], "config": {"registration_keys": ["id"], "checkin_keys": ["id"]}}).json()
    matches = client.get(f'/api/runs/{source["id"]}/makeup-candidates?q=person249').json()
    assert matches["total"] == 1 and matches["candidates"][0]["key"] == ["249"]
    run = client.post(f'/api/runs/{source["id"]}/makeups', json=payload(["249"])).json()
    assert run["result"]["totals"]["arrived"] == 1


def test_makeups_persist_and_do_not_apply_to_other_runs(client):
    source = make_run(client)
    corrected = client.post(f'/api/runs/{source["id"]}/makeups', json=payload()).json()
    reloaded = Store(client.app.state.store.root).run(corrected["id"])
    assert reloaded["result"]["makeups"][0]["key"] == ["004"]
    another = client.post("/api/runs/local", json={"dataset_id": source["dataset_id"], "config": source["result"]["config"]}).json()
    assert another["result"]["totals"]["arrived"] == 4


def test_makeup_only_applies_to_finished_runs(client):
    source = make_run(client)
    store = client.app.state.store
    pending = store.create_run(source["dataset_id"], "agent", "等待计算")
    assert client.post(f'/api/runs/{pending["id"]}/makeups', json=payload()).status_code == 409
