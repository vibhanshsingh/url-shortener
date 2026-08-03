"""
Pydantic schemas: the validation boundary. FastAPI runs these before
our route handler's body executes at all — malformed input never
reaches the service or repository layers.
"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class ShortenRequest(BaseModel):
    # HttpUrl (not `str`) rejects anything that isn't a syntactically
    # valid URL — no scheme, no host, garbage input — for free, before
    # any of our own code runs. This is Pydantic doing real validation
    # work, not just type hinting.
    url: HttpUrl = Field(
        ...,
        description="The long URL to shorten.",
        examples=["https://example.com/very/long/url/path"],
    )


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str
    created_at: datetime
    # Lets the caller distinguish "you got a brand new code" from
    # "this URL was already shortened, here's the existing code" —
    # useful for a client UI that might want to say "already shortened!"
    # instead of implying something new just happened.
    already_existed: bool

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "short_code": "0001a",
                    "short_url": "http://localhost:8000/0001a",
                    "long_url": "https://example.com/very/long/url/path",
                    "created_at": "2026-08-01T10:00:00Z",
                    "already_existed": False,
                }
            ]
        }
    }
