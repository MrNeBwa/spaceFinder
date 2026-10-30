from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from spacefinder.core.models import ScanStatus

from .helpers import auth, wait_scan


def setup_cache(client: TestClient, days_old: int = 40) -> Path:
    cache = client.app.state.config.home / ".cache" / "pip"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "blob").write_bytes(b"x" * 5000)
    timestamp = time.time() - days_old * 86_400
    os.utime(cache, (timestamp, timestamp))
    return cache


def start_scan(client: TestClient) -> dict:
    response = client.post(
        "/api/scans",
        json={"rule_ids": ["user_cache"]},
        headers=auth(client),
    )
    assert response.status_code == 202, response.text
    return wait_scan(client, response.json()["scan_id"])


def test_health_does_not_require_token(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_config_rules_and_disk(client: TestClient) -> None:
    config = client.get("/api/config", headers=auth(client))
    assert config.status_code == 200
    assert config.json()["dry_run"] is True

    rules = client.get("/api/rules", headers=auth(client))
    assert rules.status_code == 200
    payload = rules.json()
    ids = {rule["id"] for rule in payload}
    assert "user_cache" in ids
    assert "system_info" in ids
    assert {"large_caches", "app_data", "dev_modules", "system_apps"} <= ids
    assert {rule["category"] for rule in payload} >= {"junk", "apps", "system"}

    disk = client.get("/api/disk", headers=auth(client))
    assert disk.status_code == 200
    assert disk.json()["total"] > 0


def test_scan_and_dry_run_delete(client: TestClient) -> None:
    cache = setup_cache(client)
    scan = start_scan(client)

    assert scan["status"] == "done"
    assert scan["items"], scan
    item = scan["items"][0]
    assert item["path"].endswith(".cache/pip")

    response = client.post(
        f"/api/scans/{scan['scan_id']}/delete",
        json={"item_ids": [item["id"]], "mode": "trash", "confirm": True},
        headers=auth(client),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["results"][0]["status"] == "simulated"
    assert payload["freed_bytes"] > 0
    assert cache.exists()

    history = client.get("/api/history", headers=auth(client))
    assert history.status_code == 200
    assert history.json()["entries"][-1]["status"] == "simulated"


def test_non_utf8_filename_is_serialized_and_audited(client: TestClient) -> None:
    """Имена с surrogateescape не должны ронять JSON-ответы и аудит."""

    cache = client.app.state.config.home / ".cache"
    raw = os.fsencode(str(cache)) + b"/bad-\xff"
    fd = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, b"x" * 100)
    finally:
        os.close(fd)
    timestamp = time.time() - 40 * 86_400
    os.utime(raw, (timestamp, timestamp))

    scan = start_scan(client)

    assert scan["status"] == "done"
    assert len(scan["items"]) == 1
    item = scan["items"][0]
    assert "\ufffd" in item["path"]

    response = client.post(
        f"/api/scans/{scan['scan_id']}/delete",
        json={"item_ids": [item["id"]], "mode": "trash", "confirm": True},
        headers=auth(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "simulated"

    history = client.get("/api/history", headers=auth(client))
    assert history.status_code == 200, history.text
    assert "\ufffd" in history.json()["entries"][-1]["path"]


def test_delete_requires_confirmation(client: TestClient) -> None:
    setup_cache(client)
    scan = start_scan(client)
    item = scan["items"][0]

    response = client.post(
        f"/api/scans/{scan['scan_id']}/delete",
        json={"item_ids": [item["id"]], "mode": "trash", "confirm": False},
        headers=auth(client),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "confirmation_required"


def test_unknown_item_returns_404(client: TestClient) -> None:
    setup_cache(client)
    scan = start_scan(client)

    response = client.post(
        f"/api/scans/{scan['scan_id']}/delete",
        json={"item_ids": ["deadbeef"], "mode": "trash", "confirm": True},
        headers=auth(client),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "item_not_found"


def test_permanent_disabled_by_default(client: TestClient) -> None:
    setup_cache(client)
    scan = start_scan(client)
    item = scan["items"][0]

    response = client.post(
        f"/api/scans/{scan['scan_id']}/delete",
        json={"item_ids": [item["id"]], "mode": "permanent", "confirm": True},
        headers=auth(client),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permanent_disabled"


def test_delete_while_scanning_is_conflict(client: TestClient) -> None:
    session = client.app.state.manager.start(["user_cache"])
    deadline = time.time() + 5
    while session.status in {ScanStatus.QUEUED, ScanStatus.RUNNING}:
        assert time.time() < deadline
        time.sleep(0.01)
    with session.lock:
        session.status = ScanStatus.RUNNING

    response = client.post(
        f"/api/scans/{session.id}/delete",
        json={"item_ids": ["anything"], "mode": "trash", "confirm": True},
        headers=auth(client),
    )

    session.cancel_event.set()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scan_not_ready"


def test_cancel_endpoint(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        json={"rule_ids": ["user_cache"]},
        headers=auth(client),
    )
    scan_id = response.json()["scan_id"]
    cancelled = client.post(f"/api/scans/{scan_id}/cancel", headers=auth(client))
    assert cancelled.status_code == 202
    wait_scan(client, scan_id)


def test_include_system_flag_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        json={"rule_ids": ["user_cache"], "include_system": True},
        headers=auth(client),
    )
    assert response.status_code == 202, response.text
    wait_scan(client, response.json()["scan_id"])


def test_empty_rule_list_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        json={"rule_ids": []},
        headers=auth(client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_invalid_rule_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        json={"rule_ids": ["does-not-exist"]},
        headers=auth(client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_trash_end_to_end(app_factory) -> None:
    app = app_factory(dry_run=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        cache = setup_cache(client)
        scan = start_scan(client)
        item = scan["items"][0]

        response = client.post(
            f"/api/scans/{scan['scan_id']}/delete",
            json={"item_ids": [item["id"]], "mode": "trash", "confirm": True},
            headers=auth(client),
        )

        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["status"] == "trashed"
        assert response.json()["freed_bytes"] == 0
        assert not cache.exists()
        trash_files = client.app.state.config.data_home / "Trash" / "files"
        assert any(path.name == "pip" for path in trash_files.iterdir())


def test_empty_trash_rule_deletes_permanently(app_factory) -> None:
    app = app_factory(dry_run=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trash_files = client.app.state.config.data_home / "Trash" / "files"
        item_path = trash_files / "old"
        item_path.mkdir(parents=True)
        (item_path / "blob").write_text("x", encoding="utf-8")
        info_path = (
            client.app.state.config.data_home / "Trash" / "info" / "old.trashinfo"
        )
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text("[Trash Info]\nPath=/tmp/old\n", encoding="utf-8")

        response = client.post(
            "/api/scans",
            json={"rule_ids": ["trash"]},
            headers=auth(client),
        )
        scan = wait_scan(client, response.json()["scan_id"])
        assert scan["items"][0]["in_trash"] is True
        item = scan["items"][0]

        deleted = client.post(
            f"/api/scans/{scan['scan_id']}/delete",
            json={"item_ids": [item["id"]], "mode": "trash", "confirm": True},
            headers=auth(client),
        )

        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["results"][0]["status"] == "deleted"
        assert not item_path.exists()
        assert not info_path.exists()


def test_permanent_allowed_but_dry_run(app_factory) -> None:
    app = app_factory(dry_run=True, allow_permanent=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        cache = setup_cache(client)
        scan = start_scan(client)
        item = scan["items"][0]

        response = client.post(
            f"/api/scans/{scan['scan_id']}/delete",
            json={"item_ids": [item["id"]], "mode": "permanent", "confirm": True},
            headers=auth(client),
        )

        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["status"] == "simulated"
        assert cache.exists()


def test_history_limit_is_validated(client: TestClient) -> None:
    too_small = client.get("/api/history?limit=0", headers=auth(client))
    too_large = client.get("/api/history?limit=201", headers=auth(client))

    assert too_small.status_code == 400
    assert too_large.status_code == 400
    assert too_small.json()["error"]["code"] == "invalid_request"


def test_i18n_static_asset_is_served(client: TestClient) -> None:
    response = client.get("/static/i18n/ru.json")

    assert response.status_code == 200
    assert response.json()["scan.running"]
