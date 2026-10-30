from __future__ import annotations

import time

from fastapi.testclient import TestClient


def auth(client: TestClient) -> dict[str, str]:
    return {"X-Spacefinder-Token": client.app.state.token}


def wait_scan(client: TestClient, scan_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/scans/{scan_id}", headers=auth(client))
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"done", "cancelled", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("Сканирование не завершилось за отведённое время")
