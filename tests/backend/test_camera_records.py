"""Portal cameras with "Use for records" are handed to their agent while online."""
import pytest

import server
from camera_manager import CameraState, camera_manager


@pytest.fixture
def portal_camera(db):
    """Make a direct-URL camera exist in the DB and in the live registry with a given status."""
    made = []

    def _make(cid, status="online", **cfg):
        db.camera_config.update_one({"camera_id": cid}, {"$set": {
            "camera_id": cid, "name": f"{cid} name", "source": f"http://10.0.0.5:8080/{cid}", **cfg}}, upsert=True)
        with camera_manager._lock:
            camera_manager._cams[cid] = CameraState(camera_id=cid, source=f"http://10.0.0.5:8080/{cid}", status=status)
        made.append(cid)

    yield _make
    for cid in made:
        with camera_manager._lock:
            camera_manager._cams.pop(cid, None)


def _register(ws, agent_id):
    ws.send_json({"type": "agent_register", "agent_id": agent_id})
    assert ws.receive_json()["type"] == "registered"
    msg = ws.receive_json()
    assert msg["type"] == "camera_assignments"
    return [c["camera_id"] for c in msg["cameras"]], msg["cameras"]


def test_only_online_enabled_cameras_of_this_agent_are_assigned(client, db, agent_ws, portal_camera):
    portal_camera("CAM-A", use_for_records=True, record_agent_id="pc-1")
    portal_camera("CAM-OFF", status="offline", use_for_records=True, record_agent_id="pc-1")
    portal_camera("CAM-DIS", use_for_records=True, record_agent_id="pc-1", enabled=False)
    portal_camera("CAM-OTHER", use_for_records=True, record_agent_id="pc-2")
    portal_camera("CAM-NO", use_for_records=False, record_agent_id="pc-1")
    with agent_ws("pc-1") as ws:
        ids, cams = _register(ws, "pc-1")
    assert ids == ["CAM-A"]
    assert cams[0]["source"] == "http://10.0.0.5:8080/CAM-A" and cams[0]["name"] == "CAM-A name"


def test_toggle_needs_a_camera_url(client, admin_headers, db):
    db.camera_config.insert_one({"camera_id": "AGENT-CAM", "role": "both"})
    r = client.put("/api/camera-config/AGENT-CAM/records", headers=admin_headers, json={"use_for_records": True})
    assert r.status_code == 400


def test_toggle_without_agent_and_none_online_is_refused(client, admin_headers, portal_camera):
    portal_camera("CAM-A")
    r = client.put("/api/camera-config/CAM-A/records", headers=admin_headers, json={"use_for_records": True})
    assert r.status_code == 400 and "online" in r.json()["detail"]


def test_toggle_pushes_assignment_to_connected_agent(client, admin_headers, db, agent_ws, portal_camera):
    portal_camera("CAM-A")
    with agent_ws("pc-1") as ws:
        assert _register(ws, "pc-1")[0] == []
        # Only one agent online → it is picked automatically.
        r = client.put("/api/camera-config/CAM-A/records", headers=admin_headers, json={"use_for_records": True})
        assert r.status_code == 200 and r.json()["record_agent_id"] == "pc-1"
        msg = ws.receive_json()
        assert msg["type"] == "camera_assignments" and [c["camera_id"] for c in msg["cameras"]] == ["CAM-A"]

        client.put("/api/camera-config/CAM-A/records", headers=admin_headers, json={"use_for_records": False})
        assert ws.receive_json() == {"type": "camera_assignments", "cameras": []}
    row = next(x for x in client.get("/api/cameras/overview", headers=admin_headers).json() if x["camera_id"] == "CAM-A")
    assert row["use_for_records"] is False and row["record_agent_id"] == "pc-1"


def test_camera_going_offline_is_taken_back(client, db, agent_ws, portal_camera):
    portal_camera("CAM-A", use_for_records=True, record_agent_id="pc-1")
    with agent_ws("pc-1") as ws:
        assert _register(ws, "pc-1")[0] == ["CAM-A"]
        camera_manager.get("CAM-A").status = "offline"
        client.portal.call(server._push_assignments, "pc-1")  # what _assignment_loop does every few seconds
        assert ws.receive_json() == {"type": "camera_assignments", "cameras": []}


def test_unknown_agent_is_refused(client, admin_headers, portal_camera):
    portal_camera("CAM-A")
    r = client.put("/api/camera-config/CAM-A/records", headers=admin_headers,
                   json={"use_for_records": True, "agent_id": "nope"})
    assert r.status_code == 404
