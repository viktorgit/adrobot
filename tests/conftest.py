from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.keitaro import Keitaro
from app.main import create_app


class Tracker:
    """Contract-shaped fake HTTP server. No real credentials or external writes."""

    def __init__(self):
        self.calls = []
        self.fail_put = False
        self.fail_create_stream = False
        self.fail_cleanup = False
        self.campaign = {
            "id": 1,
            "name": "Demo",
            "state": "active",
            "group": "Tests",
            "domain": "https://tracker.example/",
        }
        self.offers = {i: {"id": i, "name": f"Offer {i}", "state": "active"} for i in range(1, 110)}
        self.streams = {
            10: {
                "id": 10,
                "campaign_id": 1,
                "name": "Geo",
                "position": 1,
                "type": "regular",
                "schema": "action",
                "action_payload": "https://www.google.com/",
                "offers": [],
            },
            20: {
                "id": 20,
                "campaign_id": 1,
                "name": "Offers",
                "position": 2,
                "schema": "landings",
                "filters": [{"name": "country", "payload": ["AU"]}],
                "landings": [{"landing_id": 50, "share": 100}],
                "offers": [
                    {"offer_id": 1, "share": 50, "state": "active"},
                    {"offer_id": 2, "share": 50, "state": "active"},
                ],
            },
        }

    def handle(self, request):
        import json

        path = request.url.path.removeprefix("/admin_api/v1/")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if request.method == "GET":
            if path == "campaigns":
                result = [self.campaign]
            elif path.startswith("campaigns/") and path.endswith("/streams"):
                result = list(self.streams.values())
            elif path.startswith("campaigns/"):
                result = self.campaign
            elif path.startswith("streams/"):
                result = self.streams.get(int(path.split("/")[1]))
            elif path == "offers":
                result = list(self.offers.values())
            elif path.startswith("offers/"):
                result = self.offers.get(int(path.split("/")[1]))
            elif path in {"domains", "groups", "traffic_sources"}:
                result = [{"id": 1, "name": "Tests", "state": "active"}]
            else:
                return httpx.Response(404, json={"error": "Unknown path"})
            return httpx.Response(200 if result is not None else 404, json=deepcopy(result))
        if request.method == "PUT":
            if self.fail_put:
                return httpx.Response(500, json={"error": "failure"})
            if path.startswith("streams/"):
                stream = self.streams[int(path.split("/")[1])]
                stream.update(body)
                return httpx.Response(200, json=deepcopy(stream))
            return httpx.Response(200, json=self.campaign)
        if request.method == "POST":
            if path == "streams" and not body.get("action_type"):
                return httpx.Response(422, json={"action_type": ["Action Type is required"]})
            if path == "streams" and self.fail_create_stream:
                return httpx.Response(500, json={"error": "failure"})
            if path == "campaigns":
                return httpx.Response(200, json={**body, "id": 1})
            return httpx.Response(200, json={**body, "id": 30})
        if request.method == "DELETE":
            return httpx.Response(500 if self.fail_cleanup else 200, json={"success": True})
        raise AssertionError(f"Unexpected call {request.method} {path}")


@pytest.fixture
def tracker():
    return Tracker()


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "test.db",
        keitaro_base_url="https://tracker.example",
        keitaro_api_key="test-key",
    )


@pytest.fixture
def client(settings, tracker):
    api = Keitaro(
        settings.keitaro_base_url,
        settings.keitaro_api_key,
        transport=httpx.MockTransport(tracker.handle),
    )
    with TestClient(
        create_app(settings, api), headers={"Content-Type": "application/json"}
    ) as client:
        yield client


@pytest.fixture
def synced(client):
    response = client.post("/api/campaigns/1/sync", json=None)
    assert response.status_code == 200, response.text
    return response.json()
