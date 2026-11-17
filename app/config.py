from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keitaro_base_url: str = "https://your-tracker.example"
    keitaro_api_key: str = ""
    database_path: Path = Path("data/adrobot.sqlite3")
    app_access_token: str = ""

    @field_validator("keitaro_base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username:
            raise ValueError("Use an http(s) tracker URL without credentials")
        if url.path.rstrip("/") or url.query or url.fragment:
            raise ValueError("Use the tracker root URL, without /admin or /admin_api")
        return value.rstrip("/")
