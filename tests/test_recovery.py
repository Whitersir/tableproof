from app.main import create_app
from app.storage import Store


def test_service_restart_marks_unfinished_task_failed(tmp_path, demo_tables):
    root = tmp_path / "data"
    store = Store(root)
    dataset_id = store.save_dataset(*demo_tables, (b"r", b"c"))
    run = store.create_run(dataset_id, "agent", "核对")
    app = create_app(root)
    saved = app.state.store.run(run["id"])
    assert saved["status"] == "failed"
    assert saved["interrupted"] is True
    assert "上次服务退出" in saved["error"]
