from copy import deepcopy

from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.database import create_user
from app.main import app
from app.services.auth import hash_password
from app.services.regions import create_region, get_region_profile, region_snapshot, update_region


def test_region_copy_and_configuration_isolation():
    source = get_region_profile("US", include_disabled=True)
    code = "EU_TEST"
    try:
        created = create_region({"code": code, "name": "欧洲测试区", "currency": "EUR", "copy_from": "US"})
    except ValueError:
        created = get_region_profile(code, include_disabled=True)

    assert created["settings"] == source["settings"]
    changed = deepcopy(created)
    changed["settings"]["order"]["operation_fee"] = 19
    updated = update_region(code, changed)

    assert updated["settings"]["order"]["operation_fee"] == 19
    assert get_region_profile("US", include_disabled=True)["settings"]["order"]["operation_fee"] != 19


def test_region_snapshot_does_not_change_after_update():
    code = "SNAP_TEST"
    try:
        create_region({"code": code, "name": "快照测试区", "currency": "CNY", "copy_from": "US"})
    except ValueError:
        pass
    before = region_snapshot(code)
    profile = get_region_profile(code, include_disabled=True)
    profile["settings"]["activity"]["headcost"] += 3
    update_region(code, profile)

    assert before["settings"]["activity"]["headcost"] != region_snapshot(code)["settings"]["activity"]["headcost"]
    assert before["versions"]["activity"] < region_snapshot(code)["versions"]["activity"]


def test_public_region_endpoints_only_return_enabled_regions():
    username = "region_api_user"
    try:
        create_user(username, hash_password("regionpass123"), status="approved")
    except Exception:
        pass
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": username, "password": "regionpass123"}).status_code == 200
        response = client.get("/api/regions")
        assert response.status_code == 200
        assert any(item["code"] == "US" for item in response.json()["items"])
        profile = client.get("/api/regions/US/settings")
        assert profile.status_code == 200
        assert "order" in profile.json()["settings"]

def test_activity_submission_keeps_selected_region_snapshot():
    username = "region_task_user"
    try:
        create_user(username, hash_password("regiontask123"), status="approved")
    except Exception:
        pass
    code = "TASK_TEST"
    try:
        create_region({"code": code, "name": "任务测试区", "currency": "CNY", "copy_from": "US"})
    except ValueError:
        pass
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-A-5", 30])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": username, "password": "regiontask123"})
        response = client.post(
            "/api/activities/bulk",
            data={"region_code": code},
            files={"file": ("activity.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 202
        task = response.json()
        assert task["region_code"] == code
        assert task["region_name"] == "任务测试区"
        assert task["config_version"] >= 1