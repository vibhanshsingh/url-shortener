"""
This is the wire format for click events — the contract between the
producer (redirect endpoint) and the consumer (worker process). Both
sides import this module rather than each hand-rolling their own
dict/JSON shape, so a field rename or type change is caught by every
caller at once instead of silently drifting apart between two
independently-maintained pieces of code.
"""

import json
from datetime import datetime, timezone

from pydantic import BaseModel


class ClickEvent(BaseModel):
    short_code: str
    timestamp: datetime
    ip_address: str | None = None
    user_agent: str | None = None
    referrer: str | None = None

    def to_kafka_value(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_kafka_value(cls, raw: bytes) -> "ClickEvent":
        return cls.model_validate(json.loads(raw))

    @classmethod
    def now(
        cls,
        short_code: str,
        ip_address: str | None,
        user_agent: str | None,
        referrer: str | None,
    ) -> "ClickEvent":
        return cls(
            short_code=short_code,
            timestamp=datetime.now(timezone.utc),
            ip_address=ip_address,
            user_agent=user_agent,
            referrer=referrer,
        )
