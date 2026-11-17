"""The only module that knows Keitaro's HTTP contract. Never retries mutations."""

import ssl
from typing import Any

import httpx

from app.errors import AppError


class Keitaro:
    def __init__(self, base_url: str, api_key: str, transport=None):
        self.client = httpx.Client(
            base_url=f"{base_url}/admin_api/v1/",
            headers={"Api-Key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(30, connect=15),
            verify=ssl.create_default_context(),
            transport=transport,
            follow_redirects=False,
        )
        self.configured = bool(api_key)

    def request(self, method: str, path: str, payload: dict | None = None) -> Any:
        if not self.configured:
            raise AppError("Укажите KEITARO_BASE_URL и KEITARO_API_KEY в .env.", 503)
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(method, path, json=payload)
                break
            except httpx.RequestError as exc:
                if (
                    isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                    and attempt + 1 < attempts
                ):
                    continue
                raise AppError(
                    "Keitaro недоступен или не ответил вовремя. Если выполнялась запись, "
                    "проверьте её результат в Keitaro перед повтором.",
                    502,
                ) from exc
        if response.is_error or response.is_redirect:
            status = 404 if response.status_code == 404 else 502
            raise AppError(
                f"Keitaro: {method} /{path.split('?')[0]} — HTTP {response.status_code}. "
                "Проверьте доступ, данные и состояние трекера.",
                status,
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AppError("Keitaro вернул ответ в неожиданном формате.", 502) from exc

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, payload: dict):
        return self.request("POST", path, payload)

    def put(self, path: str, payload: dict):
        return self.request("PUT", path, payload)

    def close(self):
        self.client.close()
