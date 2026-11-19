"""Single HTTP GraphQL edge with bounded retry and secret redaction."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FetchResult:
    """Structured boundary result for outbound GitHub GraphQL requests."""

    ok: bool
    data: dict[str, Any] | None = None
    error: str = ""


def redact_secret(text: str, secret: str) -> str:
    """Strip any occurrence of secret from error or diagnostic strings."""
    if not secret:
        return text
    return text.replace(secret, "[REDACTED]")


def _post_once(
    payload: bytes,
    token: str,
    gh_cfg: dict[str, Any],
    messages: dict[str, str],
) -> tuple[FetchResult, bool]:
    req = urllib.request.Request(
        str(gh_cfg["endpoint"]),
        data=payload,
        method="POST",
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": str(gh_cfg["user_agent"]),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=int(gh_cfg["timeout_sec"])) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = redact_secret(exc.read().decode("utf-8", errors="replace"), token)
        msg = messages["http_error"].format(code=exc.code, detail=detail)
        return FetchResult(ok=False, error=msg), exc.code >= int(gh_cfg["retry_status_min"])
    except urllib.error.URLError as exc:
        msg = messages["network_error"].format(reason=redact_secret(str(exc.reason), token))
        return FetchResult(ok=False, error=msg), True

    parsed = json.loads(body)
    if parsed.get("errors"):
        err_json = redact_secret(json.dumps(parsed["errors"]), token)
        return FetchResult(ok=False, error=messages["graphql_errors"].format(errors=err_json)), False
    if not parsed.get("data"):
        return FetchResult(ok=False, error=messages["no_data"]), False
    return FetchResult(ok=True, data=parsed["data"]), False


def graphql_request(
    query: str,
    variables: dict[str, Any],
    token: str,
    gh_cfg: dict[str, Any],
    messages: dict[str, str],
) -> FetchResult:
    """Execute a GitHub GraphQL request with one transient-failure retry."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    attempts = 1 + max(0, int(gh_cfg["max_retries"]))
    result = FetchResult(ok=False, error=messages["no_data"])
    for _ in range(attempts):
        result, retryable = _post_once(payload, token, gh_cfg, messages)
        if result.ok or not retryable:
            return result
    return result


def fetch_repositories(
    login: str,
    token: str,
    include_private: bool,
    queries: dict[str, str],
    gh_cfg: dict[str, Any],
    messages: dict[str, str],
    username: str,
) -> FetchResult:
    """Fetch owned non-fork repositories across all GraphQL pages."""
    privacy_arg = "" if include_private else str(gh_cfg["privacy_public_arg"])
    query = (
        queries["repos"]
        .replace("__REPOS_PAGE_SIZE__", str(int(gh_cfg["repos_page_size"])))
        .replace("__LANGS_PER_REPO__", str(int(gh_cfg["langs_per_repo"])))
        .replace("__PRIVACY_ARG__", privacy_arg)
    )
    nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    total_count = 0

    while True:
        res = graphql_request(query, {"login": login, "cursor": cursor}, token, gh_cfg, messages)
        if not res.ok or not res.data:
            return res
        user = res.data.get("user")
        if not user:
            return FetchResult(ok=False, error=messages["missing_user"].format(username=username))
        repo_conn = user["repositories"]
        total_count = int(repo_conn["totalCount"])
        nodes.extend(repo_conn.get("nodes") or [])
        page_info = repo_conn["pageInfo"]
        if not page_info["hasNextPage"]:
            return FetchResult(ok=True, data={"totalCount": total_count, "nodes": nodes})
        cursor = page_info["endCursor"]


def fetch_all_time_contributions(
    login: str,
    token: str,
    years: list[int],
    queries: dict[str, str],
    gh_cfg: dict[str, Any],
    messages: dict[str, str],
) -> FetchResult:
    """Fetch and sum contributionCalendar totals across all active years."""
    if not years:
        return FetchResult(ok=True, data={"total": 0})
    unique_years = sorted({int(y) for y in years})
    alias_tmpl = queries["year_alias"]
    parts = "\n".join(alias_tmpl.replace("__YEAR__", str(y)) for y in unique_years)
    query = queries["all_time"].replace("__YEAR_PARTS__", parts)
    res = graphql_request(query, {"login": login}, token, gh_cfg, messages)
    if not res.ok or not res.data:
        return res
    user = res.data.get("user") or {}
    total = sum(
        int(user[f"y{y}"]["contributionCalendar"]["totalContributions"])
        for y in unique_years
        if user.get(f"y{y}")
    )
    return FetchResult(ok=True, data={"total": total})
