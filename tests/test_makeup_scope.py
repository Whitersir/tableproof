from test_makeups import payload


def make_source(client, registration, checkin, config):
    dataset = client.post("/api/datasets", files={"registration": ("r.csv", registration.encode()), "checkin": ("c.csv", checkin.encode())}).json()
    return client.post("/api/runs/local", json={"dataset_id": dataset["id"], "config": config}).json()


def test_composite_key_makeup_does_not_mark_same_name_other_class(client):
    source = make_source(client, "姓名,班级\n甲,一班\n甲,二班\n", "姓名,班级\n", {
        "registration_keys": ["姓名", "班级"], "checkin_keys": ["姓名", "班级"]})
    corrected = client.post(f'/api/runs/{source["id"]}/makeups', json=payload(["甲", "二班"])).json()
    people = {tuple(p["key"]): p["status"] for p in corrected["result"]["details"]}
    assert people == {("甲", "一班"): "未到场", ("甲", "二班"): "已到场"}


def test_out_of_scope_person_cannot_be_added_by_makeup(client):
    source = make_source(client, "id,班级\n1,一班\n2,二班\n", "id\n", {
        "registration_keys": ["id"], "checkin_keys": ["id"], "registration_filters": [{"column": "班级", "value": "一班"}]})
    assert client.post(f'/api/runs/{source["id"]}/makeups', json=payload(["2"])).status_code == 409
    assert client.get(f'/api/runs/{source["id"]}').json()["result"]["totals"]["arrived"] == 0


def test_conflicting_registration_cannot_be_force_matched(client):
    source = make_source(client, "id,姓名\n1,甲\n1,乙\n2,丙\n", "id\n", {"registration_keys": ["id"], "checkin_keys": ["id"]})
    assert client.post(f'/api/runs/{source["id"]}/makeups', json=payload(["1"])).status_code == 409
    assert len(client.get("/api/runs").json()) == 1
