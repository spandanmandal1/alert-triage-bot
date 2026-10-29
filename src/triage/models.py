from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL")
    fingerprint: str

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "unknown")

    @property
    def severity(self) -> str:
        return self.labels.get("severity", "none")

    @property
    def summary(self) -> str:
        return self.annotations.get("summary", "")

    @property
    def description(self) -> str:
        return self.annotations.get("description", "")


class WebhookPayload(BaseModel):
    """Alertmanager webhook body (version 4). Alerts arrive grouped, so one call can carry many."""

    model_config = ConfigDict(populate_by_name=True)

    version: str = "4"
    status: Literal["firing", "resolved"]
    receiver: str = ""
    external_url: str = Field(default="", alias="externalURL")
    alerts: list[Alert]
