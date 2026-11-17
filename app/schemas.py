from typing import Literal

import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCampaign(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    geo: list[str] = Field(min_length=1, max_length=249)
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Название не может быть пустым")
        return value.strip()

    @field_validator("geo")
    @classmethod
    def clean_geo(cls, values):
        codes = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(pycountry.countries.get(alpha_2=code) is None for code in codes):
            raise ValueError("Используйте двухбуквенные ISO-коды стран: AU, MX, RO")
        return codes


class Revision(StrictModel):
    revision: int = Field(ge=1)


class EditOffer(Revision):
    action: Literal["add", "remove", "restore", "pin", "unpin"]
    offer_id: int = Field(gt=0)
    share: int | None = Field(default=None, ge=1, le=100)
