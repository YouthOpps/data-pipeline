"""Inspect the historical approval through GitHub only."""
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = "YouthOpps/data-pipeline"
REF = "refs/heads/qa/tartu419-github-metadata"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
COMMENT_URL = f"https://github.com/{REPOSITORY}/issues/419#issuecomment-6961752511"
GRANT = "Approved Tartu QA head 365894351135f6521e83a734bba389a63f4f335e"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


OPENER = build_opener(NoRedirect())


def request_json(path, token):
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
        "User-Agent": "YouthOppsBot/1.0 (+https://youthopps.org/bot/)",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    nonce = urlencode({"state_read": time.time_ns()})
    url = f"{API_ROOT}{path}{'&' if '?' in path else '?'}{nonce}"
    try:
        with OPENER.open(Request(url, headers=headers), timeout=12) as response:
            return response.status, json.loads(response.read(262144))
    except HTTPError as error:
        error.close()
        return error.code, None
    except (URLError, TimeoutError, ValueError):
        return None, None


def check_comment(token):
    status, value = request_json("/issues/comments/6961752511", token)
    if not isinstance(value, dict):
        return {"http_status": status, "object": False}
    body, author = value.get("body"), value.get("user")
    association = value.get("author_association")
    return {
        "http_status": status,
        "object": True,
        "exact_url": value.get("html_url") == COMMENT_URL,
        "exact_issue": value.get("issue_url") == API_ROOT + "/issues/419",
        "exact_comment_id": value.get("id") == 6961752511,
        "exact_author_id": isinstance(author, dict) and author.get("id") == 12278069,
        "maintainer_association": association in ("OWNER", "MEMBER", "COLLABORATOR"),
        "association": association if association in (
            "OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR",
            "FIRST_TIMER", "MANNEQUIN",
        ) else None,
        "exact_grant_line": isinstance(body, str) and GRANT in body.splitlines(),
        "known_update_timestamp": value.get("updated_at") == "2026-11-17T21:40:19Z",
    }


def main():
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": REF,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise SystemExit("Unexpected metadata job identity")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    if event.get("created") is not True or event.get("ref") != REF:
        raise SystemExit("Expected isolated diagnostic branch creation")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Expected Actions token for scope comparison")
    print(json.dumps({
        "job": os.environ.get("GITHUB_JOB"),
        "action_token": check_comment(token),
        "anonymous": check_comment(None),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
