from __future__ import annotations

from fastapi.testclient import TestClient

from .helpers import auth


def test_api_requires_token(client: TestClient) -> None:
    response = client.get("/api/config")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "unauthorized"


def test_wrong_token_rejected(client: TestClient) -> None:
    response = client.get("/api/config", headers={"X-Spacefinder-Token": "wrong"})
    assert response.status_code == 403


def test_valid_token_accepted(client: TestClient) -> None:
    response = client.get("/api/config", headers=auth(client))
    assert response.status_code == 200


def test_non_ascii_token_is_rejected_without_crashing(client: TestClient) -> None:
    # httpx не отправляет non-ASCII str в заголовке, поэтому передаём байты.
    response = client.get(
        "/api/config",
        headers={"X-Spacefinder-Token": "не-токен".encode()},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "unauthorized"


def test_non_utf8_rule_id_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        content=b'{"rule_ids":["bad\\udcff"]}',
        headers={**auth(client), "Content-Type": "application/json"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_bad_host_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_host"


def test_foreign_origin_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        json={"rule_ids": ["user_cache"]},
        headers={**auth(client), "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "unauthorized"


def test_local_origin_allowed(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        json={"rule_ids": ["user_cache"]},
        headers={**auth(client), "Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 202


def test_static_allowlist(client: TestClient) -> None:
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/nope.js").status_code == 404

    traversal = client.get("/static/../pyproject.toml")
    assert traversal.status_code != 200
    assert "[build-system]" not in traversal.text


def test_security_headers_and_token_meta(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert 'name="spacefinder-token"' in response.text
    assert client.app.state.token in response.text


def test_invalid_json_returns_unified_error(client: TestClient) -> None:
    response = client.post(
        "/api/scans",
        content="{not json",
        headers={**auth(client), "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_unknown_route_returns_unified_error(client: TestClient) -> None:
    response = client.get("/api/nope", headers=auth(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_csp_is_set_on_error_responses(client: TestClient) -> None:
    response = client.get("/api/config")
    assert response.status_code == 403
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_encoded_static_traversal_is_rejected(client: TestClient) -> None:
    response = client.get("/static/%2e%2e/pyproject.toml")
    assert response.status_code == 404
    assert "[build-system]" not in response.text


def test_index_has_noscript_fallback(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "<noscript>" in response.text
    assert "<script>" not in response.text.lower()
