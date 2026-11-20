"""Central Cameras page: one overview, persisted direct-URL sources, one display name."""
import pytest

import server
from camera_manager import camera_manager
from conftest import login, make_user

URL = "rtsp://admin:s3cret@10.0.0.5:554/stream"


@pytest.fixture(autouse=True)
def _no_real_capture(monkeypatch):
    # Capture threads start but never open a real stream; clean the registry afterwards.
    monkeypatch.setattr(camera_manager, "_open_capture", lambda source: None)
    yield
    for cid in list(camera_manager.list()):
        camera_manager.remove(cid)


def overview_row(client, headers, cid):
    rows = client.get("/api/cameras/overview", headers=headers).json()
    return next(r for r in rows if r["camera_id"] == cid)


def test_connect_persists_source_role_and_name(client, db, admin_headers):
    r = client.post("/api/cameras/connect", headers=admin_headers,
                    json={"camera_id": "gate-01", "source": URL, "label": "Main gate in", "role": "entry", "gate": "Main"})
    assert r.status_code == 200, r.text
    assert "s3cret" not in r.json()["source"]
    cfg = db.camera_config.find_one({"camera_id": "GATE-01"})
    assert cfg["source"] == URL and cfg["role"] == "entry" and cfg["name"] == "Main gate in" and cfg["gate"] == "Main"

    row = overview_row(client, admin_headers, "GATE-01")
    assert row["source_type"] == "rtsp" and row["role"] == "entry" and row["configured"] is True and row["live"] is True
    assert row["source"] == "rtsp://admin:***@10.0.0.5:554/stream"

    live = client.get("/api/cameras", headers=admin_headers).json()["GATE-01"]
    assert live["role"] == "entry" and live["label"] == "Main gate in" and "s3cret" not in live["source"]


def test_direct_camera_restored_after_restart(client, db, admin_headers):
    client.post("/api/cameras/connect", headers=admin_headers, json={"camera_id": "GATE-02", "source": URL})
    camera_manager.remove("GATE-02")  # simulate the in-memory registry being lost on restart
    assert client.portal.call(server.restore_direct_cameras) == 1
    assert camera_manager.get("GATE-02") is not None


def test_disconnect_forgets_source_but_keeps_role(client, db, admin_headers):
    client.post("/api/cameras/connect", headers=admin_headers,
                json={"camera_id": "GATE-03", "source": URL, "role": "exit"})
    assert client.post("/api/cameras/GATE-03/disconnect", headers=admin_headers).status_code == 200
    cfg = db.camera_config.find_one({"camera_id": "GATE-03"})
    assert "source" not in cfg and cfg["role"] == "exit"
    assert client.portal.call(server.restore_direct_cameras) == 0
    row = overview_row(client, admin_headers, "GATE-03")
    assert row["live"] is False and row["source_type"] == "none"


def test_manager_cannot_set_role_on_connect(client, db, admin_headers):
    _, pw = make_user(client, admin_headers, "manager")
    mgr = db.users.find_one({"role": "manager"})
    headers = login(client, mgr["email"], pw)
    r = client.post("/api/cameras/connect", headers=headers, json={"camera_id": "GATE-04", "source": URL, "role": "exit"})
    assert r.status_code == 200, r.text
    assert "role" not in db.camera_config.find_one({"camera_id": "GATE-04"})
    row = overview_row(client, headers, "GATE-04")
    assert row["configured"] is False and row["role"] == "both"


def test_agent_camera_listed_and_rename_updates_live_label(client, db, admin_headers):
    db.agents.insert_one({"agent_id": "showroom-pc", "cameras": ["CAM-A"]})
    camera_manager.register_agent_camera("CAM-A", agent_id="showroom-pc")
    row = overview_row(client, admin_headers, "CAM-A")
    assert row["source_type"] == "agent" and row["agent_id"] == "showroom-pc" and row["source"] == ""

    client.put("/api/camera-config/CAM-A", headers=admin_headers, json={"role": "exit", "name": "Exit lane"})
    assert client.get("/api/cameras", headers=admin_headers).json()["CAM-A"]["label"] == "Exit lane"


def test_remove_deletes_config_and_stops_stream(client, db, admin_headers):
    client.post("/api/cameras/connect", headers=admin_headers, json={"camera_id": "GATE-05", "source": URL, "role": "entry"})
    assert client.delete("/api/camera-config/GATE-05", headers=admin_headers).status_code == 200
    assert db.camera_config.find_one({"camera_id": "GATE-05"}) is None
    assert camera_manager.get("GATE-05") is None
