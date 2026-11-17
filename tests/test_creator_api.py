import httpx
import pytest
from fastapi.testclient import TestClient

from app.keitaro import Keitaro
from app.main import create_app

CREATE = {
    "name": " New campaign ",
    "geo": ["au", "MX", "AU"],
    "offer_id": 1,
    "domain_id": 1,
    "group_id": 1,
    "traffic_source_id": 1,
}


def test_creator_uses_position_geo_filter_and_exactly_two_streams(client, tracker):
    response = client.post("/api/campaigns", json=CREATE)
    assert response.status_code == 201, response.text
    calls = [c for c in tracker.calls if c[0] == "POST"]
    assert [c[1] for c in calls] == ["campaigns", "streams", "streams"]
    campaign, geo, offers = [c[2] for c in calls]
    assert campaign["name"] == "New campaign"
    assert campaign["state"] == "disabled" and campaign["type"] == "position"
    assert all(campaign[key] == 1 for key in ["domain_id", "group_id", "traffic_source_id"])
    assert geo["position"] == 1 and geo["action_payload"] == "https://www.google.com/"
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]
    assert offers["position"] == 2 and offers["schema"] == "landings"
    assert offers["action_type"] == "http"
    assert offers["offers"] == [{"offer_id": 1, "share": 100, "state": "active"}]
    assert ("PUT", "campaigns/1", {"state": "active"}) in tracker.calls


@pytest.mark.parametrize(
    "change",
    [
        {"geo": ["ZZ"]},
        {"geo": []},
        {"name": " "},
        {"offer_id": 0},
        {"domain_id": -1},
        {"unexpected": True},
    ],
)
def test_invalid_create_never_writes_to_tracker(client, tracker, change):
    assert client.post("/api/campaigns", json={**CREATE, **change}).status_code == 422
    assert not tracker.calls


def test_creator_validates_reference_ids_before_writing(client, tracker):
    assert client.post("/api/campaigns", json={**CREATE, "group_id": 999}).status_code == 400
    assert not any(c[0] == "POST" for c in tracker.calls)


def test_partial_creation_is_archived(client, tracker):
    tracker.fail_create_stream = True
    response = client.post("/api/campaigns", json=CREATE)
    assert response.status_code == 502
    assert "перемещена в архив" in response.json()["detail"]
    assert ("DELETE", "campaigns/1", None) in tracker.calls


def test_failed_cleanup_reports_partial_campaign_id(client, tracker):
    tracker.fail_create_stream = tracker.fail_cleanup = True
    response = client.post("/api/campaigns", json=CREATE)
    assert response.status_code == 502
    assert "#1" in response.json()["detail"] and "Автоархивация не удалась" in response.text


def test_offer_search_by_id_name_and_pagination(client):
    assert client.get("/api/offers?q=Offer%20109").json()["items"] == [
        {"id": 109, "name": "Offer 109"}
    ]
    result = client.get("/api/offers?q=1&limit=2").json()
    assert result["items"][0]["id"] == 1
    assert result["total"] > 2
    assert client.get("/api/offers?offset=100&limit=10").json()["items"]
    assert client.get("/api/offers?limit=1000").status_code == 422


def test_catalogue_loads_on_fresh_boot_and_expires_after_ttl(client, tracker, monkeypatch):
    monkeypatch.setattr("app.service.monotonic", lambda: 5.0)
    assert client.get("/api/offers").json()["total"] == 109
    assert client.get("/api/offers").json()["total"] == 109
    assert len([c for c in tracker.calls if c[:2] == ("GET", "offers")]) == 1
    monkeypatch.setattr("app.service.monotonic", lambda: 36.0)
    assert client.get("/api/offers").json()["total"] == 109
    assert len([c for c in tracker.calls if c[:2] == ("GET", "offers")]) == 2


def test_no_credentials_or_internal_snapshots_in_responses(client, synced):
    text = client.get("/api/campaigns/1").text
    for secret in ['"baseline"', '"remote"', "test-key", "Api-Key"]:
        assert secret not in text


def test_security_headers_and_csrf(client):
    assert client.get("/").status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["Content-Security-Policy"]
    assert (
        client.post(
            "/api/campaigns", json=CREATE, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/campaigns", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.get("/api/campaigns", headers={"Host": "evil.example"}).status_code == 400


def test_optional_access_token(settings, tracker):
    settings.app_access_token = "app-test-token"
    api = Keitaro(
        settings.keitaro_base_url,
        settings.keitaro_api_key,
        transport=httpx.MockTransport(tracker.handle),
    )
    with TestClient(create_app(settings, api)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/config").status_code == 401
        assert (
            client.get(
                "/api/config", headers={"Authorization": "Bearer app-test-token"}
            ).status_code
            == 200
        )


def test_network_errors_are_safe_and_mutations_are_not_retried():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret-token", request=request)

    api = Keitaro("https://tracker.example", "secret-token", transport=httpx.MockTransport(timeout))
    from app.errors import AppError

    with pytest.raises(AppError) as error:
        api.post("campaigns", {"name": "test"})
    assert "secret-token" not in error.value.message
    assert len(calls) == 1
    api.close()


def test_only_safe_connect_failures_on_reads_are_retried():
    calls = []

    def transient(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ConnectTimeout("temporary", request=request)
        return httpx.Response(200, json=[])

    api = Keitaro("https://tracker.example", "test", transport=httpx.MockTransport(transient))
    assert api.get("campaigns") == []
    assert len(calls) == 3
    api.close()
