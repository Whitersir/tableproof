import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import create_app
from app.reconcile import MatchConfig
from app.tables import parse_csv


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("BASE_URL", "MODEL", "API_KEY", "EXTRA_JSON"):
        monkeypatch.delenv("TABLE_AGENT_" + key, raising=False)
    with TestClient(create_app(tmp_path / "data", config_root=tmp_path), headers={"X-Requested-With": "table-report-agent"}) as c:
        yield c


@pytest.fixture
def demo_tables():
    root = Path(__file__).resolve().parents[1] / "samples"
    return parse_csv((root / "registration.csv").read_bytes(), "报名.csv"), parse_csv((root / "checkin.csv").read_bytes(), "签到.csv")


@pytest.fixture
def config():
    return MatchConfig(registration_keys=["学号"], checkin_keys=["学生编号"], group_by="班级")
