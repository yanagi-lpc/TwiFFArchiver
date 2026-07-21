from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class UserRecord:
    """One archived account. Stable for future auto-follow tools."""

    user_id: str
    account_name: str
    display_name: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class FetchMeta:
    target_user_id: str
    target_account_name: str
    target_display_name: str
    started_at: str
    finished_at: str
    following_count: int
    followers_count: int
    tool_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
