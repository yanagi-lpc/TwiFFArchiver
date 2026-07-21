from __future__ import annotations

import json
import time
from typing import Any, Callable, Literal

import httpx

from twiffarchiver.auth import Credentials
from twiffarchiver.models import UserRecord
from twiffarchiver.query_ids import (
    BEARER_TOKEN,
    USER_AGENT,
    discover_query_ids,
    iter_fallback_ids,
    resolve_query_id,
)

ListKind = Literal["following", "followers"]

USER_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

TIMELINE_FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "organizations_verification_info_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


class XClientError(RuntimeError):
    """X API / GraphQL failure."""


class XClient:
    GRAPHQL_BASE = "https://x.com/i/api/graphql"
    REST_BASE = "https://x.com/i/api/1.1"

    def __init__(
        self,
        credentials: Credentials,
        *,
        request_gap: float = 1.0,
        max_retries: int = 6,
    ) -> None:
        self.credentials = credentials
        self.request_gap = request_gap
        self.max_retries = max_retries
        self._last_request = 0.0
        self._query_ids = discover_query_ids()
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {BEARER_TOKEN}",
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "x-twitter-active-user": "yes",
                "x-twitter-auth-type": "OAuth2Session",
                "x-twitter-client-language": "en",
                "x-csrf-token": credentials.ct0,
                "Cookie": credentials.as_cookie_header(),
                "Referer": "https://x.com/",
                "Origin": "https://x.com",
            },
            timeout=45.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> XClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.request_gap:
            time.sleep(self.request_gap - elapsed)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        delay = 2.0
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                response = self._client.request(method, url, **kwargs)
                self._last_request = time.monotonic()
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue

            if response.status_code == 429:
                reset = response.headers.get("x-rate-limit-reset")
                wait = delay
                if reset and reset.isdigit():
                    wait = max(delay, int(reset) - int(time.time()) + 1)
                time.sleep(min(wait, 180.0))
                delay = min(delay * 2, 60.0)
                continue

            if response.status_code in {500, 502, 503, 504}:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue

            return response

        if last_error:
            raise XClientError(f"Request failed after retries: {last_error}") from last_error
        raise XClientError(f"Request failed after {self.max_retries} retries: {url}")

    def _graphql_get(
        self,
        operation: str,
        variables: dict[str, Any],
        features: dict[str, Any],
        field_toggles: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        primary = resolve_query_id(operation, self._query_ids)
        params_base: dict[str, str] = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(features, separators=(",", ":")),
        }
        if field_toggles is not None:
            params_base["fieldToggles"] = json.dumps(
                field_toggles, separators=(",", ":")
            )

        last_error = "unknown error"
        for qid in iter_fallback_ids(operation, primary):
            url = f"{self.GRAPHQL_BASE}/{qid}/{operation}"
            response = self._request("GET", url, params=params_base)
            if response.status_code == 404:
                last_error = f"404 for queryId {qid}"
                continue
            if response.status_code >= 400:
                body = response.text[:300]
                raise XClientError(
                    f"GraphQL {operation} HTTP {response.status_code}: {body}"
                )
            data = response.json()
            if data.get("errors") and not data.get("data"):
                msgs = "; ".join(
                    str(e.get("message", e)) for e in data["errors"]
                )
                # stale query id sometimes returns errors — try next
                if "Query not found" in msgs or "DependencyError" in msgs:
                    last_error = msgs
                    continue
                raise XClientError(f"GraphQL {operation} errors: {msgs}")
            # remember working id
            self._query_ids[operation] = qid
            return data
        raise XClientError(f"GraphQL {operation} failed: {last_error}")

    @staticmethod
    def normalize_screen_name(value: str) -> str:
        name = value.strip()
        if name.startswith("@"):
            name = name[1:]
        if not name:
            raise XClientError("Empty account name")
        return name

    @staticmethod
    def _user_from_result(result: dict[str, Any] | None) -> UserRecord | None:
        if not result or not isinstance(result, dict):
            return None
        if result.get("__typename") == "UserUnavailable":
            return None
        user_id = result.get("rest_id")
        legacy = result.get("legacy") or {}
        core = result.get("core") or {}
        account_name = (
            legacy.get("screen_name")
            or core.get("screen_name")
            or result.get("username")
        )
        display_name = legacy.get("name") or core.get("name") or account_name
        if not user_id or not account_name:
            return None
        return UserRecord(
            user_id=str(user_id),
            account_name=str(account_name),
            display_name=str(display_name or account_name),
        )

    def resolve_user(self, screen_name: str) -> UserRecord:
        handle = self.normalize_screen_name(screen_name)
        data = self._graphql_get(
            "UserByScreenName",
            {"screen_name": handle, "withSafetyModeUserFields": True},
            USER_FEATURES,
            {"withAuxiliaryUserLabels": False},
        )
        result = (
            data.get("data", {})
            .get("user", {})
            .get("result")
        )
        if isinstance(result, dict) and result.get("__typename") == "UserUnavailable":
            raise XClientError(
                f"User @{handle} is unavailable (private/suspended/not found)"
            )
        user = self._user_from_result(result if isinstance(result, dict) else None)
        if user:
            return user

        # REST fallback
        url = f"{self.REST_BASE}/users/show.json"
        response = self._request("GET", url, params={"screen_name": handle})
        if response.status_code == 404:
            raise XClientError(f"User @{handle} not found")
        if response.status_code >= 400:
            raise XClientError(
                f"users/show HTTP {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        uid = payload.get("id_str") or payload.get("id")
        if not uid:
            raise XClientError(f"Could not resolve @{handle}")
        return UserRecord(
            user_id=str(uid),
            account_name=str(payload.get("screen_name") or handle),
            display_name=str(payload.get("name") or handle),
        )

    @staticmethod
    def _parse_timeline_users(data: dict[str, Any]) -> tuple[list[UserRecord], str | None]:
        users: list[UserRecord] = []
        bottom_cursor: str | None = None
        instructions = (
            data.get("data", {})
            .get("user", {})
            .get("result", {})
            .get("timeline", {})
            .get("timeline", {})
            .get("instructions")
        )
        # Alternate shapes
        if not instructions:
            instructions = (
                data.get("data", {})
                .get("user", {})
                .get("result", {})
                .get("timeline_v2", {})
                .get("timeline", {})
                .get("instructions")
            )
        if not isinstance(instructions, list):
            return users, None

        entries: list[dict[str, Any]] = []
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            itype = instruction.get("type")
            if itype == "TimelineAddEntries":
                entries.extend(instruction.get("entries") or [])
            elif itype == "TimelineReplaceEntry":
                entry = instruction.get("entry")
                if entry:
                    entries.append(entry)

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("entryId") or "")
            content = entry.get("content") or {}
            if entry_id.startswith("cursor-bottom") or content.get("cursorType") == "Bottom":
                value = content.get("value")
                if isinstance(value, str) and value:
                    bottom_cursor = value
                continue
            if entry_id.startswith("cursor-"):
                continue

            item = content.get("itemContent") or {}
            user_result = (
                item.get("user_results", {}).get("result")
                if isinstance(item, dict)
                else None
            )
            # Some layouts nest under content.user
            if not user_result and isinstance(content.get("user"), dict):
                user_result = content["user"].get("result") or content["user"]

            record = XClient._user_from_result(
                user_result if isinstance(user_result, dict) else None
            )
            if record:
                users.append(record)

        return users, bottom_cursor

    def _fetch_list_graphql(
        self,
        kind: ListKind,
        user_id: str,
        *,
        on_page: Callable[[int, int], None] | None = None,
    ) -> list[UserRecord]:
        operation = "Following" if kind == "following" else "Followers"
        cursor: str | None = None
        collected: list[UserRecord] = []
        seen: set[str] = set()
        page = 0

        while True:
            variables: dict[str, Any] = {
                "userId": user_id,
                "count": 100,
                "includePromotedContent": False,
            }
            if cursor:
                variables["cursor"] = cursor

            data = self._graphql_get(operation, variables, TIMELINE_FEATURES)
            page_users, next_cursor = self._parse_timeline_users(data)
            page += 1
            for user in page_users:
                if user.user_id not in seen:
                    seen.add(user.user_id)
                    collected.append(user)
            if on_page:
                on_page(page, len(collected))
            if not next_cursor or next_cursor == cursor or not page_users:
                break
            cursor = next_cursor

        return collected

    def _fetch_list_rest(
        self,
        kind: ListKind,
        user_id: str,
        *,
        on_page: Callable[[int, int], None] | None = None,
    ) -> list[UserRecord]:
        endpoint = "friends/list.json" if kind == "following" else "followers/list.json"
        cursor = "-1"
        collected: list[UserRecord] = []
        seen: set[str] = set()
        page = 0

        while cursor and cursor != "0":
            response = self._request(
                "GET",
                f"{self.REST_BASE}/{endpoint}",
                params={
                    "user_id": user_id,
                    "count": 200,
                    "cursor": cursor,
                    "skip_status": "true",
                    "include_user_entities": "false",
                },
            )
            if response.status_code >= 400:
                raise XClientError(
                    f"REST {endpoint} HTTP {response.status_code}: {response.text[:200]}"
                )
            payload = response.json()
            page += 1
            for raw in payload.get("users") or []:
                uid = raw.get("id_str") or raw.get("id")
                account = raw.get("screen_name")
                if not uid or not account:
                    continue
                uid_s = str(uid)
                if uid_s in seen:
                    continue
                seen.add(uid_s)
                collected.append(
                    UserRecord(
                        user_id=uid_s,
                        account_name=str(account),
                        display_name=str(raw.get("name") or account),
                    )
                )
            if on_page:
                on_page(page, len(collected))
            next_cursor = payload.get("next_cursor_str")
            if next_cursor is None:
                next_cursor = str(payload.get("next_cursor", "0"))
            if next_cursor in {"0", "", cursor}:
                break
            cursor = next_cursor

        return collected

    def fetch_list(
        self,
        kind: ListKind,
        user_id: str,
        *,
        on_page: Callable[[int, int], None] | None = None,
    ) -> list[UserRecord]:
        try:
            users = self._fetch_list_graphql(kind, user_id, on_page=on_page)
            if users:
                return users
        except XClientError:
            # Fall through to REST
            pass
        return self._fetch_list_rest(kind, user_id, on_page=on_page)
