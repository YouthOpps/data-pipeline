"""Collect attributed noncommercial WBI funding opportunities."""

import argparse
import copy
import email.utils
import errno
import io
import math
import socket
import ssl
import stat
import zipfile
import base64
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
import random
import re
import signal
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_ID = "be-wbi"
SOURCE_URL = "https://www.wbi.be/en/bourses"
WEBSITE_URL = "https://www.wbi.be/"
LANGUAGE = "fr"
PUBLISHER_COUNTRY = "BE"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Wallonie-Bruxelles International (WBI) – www.wbi.be"


_PAGE_COMPLETED = None




class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style", "noscript"):
            return ""
        parts = []
        for child in self.children:
            parts.append(child.text() if isinstance(child, Node) else child)
        return " ".join(" ".join(parts).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class PublisherHTML(HTMLParser):
    """Small structural parser retaining the publisher's content boundaries."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in (
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        ):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def nodes(node, tag=None, class_name=None):
    return [
        n
        for n in node.walk()
        if (tag is None or n.tag == tag)
        and (
            class_name is None or class_name in n.attrs.get("class", "").split()
        )
    ]


def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]




def content(root):
    return one(
        [
            n
            for n in nodes(root, "div", "field--name-body")
            if not nodes(n, "a", "printButton")
        ],
        "WBI factual article",
    )


_WBI_HELD_JOURNAL_SHA256 = (
    "aa544101765e65c7f3126501cce8e67881062ccbefec77fb1930cd5efab26e0c"
)
_WBI_REMOVED_BODY_SHA256 = (
    "befd4e4a843a70b415e8b99d479820598a322e4b404c8c63e2464e2babe6d917"
)
_WBI_REMOVED_SLUG = (
    "offre-stage-centre-international-recherche-cancer-circ"
)
_WBI_REMOVED_URL = (
    "https://www.wbi.be/fr/actualites/"
    "offre-stage-centre-international-recherche-cancer-circ"
)
_WBI_TOURNESOL_URL = (
    "https://www.wbi.be/fr/aide-service/beneficier-dun-soutien-projets-"
    "cooperation-scientifique-france-programme-hubert-curien"
)
_WBI_DYNAMIC_ROOTS = (
    "https://www.wbi.be/en/bourses",
    "https://www.wbi.be/fr/appels-a-projets",
)
_WBI_MONTHS = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}


def wbi_retired_resource(url):
    """Deny known aliases independently of caller purpose or URL encoding."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"wbi.be", "www.wbi.be"}:
        return False
    path = parsed.path
    for _ in range(4):
        if re.search(r"%(?![0-9a-fA-F]{2})", path):
            return True
        try:
            decoded = urllib.parse.unquote(path, errors="strict")
        except UnicodeError:
            return True
        if decoded == path:
            break
        path = decoded
    if (
        "%" in path or "\\" in path or ";" in path
        or any(part in (".", "..") for part in path.split("/"))
    ):
        return True
    path = path.rstrip("/").lower()
    return (
        path.rsplit("/", 1)[-1] == _WBI_REMOVED_SLUG
        or bool(re.search(r"(?:^|/)node/0*25108$", path))
    )


# Only an independently accepted, committed source decision can populate this.
_WBI_SCOPE_DECISION = {
    "schema": 1,
    "source": "be-wbi",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/373"
        "#issuecomment-6925600137"
    ),
    "held_sha256": (
        "aa544101765e65c7f3126501cce8e67881062ccbefec77fb1930cd5efab26e0c"
    ),
    "response_sha256": (
        "befd4e4a843a70b415e8b99d479820598a322e4b404c8c63e2464e2babe6d917"
    ),
    "native_revision": (
        "06cc8b0de832b4f055c34a47bf16c435dc75365d0260ee260cd37a8766034086"
    ),
    "migration_revision": "wbi373-content-scope-v1",
    "paths": [
        "/robots.txt", "/en/bourses", "/en/taxonomy/term/843",
        "/en/taxonomy/term/844", "/en/taxonomy/term/846",
        "/en/taxonomy/term/847", "/fr/appels-a-projets", "/actualites",
        "/fr/actualites", "/fr/rss-feeds",
    ],
    "detail_prefixes": ["/fr/actualites/", "/fr/aide-service/"],
}
_WBI_SCOPE_EXTENSION = {
    "schema": 1,
    "source": "be-wbi",
    "migration_revision": "wbi373-content-scope-v2",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/373"
        "#issuecomment-6941417638"
    ),
    "parent_decision_sha256": (
        "04ebc93fb4dd788782dbaa4468414f48276039889e93ef4d2551837d3bad526a"
    ),
    "prior_journal_sha256": (
        "271667f2f3958af5cc217f1143cf3fe56d53c585fab620bcaf2aff2cf1459c53"
    ),
    "held_sha256": (
        "aa544101765e65c7f3126501cce8e67881062ccbefec77fb1930cd5efab26e0c"
    ),
    "response_sha256": (
        "befd4e4a843a70b415e8b99d479820598a322e4b404c8c63e2464e2babe6d917"
    ),
    "paths": ["/fr/WBI-World"],
}
_WBI_V1_JOURNAL_SHA256 = (
    "271667f2f3958af5cc217f1143cf3fe56d53c585fab620bcaf2aff2cf1459c53"
)
_WBI_NATIVE_REVISION = (
    "06cc8b0de832b4f055c34a47bf16c435dc75365d0260ee260cd37a8766034086"
)
_WBI_SCOPE_PATHS = (
    "/robots.txt", "/en/bourses", "/en/taxonomy/term/843",
    "/en/taxonomy/term/844", "/en/taxonomy/term/846",
    "/en/taxonomy/term/847", "/fr/appels-a-projets", "/actualites",
    "/fr/actualites", "/fr/rss-feeds",
)


def wbi_scope_decision():
    """No environment value or parser result can approve refusal scope."""
    value = _WBI_SCOPE_DECISION
    if value is None:
        raise AdapterError(
            "WBI refusal scope awaits independent review", "access"
        )
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "source", "evidence", "held_sha256",
            "response_sha256", "native_revision", "migration_revision",
            "paths", "detail_prefixes",
        }
        or type(value["schema"]) is not int or value["schema"] != 1
        or value["source"] != SOURCE_ID
        or value["held_sha256"] != _WBI_HELD_JOURNAL_SHA256
        or value["response_sha256"] != _WBI_REMOVED_BODY_SHA256
        or value["native_revision"] != _WBI_NATIVE_REVISION
        or value["migration_revision"] != "wbi373-content-scope-v1"
        or value["paths"] != list(_WBI_SCOPE_PATHS)
        or value["detail_prefixes"] != [
            "/fr/actualites/", "/fr/aide-service/",
        ]
        or not isinstance(value["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/373"
            r"#issuecomment-[1-9][0-9]*", value["evidence"]
        )
    ):
        raise AdapterError("Invalid WBI committed scope decision", "access")
    return copy.deepcopy(value)


def wbi_scope_extension():
    """Accept only the separately committed World-route scope amendment."""
    value = _WBI_SCOPE_EXTENSION
    if value is None:
        return None
    parent = hashlib.sha256(json.dumps(
        wbi_scope_decision(), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "source", "migration_revision", "evidence",
            "parent_decision_sha256", "prior_journal_sha256",
            "held_sha256", "response_sha256", "paths",
        }
        or type(value["schema"]) is not int or value["schema"] != 1
        or value["source"] != SOURCE_ID
        or value["migration_revision"] != "wbi373-content-scope-v2"
        or value["parent_decision_sha256"] != parent
        or value["prior_journal_sha256"] != _WBI_V1_JOURNAL_SHA256
        or value["held_sha256"] != _WBI_HELD_JOURNAL_SHA256
        or value["response_sha256"] != _WBI_REMOVED_BODY_SHA256
        or value["paths"] != ["/fr/WBI-World"]
        or not isinstance(value["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/373"
            r"#issuecomment-[1-9][0-9]*", value["evidence"]
        )
    ):
        raise AdapterError("Unreviewed WBI scope extension", "access")
    return copy.deepcopy(value)


def wbi_upgrade_scope(state, raw=None):
    """Preserve v1 provenance while durably adding only reviewed scope v2."""
    validate_budget(state)
    extension = wbi_scope_extension()
    if state["schema"] != 4 or extension is None:
        return copy.deepcopy(state)
    scope = state["refusal_scope"]
    if scope["additional_holds"] or state["in_flight"]:
        raise AdapterError("Held or pending WBI scope cannot expand", "access")
    if scope.get("extensions") == [extension]:
        return copy.deepcopy(state)
    if (
        not isinstance(raw, bytes)
        or hashlib.sha256(raw).hexdigest() != _WBI_V1_JOURNAL_SHA256
        or json.loads(raw) != state
    ):
        raise AdapterError("WBI v1 scope generation changed", "access")
    result = copy.deepcopy(state)
    result["refusal_scope"]["extensions"] = [extension]
    return validate_budget(result)


def wbi_initial_refusal(state):
    """Keep the exact failed native event without rewriting its outcome."""
    return {
        "url": _WBI_REMOVED_URL, "status": 403,
        "body_sha256": _WBI_REMOVED_BODY_SHA256,
        "completed": state["completed"],
        "native_revision": _WBI_NATIVE_REVISION,
        "resource_slug": _WBI_REMOVED_SLUG, "node_id": 25108,
        "origins": ["https://wbi.be", "https://www.wbi.be"],
    }


def wbi_migrate_scope(raw):
    """Derive a scope-aware journal, retaining exact held bytes and history."""
    decision = wbi_scope_decision()
    if (
        not isinstance(raw, bytes) or len(raw) > 16384
        or hashlib.sha256(raw).hexdigest() != _WBI_HELD_JOURNAL_SHA256
    ):
        raise AdapterError("Unreviewed WBI held journal generation", "access")
    state = validate_budget(json.loads(raw))
    if state["schema"] != 3 or not state["blocked"] or state["in_flight"]:
        raise AdapterError("WBI held event is incomplete", "access")
    original = copy.deepcopy(state)
    state["schema"] = 4
    state["refusal_scope"] = {
        "decision": decision,
        "original_journal_base64": base64.b64encode(raw).decode(),
        "initial_refusal": wbi_initial_refusal(original),
        "additional_holds": [],
    }
    return validate_budget(state)


def wbi_validate_scope(state):
    """Reject detached provenance, weakened MAX state and unreviewed events."""
    scope = state.get("refusal_scope")
    try:
        if (
            not isinstance(scope, dict)
            or set(scope) not in (
                {"decision", "original_journal_base64", "initial_refusal",
                 "additional_holds"},
                {"decision", "original_journal_base64", "initial_refusal",
                 "additional_holds", "extensions"},
            )
            or scope["decision"] != wbi_scope_decision()
            or state["blocked"] is not True
            or not isinstance(scope["original_journal_base64"], str)
        ):
            raise ValueError()
        if "extensions" in scope:
            extension = wbi_scope_extension()
            if extension is None or scope["extensions"] != [extension]:
                raise ValueError()
        raw = base64.b64decode(scope["original_journal_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != _WBI_HELD_JOURNAL_SHA256:
            raise ValueError()
        original = json.loads(raw)
        if original.get("schema") != 3:
            raise ValueError()
        validate_budget(original)
        if scope["initial_refusal"] != wbi_initial_refusal(original):
            raise ValueError()
        for key in (
            "interval", "page_gap", "until", "completed",
            "inventory_completed", "rate_limit_until",
            "observed_429_since_transition",
        ):
            if state[key] < original[key]:
                raise ValueError()
        for key in (
            "legacy_hold_history", "legacy_access_history_unknown",
            "legacy_access_observations", "legacy_429_history",
            "research_provenance",
        ):
            if state[key] != original[key]:
                raise ValueError()
        holds = scope["additional_holds"]
        if not isinstance(holds, list):
            raise ValueError()
        for item in holds:
            if (
                not isinstance(item, dict)
                or set(item) != {"kind", "url", "status", "observed_at"}
                or item["kind"] not in {
                    "content_status", "visible_refusal", "robots_disallow",
                    "unattributed_held_state",
                }
                or not isinstance(item["url"], str)
                or not item["url"]
                or type(item["status"]) is not int
                or not 0 <= item["status"] <= 599
                or not numeric(item["observed_at"])
                or item["observed_at"] > state["observed_at"]
            ):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise AdapterError("Invalid WBI retained refusal scope", "access")


def wbi_scope_admission(state, url, method="GET"):
    """Admit only reviewed unrelated public routes; retain every denial."""
    if state["schema"] != 4 or state["in_flight"]:
        raise AdapterError("Unreviewed or pending WBI access state", "access")
    wbi_validate_scope(state)
    if state["refusal_scope"]["additional_holds"]:
        raise AdapterError("Additional WBI refusal remains global", "access")
    parsed = urllib.parse.urlsplit(url)
    if (
        method != "GET" or parsed.scheme != "https"
        or parsed.hostname != "www.wbi.be"
        or parsed.username or parsed.password or parsed.port or parsed.fragment
        or "%" in parsed.path or "\\" in parsed.path
        or "//" in parsed.path or ";" in parsed.path
        or any(part in (".", "..") for part in parsed.path.split("/"))
        or wbi_retired_resource(url)
    ):
        raise AdapterError("WBI resource identity not admitted", "access")
    path = parsed.path
    detail = any(
        path.startswith(prefix)
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", path[len(prefix):])
        for prefix in state["refusal_scope"]["decision"]["detail_prefixes"]
    )
    extension_paths = {
        path for extension in state["refusal_scope"].get("extensions", [])
        for path in extension["paths"]
    }
    if (path not in _WBI_SCOPE_PATHS
            and path not in extension_paths and not detail):
        raise AdapterError("WBI route outside reviewed scope", "access")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if parsed.query and (
        detail or path in ("/robots.txt", "/fr/rss-feeds", "/fr/WBI-World")
        or set(query) != {"page"} or len(query["page"]) != 1
        or not re.fullmatch(r"0|[1-9][0-9]*", query["page"][0])
    ):
        raise AdapterError("Unreviewed WBI query scope", "access")


def wbi_retain_new_hold(state, url, status, kind):
    """A later real refusal is globally restrictive, never auto-reclassified."""
    state["blocked"] = True
    state["observed_at"] = time.time()
    if state["schema"] == 4:
        event = {
            "kind": kind, "url": url, "status": status,
            "observed_at": state["observed_at"],
        }
        holds = state["refusal_scope"]["additional_holds"]
        if event not in holds:
            holds.append(event)


def wbi_merge_scope(result, left, right):
    """Union events; an unproved blocked input retains a global veto."""
    scopes = [s["refusal_scope"] for s in (left, right) if s["schema"] == 4]
    if not scopes:
        return
    result["schema"] = 4
    result["refusal_scope"] = copy.deepcopy(scopes[0])
    holds = result["refusal_scope"]["additional_holds"]
    for scope in scopes[1:]:
        for key in ("decision", "original_journal_base64", "initial_refusal"):
            if scope[key] != result["refusal_scope"][key]:
                raise AdapterError("Conflicting WBI scope provenance", "access")
        for event in scope["additional_holds"]:
            if event not in holds:
                holds.append(copy.deepcopy(event))
        if "extensions" in scope:
            existing = result["refusal_scope"].get("extensions")
            if existing is not None and existing != scope["extensions"]:
                raise AdapterError("Conflicting WBI scope extension", "access")
            result["refusal_scope"]["extensions"] = copy.deepcopy(
                scope["extensions"]
            )
    original = json.loads(base64.b64decode(
        result["refusal_scope"]["original_journal_base64"]
    ))
    for state in (left, right):
        if state["schema"] == 3 and state["blocked"] and state != original:
            event = {
                "kind": "unattributed_held_state", "url": WEBSITE_URL,
                "status": 0, "observed_at": state["observed_at"],
            }
            if event not in holds:
                holds.append(event)
    holds.sort(key=lambda item: json.dumps(item, sort_keys=True))


def wbi_canonical_url(base, href):
    """Resolve public source links without accepting alternate access paths."""
    url = urllib.parse.urljoin(base, href)
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.wbi.be"
        or parsed.username or parsed.password or parsed.port
        or parsed.fragment
        or re.search(r"/(?:search|recherche|user|admin|node)/", parsed.path)
    ):
        raise AdapterError("Unsupported WBI public inventory link", "parse")
    return url


def wbi_inventory_page(root, url):
    """Parse supported public catalogue cards and every advertised pager."""
    main = one(nodes(root, "main"), "WBI inventory main")
    view = one(nodes(main, "div", "view-page"), "WBI inventory view")
    path = urllib.parse.urlsplit(url).path
    if path == "/en/bourses":
        cards = nodes(view, "article", "search-result")
    elif path == "/fr/appels-a-projets":
        cards = nodes(view, "article", "article-item")
    else:
        raise AdapterError("Unverified WBI inventory scope", "parse")
    if not cards:
        raise AdapterError("Unverified WBI inventory structure", "parse")
    candidates = set()
    for card in cards:
        links = {
            wbi_canonical_url(url, node.attrs["href"])
            for node in nodes(card, "a")
            if node.attrs.get("href")
            and (
                re.search(r"/(?:aide-service|actualites)/", node.attrs["href"])
                or node.attrs["href"] == "/fr/WBI-World"
            )
        }
        if len(links) != 1:
            raise AdapterError("Ambiguous WBI inventory card", "parse")
        candidates.update(links)
    pagers = nodes(view, "nav", "pager")
    if len(pagers) > 1:
        raise AdapterError("Ambiguous WBI pager", "parse")
    targets = set()
    base = urllib.parse.urlsplit(url)
    for nav in pagers:
        if not nodes(nav, "a"):
            raise AdapterError("Empty WBI pagination control", "parse")
        for node in nodes(nav, "a"):
            if not node.attrs.get("href"):
                raise AdapterError("Incomplete WBI pager link", "parse")
            target = wbi_canonical_url(url, node.attrs["href"])
            parsed = urllib.parse.urlsplit(target)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if (
                parsed.path != base.path
                or set(query) not in (set(), {"page"})
                or (query and (len(query["page"]) != 1
                    or not re.fullmatch(
                        r"0|[1-9][0-9]*", query["page"][0]
                    )))
            ):
                raise AdapterError("Unverified WBI pagination scope", "parse")
            if not query or query["page"] == ["0"]:
                target = urllib.parse.urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, "", "")
                )
            targets.add(target)
    return sorted(candidates), sorted(targets)


def wbi_expiry_disposition(url, checked_at):
    """Exclude only the exact proven expired CIRC call, never ambiguous URLs."""
    if url != _WBI_REMOVED_URL:
        return None
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.utcoffset() is None:
        raise AdapterError("WBI expiry requires aware time", "validate")
    if checked.astimezone(timezone.utc).date().isoformat() <= "2026-11-14":
        raise AdapterError("CIRC expiry boundary not established", "validate")
    return {
        "url": _WBI_REMOVED_URL, "deadline": "2026-11-13",
        "disposition": "expired_known_call_do_not_fetch",
        "body_sha256": _WBI_REMOVED_BODY_SHA256,
    }


def wbi_article(root, url):
    """Bind factual content to a single publisher-native article identity."""
    main = one(nodes(root, "main"), "WBI article main")
    article = one([
        node for node in nodes(main, "article", "node--view-mode-full")
        if node.attrs.get("data-history-node-id")
    ], "WBI full article")
    native = article.attrs["data-history-node-id"]
    if not re.fullmatch(r"[1-9][0-9]*", native):
        raise AdapterError("Invalid WBI native article identity", "parse")
    canonical = one([
        node.attrs.get("href") for node in nodes(root, "link")
        if node.attrs.get("rel") == "canonical"
    ], "WBI canonical article")
    shortlink = one([
        node.attrs.get("href") for node in nodes(root, "link")
        if node.attrs.get("rel") == "shortlink"
    ], "WBI native shortlink")
    if canonical != url or shortlink != WEBSITE_URL + "fr/node/" + native:
        raise AdapterError("Conflicting WBI article identity", "parse")
    title = one(nodes(main, "h1"), "WBI opportunity title").text()
    body = content(article)
    if not title or not body.text():
        raise AdapterError("Missing WBI current factual article", "parse")
    return native, title, body


def wbi_explicit_deadline(text):
    """Only explicit application dates with a published year are deadlines."""
    text = re.sub(r"[^\s]+@[^\s]+", "[email]", text)
    months = "|".join(_WBI_MONTHS)
    lead = (
        r"(?:candidatures?|dossier|demande)[^.!?]{0,120}?"
        r"(?:pour|avant|jusqu'au)|"
        r"date limite (?:de dépôt )?(?:des? candidatures?)?\s*:"
    )
    pattern = (
        r"(?:" + lead + r")\s+(?:le\s+)?"
        r"(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?"
        r"(\d{1,2})\s+(" + months + r")\s+(20\d{2})"
    )
    matches = {
        datetime(int(year), _WBI_MONTHS[month], int(day)).date().isoformat()
        for day, month, year in re.findall(pattern, text.lower())
    }
    if len(matches) > 1:
        raise AdapterError("Multiple WBI application deadlines", "parse")
    return next(iter(matches), None)


def wbi_record(native, title, url, category, kind, summary,
               *, slot=None, legacy_track=None, deadline=None):
    record = make_record(
        title, url, [category], kind=kind,
        method="official-structured-facts",
        evidence=[ATTRIBUTION, "Publisher article node " + native],
    )
    identity = "node:" + native
    if slot is not None:
        identity += "|" + slot
    if legacy_track is not None:
        identity = legacy_track
    record["id"] = hashlib.sha256(
        (SOURCE_ID + "|" + identity).encode()
    ).hexdigest()[:24]
    record["summary"] = summary
    record["deadline"] = deadline
    return record


def wbi_unesco_records(native, title, body, url, root):
    """Use separately named attachment identities without fetching files."""
    attachments = {}
    for link in nodes(root, "a"):
        href = link.attrs.get("href", "")
        if href.startswith("/sites/default/files/"):
            attachments.setdefault(link.text(), set()).add(href)
    headings = [h.text() for h in nodes(body, "h3")]
    if headings.count("Financement") != 1:
        raise AdapterError("Missing WBI internship section boundary", "parse")
    named = headings[:headings.index("Financement")]
    if (
        len(named) < 2 or len(named) != len(set(named))
        or set(named) != set(attachments)
    ):
        raise AdapterError("Incomplete WBI named internship inventory", "parse")
    records = []
    for name in named:
        attachment = one(list(attachments[name]), "WBI internship attachment")
        records.append(wbi_record(
            native, title + " — " + name, url, "internships", "opportunity",
            "WBI publishes an internship call for this UNESCO unit.",
            slot=attachment, deadline=wbi_explicit_deadline(body.text()),
        ))
    validate_records(records)
    return records


def wbi_world_records(native, title, body, url):
    """Extract each explicitly enumerated award type, preserving legacy IDs."""
    types = [
        node.text() for node in nodes(body, "li")
        if re.match(r"Bourses de (?:longue|courte) durée", node.text())
    ]
    records, seen = [], set()
    for label in types:
        lowered = label.lower()
        if "courte" in lowered:
            track, variant = "world-short", "Short research stay"
        elif "postdoctoral" in lowered:
            track, variant = "world-postdoctoral", "Postdoctoral research"
        elif "doctoral" in lowered:
            track, variant = "world-doctoral", "Doctoral research"
        else:
            raise AdapterError("Unclassified WBI WORLD award type", "parse")
        if track in seen:
            raise AdapterError("Duplicate WBI WORLD award type", "parse")
        seen.add(track)
        records.append(wbi_record(
            native, title + " — " + variant, url,
            "scholarships", "programme-overview",
            "WBI supports " + variant.lower() + " outside Belgium.",
            legacy_track=track,
            deadline=wbi_explicit_deadline(body.text()),
        ))
    if seen != {"world-short", "world-postdoctoral", "world-doctoral"}:
        raise AdapterError("Incomplete WBI WORLD award inventory", "parse")
    validate_records(records)
    return records


def wbi_excellence_in_records(native, title, body, url):
    types = [
        node.text() for node in nodes(body, "li")
        if re.match(r"Bourses de (?:longue|courte) durée", node.text())
    ]
    records, seen = [], set()
    for label in types:
        if "postdoctoral" not in label.lower():
            raise AdapterError("Unclassified WBI IN award type", "parse")
        variant = "long" if "longue" in label.lower() else "short"
        if variant in seen:
            raise AdapterError("Duplicate WBI IN award type", "parse")
        seen.add(variant)
        record = wbi_record(
            native, title + " — " + variant + " postdoctoral stay",
            url, "scholarships", "programme-overview",
            "WBI supports a " + variant + " postdoctoral research stay "
            "at a higher education institution in Wallonia or Brussels.",
            legacy_track="excellence-in-" + variant,
            deadline=wbi_explicit_deadline(body.text()),
        )
        record["host_countries"] = ["BE"]
        records.append(record)
    if seen != {"long", "short"}:
        raise AdapterError("Incomplete WBI IN award inventory", "parse")
    validate_records(records)
    return records


def wbi_mobility_records(native, title, body, url):
    descriptions = [
        node.text() for node in nodes(body, "p")
        if node.text().startswith("Soutien des membres")
    ]
    records, seen = [], set()
    for description in descriptions:
        if "en Belgique" in description:
            variant = "in"
            summary = (
                "Institutional support for visiting academic, scientific "
                "or administrative staff from Maghreb-Lebanon institutions."
            )
        elif "en Amérique latine" in description:
            variant = "out"
            summary = (
                "Institutional mobility support for FWB higher education "
                "staff collaborating with overseas partner institutions."
            )
        else:
            raise AdapterError("Unclassified WBI mobility route", "parse")
        if variant in seen:
            raise AdapterError("Duplicate WBI mobility route", "parse")
        seen.add(variant)
        record = wbi_record(
            native, title + " — " + {"in": "Incoming", "out": "Outgoing"}
            [variant], url, "grants", "institutional-grant", summary,
            legacy_track="mobility-" + variant,
            deadline=wbi_explicit_deadline(body.text()),
        )
        if variant == "in":
            record["host_countries"] = ["BE"]
        records.append(record)
    if seen != {"in", "out"}:
        raise AdapterError("Incomplete WBI mobility route inventory", "parse")
    validate_records(records)
    return records


def wbi_specialisation_records(native, title, body, url):
    exemption = [
        node for node in nodes(body, "h2")
        if "Québec" in node.text() and "exemption" in node.text()
    ]
    one(exemption, "WBI separately named Quebec fee exemption")
    if not re.search(r"étudiants belges francophones", body.text()):
        raise AdapterError("Unverified WBI fee exemption eligibility", "parse")
    records = [wbi_record(
        native, title, url, "scholarships", "programme-overview",
        "WBI lists academic specialisation scholarships abroad; "
        "destination-specific requirements apply.",
        legacy_track="specialisation",
        deadline=wbi_explicit_deadline(body.text()),
    ), wbi_record(
        native, "Québec tuition fee exemption", url,
        "scholarships", "programme-overview",
        "A separately described tuition fee exemption for qualifying "
        "French-speaking Belgian students at Quebec universities.",
        legacy_track="quebec-exemption",
    )]
    records[1]["host_countries"] = ["CA"]
    records[1]["eligible_countries"] = ["BE"]
    validate_records(records)
    return records


_WBI_LEGACY_SINGLE_TRACKS = {
    "734": "explort", "715": "language", "8277": "nearby",
    "24526": "hague", "1197": "assistants", "1195": "louisiana",
    "728": "oi", "24604": "circ-postdoc", "716": "research",
    "729": "company", "8641": "tournesol",
}


def wbi_single_record(native, title, body, url):
    """Classify current source wording, without projecting stale profiles."""
    wording = title.lower()
    facts = body.text().lower()
    if "stage" in wording:
        category = "internships"
    elif "bourse" in wording or (
        "doctin" in wording and "aide financière" in facts
    ) or ("langue étrangère" in wording and "bourse" in facts):
        category = "scholarships"
    elif re.search(r"enseignant|formateur|conversation", wording):
        category = "jobs"
    elif re.search(r"formation|apprendre|explort", wording):
        category = "training"
    elif re.search(r"soutien|fonds|appel.*projets", wording):
        category = "grants"
    else:
        category = "other"
    deadline = wbi_explicit_deadline(body.text())
    programme = "/aide-service/" in url
    kind = "programme-overview" if programme and not deadline else "opportunity"
    if (
        category == "grants" and (not programme or native == "8641")
        and re.search(
            r"opérateurs|établissements d’enseignement|associations", facts
        )
    ):
        kind = "institutional-grant"
    if category == "other":
        # A call for residency applications establishes the opportunity kind.
        residency_call = "/actualites/" in url and re.match(
            r"appel (?:de|à) candidatures?\s*[|:–—-]\s*résidence\b", wording
        )
        kind = "opportunity" if residency_call else "unknown"
    summaries = {
        "internships": (
            "WBI publishes internship support and application information."
        ),
        "scholarships": (
            "WBI publishes study or research funding and application "
            "information."
        ),
        "jobs": "WBI publishes teaching placement and application information.",
        "training": (
            "WBI publishes training support and application information."
        ),
        "grants": (
            "WBI publishes project or mobility support and application "
            "information."
        ),
        "other": (
            "WBI publishes this opportunity in its official catalogue or "
            "calls index."
        ),
    }
    record = wbi_record(
        native, title, url, category, kind, summaries[category],
        legacy_track=_WBI_LEGACY_SINGLE_TRACKS.get(native), deadline=deadline,
    )
    validate_records([record])
    return record


def wbi_dynamic_records(root, url):
    native, title, body = wbi_article(root, url)
    parsers = {
        "665": wbi_world_records, "1271": wbi_excellence_in_records,
        "5214": wbi_mobility_records, "705": wbi_specialisation_records,
    }
    if native == "24910":
        return wbi_unesco_records(native, title, body, url, root)
    if native in parsers:
        return parsers[native](native, title, body, url)
    return [wbi_single_record(native, title, body, url)]


def wbi_complete_records(pages):
    """Reconcile only publisher-linked calls with equal explicit deadlines."""
    parsed = {}
    for url, root in pages.items():
        native, _, _ = wbi_article(root, url)
        if native in parsed:
            raise AdapterError("Duplicate WBI canonical article", "parse")
        parsed[native] = (url, root, wbi_dynamic_records(root, url))
    programmes = {
        url: (native, records[0])
        for native, (url, _, records) in parsed.items()
        if "/aide-service/" in url and len(records) == 1
    }
    aliases = {}
    for native, (url, root, records) in parsed.items():
        if "/actualites/" not in url or len(records) != 1:
            continue
        deadline = records[0]["deadline"]
        if deadline is None:
            continue
        links = set()
        for link in nodes(content(root), "a"):
            href = urllib.parse.urlsplit(link.attrs.get("href", ""))
            if (
                href.scheme == "https"
                and href.hostname in {"wbi.be", "www.wbi.be"}
                and not href.query and not href.fragment
                and not href.username and not href.password and not href.port
            ):
                links.add("https://www.wbi.be" + href.path)
        matches = [
            programmes[target] for target in links
            if target in programmes
            and programmes[target][1]["deadline"] == deadline
        ]
        if len(matches) > 1:
            raise AdapterError("Ambiguous WBI linked programme identity", "parse")
        if not matches:
            # A distinct cycle or an unknown programme date proves no alias.
            continue
        programme_native, programme = matches[0]
        if programme_native in aliases:
            raise AdapterError("Multiple WBI calls claim one identity", "parse")
        aliases[programme_native] = native
        records[0]["id"] = programme["id"]
    records = [
        record for native, (_, _, group) in parsed.items()
        if native not in aliases for record in group
    ]
    validate_records(records)
    return records


def collect_dynamic_wbi():
    """Walk complete public index pagination, then fetch all detail evidence."""
    pending = list(_WBI_DYNAMIC_ROOTS)
    visited, fingerprints, candidates = set(), {}, set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        root = fetch_inventory_page(page, url)
        details, following = wbi_inventory_page(root, url)
        fingerprint = hashlib.sha256(
            json.dumps([details, following], sort_keys=True).encode()
        ).hexdigest()
        fingerprint_key = (urllib.parse.urlsplit(url).path, fingerprint)
        prior = fingerprints.get(fingerprint_key)
        if prior is not None and prior != url:
            raise AdapterError("Repeated WBI inventory page", "parse")
        fingerprints[fingerprint_key] = url
        visited.add(url)
        candidates.update(details)
        pending.extend(target for target in following if target not in visited)
    for root_url in _WBI_DYNAMIC_ROOTS:
        path = urllib.parse.urlsplit(root_url).path
        indices = {
            int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                .get("page", ["0"])[0])
            for url in visited if urllib.parse.urlsplit(url).path == path
        }
        if indices != set(range(max(indices) + 1)):
            raise AdapterError("Incomplete WBI page sequence", "parse")
    # Tournesol remains an actual public programme outside the bourses filter.
    candidates.add(_WBI_TOURNESOL_URL)
    pages, dispositions = {}, []
    checked_at = utc_now()
    for url in sorted(candidates):
        expired = wbi_expiry_disposition(url, checked_at)
        if expired is not None:
            dispositions.append(expired)
        else:
            pages[url] = page(url)
    # Preserve the entire actual input capture before attempting normalization.
    records = wbi_complete_records(pages)
    print(json.dumps({
        "event": "dynamic_inventory_complete",
        "inventory_pages": len(visited), "candidates": len(candidates),
        "detail_pages": len(pages), "expired_exclusions": dispositions,
        "records": len(records),
    }), file=sys.stderr)
    return records


def collect_body():
    return collect_dynamic_wbi()


CATEGORIES = (
    "scholarships",
    "internships",
    "volunteering",
    "fellowships",
    "training",
    "competitions",
    "grants",
    "jobs",
    "other",
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"


class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)
_ROBOTS = {}


def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit(
            (
                urllib.parse.urlsplit(match[0]).scheme,
                urllib.parse.urlsplit(match[0]).hostname or "",
                urllib.parse.urlsplit(match[0]).path,
                "",
                "",
            )
        ),
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return " ".join(text.split())[:500]


def legacy_pace(url, interval=6, *, start_padding=0):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
    if start_padding:
        padding_until = None
    host = (
        (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    )
    directory = os.path.join(
        tempfile.gettempdir(), "wbi-publisher-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    information = os.lstat(directory)
    if (
        not os.path.isdir(directory)
        or os.path.islink(directory)
        or information.st_uid != os.getuid()
        or information.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    publisher = "wbi.be" if host == "wbi.be" else host
    key = hashlib.sha256(publisher.encode()).hexdigest()
    descriptor = os.open(
        os.path.join(directory, key),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    state = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read()
        budget = (
            json.loads(raw)
            if raw
            else {"starts": [], "interval": 6, "until": 0}
        )
        budget["interval"] = max(budget["interval"], interval, 6)
        while True:
            now = time.time()
            budget["starts"] = [
                start for start in budget["starts"] if now - start < 60
            ]
            ready = max(
                budget["until"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (
                    budget["starts"][0] + 60
                    if len(budget["starts"]) >= 10
                    else now
                ),
            )
            if start_padding and ready <= now:
                if padding_until is None:
                    padding_until = now + start_padding
                ready = max(ready, padding_until)
            if ready <= now:
                budget["starts"].append(now)
                legacy_save_budget(state, budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def legacy_save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


def legacy_request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    deadline=None,
    retry_jitter=0
):
    received_publisher_429 = False
    try:
        if retry_jitter:
            state, budget = legacy_pace(url, interval, start_padding=retry_jitter)
        else:
            state, budget = legacy_pace(url, interval)
        request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
        try:
            request = urllib.request.Request(
                url,
                data=payload,
                method=method,
                headers=request_headers,
            )
            try:
                transport_timeout = 30
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AdapterError(
                            "Publication deadline exhausted", "publish"
                        )
                    transport_timeout = min(30, remaining)
                response = _OPENER.open(request, timeout=transport_timeout)
            except urllib.error.HTTPError as error:
                response = error
            if (
                response.status == 429
                and urllib.parse.urlsplit(url).hostname != "api.github.com"
            ):
                received_publisher_429 = True
                try:
                    retry = response.headers.get("Retry-After")
                    if retry:
                        try:
                            seconds = (
                                float(retry)
                                if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                                else parsedate_to_datetime(retry).timestamp()
                                - time.time()
                            )
                            budget["until"] = max(
                                budget["until"], time.time() + max(0, seconds)
                            )
                            legacy_save_budget(state, budget)
                        except (ValueError, TypeError, OverflowError):
                            pass
                finally:
                    response.close()
                raise AdapterError("Publisher returned HTTP 429", "fetch", 429)
            with response:
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        seconds = (
                            float(retry)
                            if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                            else parsedate_to_datetime(retry).timestamp()
                            - time.time()
                        )
                        budget["until"] = max(
                            budget["until"], time.time() + max(0, seconds)
                        )
                        legacy_save_budget(state, budget)
                    except (ValueError, TypeError, OverflowError):
                        pass
                body = response.read(5_000_001)
                if len(body) > 5_000_000:
                    raise AdapterError("Response exceeds 5 MB", "fetch")
                return response.status, response.headers, body
        finally:
            state.close()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if received_publisher_429:
            raise AdapterError(
                "Publisher returned HTTP 429; response cleanup failed",
                "fetch",
                429,
            ) from error
        raise






def make_record(
    title,
    url,
    categories,
    kind="opportunity",
    published_at=None,
    tags=None,
    host_countries=None,
    method="editorial-review",
    evidence=None,
):
    now = utc_now()
    return {
        "id": hashlib.sha256(f"{SOURCE_ID}|{url}".encode()).hexdigest()[:24],
        "title": title,
        "url": url,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": published_at,
        "summary": "",
        "tags": tags or [],
        "location": None,
        "deadline": None,
        "language": LANGUAGE,
        "category": categories[0],
        "categories": categories,
        "kind": kind,
        "host_countries": host_countries or [],
        "eligible_countries": [],
        "publisher_country": PUBLISHER_COUNTRY,
        "created_at": now,
        "updated_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "last_checked_at": now,
        "status": "unknown",
        "classification": {
            "method": method,
            "status": "unknown" if categories == ["other"] else "classified",
            "evidence": evidence or [],
        },
    }


def remove_expired_records(records, checked_at):
    """Filter validated deadlines without guessing missing semantics."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    retained = []
    for record in records:
        deadline = record["deadline"]
        if deadline is None:
            retained.append(record)
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing_date = datetime.strptime(deadline, "%Y-%m-%d").date()
            expired = (
                checked.astimezone(timezone.utc).date()
                > closing_date
            )
        else:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            expired = checked > closing
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, *, allow_empty=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError(
            "Empty or invalid opportunity collection", "validate"
        )
    ids = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("source") != SOURCE_ID
            or record.get("source_url") != SOURCE_URL
        ):
            raise AdapterError("Record source identity mismatch", "validate")
        for field in (
            "id",
            "title",
            "url",
            "created_at",
            "updated_at",
            "first_seen_at",
            "last_seen_at",
            "last_checked_at",
        ):
            if (
                not isinstance(record.get(field), str)
                or not record[field].strip()
            ):
                raise AdapterError("Missing record field: " + field, "validate")
        if record["id"] in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"])
        url = urllib.parse.urlsplit(record["url"])
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
        ):
            raise AdapterError("Unsafe opportunity URL", "validate")
        for field in (
            "created_at",
            "updated_at",
            "first_seen_at",
            "last_seen_at",
            "last_checked_at",
            "published_at",
            "deadline",
        ):
            value = record.get(field)
            if field not in record:
                raise AdapterError("Missing record field: " + field, "validate")
            if value is not None:
                try:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        if field != "deadline":
                            raise ValueError()
                        datetime.strptime(value, "%Y-%m-%d")
                    elif (
                        datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        ).tzinfo
                        is None
                    ):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError(
                        "Invalid record timestamp: " + field, "validate"
                    )
        for field in ("tags", "host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise AdapterError("Invalid record list: " + field, "validate")
            if field != "tags" and any(
                not re.fullmatch("[A-Z]{2}", value) for value in values
            ):
                raise AdapterError("Invalid country code", "validate")
        categories = record.get("categories")
        if (
            not isinstance(categories, list)
            or not categories
            or any(value not in CATEGORIES for value in categories)
            or len(set(categories)) != len(categories)
            or record.get("category") != categories[0]
        ):
            raise AdapterError("Invalid opportunity categories", "validate")
        if record.get("kind") not in (
            "opportunity",
            "programme-overview",
            "institutional-grant",
            "unknown",
        ) or record.get("status") not in ("open", "expired", "unknown"):
            raise AdapterError("Invalid opportunity kind or status", "validate")
        summary = record.get("summary")
        if not isinstance(summary, str) or re.search("<[^>]+>", summary):
            raise AdapterError("Invalid plain summary", "validate")
        length = len(summary.encode("utf-16-le")) // 2
        if length > 600:
            raise AdapterError("Invalid plain summary", "validate")
        for field in ("location", "language", "publisher_country"):
            if (
                field not in record
                or record[field] is not None
                and not isinstance(record[field], str)
            ):
                raise AdapterError("Invalid record field: " + field, "validate")
        classification = record.get("classification")
        if (
            not isinstance(classification, dict)
            or not isinstance(classification.get("method"), str)
            or classification.get("status") not in ("classified", "unknown")
            or not isinstance(classification.get("evidence"), list)
            or any(
                not isinstance(value, str)
                for value in classification["evidence"]
            )
        ):
            raise AdapterError("Invalid classification evidence", "validate")


_PUBLICATION_WINDOW = None
_PUBLICATION_IDENTITY = None
_PUBLICATION_FAILURE = None
_PUBLICATION_RETRY_JITTER = 0


@contextlib.contextmanager
def publication_http_guard(deadline):
    """Bound one reviewed Unix/Python call, including waits and body reads."""
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "ITIMER_REAL")
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "getitimer")
        or not hasattr(signal, "SIGALRM")
    ):
        raise AdapterError("Publication timer is unavailable", "publish")
    active, repeating = signal.getitimer(signal.ITIMER_REAL)
    if active or repeating:
        raise AdapterError("An existing timer prevents publication", "publish")
    end = min(deadline, time.monotonic() + 30)
    remaining = end - time.monotonic()
    if remaining <= 0:
        raise AdapterError(
            "Publication deadline exhausted", "publish"
        )
    previous = signal.getsignal(signal.SIGALRM)

    def exhausted(signum, frame):
        raise AdapterError("Publication call deadline exhausted", "publish")

    try:
        signal.signal(signal.SIGALRM, exhausted)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield end
        if time.monotonic() >= end:
            raise AdapterError("Publication call deadline exhausted", "publish")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def prove_publication_window():
    """Prove and freeze the exact current hosted job's remaining time."""
    global _PUBLICATION_WINDOW, _PUBLICATION_IDENTITY
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    head = os.environ.get("GITHUB_SHA", "")
    workflow = (
        "YouthOpps/data-pipeline/.github/workflows/fetch-"
        + SOURCE_ID
        + ".yml@refs/heads/main"
    )
    identity = (run, attempt, head, workflow)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_JOB") != "collect"
        or os.environ.get("GITHUB_WORKFLOW_REF") != workflow
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(r"[1-9][0-9]{0,19}", run)
        or not re.fullmatch(r"[1-9][0-9]{0,9}", attempt)
        or not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise AdapterError(
            "Exact hosted publication context required", "publish"
        )
    if _PUBLICATION_WINDOW is not None:
        if identity != _PUBLICATION_IDENTITY:
            raise AdapterError("Publication job identity changed", "publish")
        return _PUBLICATION_WINDOW
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AdapterError(
            "Job deadline read credential is required", "publish"
        )
    url = (
        "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/"
        + run
        + "/attempts/"
        + attempt
        + "/jobs?per_page=100&deadline_observation="
        + str(time.time_ns())
    )
    wall_anchor = time.time()
    anchor = time.monotonic()
    with publication_http_guard(anchor + 30) as call_end:
        status, headers, body = request_bytes(
            url,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
            },
            deadline=call_end,
        )
    if status != 200:
        raise AdapterError(
            "GitHub job deadline context returned HTTP " + str(status),
            "publish",
            status,
        )

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous job context")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Invalid job context constant")

    try:
        dates = headers.get_all("Date", [])
        ages = headers.get_all("Age", [])
        if len(dates) != 1 or ages not in ([], ["0"]):
            raise ValueError("Untrusted job context time")
        if not re.fullmatch(
            r"[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} "
            r"[0-9]{2}:[0-9]{2}:[0-9]{2} GMT",
            dates[0],
        ):
            raise ValueError("Ambiguous HTTP Date")
        server = parsedate_to_datetime(dates[0])
        if server.tzinfo is None or server.utcoffset().total_seconds() != 0:
            raise ValueError("Expected UTC server time")
        if (
            not wall_anchor - 2
            <= server.timestamp()
            <= (wall_anchor + time.monotonic() - anchor + 2)
        ):
            raise ValueError("Stale or inconsistent server time")
        if len(body) > 65536:
            raise ValueError("Oversized job context")
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        if (
            not isinstance(decoded, dict)
            or type(decoded.get("total_count")) is not int
            or decoded["total_count"] != 1
            or not isinstance(decoded.get("jobs"), list)
            or len(decoded["jobs"]) != 1
        ):
            raise ValueError("Incomplete or ambiguous current jobs")
        job = decoded["jobs"][0]
        if (
            not isinstance(job, dict)
            or type(job.get("id")) is not int
            or job["id"] <= 0
            or job.get("name") != "collect"
            or job.get("workflow_name") != "fetch-" + SOURCE_ID
            or type(job.get("run_id")) is not int
            or job["run_id"] != int(run)
            or type(job.get("run_attempt")) is not int
            or job["run_attempt"] != int(attempt)
            or job.get("head_sha") != head
            or job.get("head_branch") != "main"
            or job.get("status") != "in_progress"
            or "conclusion" not in job
            or job["conclusion"] is not None
            or type(job.get("runner_id")) is not int
            or job["runner_id"] <= 0
            or not isinstance(job.get("started_at"), str)
        ):
            raise ValueError("Current running job identity mismatch")
        clock_type = getattr(datetime, "datetime", datetime)
        started = clock_type.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.tzinfo is None or started.utcoffset().total_seconds() != 0:
            raise ValueError("Expected UTC job start")
        elapsed = server.timestamp() - started.timestamp()
        if not 0 <= elapsed < 1800:
            raise ValueError("Job time is inconsistent or exhausted")
        end = anchor + 1800 - elapsed - 2
        if time.monotonic() >= end - 60:
            raise ValueError("No safe publication write window remains")
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
        OSError,
    ):
        raise AdapterError(
            "Unusable exact job deadline proof", "publish"
        ) from None
    _PUBLICATION_IDENTITY = identity
    _PUBLICATION_WINDOW = {"write": end - 60, "read": end - 30, "job": end}
    return _PUBLICATION_WINDOW


def publication_window():
    """Reuse one proof or keep its first failure closed without another read."""
    if _PUBLICATION_WINDOW is None and _PUBLICATION_FAILURE is not None:
        raise _PUBLICATION_FAILURE
    try:
        return prove_publication_window()
    except AdapterError as error:
        stop_publication(error)
        raise


def wait_for_publication_retry(attempt):
    """Queue padding after the next write reaches its ordinary pacing gate."""
    global _PUBLICATION_RETRY_JITTER
    if attempt not in (0, 1):
        raise AdapterError("Publication retry limit reached", "publish")
    delay = random.uniform(1, 2) if attempt == 0 else random.uniform(2, 4)
    deadline = publication_window()["write"]
    if deadline - time.monotonic() <= delay:
        raise AdapterError("No publication retry headroom remains", "publish")
    _PUBLICATION_RETRY_JITTER = delay


def stop_publication(error):
    """Keep a conclusive failure from starting another write phase."""
    global _PUBLICATION_FAILURE, _PUBLICATION_RETRY_JITTER
    _PUBLICATION_FAILURE = error
    _PUBLICATION_RETRY_JITTER = 0
    return error


def github_error_context(method, path):
    """Describe only known API routes, without credentials or URL queries."""
    verb = method if method in {"GET", "POST", "PATCH"} else "REQUEST"
    try:
        route = urllib.parse.urlsplit(path).path
    except (TypeError, ValueError):
        route = ""
    if not re.fullmatch(
        r"/(?:git/(?:trees|commits(?:/[0-9a-f]{40})?|ref/heads/main|"
        r"refs/heads/main)|contents/datas/[A-Za-z0-9-]+/"
        r"(?:data|metadata)\.json)",
        route,
    ):
        route = "[unrecognized API route]"
    return f"GitHub {verb} {route}"


def github_error_details(method, path, status, body, *, with_reason=False):
    """Return fixed labels from a bounded, unambiguous GitHub error body."""
    label = "unclassified"
    codes = set()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous GitHub error object")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Invalid GitHub JSON constant")

    try:
        if not isinstance(body, bytes) or len(body) > 16384:
            raise ValueError("Unusable GitHub error body")
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        if not isinstance(decoded, dict):
            raise ValueError("Expected GitHub error object")
        message = decoded.get("message")
        labels = {
            "Bad credentials": "bad-credentials",
            "Resource not accessible by integration": "integration-permission",
            (
                "Resource not accessible by personal " "access token"
            ): "token-permission",
            "Validation Failed": "validation-failed",
            "Reference update failed": "reference-update-failed",
            "Reference does not exist": "reference-missing",
            "Not Found": "not-found",
        }
        if isinstance(message, str):
            label = labels.get(message, label)
            if (
                method == "PATCH"
                and urllib.parse.urlsplit(path).path == "/git/refs/heads/main"
                and status == 422
                and message == "Update is not a fast forward"
            ):
                label = "non-fast-forward"
        errors = decoded.get("errors")
        if isinstance(errors, list) and len(errors) <= 10:
            allowed = {
                "missing",
                "missing_field",
                "invalid",
                "already_exists",
                "unprocessable",
                "custom",
            }
            for error in errors:
                code = error.get("code") if isinstance(error, dict) else None
                if isinstance(code, str) and code in allowed:
                    codes.add(code)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        label, codes = "unclassified", set()
    detail = "; GitHub error: " + label
    if codes:
        detail += "; codes=" + ",".join(sorted(codes)[:3])
    return (label, detail) if with_reason else detail


def github(method, path, payload=None, missing_ok=False):
    global _PUBLICATION_RETRY_JITTER
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError(
            "DATA_SOURCE_TOKEN is required for publication", "publish"
        )
    if method != "GET" and _PUBLICATION_FAILURE is not None:
        raise _PUBLICATION_FAILURE
    window = publication_window()
    deadline = window["read"] if method == "GET" else window["write"]
    retry_jitter = 0
    if method != "GET":
        retry_jitter = _PUBLICATION_RETRY_JITTER
        _PUBLICATION_RETRY_JITTER = 0
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        with publication_http_guard(deadline) as call_end:
            status, _, body = request_bytes(
                _GITHUB + path,
                method=method,
                headers=headers,
                deadline=call_end,
                retry_jitter=retry_jitter,
                payload=(
                    json.dumps(payload).encode()
                    if payload is not None
                    else None
                ),
            )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            github_error_context(method, path)
            + " connection failed; publication is unconfirmed",
            "publish",
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        reason, detail = github_error_details(
            method, path, status, body, with_reason=True
        )
        error = AdapterError(
            f"{github_error_context(method, path)} returned HTTP {status}"
            + detail,
            "publish",
            status,
        )
        error.github_reason = reason
        raise error
    try:
        return json.loads(body)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AdapterError(
            github_error_context(method, path)
            + " returned unusable JSON; publication is unconfirmed",
            "publish",
            status,
        ) from None


def read_snapshot():
    head = github("GET", "/git/ref/heads/main")["object"]["sha"]
    tree = github("GET", "/git/commits/" + head)["tree"]["sha"]
    files = {}
    for name in ("data.json", "metadata.json"):
        file = github(
            "GET",
            f"/contents/datas/{SOURCE_ID}/{name}?ref={head}",
            missing_ok=True,
        )
        if file is not None and file.get("encoding") != "base64":
            raise AdapterError(
                "Unsupported existing source file encoding", "publish"
            )
        files[name] = (
            None
            if file is None
            else base64.b64decode(file["content"]).decode("utf-8")
        )
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish"
        )
    return {"head": head, "tree": tree, "files": files}


def publish_files(snapshot, files):
    if _PUBLICATION_FAILURE is not None:
        raise _PUBLICATION_FAILURE
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        candidate = None
        try:
            tree = github(
                "POST",
                "/git/trees",
                {
                    "base_tree": snapshot["tree"],
                    "tree": [
                        {
                            "path": f"datas/{SOURCE_ID}/{name}",
                            "mode": "100644",
                            "type": "blob",
                            "content": text,
                        }
                        for name, text in files.items()
                    ],
                },
            )["sha"]
            candidate = github(
                "POST",
                "/git/commits",
                {
                    "message": f"Update {SOURCE_ID} collection outcome",
                    "tree": tree,
                    "parents": [snapshot["head"]],
                },
            )["sha"]
            github(
                "PATCH",
                "/git/refs/heads/main",
                {"sha": candidate, "force": False},
            )
            return
        except Exception as error:
            if not isinstance(error, AdapterError):
                error = AdapterError(
                    "Unusable publication response; outcome is unconfirmed",
                    "publish",
                )
            try:
                latest = read_snapshot()
            except Exception as verification:
                detail = (
                    safe_error(verification)
                    if isinstance(verification, AdapterError)
                    else type(verification).__name__
                )
                raise stop_publication(
                    AdapterError(
                        f"Publication attempt {attempt + 1}/3 failed: "
                        f"{safe_error(error)}; outcome verification "
                        f"failed: {detail}",
                        "publish",
                        getattr(verification, "status", None),
                    )
                ) from verification
            if candidate and (
                latest["head"] == candidate or latest["files"] == desired
            ):
                return
            if latest["files"] != expected:
                raise stop_publication(
                    AdapterError(
                        "Source changed concurrently; newer remote data "
                        "was not overwritten",
                        "publish",
                    )
                )
            if getattr(error, "github_reason", None) != "non-fast-forward":
                raise stop_publication(
                    AdapterError(
                        "Publication stopped after a non-retryable failure: "
                        + safe_error(error),
                        "publish",
                        error.status,
                    )
                ) from error
            snapshot = latest
            if attempt == 2:
                raise stop_publication(
                    AdapterError(
                        f"Publication failed after {attempt + 1} attempts; "
                        f"last failure: {safe_error(error)}",
                        "publish",
                        error.status,
                    )
                ) from error
            try:
                wait_for_publication_retry(attempt)
            except AdapterError as limit:
                stop_publication(limit)
                raise


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "display_name": "Wallonie-Bruxelles International",
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (
            previous.get("last_success_at") if error else attempt
        ),
        "last_checked_at": (
            previous.get("last_checked_at")
            if error
            else checked_at
            or max(
                (record["last_checked_at"] for record in records),
                default=attempt,
            )
        ),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (
                f"Collected {len(records)} validated WBI "
                f"funded opportunities"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def run_body(publishing):
    enter_phase("snapshot", 120)
    snapshot = read_snapshot() if publishing else None
    old = (
        json.loads(snapshot["files"]["data.json"])
        if snapshot and snapshot["files"]["data.json"] is not None
        else []
    )
    previous = (
        json.loads(snapshot["files"]["metadata.json"])
        if snapshot and snapshot["files"]["metadata.json"] is not None
        else {}
    )
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = utc_now(), None
    try:
        records = collect()
        checked_at = utc_now()
        validate_records(records)
        prior = {record["id"]: record for record in old}
        temporal = {
            "created_at",
            "updated_at",
            "first_seen_at",
            "last_seen_at",
            "last_checked_at",
        }
        for record in records:
            before = prior.get(record["id"], {})
            same = {
                key: value
                for key, value in before.items()
                if key not in temporal
            } == {
                key: value
                for key, value in record.items()
                if key not in temporal
            }
            record.update(
                created_at=before.get("created_at", attempt),
                first_seen_at=before.get("first_seen_at", attempt),
                updated_at=(
                    before.get("updated_at", attempt) if same else attempt
                ),
                last_seen_at=attempt,
                last_checked_at=checked_at,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
        validate_records(records)
        records = remove_expired_records(records, checked_at)
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    enter_phase(
        "failure" if failure else "publication",
        _FAILURE_SECONDS if failure else _PUBLICATION_SECONDS,
    )
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
    if publishing and (not failure or previous):
        files = {
            "metadata.json": json.dumps(outcome, ensure_ascii=False, indent=2)
            + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        try:
            publish_files(snapshot, files)
        except Exception as error:
            enter_phase("failure", _FAILURE_SECONDS)
            failure = error
            outcome = metadata(
                attempt,
                previous,
                old,
                AdapterError(
                    "Publication failed: " + safe_error(error), "publish"
                ),
            )
            try:
                latest = read_snapshot()
                if not previous or latest["files"] != snapshot["files"]:
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files(
                    latest,
                    {
                        "metadata.json": json.dumps(
                            outcome, ensure_ascii=False, indent=2
                        )
                        + "\n"
                    },
                )
            except Exception:
                outcome["message"] = (
                    "Publication failed; durable failure status could not be "
                    "recorded. Remote data was not force-overwritten."
                )
    elif publishing:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0



# Source-local state is separate from the preserved default GitHub namespace.
FAMILY = "youthopps-be-wbi-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_IDS = {SOURCE_ID: 378390052}
COLLECTION_TIMEOUT = 1200
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 30
_PUBLICATION_SECONDS = 180
_FAILURE_SECONDS = 180
_PUBLISHER_TIMEOUT = _DEFAULT_HTTP_TIMEOUT = 30
_ARTIFACT_NAME = "be-wbi-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = _COLLECTION_LOCK_CONTEXT = None
_COLLECTION_STARTED = None
_LEGACY_COLLECTION_OBSERVATION = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_RUN_END = _PHASE_END = _SOURCE_END = None
_PHASE_NAME = None
_JOURNAL_NEW = _INITIAL_DIRECTORY_PROVEN = False
_STATE_PARENT_FD = _STATE_PARENT_ID = _STATE_PARENT_PATH = None
_PUBLISHER_GATE_FAILED = False
_LAST_KNOWN_STATE = None
_JOURNAL_LOCKS = {}
_JOURNAL_GUARDS = {}
_JOURNAL_ACK = None
_JOURNAL_WRITE_ACK = None
_OWNED_EXPORT = None
_VERIFIED_REMOTE_CREATE = None
_SCANNED_HISTORY = {}

# Reviewed complete pre-access failure ancestry; unknown gaps remain fatal.
_STARTUP_QUIET_UNTIL = None
_HISTORY_COMPLETED = []
_RECOVERY_JOBS = {}
_PREACCESS_OWNER = (40889063854, 1, SOURCE_ID)
_PREACCESS_ARTIFACT = (
    12903619139,
    "sha256:10f6fe52c39de27726595d551d4adfeab74685983bc5db204db1583adeeb2028",
)
_PREACCESS_WORKFLOW_BLOB = "8769177f6248acfbb5f21ebd3213475136d57f61"
_RECOVERY_WORKFLOW_BLOBS = {_PREACCESS_WORKFLOW_BLOB}
_PREACCESS_SOURCE_BLOBS = {
    "5fd9fa82d5d2d5f0871aba653376765a6e00c897":
        "46ced66a5c36476d3bd5abb8cf38003b0a7285fe",
    "dd15bee13fc772ecbf6d72fa19ea02f0f6ffd31a":
        "46ced66a5c36476d3bd5abb8cf38003b0a7285fe",
}
_PREACCESS_ATTEMPTS = {
    41068492690: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        124106100773,
        '2026-10-30T14:03:38Z',
        '2026-10-30T14:04:07Z',
    ),
    41206274937: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        124551869581,
        '2026-10-31T13:35:41Z',
        '2026-10-31T13:36:07Z',
    ),
    41315187976: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        124873612758,
        '2026-11-01T14:14:24Z',
        '2026-11-01T14:14:45Z',
    ),
    41486816367: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        125419262871,
        '2026-11-02T16:59:43Z',
        '2026-11-02T17:00:21Z',
    ),
    41659664520: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        125999206152,
        '2026-11-03T15:02:18Z',
        '2026-11-03T15:02:52Z',
    ),
    41856931986: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        126667982712,
        '2026-11-04T15:21:52Z',
        '2026-11-04T15:22:30Z',
    ),
    42054804796: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        127340152709,
        '2026-11-05T15:25:23Z',
        '2026-11-05T15:25:49Z',
    ),
    42238597392: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        127962645577,
        '2026-11-06T14:57:47Z',
        '2026-11-06T14:58:19Z',
    ),
    42379195981: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        128423023353,
        '2026-11-07T13:55:56Z',
        '2026-11-07T13:56:25Z',
    ),
    42488886990: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        128756677411,
        '2026-11-08T14:15:15Z',
        '2026-11-08T14:15:36Z',
    ),
    42664449837: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        129324235607,
        '2026-11-09T17:25:22Z',
        '2026-11-09T17:25:52Z',
    ),
    42845199694: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        129930700435,
        '2026-11-10T15:31:25Z',
        '2026-11-10T15:32:12Z',
    ),
    43044964741: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        130606359485,
        '2026-11-11T15:06:16Z',
        '2026-11-11T15:06:56Z',
    ),
    43253123702: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        131304063725,
        '2026-11-12T15:56:04Z',
        '2026-11-12T15:56:40Z',
    ),
    43451305232: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        131973871948,
        '2026-11-13T15:18:40Z',
        '2026-11-13T15:19:22Z',
    ),
    43603011847: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        132471602690,
        '2026-11-14T14:05:17Z',
        '2026-11-14T14:05:54Z',
    ),
    43725816277: (
        1,
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
        132847385837,
        '2026-11-15T14:42:31Z',
        '2026-11-15T14:43:04Z',
    ),
    43913201360: (
        1,
        'dd15bee13fc772ecbf6d72fa19ea02f0f6ffd31a',
        133460313386,
        '2026-11-16T16:57:41Z',
        '2026-11-16T16:58:27Z',
    ),
}

_SCANNED_JOB_DIGESTS = {}
_ATTEMPT_HEADS = {}
_INVENTORY_ACTIVE = False
_ROBOTS_CONTEXT = None
_INFRASTRUCTURE_UNTIL = {}
_LOCAL_FIRST_ACCESS_CHECKED = False
_COLLECTION_PREPARED = False
_RESTORED_LATEST = _RESTORED_HISTORY = None
_PUBLISHER_HOSTS = {"wbi.be", "www.wbi.be"}
_LEGACY_NO_JOB = {}
_LOCAL_RESEARCH_MIGRATION = {
    'schema': 1,
    'source': 'be-wbi',
    'evidence': (
        'https://github.com/YouthOpps/data-pipeline/issues/312#issuecomment-6'
        '209076636'
    ),
    'state': {
        'schema': 3,
        'family': 'youthopps-be-wbi-publisher-v1',
        'legacy_hold_history': 'unlogged_fields_unknown',
        'legacy_access_history_unknown': True,
        'legacy_access_observations': [],
        'research_provenance': {
            'status': 'ROOT_READONLY_FULL27_JOBS_LEGACY_METADATA_MAX_PROPOSAL',
            'fresh_history_sha256': (
                '0de816068b6903c6ddfa4de9b65bf85fe37c8c96e4f9d5d91c35753c0515'
                'f2a6'
            ),
            'complete_attempts': 27,
            'executed_job_completion_upper_bounds': [
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38763419493,
                    'attempt': 1,
                    'job_id': 116520331183,
                    'time': 1792080109.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38721259612,
                    'attempt': 1,
                    'job_id': 116376106162,
                    'time': 1792061043.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38720084129,
                    'attempt': 1,
                    'job_id': 116422193478,
                    'time': 1792067623.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38699535766,
                    'attempt': 1,
                    'job_id': 116307469573,
                    'time': 1792050360.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38684235840,
                    'attempt': 1,
                    'job_id': 116255573779,
                    'time': 1792040693.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38653185962,
                    'attempt': 1,
                    'job_id': 116156655980,
                    'time': 1792020080.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38617479722,
                    'attempt': 1,
                    'job_id': 116036182946,
                    'time': 1792003582.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38575119501,
                    'attempt': 1,
                    'job_id': 115890068272,
                    'time': 1791985998.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38534756435,
                    'attempt': 1,
                    'job_id': 115762998186,
                    'time': 1791967710.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38530521163,
                    'attempt': 1,
                    'job_id': 115741034251,
                    'time': 1791964188.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38486156395,
                    'attempt': 1,
                    'job_id': 115600197042,
                    'time': 1791935337.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38461567531,
                    'attempt': 1,
                    'job_id': 115517770389,
                    'time': 1791922833.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38377089028,
                    'attempt': 1,
                    'job_id': 115228079907,
                    'time': 1791884178.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38315043136,
                    'attempt': 1,
                    'job_id': 115029551063,
                    'time': 1791843201.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38229534500,
                    'attempt': 1,
                    'job_id': 114745307716,
                    'time': 1791797195.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38185405300,
                    'attempt': 1,
                    'job_id': 114606538585,
                    'time': 1791762474.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38166116917,
                    'attempt': 1,
                    'job_id': 114548924389,
                    'time': 1791745380.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38126916113,
                    'attempt': 1,
                    'job_id': 114433083194,
                    'time': 1791709531.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38095753116,
                    'attempt': 1,
                    'job_id': 114341175439,
                    'time': 1791675744.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38076801414,
                    'attempt': 1,
                    'job_id': 114285369452,
                    'time': 1791657905.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38071840281,
                    'attempt': 1,
                    'job_id': 114274950679,
                    'time': 1791654764.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38038572606,
                    'attempt': 1,
                    'job_id': 114174088776,
                    'time': 1791621750.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38007470104,
                    'attempt': 1,
                    'job_id': 114079480609,
                    'time': 1791590908.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37981135658,
                    'attempt': 1,
                    'job_id': 113991645483,
                    'time': 1791574671.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37909980312,
                    'attempt': 1,
                    'job_id': 113754795259,
                    'time': 1791537895.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37869499633,
                    'attempt': 1,
                    'job_id': 113624886485,
                    'time': 1791509380.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37869478325,
                    'attempt': 1,
                    'job_id': 113623998633,
                    'time': 1791509172.0,
                },
            ],
            'original_legacy_observation': {
                'namespace': 'wbi-publisher-pacing',
                'status': 'ABSENT_NOT_ZERO_HISTORY',
                'collection_lock_before_entry': {
                    'status': 'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE',
                },
            },
            'metadata_sha256': (
                '0648ac28a5e1e5f46f77a699ba546c2cc6d274483113784687ea3b6e3be0'
                '8292'
            ),
            'data_source_pin': 'a6713e419a1fdca3299d60ce81080d4d3223bc07',
            'unknown_constraints': (
                'unlogged historical publisher access/refusal/429 headers rem'
                'ain unknown; zero observed strikes only after explicit trans'
                'ition'
            ),
            'baseline_collection_lock': (
                'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE'
            ),
        },
        'starts': [
            1791509172.0,
            1791509380.0,
            1791537895.0,
            1791574671.0,
            1791590908.0,
            1791621750.0,
            1791654764.0,
            1791657905.0,
            1791675744.0,
            1791709531.0,
            1791745380.0,
            1791762474.0,
            1791797195.0,
            1791843201.0,
            1791884178.0,
            1791922833.0,
            1791935337.0,
            1791964188.0,
            1791967710.0,
            1791985998.0,
            1792003582.0,
            1792020080.0,
            1792040693.0,
            1792050360.0,
            1792061043.0,
            1792067623.0,
            1792080109.0,
        ],
        'interval': 12.2,
        'until': 1792129880.7685463,
        'page_gap': 30,
        'completed': 1792080109.0,
        'inventory_completed': 1792080109.0,
        'blocked': False,
        'in_flight': False,
        'legacy_429_history': 'unknown',
        'observed_429_since_transition': 0,
        'rate_limit_until': 0,
        'observed_at': 1792129820.7685463,
    },
    'state_sha256': (
        '0b24d5f6acea76920c2ff79612500b0092ccfcf8c8717ae0591bc1a5cbad54f7'
    ),
    'legacy_state_sha256': (
        'b82af02c6f8ed2bb125b7d104ba6c9c89fc1f85ee141655120daf5860f2cd609'
    ),
}
_REVIEWED_NATIVE_MIGRATION = {'schema': 1,
 'source': 'be-wbi',
 'evidence': (
                'https://github.com/YouthOpps/data-pipeli'
                'ne/issues/312#issuecomment-6209686521'
            ),
 'state': {'schema': 3,
           'family': 'youthopps-be-wbi-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [],
           'research_provenance': {'baseline_collection_lock': 'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE',
                                   'complete_attempts': 27,
                                   'data_source_pin': (
                                                         'a6713e419a1fdca3299d60ce81080d4d3223bc07'
                                                     ),
                                   'executed_job_completion_upper_bounds': [{'attempt': 1,
                                                                             'job_id': 116520331183,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38763419493,
                                                                             'time': 1792080109.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116376106162,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38721259612,
                                                                             'time': 1792061043.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116422193478,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38720084129,
                                                                             'time': 1792067623.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116307469573,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38699535766,
                                                                             'time': 1792050360.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116255573779,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38684235840,
                                                                             'time': 1792040693.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116156655980,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38653185962,
                                                                             'time': 1792020080.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116036182946,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38617479722,
                                                                             'time': 1792003582.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115890068272,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38575119501,
                                                                             'time': 1791985998.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115762998186,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38534756435,
                                                                             'time': 1791967710.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115741034251,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38530521163,
                                                                             'time': 1791964188.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115600197042,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38486156395,
                                                                             'time': 1791935337.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115517770389,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38461567531,
                                                                             'time': 1791922833.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115228079907,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38377089028,
                                                                             'time': 1791884178.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115029551063,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38315043136,
                                                                             'time': 1791843201.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114745307716,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38229534500,
                                                                             'time': 1791797195.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114606538585,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38185405300,
                                                                             'time': 1791762474.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114548924389,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38166116917,
                                                                             'time': 1791745380.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114433083194,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38126916113,
                                                                             'time': 1791709531.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114341175439,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38095753116,
                                                                             'time': 1791675744.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114285369452,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38076801414,
                                                                             'time': 1791657905.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114274950679,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38071840281,
                                                                             'time': 1791654764.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114174088776,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38038572606,
                                                                             'time': 1791621750.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114079480609,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38007470104,
                                                                             'time': 1791590908.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113991645483,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37981135658,
                                                                             'time': 1791574671.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113754795259,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37909980312,
                                                                             'time': 1791537895.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113624886485,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37869499633,
                                                                             'time': 1791509380.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113623998633,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37869478325,
                                                                             'time': 1791509172.0}],
                                   'fresh_history_sha256': (
                                                              '0de816068b6903c6ddfa4de9b65bf85fe37c8c96'
                                                              'e4f9d5d91c35753c0515f2a6'
                                                          ),
                                   'metadata_sha256': (
                                                         '0648ac28a5e1e5f46f77a699ba546c2cc6d27448'
                                                         '3113784687ea3b6e3be08292'
                                                     ),
                                   'original_legacy_observation': {'collection_lock_before_entry': {'status': 'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE'},
                                                                   'namespace': (
                                                                                   'wbi-publisher-pacing'
                                                                               ),
                                                                   'status': (
                                                                                'ABSENT_NOT_ZERO_HISTORY'
                                                                            )},
                                   'status': (
                                                'ROOT_READONLY_FULL27_JOBS_LEGACY_METADAT'
                                                'A_MAX_PROPOSAL'
                                            ),
                                   'unknown_constraints': 'unlogged '
                                                          'historical '
                                                          'publisher '
                                                          'access/refusal/429 '
                                                          'headers remain '
                                                          'unknown; zero '
                                                          'observed '
                                                          'strikes only '
                                                          'after explicit '
                                                          'transition'},
           'starts': [1791509172.0,
                      1791509380.0,
                      1791537895.0,
                      1791574671.0,
                      1791590908.0,
                      1791621750.0,
                      1791654764.0,
                      1791657905.0,
                      1791675744.0,
                      1791709531.0,
                      1791745380.0,
                      1791762474.0,
                      1791797195.0,
                      1791843201.0,
                      1791884178.0,
                      1791922833.0,
                      1791935337.0,
                      1791964188.0,
                      1791967710.0,
                      1791985998.0,
                      1792003582.0,
                      1792020080.0,
                      1792040693.0,
                      1792050360.0,
                      1792061043.0,
                      1792067623.0,
                      1792080109.0,
                      1792131384.1868145,
                      1792131397.2941773,
                      1792131397.7312076,
                      1792131398.9309628,
                      1792131411.1312177,
                      1792131411.1340926,
                      1792131411.8506842,
                      1792131413.9158933,
                      1792131426.1177025,
                      1792131426.1215372,
                      1792131426.7412431,
                      1792131429.487292,
                      1792131441.689956,
                      1792131441.6922927,
                      1792131442.1870565,
                      1792131444.2191887,
                      1792131456.421561,
                      1792131456.4304988,
                      1792131456.4472606,
                      1792131458.3707855,
                      1792131470.5730934,
                      1792131470.5762203,
                      1792131471.067965,
                      1792131473.4885182,
                      1792131485.6901727,
                      1792131485.6959174,
                      1792131485.869367,
                      1792131488.435011,
                      1792131500.6375291,
                      1792131500.6458335,
                      1792131500.8216357,
                      1792131503.1257904,
                      1792131515.328046,
                      1792131515.3312814,
                      1792131516.0695753,
                      1792131518.5898871,
                      1792131530.7952716,
                      1792131530.7991614,
                      1792131531.49404,
                      1792131533.5803432,
                      1792131545.7885568,
                      1792131545.800235,
                      1792131546.3913147,
                      1792131549.0843418,
                      1792131561.287793,
                      1792131561.2931323,
                      1792131561.8584912,
                      1792131563.9234233,
                      1792131576.126344,
                      1792131576.1506567,
                      1792131576.6575992,
                      1792131578.3168917,
                      1792131590.5200226,
                      1792131590.5386977,
                      1792131590.7138426,
                      1792131592.302015,
                      1792131604.5053082,
                      1792131604.5149686,
                      1792131604.6985579,
                      1792131607.2117317,
                      1792131619.4176486,
                      1792131619.423902,
                      1792131619.867172,
                      1792131621.7006733,
                      1792131633.9030821,
                      1792131633.9063962,
                      1792131633.920456,
                      1792131635.6245553,
                      1792131647.8270695,
                      1792131647.8328147,
                      1792131648.0059683,
                      1792131649.6347728,
                      1792131661.8368022,
                      1792131661.8399518,
                      1792131662.3815768,
                      1792131664.3887634,
                      1792131676.5904834,
                      1792131676.5990784,
                      1792131676.618145,
                      1792131677.985494,
                      1792131690.1868813,
                      1792131690.1901367,
                      1792131690.371414,
                      1792131692.1443071,
                      1792131704.3459175,
                      1792131704.3486452,
                      1792131704.379663,
                      1792131707.1474388,
                      1792131737.1495957,
                      1792131737.1522133,
                      1792131737.3169506,
                      1792131739.4805417,
                      1792131769.483775,
                      1792131769.4887035,
                      1792131770.1759398,
                      1792131776.1453888,
                      1792131806.148205,
                      1792131806.150804,
                      1792131806.5195644,
                      1792131814.1475723,
                      1792131826.3496315,
                      1792131826.3524706,
                      1792131827.0639372,
                      1792131832.4803247,
                      1792131844.681524,
                      1792131844.684324,
                      1792131845.094238,
                      1792131856.5225348,
                      1792131886.5307398,
                      1792131886.5356104,
                      1792131887.0230234,
                      1792131896.9577084,
                      1792131909.187787,
                      1792131909.2189054,
                      1792131910.2108521,
                      1792131918.9069145],
           'interval': 12.2,
           'until': 1792131979.5071602,
           'page_gap': 30,
           'completed': 1792131918.9082556,
           'inventory_completed': 1792131896.9610837,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792131919.5071602},
 'state_sha256': (
                    '276362215bf8752eedbdbb50a9da0466c6307092'
                    'f8036ec67a9480fe8db1cfdd'
                ),
 'legacy_state_sha256': (
                           'b82af02c6f8ed2bb125b7d104ba6c9c89fc1f85e'
                           'e141655120daf5860f2cd609'
                       )}
_LEGACY_ATTEMPTS = {38763419493: {'source': 'be-wbi',
               'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '753baa98eb042cbedd26a2d683b5ac9a'
                   'd09a4a8ce68d0aacccc4ef0fac87b268'
               )},
 38721259612: {'source': 'be-wbi',
               'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'a4ae2f6d3b4aae9f62d625890c6ca3e3'
                   '1b7e4ae45334394a1d3187b97ba6bc08'
               )},
 38720084129: {'source': 'be-wbi',
               'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'ec0514ea60ed1014378ee08398fa7e7a'
                   'fd90ea0551b728482a8c04ea670fa1ad'
               )},
 38699535766: {'source': 'be-wbi',
               'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '1a206d1e62f7c0425ba7a80919a65d21'
                   '36be4a937df01698c955f6f2b779482b'
               )},
 38684235840: {'source': 'be-wbi',
               'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '7ec02ef594b073d7bfaf039390a62659'
                   'e5a2b250a5ae48da23f2787b27f9d7e4'
               )},
 38653185962: {'source': 'be-wbi',
               'head_sha': 'e1a68725fe923582b5640481e29c7166eb77397c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '1c94060acdf67bc8067e19db53d396d1'
                   'a9c2e1680be8000813be715d709319e3'
               )},
 38617479722: {'source': 'be-wbi',
               'head_sha': '92235b48ad33b9fa377e0736b689010173d7bfab',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'a8425f6b8e4c8451777a69a5830e0f4d'
                   '4156b8075df28f8f2966578312fb83a0'
               )},
 38575119501: {'source': 'be-wbi',
               'head_sha': '92235b48ad33b9fa377e0736b689010173d7bfab',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '482e11ead7c931a65334c02ef0995da2'
                   'c1df8b7e954dcdd4c16737c64bd7e438'
               )},
 38534756435: {'source': 'be-wbi',
               'head_sha': '5ab9b0237b7fa2b043fe276ec43f29e493c685ab',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '7b0c30f937078a7191e55f3823b1c469'
                   '03df8cca2cc2482545a3a24ced8e70e1'
               )},
 38530521163: {'source': 'be-wbi',
               'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '6a18abbc6e3d75af4eb583f6ba6b5523'
                   'bea7954a65d3664f2936b4c4bb5f2c52'
               )},
 38486156395: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'f39ef67191adf98892662f3c12f74745'
                   'b9afc3009c946d2f0f8d2a9af6036563'
               )},
 38461567531: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'dabb9aaf0368383c674ef49116f5e277'
                   '882fbb9551d136794aa42523932743f2'
               )},
 38377089028: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '854273b8fb280a26826fe2cb959e73a7'
                   '86e42f92920f3983afeedf8d8a971588'
               )},
 38315043136: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '3d25cc7b4077337eb973f097306acc99'
                   'ec29ca95b11f13a935a4679c7c84c660'
               )},
 38229534500: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '90667da7b6a22423c1eba8362657c5fc'
                   'c4d540d14154ed8c5af67bb50aef8b2c'
               )},
 38185405300: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '629211b568b24863db7927fa9d7f7a52'
                   '0b2cd4d82bdd6365bebf237cb6ba7a86'
               )},
 38166116917: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '53690b5c50664d725d6f1864f0574472'
                   '7c4270971d5796b95cfa964701bc80f2'
               )},
 38126916113: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '96c683b3b960b09a8be17a9019500659'
                   '70bb2a13651858781a4c1b4ee6a762d6'
               )},
 38095753116: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '14acc7eae3a85c7e4c410734bdab52e2'
                   '142052f438ce639638060c96802df041'
               )},
 38076801414: {'source': 'be-wbi',
               'head_sha': 'a97e28475634b9ff61489a11cd6378c9dafc67ae',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'fa5c791779a0a4d385a5719482ac7aeb'
                   'da77818898f8e099f3544c8c1e2b6aec'
               )},
 38071840281: {'source': 'be-wbi',
               'head_sha': '9b155b179283c8fa08cf56c1a83a54bd19d51814',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '827ee9b1dfb15474ea8534ee3549e4a8'
                   '0eba9b46ff68b0953aba0a43ca977491'
               )},
 38038572606: {'source': 'be-wbi',
               'head_sha': '68868a743f2ae9dcb55de9443c2ea7fcd8714696',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'e5b5aaf0ddac91dfe9dd22172a954c4d'
                   '2390cf63e51db9f63a22c781d4dd9e41'
               )},
 38007470104: {'source': 'be-wbi',
               'head_sha': '029a500dbf22c1d44ae592c667f26f33d9d364c8',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'b01693933b17e1847411b310b4541d7c'
                   '5d1cc04d225b0e18279ca95868360c89'
               )},
 37981135658: {'source': 'be-wbi',
               'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'c755c6451a841dc5a5153981fd12249c'
                   'a21a528c35b2b128a61527acfd6415fd'
               )},
 37909980312: {'source': 'be-wbi',
               'head_sha': 'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'bed1529633de78ebcc230e6f46b72d5f'
                   'afc26549e285cd4573cf728a07dce528'
               )},
 37869499633: {'source': 'be-wbi',
               'head_sha': '7026281e1d457a019aaf0f75f6fbc9b3fe70d3ad',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '994e8dae54a77a8354f06bfbd2615593'
                   '343019c3e5ad60efb0ea42fbe923773f'
               )},
 37869478325: {'source': 'be-wbi',
               'head_sha': '7026281e1d457a019aaf0f75f6fbc9b3fe70d3ad',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '0979a7f4b8a2d028ea707a1d6e056044'
                   'fb5ae05f8daf4bb4722186bede867438'
               )}}
_LEGACY_ACCESS_OBSERVATIONS = []




def fetch_source(url, min_interval=6, *, policy_response=False):
    original = urllib.parse.urlsplit(url)

    def checked(value):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or host != (original.hostname or "").lower().removeprefix("www.")
            or parsed.port != original.port
        ):
            raise AdapterError(
                "Source redirect leaves the reviewed HTTPS host", "fetch"
            )
        if host == "localhost" or host.endswith(".localhost"):
            raise AdapterError("Public HTTPS source required", "fetch")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise AdapterError("Public HTTPS source required", "fetch")
        except ValueError:
            pass

    for attempt in range(2):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(
                    current, interval=min_interval
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    destination = urllib.parse.urljoin(
                        current, headers["Location"]
                    )
                    checked(destination)
                    target = urllib.parse.urlsplit(destination)
                    if original.path == "/robots.txt" and (
                        target.path != "/robots.txt"
                        or target.query
                        or target.fragment
                    ):
                        raise AdapterError(
                            "Robots redirect leaves literal policy route",
                            "access",
                        )
                    current = destination
                    min_interval = max(min_interval, check_robots(current))
                    continue
                if policy_response:
                    return status, headers, body
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status}", "fetch", status
                    )
                return body.decode(headers.get_content_charset() or "utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error), "fetch"
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")



class PrimaryText(HTMLParser):
    """Inspect primary visible text and genuine head titles."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack, self.head_title_parts = [], [], []
        self.head_seen = self.head_allowed = False
        self.title_depth = None
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        # HTML block starts close a paragraph in button scope, including its
        # inline descendants. Keep suppression inherited from outer elements.
        if tag in {
            "address",
            "article",
            "aside",
            "blockquote",
            "center",
            "details",
            "dialog",
            "dir",
            "div",
            "dl",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "header",
            "hgroup",
            "main",
            "menu",
            "nav",
            "ol",
            "p",
            "search",
            "section",
            "summary",
            "ul",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "pre",
            "listing",
            "hr",
        }:
            for index in range(len(self.stack) - 1, -1, -1):
                ancestor = self.stack[index][0]
                if ancestor == "p":
                    del self.stack[index:]
                    break
                if ancestor in {
                    "applet",
                    "button",
                    "caption",
                    "html",
                    "marquee",
                    "object",
                    "table",
                    "td",
                    "template",
                    "th",
                }:
                    break
        values = {}
        for name, value in attrs:
            values.setdefault(name, value)
        style = re.sub(
            r"/\*.*?\*/", "", values.get("style") or "", flags=re.S
        ).lower()
        declarations, priorities = {}, {}
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                name = name.strip()
                important = bool(re.search(r"!\s*important\s*$", value))
                if priorities.get(name) and not important:
                    continue
                declarations[name] = re.sub(
                    r"\s*!\s*important\s*$", "", value
                ).strip()
                priorities[name] = important
        explicit_suppressed = (
            "hidden" in values
            or (values.get("aria-hidden") or "").strip().lower() == "true"
            or declarations.get("display") == "none"
            or declarations.get("visibility") == "hidden"
        )
        if (
            tag == "head"
            and not self.head_seen
            and all(
                name == "html" and not suppressed and not visibility
                for name, suppressed, visibility in self.stack
            )
        ):
            self.head_seen = True
            self.head_allowed = not explicit_suppressed
        if (
            tag == "title"
            and self.head_allowed
            and self.stack
            and self.stack[-1][0] == "head"
            and not explicit_suppressed
        ):
            self.title_depth = len(self.stack) + 1
        suppressed = (
            tag
            in {
                "head",
                "title",
                "script",
                "style",
                "template",
                "noscript",
                "footer",
                "nav",
                "aside",
            }
            or "hidden" in values
            or (values.get("aria-hidden") or "").strip().lower() == "true"
            or declarations.get("display") == "none"
        )
        inherited = self.stack[-1][1] if self.stack else False
        visibility = self.stack[-1][2] if self.stack else False
        if declarations.get("visibility") == "hidden":
            visibility = True
        elif declarations.get("visibility") == "visible":
            visibility = False
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((tag, inherited or suppressed, visibility))

    def handle_endtag(self, tag):
        if tag == "title":
            self.title_depth = None
        if tag == "head":
            self.head_allowed = False
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                del self.stack[position:]
                break

    def handle_data(self, value):
        if (
            self.title_depth is not None
            and len(self.stack) == self.title_depth
            and self.stack[-1][0] == "title"
        ):
            self.head_title_parts.append(value)
        if not self.stack or not any(self.stack[-1][1:]):
            self.parts.append(value)



def visible_access_challenge(body, challenge_only=False):
    """Recognize primary HTML refusals, not arbitrary JS/JSON/PDF strings."""
    document = body.decode("utf-8-sig", "replace")
    if not document.lstrip().startswith("<"):
        return False

    class RefusalText(PrimaryText):
        """Retain already-visible headlines as independent refusal evidence."""

        def __init__(self, value):
            self.headlines = []
            self.heading = None
            super().__init__(value)

        def handle_starttag(self, tag, attrs):
            super().handle_starttag(tag, attrs)
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.headlines.append([])
                self.heading = (tag, len(self.headlines) - 1)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            if self.heading is not None and self.heading[0] == tag:
                self.heading = None

        def handle_data(self, value):
            before = len(self.parts)
            super().handle_data(value)
            if self.heading is not None and len(self.parts) > before:
                self.headlines[self.heading[1]].append(value)

    parser = RefusalText(document)

    # This support instruction describes a possible error, not a refusal.
    # Remove only its own mention; adjacent denials and real HEAD titles stay.
    def without_instruction(parts):
        text = " ".join(" ".join(parts).lower().split())
        return re.sub(
            r"\bif you see access denied\s*,?\s*"
            r"(?:please\s+)?contact support\b",
            " ",
            text,
        )

    units = [parser.parts, parser.head_title_parts] + parser.headlines
    text = " ".join(without_instruction(parts) for parts in units)
    phrases = (
        "verify you are human",
        "verify that you are human",
        "human verification",
        "complete the captcha",
        "enable javascript and cookies to continue",
        "checking your browser",
    )
    if not challenge_only:
        phrases += (
            "access denied",
            "permission denied",
            "request rejected",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    explicit_title = without_instruction(parser.head_title_parts)
    return any(phrase in text for phrase in phrases) or bool(
        re.search(
            r"just a moment|attention required|access denied|captcha",
            explicit_title,
        )
    )



def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False



def empty_budget(now):
    return {
        "schema": 3,
        "family": FAMILY,
        "legacy_hold_history": "unlogged_fields_unknown",
        "legacy_access_history_unknown": True,
        "legacy_access_observations": copy.deepcopy(
            _LEGACY_ACCESS_OBSERVATIONS
        ),
        "research_provenance": {},
        "starts": [],
        "interval": 12.2,
        "until": now + 60,
        "page_gap": 30,
        "completed": 0,
        "inventory_completed": 0,
        "blocked": False,
        "in_flight": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": now,
    }



def validate_budget(state):
    """Validate retained constraints without resetting unknown history."""
    if not isinstance(state, dict):
        raise AdapterError("Invalid publisher state", "access")
    expected = set(empty_budget(0))
    if state.get("schema") == 4:
        expected.add("refusal_scope")
    if (
        set(state) != expected
        or type(state["schema"]) is not int
        or state["schema"] not in (3, 4)
        or state["family"] != FAMILY
        or any(
            not numeric(state[k])
            for k in (
                "interval",
                "until",
                "page_gap",
                "completed",
                "inventory_completed",
                "rate_limit_until",
                "observed_at",
            )
        )
        or state["interval"] < 12.2
        or state["page_gap"] < 30
        or state["legacy_429_history"] != "unknown"
        or state["legacy_hold_history"] != "unlogged_fields_unknown"
        or state["legacy_access_history_unknown"] is not True
        or state["legacy_access_observations"] != _LEGACY_ACCESS_OBSERVATIONS
        or not isinstance(state["research_provenance"], dict)
        or type(state["blocked"]) is not bool
        or type(state["in_flight"]) is not bool
        or type(state["observed_429_since_transition"]) is not int
        or not 0 <= state["observed_429_since_transition"] <= 100
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 1000
        or any(
            not numeric(t) or t > state["observed_at"] for t in state["starts"]
        )
        or state["starts"] != sorted(set(state["starts"]))
        or state["completed"] > state["observed_at"]
        or state["inventory_completed"] > state["observed_at"]
        or state["observed_at"] > time.time() + 1
    ):
        raise AdapterError("Invalid retained publisher constraints", "access")
    epochs = (
        "until",
        "completed",
        "inventory_completed",
        "rate_limit_until",
        "observed_at",
    )
    if (
        any(not supported_epoch(state[key]) for key in epochs)
        or any(not supported_epoch(value) for value in state["starts"])
        or not supported_epoch(time.time() + state["interval"])
        or not supported_epoch(time.time() + state["page_gap"])
    ):
        raise AdapterError("Unsupported retained clock magnitude", "access")
    if state["schema"] == 4:
        wbi_validate_scope(state)
    return state



class JournalAuthorityChanged(AdapterError):
    """A replacement journal cannot inherit a stale descriptor's authority."""



def journal_generation(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_gid, info.st_nlink, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


def journal_snapshot(descriptor):
    """Read the complete bounded bytes without adopting a new generation."""
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    before = validate_state_descriptor(descriptor, parent, name)
    raw = os.pread(descriptor, 16385, 0)
    after = validate_state_descriptor(descriptor, parent, name)
    if (len(raw) > 16384 or len(raw) != after.st_size
            or journal_generation(before) != journal_generation(after)):
        raise JournalAuthorityChanged("Publisher read changed", "access")
    return journal_generation(after), raw


def require_journal_snapshot(descriptor, expected):
    if expected is None or journal_snapshot(descriptor) != expected:
        raise JournalAuthorityChanged(
            "Publisher journal acknowledgement changed", "access"
        )


def acknowledge_journal_write(descriptor, raw):
    """Track only bytes observed after our own completed write syscall."""
    global _JOURNAL_WRITE_ACK
    current = journal_snapshot(descriptor)
    if current[1] != raw:
        raise JournalAuthorityChanged("Publisher write changed", "access")
    _JOURNAL_WRITE_ACK = current


def verify_current_journal():
    """Recheck the retained acknowledgement across close and reopen."""
    if _JOURNAL_ACK is not None:
        with locked_budget():
            locked_journal_authority()


def locked_journal_authority(check_generation=True):
    """Require the original RW flock and last acknowledged full bytes."""
    try:
        collection_lock_authority()
        if len(_JOURNAL_LOCKS) != 1:
            raise ValueError("Exactly one original journal lock required")
        descriptor, identity = next(iter(_JOURNAL_LOCKS.items()))
        guard = _JOURNAL_GUARDS[descriptor]
        parent = state_parent()
        name = os.path.basename(_STATE_PATH)
        for held in (descriptor, guard):
            info = validate_state_descriptor(held, parent, name)
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("Retained journal identity changed")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if check_generation:
            require_journal_snapshot(guard, _JOURNAL_ACK)
        return guard
    except BaseException as error:
        fail_publisher_cleanup()
        raise JournalAuthorityChanged(
            "Locked current publisher journal changed", "access"
        ) from error



def state_parent(directory=None):
    """Pin the verified parent and refuse replacement of its current path."""
    global _STATE_PARENT_FD, _STATE_PARENT_ID, _STATE_PARENT_PATH
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    directory = directory or (
        os.path.dirname(_STATE_PATH) if _STATE_PATH else _STATE_PARENT_PATH
    )
    if not directory or not os.path.isabs(directory):
        raise AdapterError("Publisher state parent unavailable", "access")
    fresh = _STATE_PARENT_FD is None
    descriptor = (
        os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if fresh
        else _STATE_PARENT_FD
    )
    try:
        info = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not stat.S_ISDIR(current.st_mode)
            or current.st_uid != os.getuid()
            or current.st_mode & 0o077
            or identity != (current.st_dev, current.st_ino)
            or not fresh
            and (
                directory != _STATE_PARENT_PATH or identity != _STATE_PARENT_ID
            )
        ):
            raise AdapterError("Publisher state parent changed", "access")
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if fresh:
            os.close(descriptor)
        raise
    if fresh:
        _STATE_PARENT_FD = descriptor
        _STATE_PARENT_ID = identity
        _STATE_PARENT_PATH = directory
    return descriptor



def validate_state_descriptor(descriptor, parent, name):
    """Bind the original safe read/write inode in its verified parent."""
    info = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if ((info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_RDWR):
        raise JournalAuthorityChanged(
            "Publisher journal path or RW descriptor changed", "access"
        )
    validate_owned_output(info)
    state_parent(_STATE_PARENT_PATH)
    return info



def budget_file():
    global _STATE_PATH, _STATE_LOCK, _INITIAL_DIRECTORY_PROVEN
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    if _STATE_PARENT_FD is not None:
        state_parent(directory)
    else:
        try:
            os.mkdir(directory, mode=0o700)
        except FileExistsError:
            pass
        else:
            _INITIAL_DIRECTORY_PROVEN = True
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    state_parent(directory)
    _STATE_PATH = _STATE_LOCK = os.path.join(directory, "state.json")



def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        if (
            _INITIAL_DIRECTORY_PROVEN
            and not _RESTORED
            and _VERIFIED_REMOTE_CREATE is not None
        ):
            try:
                fd = os.open(
                    name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent
                )
                _JOURNAL_NEW = True
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=parent)
                _JOURNAL_NEW = False
        else:
            fd = os.open(name, flags, dir_fd=parent)
    except BaseException:
        fail_publisher_cleanup()
        raise
    acquired = None
    try:
        acquired = os.fstat(fd)
        info = validate_state_descriptor(fd, parent, name)
    except BaseException:
        fail_publisher_cleanup()
        if acquired is not None:
            try:
                close_retained_descriptor(
                    fd, (acquired.st_dev, acquired.st_ino)
                )
            except BaseException:
                pass
        raise
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    file = os.fdopen(fd, "r+", closefd=False)
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        info = validate_state_descriptor(file.fileno(), parent, name)
        return BudgetLock(file, info)
    except BaseException:
        fail_publisher_cleanup()
        try:
            file.close()
        except BaseException:
            pass
        try:
            close_retained_descriptor(fd, (info.st_dev, info.st_ino))
        except BaseException:
            pass
        raise



def load_budget():
    """Acknowledge a first validated read; never adopt later unseen bytes."""
    global _JOURNAL_ACK
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        authority = locked_journal_authority(False)
        snapshot = journal_snapshot(authority)
        if _JOURNAL_ACK is not None:
            require_journal_snapshot(authority, _JOURNAL_ACK)
        raw = snapshot[1]
        if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
            state = validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
        else:
            try:
                state = validate_budget(json.loads(raw))
            except (ValueError, TypeError):
                raise AdapterError(
                    "Corrupt publisher journal", "access"
                ) from None
        require_journal_snapshot(authority, snapshot)
        if _JOURNAL_ACK is None:
            _JOURNAL_ACK = snapshot
        locked_journal_authority()
        return state
    except BaseException:
        fail_publisher_cleanup()
        raise



def write_budget(state):
    """Latch and invalidate the original journal on persistence failure."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED, _LAST_KNOWN_STATE
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        if _LAST_KNOWN_STATE is None:
            _LAST_KNOWN_STATE = copy.deepcopy(state)
        else:
            _LAST_KNOWN_STATE = merge_budget(_LAST_KNOWN_STATE, state)
        return write_budget_body(state)
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if not isinstance(error, JournalAuthorityChanged):
            try:
                locked_journal_authority(False)
                invalidate_failed_journal()
            except BaseException:
                # Failed cleanup never replaces the original persistence error.
                pass
        raise



def retained_descriptor_info(descriptor, identity):
    """Validate original ownership without adopting a named replacement."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_nlink not in (0, 1)
        or fcntl.fcntl(descriptor, fcntl.F_GETFL)
        & os.O_ACCMODE != os.O_RDWR
    ):
        raise AdapterError("Retained descriptor ownership changed", "access")
    return info



def close_retained_descriptor(descriptor, identity):
    """Never close a descriptor reused for an unrelated object."""
    retained_descriptor_info(descriptor, identity)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(descriptor)



def fail_publisher_cleanup():
    """Cleanup failure stays fatal across later collection/export entries."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    _STATE_TRUSTED = False
    _PUBLISHER_GATE_FAILED = True
    try:
        invalidate_family_artifact()
    except BaseException:
        # Unsafe objects remain untouched; the sticky gate still forbids export.
        pass



def collection_lock_authority():
    """Require the acquired lifecycle descriptors and current named inode."""
    if _COLLECTION_LOCK_FD is None:
        return
    try:
        descriptor, guard, identity, path = _COLLECTION_LOCK_CONTEXT
        if descriptor != _COLLECTION_LOCK_FD:
            raise ValueError("Lifecycle descriptor changed")
        for held in (descriptor, guard):
            retained_descriptor_info(held, identity)
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat(path, follow_symlinks=False)
        validate_owned_output(current)
        if (current.st_dev, current.st_ino) != identity:
            raise ValueError("Lifecycle path changed")
    except BaseException as error:
        fail_publisher_cleanup()
        raise AdapterError("Shared lifecycle lock changed", "access") from error



def close_collection_lock():
    """Release only acquired lifecycle FDs and preserve a primary error."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT
    primary = sys.exc_info()[0] is not None
    context = _COLLECTION_LOCK_CONTEXT
    descriptor = _COLLECTION_LOCK_FD
    if descriptor is None:
        return
    failure = None
    if not _PUBLISHER_GATE_FAILED:
        try:
            collection_lock_authority()
            verify_current_journal()
        except BaseException as error:
            failure = error
    _COLLECTION_LOCK_FD = _COLLECTION_LOCK_CONTEXT = None
    if context is None or context[0] != descriptor:
        failure = AdapterError("Lifecycle ownership unavailable", "access")
    else:
        original, guard, identity, path = context
        safe = []
        for held in (guard, original):
            try:
                retained_descriptor_info(held, identity)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                safe.append(held)
            except BaseException as error:
                failure = failure or error
        for held in safe:
            try:
                close_retained_descriptor(held, identity)
            except BaseException as error:
                failure = failure or error
    if failure is not None:
        fail_publisher_cleanup()
        if not primary:
            raise failure



class BudgetLock:
    """Retain owned original descriptors without implicit foreign-FD close."""

    def __init__(self, file, info):
        self.file = file
        self.descriptor = file.fileno()
        self.identity = (info.st_dev, info.st_ino)
        self.guard = os.dup(self.descriptor)
        _JOURNAL_LOCKS[self.descriptor] = self.identity
        _JOURNAL_GUARDS[self.descriptor] = self.guard

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        primary = sys.exc_info()[0] is not None
        if self.descriptor is None:
            return
        descriptor, guard = self.descriptor, self.guard
        self.descriptor = self.guard = None
        failure = None
        safe = []
        if not _PUBLISHER_GATE_FAILED:
            try:
                locked_journal_authority()
            except BaseException as error:
                failure = error
        _JOURNAL_LOCKS.pop(descriptor, None)
        _JOURNAL_GUARDS.pop(descriptor, None)
        for held in (guard, descriptor):
            try:
                retained_descriptor_info(held, self.identity)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                safe.append(held)
            except BaseException as error:
                failure = failure or error
        try:
            # Flush the unused, non-owning wrapper before closing owned FDs.
            # The wrapper cannot close a numeric FD even if it was reused.
            self.file.close()
        except BaseException as error:
            failure = failure or error
        for held in safe:
            try:
                close_retained_descriptor(held, self.identity)
            except BaseException as error:
                failure = failure or error
        if failure is not None:
            fail_publisher_cleanup()
            if not primary:
                raise failure



def invalidate_failed_journal():
    """Invalidate only our still-current last observed owned generation."""
    authority = locked_journal_authority(False)
    expected = _JOURNAL_WRITE_ACK or _JOURNAL_ACK
    require_journal_snapshot(authority, expected)
    os.ftruncate(authority, 0)
    os.fsync(authority)
    validate_state_descriptor(
        authority, state_parent(), os.path.basename(_STATE_PATH)
    )



def write_budget_body(state):
    """Commit only acknowledged bytes through the retained original FD."""
    global _INITIAL_DIRECTORY_PROVEN, _JOURNAL_ACK, _JOURNAL_WRITE_ACK
    state = validate_budget(state)
    raw = json.dumps(state, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher journal commit", "access")
    authority = locked_journal_authority()
    _JOURNAL_WRITE_ACK = _JOURNAL_ACK
    os.ftruncate(authority, 0)
    acknowledge_journal_write(authority, b"")
    os.fsync(authority)
    require_journal_snapshot(authority, _JOURNAL_WRITE_ACK)
    offset = 0
    while offset < len(raw):
        require_journal_snapshot(authority, _JOURNAL_WRITE_ACK)
        written = os.pwrite(authority, raw[offset:], offset)
        if type(written) is not int or not 0 < written <= len(raw) - offset:
            raise AdapterError("Incomplete publisher journal write", "access")
        offset += written
        acknowledge_journal_write(authority, raw[:offset])
    os.fsync(authority)
    require_journal_snapshot(authority, _JOURNAL_WRITE_ACK)
    _JOURNAL_ACK = _JOURNAL_WRITE_ACK
    _JOURNAL_WRITE_ACK = None
    _INITIAL_DIRECTORY_PROVEN = False



def merge_budget(left, right):
    left, right = validate_budget(left), validate_budget(right)
    result = empty_budget(time.time())
    for key in (
        "until",
        "interval",
        "page_gap",
        "completed",
        "inventory_completed",
        "observed_429_since_transition",
        "rate_limit_until",
    ):
        result[key] = max(left[key], right[key])
    if (
        left["research_provenance"]
        and right["research_provenance"]
        and left["research_provenance"] != right["research_provenance"]
    ):
        raise AdapterError("Research provenance differs across state", "access")
    result["research_provenance"] = (
        left["research_provenance"] or right["research_provenance"]
    )
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["legacy_access_history_unknown"] = (
        left["legacy_access_history_unknown"]
        or right["legacy_access_history_unknown"]
    )
    result["blocked"] = left["blocked"] or right["blocked"]
    result["in_flight"] = left["in_flight"] or right["in_flight"]
    wbi_merge_scope(result, left, right)
    return validate_budget(result)



def remaining():
    check_deadline()
    return min(_RUN_END - EXPORT_TIMEOUT, _PHASE_END) - time.monotonic()



def pace(interval=12.2, *, target_url=None, method="GET"):
    """Hold the physical journal lock through the response completion."""
    lock = locked_budget()
    try:
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError("Unsettled publisher request retained", "access")
        if state["blocked"]:
            if target_url is None:
                raise AdapterError("Retained actual publisher refusal", "access")
            wbi_scope_admission(state, target_url, method)
        if state["observed_429_since_transition"] >= 100:
            raise AdapterError(
                "Unsupported retained429 history; no traffic", "access"
            )
        state["interval"] = max(state["interval"], interval, 12.2)
        if state["rate_limit_until"] > time.time():
            raise AdapterError(
                "Retained publisher rate-limit embargo", "access", 429
            )
        while True:
            now = time.time()
            recent = [t for t in state["starts"] if now - t < 60]
            ready = max(
                state["until"],
                state["completed"] + state["interval"],
                recent[-1] + state["interval"] if recent else now,
                recent[-5] + 60 if len(recent) >= 5 else now,
            )
            if ready - now + 120 >= remaining():
                raise AdapterError(
                    "Retained pacing exceeds remaining collection budget",
                    "access",
                )
            if ready <= now:
                state["starts"] = recent + [now]
                state["observed_at"] = now
                state["in_flight"] = True
                write_budget(state)
                return lock, state
            time.sleep(min(ready - now, 30))
    except BaseException:
        lock.close()
        raise



def retry_embargo(value):
    """Return finite future timing without mutating malformed input."""
    if value is None:
        return 0
    try:
        value = value.strip()
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            until = time.time() + float(value)
        else:
            date = email.utils.parsedate_to_datetime(value)
            if date.utcoffset() is None:
                return 0
            until = date.timestamp()
        if not numeric(until) or until <= time.time():
            return 0
        datetime.fromtimestamp(until, timezone.utc)
        return until
    except (ValueError, TypeError, OverflowError, OSError, AttributeError):
        return 0



def infrastructure_pace(url):
    """Honor new metadata embargoes without adding a publication floor."""
    host = urllib.parse.urlsplit(url).hostname
    while True:
        wait = _INFRASTRUCTURE_UNTIL.get(host, 0) - time.time()
        if wait <= 0:
            return
        if wait + 1 >= remaining():
            raise AdapterError("GitHub embargo exceeds phase budget", "access")
        time.sleep(min(wait, 30))



def retain_infrastructure_retry(url, value):
    host = urllib.parse.urlsplit(url).hostname
    _INFRASTRUCTURE_UNTIL[host] = max(
        _INFRASTRUCTURE_UNTIL.get(host, 0), retry_embargo(value)
    )



def inventory_guard():
    """Each physical inventory retry/redirect retains the completion gap."""
    with locked_budget():
        state = load_budget()
    while True:
        wait = state["inventory_completed"] + state["page_gap"] - time.time()
        if wait <= 0:
            return
        if wait + 30 >= remaining():
            raise AdapterError(
                "Page guard exceeds remaining collection budget", "access"
            )
        time.sleep(min(wait, 30))



def fetch_inventory_page(fetch, *args, **kwargs):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = True
    try:
        return fetch(*args, **kwargs)
    finally:
        _INVENTORY_ACTIVE = previous



def check_deadline():
    if _PHASE_END is None or _RUN_END is None:
        raise AdapterError("Bounded execution context required", "access")
    if time.monotonic() >= min(_PHASE_END, _RUN_END):
        raise PhaseExpired()



def request_timeout(publisher=False):
    check_deadline()
    remaining = min(_PHASE_END, _RUN_END) - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    return min(
        _PUBLISHER_TIMEOUT if publisher else _DEFAULT_HTTP_TIMEOUT, remaining
    )



def verify_local_publisher_start():
    """Recheck remote continuity after the local startup quiet guard."""
    global _LOCAL_FIRST_ACCESS_CHECKED
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or _LOCAL_FIRST_ACCESS_CHECKED
    ):
        return
    latest = latest_family_run()
    current = (_SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS)
    if latest != _RESTORED_LATEST or current != _RESTORED_HISTORY:
        raise AdapterError(
            "Family history changed before local publisher access", "access"
        )
    local_workflow_definition()
    _LOCAL_FIRST_ACCESS_CHECKED = True



def workflow_api(path):
    """Read verified workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError("Workflow state credentials missing", "access")
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    nonce = urllib.parse.urlencode({"state_read": time.time_ns()})
    path += ("&" if "?" in path else "?") + nonce
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers=headers,
            infrastructure=True,
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            ("Workflow pacing state unavailable"), ("access")
        ) from None



def current_run_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "")
        for key in (("GITHUB_RUN_ID"), ("GITHUB_RUN_ATTEMPT"))
    ]
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))



def family_source(run):
    path = run.get("path", "")
    for source in FAMILY_SOURCES:
        if path == ".github/workflows/fetch-" + source + ".yml":
            return source
    return None



def attempt_completion(identity, attempt, head):
    """Use exact-attempt job completion rather than mutable run updates."""
    total, jobs_seen = None, set()
    finished = []
    retained_jobs = []
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}"
        )
        _SCANNED_JOB_DIGESTS[(identity, attempt, page)] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        count, jobs = result.get("total_count"), result.get("jobs")
        if count == 0 and jobs == []:
            marker = _LEGACY_NO_JOB.get(identity)
            if (
                page != 1
                or attempt != 1
                or not marker
                or marker["head_sha"] != head
                or _SCANNED_JOB_DIGESTS[(identity, attempt, page)]
                != marker["jobs_sha256"]
            ):
                raise ValueError("Unreviewed empty exact-attempt jobs")
            return None
        if (
            type(count) is not int
            or count <= 0
            or not isinstance(jobs, list)
            or len(jobs) > 100
            or total is not None
            and total != count
        ):
            raise ValueError("Incomplete exact-attempt job inventory")
        total = count
        for job in jobs:
            if (
                not isinstance(job, dict)
                or type(job.get("id")) is not int
                or job["id"] <= 0
                or job["id"] in jobs_seen
                or type(job.get("run_id")) is not int
                or job["run_id"] != identity
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt
                or job.get("head_sha") != head
                or job.get("head_branch") != "main"
                or job.get("status") != "completed"
            ):
                raise ValueError("Untrusted exact-attempt job")
            retained_jobs.append(copy.deepcopy(job))
            jobs_seen.add(job["id"])
            value = datetime.fromisoformat(
                job["completed_at"].replace("Z", "+00:00")
            )
            if value.utcoffset() is None:
                raise ValueError("Missing job completion timezone")
            finished.append(value.timestamp())
        if len(jobs_seen) == total:
            _RECOVERY_JOBS[(identity, attempt)] = (None, retained_jobs)
            return max(finished)
        if len(jobs_seen) > total or len(jobs) != 100:
            raise ValueError("Truncated exact-attempt job inventory")
    raise ValueError("Exact-attempt job inventory exceeds bound")



def completed_identity(run, expected=None, revision=None):
    """Bind a completed attempt and its chronological publisher interval."""
    try:
        source = family_source(run)
        identity, attempt = run["id"], run["run_attempt"]
        if (
            not source
            or type(identity) is not int
            or identity <= 0
            or type(attempt) is not int
            or attempt <= 0
            or run["status"] != "completed"
            or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run[("head_repository")][("full_name")]
            != ("YouthOpps/data-pipeline")
            or run["head_branch"] != "main"
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
            or type(run.get("workflow_id")) is not int
            or run["workflow_id"] != _WORKFLOW_IDS[source]
            or revision is not None
            and (run["head_sha"], run["workflow_id"]) != revision
            or expected
            and (identity, attempt, source) != expected
        ):
            raise ValueError("Untrusted completed attempt")
        if identity in _LEGACY_NO_JOB and attempt == 1:
            marker = _LEGACY_NO_JOB[identity]
            if (
                run["head_sha"] != marker["head_sha"]
                or run.get("conclusion") != marker["conclusion"]
                or run.get("created_at") != marker["created_at"]
            ):
                raise ValueError("Reviewed cancelled attempt changed")
            finished = attempt_completion(identity, attempt, run["head_sha"])
            if finished is not None:
                raise ValueError("Reviewed no-job attempt now contains jobs")
            _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
            return None
        value = datetime.fromisoformat(
            run["run_started_at"].replace("Z", "+00:00")
        )
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        finished = attempt_completion(identity, attempt, run["head_sha"])
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
        _RECOVERY_JOBS[(identity, attempt)] = (
            copy.deepcopy(run), _RECOVERY_JOBS[(identity, attempt)][1]
        )
        return finished, began, identity, attempt, source
    except Exception:
        raise AdapterError(
            ("Untrusted completed family attempt"), ("access")
        ) from None



def latest_family_run():
    """Restart only count-drift races, without reusing partial candidates."""
    for attempt in range(3):
        try:
            return scan_family_history()
        except InventoryRace:
            if attempt == 2:
                raise AdapterError(
                    "Workflow inventory remained unstable", "access"
                ) from None



class InventoryRace(AdapterError):
    """The advertised complete workflow inventory changed during reading."""



def source_revision_digest(head, source):
    """Bind an artifact code digest to its authenticated source revision."""
    if not re.fullmatch(r"[0-9a-f]{40}", head or ""):
        raise ValueError("Invalid source revision")
    if source not in FAMILY_SOURCES:
        raise ValueError("Unexpected family source")
    path = "adapters/" + source + "/adapter.py"
    result = workflow_api("/contents/" + path + "?ref=" + head)
    if result.get("path") != path or result.get("encoding") != "base64":
        raise ValueError("Unexpected source revision response")
    content = base64.b64decode(result["content"], validate=False)
    if len(content) != result.get("size") or len(content) > 5_000_000:
        raise ValueError("Invalid source revision size")
    blob = b"blob " + str(len(content)).encode() + b"\0" + content
    if hashlib.sha1(blob).hexdigest() != result.get("sha"):
        raise ValueError("Source revision blob mismatch")
    return hashlib.sha256(content).hexdigest()



def download_inert_artifact(identity, artifact_id, digest=None):
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError("Workflow state credentials missing", "access")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    infrastructure_pace(url)
    try:
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retain_infrastructure_retry(
                url, response.headers.get("Retry-After")
            )
            if response.status != 302:
                raise ValueError("Expected artifact redirect")
            destination = response.headers.get("Location", "")
            response.read(1001)
        parsed = urllib.parse.urlsplit(destination)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            raise ValueError("Unsafe artifact URL")
        if "." not in parsed.hostname or parsed.hostname.endswith(
            ((".local"), (".internal"), (".localhost"))
        ):
            raise ValueError("Private artifact hostname")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Literal artifact address")
        # Signed URL is never stored or included in exceptions; no auth header.
        status, _, body = request_bytes(
            destination, byte_limit=32768, infrastructure=True
        )
        if status != 200 or len(body) > 32768:
            raise ValueError("Invalid artifact response")
        if digest is not None:
            if (
                not isinstance(digest, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                or digest[7:] != hashlib.sha256(body).hexdigest()
            ):
                raise ValueError("Artifact digest mismatch")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "pacing-state.json":
                raise ValueError("Unexpected artifact members")
            entry = entries[0]
            if entry.file_size > 16384 or entry.flag_bits & 1:
                raise ValueError("Oversized or encrypted state")
            if entry.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("State symlink")
            raw = archive.read(entry)
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "head_sha",
            "adapter_sha256",
            "state_sha256",
            "state",
        }:
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source = identity
        if (
            type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or envelope["repository"] != "YouthOpps/data-pipeline"
            or type(envelope["run_id"]) is not int
            or type(envelope["run_attempt"]) is not int
            or envelope["run_id"] != run_id
            or envelope["run_attempt"] != attempt
            or envelope["source"] != source
            or envelope["head_sha"] != _ATTEMPT_HEADS.get((run_id, attempt))
            or envelope["adapter_sha256"]
            != source_revision_digest(envelope["head_sha"], source)
            or envelope["state_sha256"]
            != hashlib.sha256(
                json.dumps(
                    envelope["state"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        ):
            raise ValueError("Artifact ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None



def source_file_digest():
    """Hash this standalone implementation without retaining its contents."""
    descriptor = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        current = os.stat(__file__, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe implementation file", "access")
        content = stream.read(5_000_001)
        current = os.stat(__file__, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Implementation file changed", "access")
    if len(content) > 5_000_000:
        raise AdapterError(
            "Source implementation exceeds reviewed bound", "access"
        )
    return hashlib.sha256(content).hexdigest()



@contextlib.contextmanager
def owned_artifact_directory(path):
    """Pin the original parent and make otherwise healthy close errors fatal."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    root, before = checked_artifact_path(path)
    descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    held = None
    try:
        held = os.fstat(descriptor)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Artifact directory changed", "access")
        verify_artifact_directory(root, descriptor)
        yield root, descriptor
    finally:
        primary = sys.exc_info()[1]
        try:
            if held is None:
                raise AdapterError("Unverified output parent", "access")
            owned_export_parent(descriptor, (held.st_dev, held.st_ino))
            os.close(descriptor)
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            with contextlib.suppress(BaseException):
                invalidate_retained_export()
            if primary is None:
                raise



def verify_artifact_directory(root, descriptor):
    """Reject replacement parents without following their contents."""
    held = os.fstat(descriptor)
    current = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o022
        or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise AdapterError("Artifact directory changed", "access")



def owned_artifact_info(root, directory):
    """Inspect only the current safely owned artifact in the pinned parent."""
    verify_artifact_directory(root, directory)
    try:
        info = os.stat(
            "pacing-state.json", dir_fd=directory, follow_symlinks=False
        )
    except FileNotFoundError:
        return None
    validate_owned_output(info)
    return info



def same_owned_artifact(root, directory, expected):
    """Prevent replacing or unlinking an independently replaced file."""
    current = owned_artifact_info(root, directory)
    if current is None or expected is None:
        same = current is None and expected is None
    else:
        same = (current.st_dev, current.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        )
    if not same:
        raise AdapterError("Owned artifact identity changed", "access")



def workflow_output_signal(value):
    """Append readiness only to a nonblocking, verified regular signal."""
    descriptor = os.open(
        os.environ["GITHUB_OUTPUT"],
        os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    with os.fdopen(descriptor, "a") as output:
        info = os.fstat(output.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise AdapterError("Unsafe workflow output signal", "access")
        output.write(f"pacing_ready={value}\n")
        output.flush()
        os.fsync(output.fileno())



def invalidate_owned_artifact(root, directory):
    """Invalidate safe JSON before fallible signal and unlink operations."""
    stale = owned_artifact_info(root, directory)
    descriptor = None
    if stale is not None:
        descriptor = os.open(
            "pacing-state.json",
            os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            held = os.fstat(descriptor)
            validate_owned_output(held)
            if (held.st_dev, held.st_ino) != (stale.st_dev, stale.st_ino):
                raise AdapterError("Owned artifact identity changed", "access")
            same_owned_artifact(root, directory, stale)
        except BaseException:
            os.close(descriptor)
            raise
    errors = []
    try:
        if descriptor is not None:
            try:
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
            except BaseException as error:
                errors.append(error)
        try:
            workflow_output_signal("false")
        except BaseException as error:
            errors.append(error)
    finally:
        if descriptor is not None:
            os.close(descriptor)
            try:
                same_owned_artifact(root, directory, stale)
                os.unlink("pacing-state.json", dir_fd=directory)
            except BaseException as error:
                errors.append(error)
    if errors:
        raise AdapterError("Owned state invalidation failed closed", "access")



def invalidate_family_artifact():
    """Invalidate safely owned stale state before readiness signal IO."""
    if _OWNED_EXPORT is not None:
        invalidate_retained_export()
        return
    path = os.environ.get("WBI_PACING_ARTIFACT_PATH")
    if not path:
        return
    with owned_artifact_directory(path) as (root, directory):
        invalidate_owned_artifact(root, directory)



def validate_owned_output(info):
    """Refuse unsafe output before reading or modifying it."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe owned artifact output", "access")



def checked_artifact_path(path):
    """Validate this attempt's private output directory and basename."""
    configured = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe artifact output path", "access")
    info = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise AdapterError("Unsafe artifact output directory", "access")
    return root, info



def owned_export_file(descriptor, identity):
    info = os.fstat(descriptor)
    if ((info.st_dev, info.st_ino) != identity
            or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink not in (0, 1)
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_WRONLY):
        raise AdapterError("Retained output descriptor changed", "access")
    return info


def owned_export_parent(descriptor, identity):
    info = os.fstat(descriptor)
    if ((info.st_dev, info.st_ino) != identity
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_RDONLY):
        raise AdapterError("Retained output parent changed", "access")


def invalidate_retained_export():
    """Invalidate original verified output before signal/unlink can fail."""
    if _OWNED_EXPORT is None:
        return
    root, directory, parent_id, descriptor, identity = _OWNED_EXPORT
    owned_export_file(descriptor, identity)
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)
    errors = []
    try:
        workflow_output_signal("false")
    except BaseException as error:
        errors.append(error)
    try:
        owned_export_parent(directory, parent_id)
        current = os.stat(
            "pacing-state.json", dir_fd=directory, follow_symlinks=False
        )
        validate_owned_output(current)
        if (current.st_dev, current.st_ino) == identity:
            os.unlink("pacing-state.json", dir_fd=directory)
    except FileNotFoundError:
        pass
    except BaseException as error:
        errors.append(error)
    if errors:
        raise AdapterError("Retained output invalidation failed", "access")


def close_retained_export():
    """Release independent known descriptors without hiding body errors."""
    global _OWNED_EXPORT, _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _OWNED_EXPORT is None:
        return
    root, directory, parent_id, descriptor, identity = _OWNED_EXPORT
    primary = sys.exc_info()[1]
    errors = []
    try:
        owned_export_parent(directory, parent_id)
        os.close(directory)
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        errors.append(error)
        with contextlib.suppress(BaseException):
            invalidate_retained_export()
    try:
        owned_export_file(descriptor, identity)
        os.close(descriptor)
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        errors.append(error)
        with contextlib.suppress(BaseException):
            invalidate_retained_export()
    _OWNED_EXPORT = None
    if errors and primary is None:
        raise errors[0]


def retain_owned_export(root, directory, descriptor, info):
    """Transfer only a fully verified handoff; keep old authority on error."""
    global _OWNED_EXPORT
    identity = (info.st_dev, info.st_ino)
    owned_export_file(descriptor, identity)
    verify_artifact_directory(root, directory)
    same_owned_artifact(root, directory, info)
    parent = os.dup(directory)
    parent_info = os.fstat(parent)
    parent_id = (parent_info.st_dev, parent_info.st_ino)
    try:
        owned_export_parent(parent, parent_id)
        close_retained_export()
        _OWNED_EXPORT = (root, parent, parent_id, descriptor, identity)
    except BaseException:
        with contextlib.suppress(BaseException):
            owned_export_parent(parent, parent_id)
            os.close(parent)
        raise


def write_owned_artifact(root, directory, name, envelope):
    """Use unbuffered verified output; never flush to a reused numeric FD."""
    raw = json.dumps(envelope).encode()
    descriptor = identity = None
    primary = None
    keep = False
    try:
        verify_artifact_directory(root, directory)
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            | os.O_NONBLOCK, 0o600, dir_fd=directory
        )
        info = os.fstat(descriptor)
        validate_owned_output(info)
        identity = (info.st_dev, info.st_ino)
        offset = 0
        while offset < len(raw):
            verify_artifact_directory(root, directory)
            held = os.fstat(descriptor)
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            validate_owned_output(held)
            if ((held.st_dev, held.st_ino) != identity
                    or (named.st_dev, named.st_ino) != identity
                    or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
                    != os.O_WRONLY):
                raise AdapterError("Owned output descriptor changed", "access")
            count = os.write(descriptor, raw[offset:])
            if type(count) is not int or not 0 < count <= len(raw) - offset:
                raise AdapterError("Incomplete owned output write", "access")
            offset += count
        os.fsync(descriptor)
        verify_artifact_directory(root, directory)
        held = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        validate_owned_output(held)
        if ((held.st_dev, held.st_ino) != identity
                or (named.st_dev, named.st_ino) != identity):
            raise AdapterError("Owned output inode changed", "access")
        keep = True
        return held, descriptor
    except BaseException as error:
        primary = error
        raise
    finally:
        if descriptor is not None and not keep:
            try:
                held = os.fstat(descriptor)
                validate_owned_output(held)
                if (identity is None
                        or (held.st_dev, held.st_ino) != identity
                        or fcntl.fcntl(descriptor, fcntl.F_GETFL)
                        & os.O_ACCMODE != os.O_WRONLY):
                    raise AdapterError("Owned output close changed", "access")
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise


def export_family_artifact():
    """Atomically export trusted state; invalidate owned output on failure."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    path = os.environ.get("WBI_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    with owned_artifact_directory(path) as (root, directory):
        prior = owned_artifact_info(root, directory)
        temporary = None
        temporary_info = None
        output_fd = None
        try:
            run_id, attempt = current_run_identity()
            with locked_budget():
                state = load_budget()
            if state["in_flight"]:
                raise AdapterError(
                    "Unsettled state cannot be exported", "access"
                )
            envelope = {
                "schema": 1,
                "repository": "YouthOpps/data-pipeline",
                "run_id": run_id,
                "run_attempt": attempt,
                "source": SOURCE_ID,
                "head_sha": os.environ.get("GITHUB_SHA"),
                "adapter_sha256": source_file_digest(),
                "state_sha256": hashlib.sha256(
                    json.dumps(
                        state, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "state": state,
            }
            if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
                raise AdapterError("Invalid artifact source revision", "access")
            temporary = ".owned-pacing-" + os.urandom(16).hex()
            verify_artifact_directory(root, directory)
            temporary_info, output_fd = write_owned_artifact(
                root, directory, temporary, envelope
            )
            verify_current_journal()
            same_owned_artifact(root, directory, prior)
            os.replace(
                temporary,
                "pacing-state.json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary = None
            same_owned_artifact(root, directory, temporary_info)
            retain_owned_export(
                root, directory, output_fd, temporary_info
            )
            output_fd = None
            verify_current_journal()
            workflow_output_signal("true")
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            with contextlib.suppress(BaseException):
                invalidate_family_artifact()
            raise
        finally:
            primary = sys.exc_info()[1]
            if output_fd is not None:
                try:
                    owned_export_file(
                        output_fd, (temporary_info.st_dev,
                                    temporary_info.st_ino)
                    )
                    os.close(output_fd)
                except BaseException:
                    fail_publisher_cleanup()
                    if primary is None:
                        raise
            try:
                if temporary is not None and temporary_info is not None:
                    verify_artifact_directory(root, directory)
                    current = os.stat(
                        temporary, dir_fd=directory, follow_symlinks=False
                    )
                    validate_owned_output(current)
                    if (current.st_dev, current.st_ino) != (
                        temporary_info.st_dev,
                        temporary_info.st_ino,
                    ):
                        raise AdapterError(
                            "Temporary artifact changed", "access"
                        )
                    os.unlink(temporary, dir_fd=directory)
            except BaseException:
                _STATE_TRUSTED = False
                _PUBLISHER_GATE_FAILED = True
                with contextlib.suppress(BaseException):
                    invalidate_family_artifact()
                if primary is None:
                    raise



def apply_robots_policy(text, url, state=None):
    """Keep policy application pending until restrictive state is durable."""
    if state is None:
        with locked_budget() as lock:
            current = load_budget()
            if current["in_flight"]:
                raise AdapterError("Unsettled publisher transaction", "access")
            current["in_flight"] = True
            write_budget(current)
            try:
                delay = apply_robots_policy(text, url, current)
            except RobotsDisallow:
                current["in_flight"] = False
                write_budget(current)
                raise
            current["in_flight"] = False
            write_budget(current)
            return delay
    try:
        delay = robots_policy(text, url)
    except RobotsDisallow:
        wbi_retain_new_hold(state, url, 0, "robots_disallow")
        write_budget(state)
        raise
    state["interval"] = max(state["interval"], delay)
    state["observed_at"] = time.time()
    write_budget(state)
    return delay



class HeaderRateLimitStop(AdapterError):
    """A durable header-only429 whose response context closed successfully."""


def physical_request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=5_000_000,
    infrastructure=False,
):
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if type(byte_limit) is not int or not 0 < byte_limit <= 30_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    purpose_target = urllib.parse.urlsplit(url)
    if robots and (
        purpose_target.path != "/robots.txt"
        or purpose_target.query
        or purpose_target.fragment
    ):
        raise AdapterError(
            "Robots purpose requires literal robots.txt", "access"
        )
    if wbi_retired_resource(url):
        raise AdapterError("Retained WBI resource refusal", "access")
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    publisher = host in _PUBLISHER_HOSTS
    if not publisher and not infrastructure:
        raise AdapterError(
            "Explicit infrastructure transport required", "access"
        )
    if infrastructure:
        if publisher:
            raise AdapterError(
                "Publisher cannot use metadata purpose", "access"
            )
        infrastructure_pace(url)
    if publisher and (not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        raise AdapterError(
            "Trusted publisher state required before access", "access"
        )
    if publisher:
        collection_lock_authority()
        wait_startup_quiet()
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = (
        pace(interval, target_url=url, method=method)
        if publisher else (None, None)
    )
    attempted = response_received = response_constraints_saved = False
    retryable_connection_failure = False
    try:
        if publisher:
            collection_lock_authority()
            verify_local_publisher_start()
            locked_journal_authority()
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={**(headers or {}), "User-Agent": _USER_AGENT},
        )
        if publisher:
            print(
                json.dumps(
                    {
                        "event": "request_start",
                        "url": url,
                        "time": time.time(),
                        "user_agent": request.get_header("User-agent"),
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
        timeout = request_timeout(publisher)
        if publisher:
            collection_lock_authority()
            locked_journal_authority()
            if (_STARTUP_QUIET_UNTIL is None
                    or time.monotonic() < _STARTUP_QUIET_UNTIL):
                raise AdapterError(
                    "Publisher startup quiet shortened", "access"
                )
        try:
            attempted = True
            response = _OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            if publisher and isinstance(reason, ssl.SSLError):
                raise AdapterError(
                    "Publisher TLS verification failed; pending retained",
                    "access",
                ) from error
            if publisher and not (
                isinstance(reason, (ConnectionError, TimeoutError))
                or isinstance(reason, socket.gaierror)
                and reason.errno == socket.EAI_AGAIN
                or isinstance(reason, OSError)
                and reason.errno
                in (
                    errno.ECONNABORTED,
                    errno.ECONNREFUSED,
                    errno.ECONNRESET,
                    errno.EHOSTDOWN,
                    errno.EHOSTUNREACH,
                    errno.ENETDOWN,
                    errno.ENETUNREACH,
                    errno.EPIPE,
                    errno.ETIMEDOUT,
                )
            ):
                raise AdapterError(
                    "Unclassified connection failure; pending retained",
                    "access",
                ) from error
            retryable_connection_failure = True
            raise
        response_received = True
        with response:
            status = response.status
            if infrastructure:
                retain_infrastructure_retry(
                    url, response.headers.get("Retry-After")
                )
            if publisher:
                state["until"] = max(
                    state["until"],
                    retry_embargo(response.headers.get("Retry-After")),
                )
                if status == 429:
                    strikes = state["observed_429_since_transition"] + 1
                    state["observed_429_since_transition"] = strikes
                    state["interval"] = max(
                        state["interval"] * 2, 12.2 * 2**strikes
                    )
                    state["page_gap"] = max(
                        state["page_gap"] * 2, 30 * 2**strikes
                    )
                    state["until"] = max(
                        state["until"], time.time() + 1800 * 2 ** (strikes - 1)
                    )
                    state["rate_limit_until"] = max(
                        state["rate_limit_until"], state["until"]
                    )
                if status in (401, 403, 451) and not robots:
                    wbi_retain_new_hold(
                        state, url, status, "content_status"
                    )
                state["observed_at"] = time.time()
                write_budget(state)
                if status == 429:
                    raise HeaderRateLimitStop(
                        "Publisher429; durable adaptive cooldown retained",
                        "access",
                        429,
                    )
            declared = response.headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) > byte_limit
            ):
                raise AdapterError(
                    "Oversized or invalid response length", "fetch"
                )
            if (
                response.headers.get("Content-Encoding", "identity")
                != "identity"
            ):
                raise AdapterError(
                    "Unsupported response content encoding", "fetch"
                )
            try:
                body = response.read(byte_limit + 1)
            except Exception as error:
                if publisher:
                    raise AdapterError(
                        "Publisher body incomplete; pending state retained",
                        "access",
                        status,
                    ) from error
                raise
            if (
                len(body) > byte_limit
                or declared
                and len(body) != int(declared)
            ):
                raise AdapterError(
                    "Response exceeds native byte bound", "fetch"
                )
            challenge = (
                publisher
                and not (robots and 400 <= status < 500 and status != 429)
                and visible_access_challenge(body, status == 429)
            )
            if challenge:
                response_constraints_saved = True
                if not robots or status == 429:
                    wbi_retain_new_hold(
                        state, url, status, "visible_refusal"
                    )
                raise AdapterError(
                    "Visible publisher verification or refusal", "access"
                )
            if publisher and not (robots and status == 200):
                response_constraints_saved = True
            if publisher and robots and status == 200:
                response_constraints_saved = False
                target = (
                    _ROBOTS_CONTEXT["target"]
                    if _ROBOTS_CONTEXT is not None
                    else SOURCE_URL
                )
                text = body.decode(
                    response.headers.get_content_charset() or "utf-8"
                )
                try:
                    delay = apply_robots_policy(text, target, state)
                except RobotsDisallow:
                    response_constraints_saved = True
                    raise
                if _ROBOTS_CONTEXT is not None:
                    _ROBOTS_CONTEXT["delay"] = delay
                response_constraints_saved = True
            if publisher and status == 429:
                raise AdapterError(
                    "Publisher429; durable adaptive cooldown retained",
                    "access",
                    429,
                )
            return status, response.headers, body
    except HeaderRateLimitStop:
        # Only a successfully closed response is fully assessed.
        response_constraints_saved = True
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if publisher and response_received:
            response_constraints_saved = False
            raise AdapterError(
                "Received publisher response failed; no connection retry",
                "access",
            ) from error
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if (
                    attempted
                    and not _PUBLISHER_GATE_FAILED
                    and (
                        response_constraints_saved
                        or not response_received
                        and retryable_connection_failure
                    )
                ):
                    state["in_flight"] = False
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = state["completed"]
                try:
                    write_budget(state)
                    if state["in_flight"]:
                        _STATE_TRUSTED = False
                        _PUBLISHER_GATE_FAILED = True
                except BaseException:
                    _STATE_TRUSTED = False
                    _PUBLISHER_GATE_FAILED = True
                    if not primary:
                        raise
                print(
                    json.dumps(
                        {
                            "event": "request_complete",
                            "url": url,
                            "time": time.time(),
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                lock.close()



def local_workflow_definition():
    """Both shared-state producers must be paused before local access."""
    for source, identity in _WORKFLOW_IDS.items():
        value = workflow_api(f"/actions/workflows/{identity}")
        if (
            value.get("id") != identity
            or value.get("path") != f".github/workflows/fetch-{source}.yml"
            or value.get("state") != "disabled_manually"
        ):
            raise AdapterError(
                "Local collection requires its exact workflow paused",
                "access",
            )



def scan_family_history():
    """Read complete producer history and rank exact job completion."""
    global _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS
    global _HISTORY_COMPLETED, _RECOVERY_JOBS
    _HISTORY_COMPLETED, _RECOVERY_JOBS = [], {}
    _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS = {}, {}, {}
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else (None, None)
    if not hosted:
        local_workflow_definition()
    seen, candidates, current_seen = set(), [], False
    for expected_source, workflow in _WORKFLOW_IDS.items():
        total, count = None, 0
        for page in range(1, 101):
            value = workflow_api(
                f"/actions/workflows/{workflow}/runs"
                f"?branch=main&per_page=100&page={page}"
            )
            size, runs = value.get("total_count"), value.get("workflow_runs")
            if (
                type(size) is not int
                or size < 0
                or not isinstance(runs, list)
                or len(runs) > 100
            ):
                raise AdapterError("Invalid family run inventory", "access")
            if total is not None and size != total:
                raise InventoryRace("Family inventory changed", "access")
            total = size
            count += len(runs)
            for run in runs:
                identity, attempt = run.get("id"), run.get("run_attempt")
                if (
                    type(identity) is not int
                    or identity <= 0
                    or identity in seen
                    or type(attempt) is not int
                    or attempt <= 0
                    or family_source(run) != expected_source
                    or run.get("workflow_id") != workflow
                    or run.get("repository", {}).get("full_name")
                    != "YouthOpps/data-pipeline"
                    or run.get("head_repository", {}).get("full_name")
                    != "YouthOpps/data-pipeline"
                    or run.get("head_branch") != "main"
                ):
                    raise AdapterError(
                        "Untrusted family run identity", "access"
                    )
                seen.add(identity)
                if identity == current[0]:
                    if (
                        attempt != current[1]
                        or expected_source != SOURCE_ID
                        or run.get("head_sha") != os.environ.get("GITHUB_SHA")
                        or run.get("status")
                        not in (
                            "in_progress",
                            "queued",
                            "pending",
                            "waiting",
                            "requested",
                        )
                    ):
                        raise AdapterError(
                            "Current family attempt changed", "access"
                        )
                    current_seen = True
                    if attempt > 1:
                        prior = workflow_api(
                            f"/actions/runs/{identity}/attempts/{attempt - 1}"
                        )
                        candidates.append(
                            completed_identity(
                                prior,
                                (identity, attempt - 1, expected_source),
                                (run["head_sha"], workflow),
                            )
                        )
                    continue
                _SCANNED_HISTORY[(identity, attempt)] = (
                    run.get("head_sha"),
                    workflow,
                )
                if not hosted and run.get("status") != "completed":
                    raise AdapterError(
                        "Local family history is not terminal", "access"
                    )
                if run.get("status") in (
                    "queued",
                    "waiting",
                    "requested",
                    "pending",
                ):
                    if attempt > 1:
                        prior = workflow_api(
                            f"/actions/runs/{identity}/attempts/{attempt - 1}"
                        )
                        candidates.append(
                            completed_identity(
                                prior,
                                (identity, attempt - 1, expected_source),
                                (run["head_sha"], workflow),
                            )
                        )
                    continue
                if run.get("status") != "completed":
                    raise AdapterError(
                        "Another family publisher is active", "access"
                    )
                candidates.append(completed_identity(run))
            if count >= total:
                if count != total:
                    raise AdapterError(
                        "Family inventory count mismatch", "access"
                    )
                break
            if len(runs) != 100:
                raise AdapterError("Incomplete family inventory", "access")
        else:
            raise AdapterError(
                "Family inventory exceeds bounded metadata scan", "access"
            )
    if hosted and not current_seen:
        raise AdapterError(
            "Current attempt missing from complete family history", "access"
        )
    ordered = sorted(value for value in candidates if value is not None)
    if not ordered:
        raise AdapterError("Existing publisher history missing", "access")
    if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
        raise AdapterError("Ambiguous actual family completion", "access")
    _HISTORY_COMPLETED = ordered
    return ordered[-1][2:]



def prepare_collection():
    """Verify the source producer before any family state initialization."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT
    global _LOCAL_FIRST_ACCESS_CHECKED, _LEGACY_COLLECTION_OBSERVATION
    global _COLLECTION_STARTED, _COLLECTION_PREPARED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        authenticate_current_job()
    else:
        local_workflow_definition()
    if _COLLECTION_LOCK_FD is None:
        _LEGACY_COLLECTION_OBSERVATION = legacy_collection_observation()
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
        descriptor = info = None
        try:
            descriptor = os.open(
                path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            info = os.fstat(descriptor)
            validate_owned_output(info)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            retained_descriptor_info(descriptor, (info.st_dev, info.st_ino))
            current = os.stat(path, follow_symlinks=False)
            validate_owned_output(current)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Lifecycle acquisition changed")
        except BaseException:
            fail_publisher_cleanup()
            if descriptor is not None and info is not None:
                try:
                    close_retained_descriptor(
                        descriptor, (info.st_dev, info.st_ino)
                    )
                except BaseException:
                    pass
            raise AdapterError(
                "Unsafe or active publisher lifecycle lifecycle lock", "access"
            ) from None
        try:
            guard = os.dup(descriptor)
        except BaseException:
            fail_publisher_cleanup()
            try:
                close_retained_descriptor(
                    descriptor, (info.st_dev, info.st_ino)
                )
            except BaseException:
                pass
            raise
        _COLLECTION_LOCK_FD = descriptor
        _COLLECTION_LOCK_CONTEXT = (
            descriptor,
            guard,
            (info.st_dev, info.st_ino),
            path,
        )
    collection_lock_authority()
    _LOCAL_FIRST_ACCESS_CHECKED = False
    _COLLECTION_STARTED = time.time()
    restore_family_artifact()
    _COLLECTION_PREPARED = True



def save_budget(state):
    """Every legacy caller uses the same failure-sticky modern write."""
    return write_budget(state)



class RobotsDisallow(AdapterError):
    """A genuine applicable REP restriction, retained independently."""



def public_url(url):
    """Keep source traffic on the original reviewed HTTPS publisher alias."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or host not in _PUBLISHER_HOSTS
        or parsed.port not in (None, 443)
    ):
        raise AdapterError("Source leaves reviewed public HTTPS host", "access")
    return url



def selected_robots_policies(groups):
    """A legacy grant cannot displace current identity's wildcard policy."""
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [g for g in groups if agent in g["agents"]]
    wildcard = [g for g in groups if "*" in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    return [current or wildcard] + ([legacy] if legacy else [])



def robots_groups(text):
    """Retain native allow/disallow rules with raw selected timing fields."""
    groups = []
    group = {"agents": [], "rules": [], "timing": []}
    has_directive = False
    for original in text.splitlines():
        key, separator, value = original.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if has_directive:
                groups.append(group)
                group = {"agents": [], "rules": [], "timing": []}
                has_directive = False
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
            has_directive = True
        elif key in ("crawl-delay", "request-rate"):
            group["timing"].append((key, value))
            has_directive = True
    groups.append(group)
    directives = [
        line.split("#", 1)[0].strip().lower()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (
        directives
        and not any(g["agents"] for g in groups)
        and not all(line.startswith("sitemap:") for line in directives)
    ):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    return groups



def robots_policy(text, url):
    """Use the original native rule matcher and strict selected numeric MAX."""
    parsed = urllib.parse.urlsplit(url)
    policies = selected_robots_policies(robots_groups(text))
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    delay = 12.2
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    for selected in policies:
        matches = []
        for group in selected:
            for directive, pattern in group["rules"]:
                if not pattern:
                    continue
                expression = (
                    "^"
                    + ".*".join(
                        re.escape(part)
                        for part in pattern.removesuffix("$").split("*")
                    )
                    + ("$" if pattern.endswith("$") else "")
                )
                if re.search(expression, path):
                    matches.append(
                        (
                            len(pattern.replace("*", "").removesuffix("$")),
                            directive == "allow",
                        )
                    )
            for key, value in group["timing"]:
                try:
                    if key == "crawl-delay":
                        if not re.fullmatch(decimal, value):
                            raise ValueError()
                        seconds = float(value)
                    else:
                        match = re.fullmatch(
                            r"([0-9]+)\s*/\s*(" + decimal + r")", value
                        )
                        if not match:
                            raise ValueError()
                        count, period = int(match[1]), float(match[2])
                        if count <= 0 or not numeric(period) or period <= 0:
                            raise ValueError()
                        seconds = period / count + 0.01
                    if not numeric(seconds):
                        raise ValueError()
                    datetime.fromtimestamp(time.time() + seconds, timezone.utc)
                    delay = max(delay, seconds)
                except (ValueError, OverflowError, OSError):
                    raise AdapterError(
                        "Unsupported applicable robots timing; "
                        "pending retained",
                        "access",
                    ) from None
        if matches and not max(matches)[1]:
            raise RobotsDisallow(
                "Robots policy disallows this source endpoint", "access"
            )
    return delay



def check_robots(url):
    """Apply every cached target policy within its durable pending scope."""
    global _ROBOTS_CONTEXT
    parsed = urllib.parse.urlsplit(public_url(url))
    if (
        parsed.path == "/robots.txt"
        and not parsed.query
        and not parsed.fragment
    ):
        return 12.2
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin in _ROBOTS:
        text = _ROBOTS[origin]
        return 12.2 if text is None else apply_robots_policy(text, url)
    previous = _ROBOTS_CONTEXT
    context = {"target": url, "delay": None}
    _ROBOTS_CONTEXT = context
    try:
        status, headers, body = fetch_source(
            origin + "/robots.txt", policy_response=True
        )
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
            return 12.2
        if status != 200:
            raise AdapterError(
                "Required robots policy unavailable", "access", status
            )
        text = body.decode(headers.get_content_charset() or "utf-8", "strict")
        _ROBOTS[origin] = text
        return (
            context["delay"]
            if context["delay"] is not None
            else apply_robots_policy(text, url)
        )
    finally:
        _ROBOTS_CONTEXT = previous



def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    deadline=None,
    retry_jitter=0,
    infrastructure=False,
    byte_limit=5_000_000,
    publisher=False,
):
    """Preserve default GitHub transport; guard each physical source hop."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if infrastructure:
        return physical_request_bytes(
            url,
            method=method,
            headers=headers,
            payload=payload,
            byte_limit=byte_limit,
            infrastructure=True,
        )
    if not publisher and host not in _PUBLISHER_HOSTS:
        clamp_publication_window()
        if _RUN_END is not None:
            check_deadline()
            deadline = min(
                deadline if deadline is not None else _PHASE_END,
                _PHASE_END,
                _RUN_END - EXPORT_TIMEOUT,
            )
        return legacy_request_bytes(
            url,
            interval=interval,
            method=method,
            headers=headers,
            payload=payload,
            deadline=deadline,
            retry_jitter=retry_jitter,
        )
    parsed = urllib.parse.urlsplit(public_url(url))
    literal = (
        parsed.path == "/robots.txt"
        and not parsed.query
        and not parsed.fragment
    )
    result = physical_request_bytes(
        url,
        interval=max(12.2, interval),
        method=method,
        headers=headers,
        payload=payload,
        robots=literal,
        byte_limit=byte_limit,
    )
    if not literal and result[0] in (401, 403, 451):
        raise AdapterError(
            f"Publisher refused access: HTTP {result[0]}", "access", result[0]
        )
    return result



def clamp_publication_window():
    """Only tighten the entry-frozen lifecycle against its actual job proof."""
    global _RUN_END, _SOURCE_END, _PHASE_END
    if _RUN_END is not None and _PUBLICATION_WINDOW is not None:
        _RUN_END = min(_RUN_END, _PUBLICATION_WINDOW["job"] - 60)
        _SOURCE_END = min(_SOURCE_END, _RUN_END - 390)
        _PHASE_END = min(
            _PHASE_END,
            (
                _SOURCE_END
                if _PHASE_NAME == "source"
                else _RUN_END - EXPORT_TIMEOUT
            ),
        )



def enter_phase(name, seconds):
    """Assign one bounded owner without renewing its existing deadline."""
    global _PHASE_END, _PHASE_NAME
    if _RUN_END is None:
        raise AdapterError("Entry-frozen lifecycle required", "access")
    if _PHASE_NAME == name:
        check_deadline()
        return
    limit = (
        _RUN_END
        if name == "export"
        else (
            _RUN_END - _FAILURE_SECONDS - EXPORT_TIMEOUT
            if name == "publication"
            else _RUN_END - EXPORT_TIMEOUT
        )
    )
    _PHASE_END = min(time.monotonic() + seconds, limit)
    _PHASE_NAME = name
    check_deadline()



@contextlib.contextmanager
def execution():
    """Freeze admission once; publication owns its original HTTP alarms."""
    global _RUN_END, _SOURCE_END, _PHASE_END, _PHASE_NAME
    global _COLLECTION_PREPARED, _STARTUP_QUIET_UNTIL
    if _RUN_END is not None:
        raise AdapterError("Nested execution refused", "access")
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    _SOURCE_END = _RUN_END - 390
    _PHASE_END, _PHASE_NAME = (
        min(time.monotonic() + 180, _SOURCE_END - 30), "restore"
    )
    _COLLECTION_PREPARED = False
    _STARTUP_QUIET_UNTIL = None
    try:
        yield
    finally:
        try:
            try:
                close_collection_lock()
            finally:
                close_retained_export()
        finally:
            _RUN_END = _SOURCE_END = _PHASE_END = _PHASE_NAME = None



@contextlib.contextmanager
def collection_lock():
    """The acquired modern lifecycle lock encloses the unchanged collector."""
    collection_lock_authority()
    yield



def collect():
    """Run the complete original graph under one fresh continuity entry."""
    global _COLLECTION_PREPARED
    if _RUN_END is not None:
        check_deadline()
        if not _COLLECTION_PREPARED:
            prepare_collection()
        _COLLECTION_PREPARED = False
        start_source_phase()
        return collect_body()
    with execution():
        prepare_collection()
        _COLLECTION_PREPARED = False
        start_source_phase()
        return collect_body()



def legacy_namespace_observation():
    """Read original host constraints without creating or adopting a budget."""
    directory = os.path.join(
        tempfile.gettempdir(), "wbi-publisher-pacing-" + str(os.getuid())
    )
    try:
        parent = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        return {
            "namespace": "wbi-publisher-pacing",
            "status": "ABSENT_NOT_ZERO_HISTORY",
            "collection_lock_before_entry": _LEGACY_COLLECTION_OBSERVATION,
        }
    try:
        initial = os.fstat(parent)
        named = os.stat(directory, follow_symlinks=False)
        identity = (initial.st_dev, initial.st_ino)
        if (
            not stat.S_ISDIR(initial.st_mode)
            or initial.st_uid != os.getuid()
            or initial.st_mode & 0o077
            or identity != (named.st_dev, named.st_ino)
        ):
            raise AdapterError("Unsafe original pacing parent", "access")
        known = {
            hashlib.sha256(b"wbi.be").hexdigest(): "publisher",
            hashlib.sha256(b"api.github.com").hexdigest(): "default_github",
        }
        files = []
        for name in sorted(os.listdir(parent)):
            if name not in known:
                raise AdapterError("Unknown original pacing bucket", "access")
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "rb") as file:
                before = os.fstat(file.fileno())
                validate_owned_output(before)
                fcntl.flock(file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                raw = file.read(16385)
                after = os.fstat(file.fileno())
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                signature = lambda value: (
                    value.st_dev,
                    value.st_ino,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
                if (
                    signature(before) != signature(after)
                    or signature(after) != signature(current)
                    or not raw
                    or len(raw) > 16384
                ):
                    raise AdapterError(
                        "Original pacing bucket changed", "access"
                    )
                state = json.loads(raw)
                if (
                    not isinstance(state, dict)
                    or set(state) != {"starts", "interval", "until"}
                    or not numeric(state["interval"])
                    or state["interval"] < 6
                    or not numeric(state["until"])
                    or not isinstance(state["starts"], list)
                    or len(state["starts"]) > 1000
                    or any(not numeric(value) for value in state["starts"])
                ):
                    raise AdapterError(
                        "Unknown original pacing constraints", "access"
                    )
                files.append(
                    {
                        "bucket": known[name],
                        "name": name,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "inode": before.st_ino,
                        "state": state,
                    }
                )
        current = os.stat(directory, follow_symlinks=False)
        if identity != (current.st_dev, current.st_ino):
            raise AdapterError("Original pacing parent replaced", "access")
        return {
            "namespace": "wbi-publisher-pacing",
            "status": "PRESENT_UNKNOWN_ANCESTRY",
            "parent_inode": initial.st_ino,
            "files": files,
            "collection_lock_before_entry": _LEGACY_COLLECTION_OBSERVATION,
        }
    finally:
        os.close(parent)



def reviewed_migration_state():
    """Apply only an exact independently reviewed first-generation boundary."""
    marker = (
        _REVIEWED_NATIVE_MIGRATION
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else _LOCAL_RESEARCH_MIGRATION
    )
    if (
        not isinstance(marker, dict)
        or set(marker)
        != {
            "schema",
            "source",
            "evidence",
            "state",
            "state_sha256",
            "legacy_state_sha256",
        }
        or type(marker["schema"]) is not int
        or marker["schema"] != 1
        or marker["source"] != SOURCE_ID
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/312"
            r"#issuecomment-[0-9]+",
            marker.get("evidence", ""),
        )
    ):
        raise AdapterError("Explicit reviewed migration missing", "access")
    legacy = legacy_namespace_observation()
    digest = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        digest != marker["legacy_state_sha256"]
        or hashlib.sha256(
            json.dumps(
                marker["state"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        != marker["state_sha256"]
    ):
        raise AdapterError("Reviewed original state changed", "access")
    state = validate_budget(copy.deepcopy(marker["state"]))
    for file in legacy.get("files", []):
        if file["bucket"] == "publisher":
            value = file["state"]
            if (
                state["interval"] < value["interval"]
                or state["until"] < value["until"]
                or not set(value["starts"]) <= set(state["starts"])
            ):
                raise AdapterError(
                    "Original publisher MAX not retained", "access"
                )
    state["until"] = max(state["until"], time.time() + 60)
    state["observed_at"] = max(state["observed_at"], time.time())
    return validate_budget(state)



def recovery_contents(path, head, cache):
    """Read immutable source evidence once per revision during this scan."""
    key = (path, head)
    if key not in cache:
        item = workflow_api(f"/contents/{path}?ref={head}")
        try:
            if item.get("encoding") != "base64":
                raise ValueError()
            body = base64.b64decode(item["content"])
            blob = hashlib.sha1(
                b"blob " + str(len(body)).encode() + b"\0" + body
            ).hexdigest()
            if (item.get("sha") != blob or not body
                or item.get("path") != path
                or type(item.get("size")) is not int
                or item["size"] != len(body)
                or len(body) > 5_000_000):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise AdapterError("Incomplete no-access code evidence", "access")
        cache[key] = (blob, body)
    return cache[key]


def prove_no_publisher_access(row, cache):
    """Require a finite historical proof or the exact current quiet code."""
    finished, _, run_id, attempt, source = row
    head = _ATTEMPT_HEADS[(run_id, attempt)]
    exact, jobs = _RECOVERY_JOBS[(run_id, attempt)]
    if len(jobs) != 1:
        raise AdapterError("Missing exact single collect job", "access")
    try:
        start = datetime.fromisoformat(
            jobs[0]["started_at"].replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            jobs[0]["completed_at"].replace("Z", "+00:00")
        )
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ValueError()
        duration = end.timestamp() - start.timestamp()
    except (KeyError, TypeError, ValueError):
        raise AdapterError("Invalid exact job interval", "access")
    if (
        attempt != 1
        or exact.get("conclusion") != "failure"
        or len(jobs) != 1
        or jobs[0].get("name") != "collect"
        or jobs[0].get("conclusion") != "failure"
        or not 0 <= duration < 58
    ):
        raise AdapterError("Missing state has no no-access proof", "access")
    source_blob, source_bytes = recovery_contents(
        "adapters/be-wbi/adapter.py", head, cache
    )
    workflow_blob, _ = recovery_contents(
        ".github/workflows/fetch-be-wbi.yml", head, cache
    )
    pin = _PREACCESS_ATTEMPTS.get(run_id)
    if pin is not None:
        actual = (
            attempt, head, jobs[0]["id"], jobs[0]["started_at"],
            jobs[0]["completed_at"],
        )
        if (
            actual != pin
            or source_blob != _PREACCESS_SOURCE_BLOBS.get(head)
            or workflow_blob != _PREACCESS_WORKFLOW_BLOB
        ):
            raise AdapterError("Historical no-access proof changed", "access")
        return
    if (
        hashlib.sha256(source_bytes).hexdigest()
        != source_file_digest()
        or workflow_blob not in _RECOVERY_WORKFLOW_BLOBS
    ):
        raise AdapterError("Unreviewed artifactless source revision", "access")


def restoration_owner(latest):
    """Cross only positively proved no-access attempts, never unknown gaps."""
    rows = list(reversed(_HISTORY_COMPLETED))
    if not rows or rows[0][2:] != latest:
        raise AdapterError("Restoration chronology changed", "access")
    skipped, cache = [], {}
    for row in rows:
        identity = row[2:]
        run_id, attempt, source = identity
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or len(artifacts) > 100
            or type(result.get("total_count")) is not int
            or result["total_count"] != len(artifacts)
            or any(not isinstance(item, dict) for item in artifacts)
        ):
            raise AdapterError(
                "Incomplete recovery artifact inventory", "access"
            )
        matches = [item for item in artifacts if item.get("name") ==
                   _ARTIFACT_NAME + "-" + str(attempt)]
        if matches:
            historical = {
                item[2] for item in skipped if item[2] in _PREACCESS_ATTEMPTS
            }
            if (historical or identity == _PREACCESS_OWNER) and (
                historical != set(_PREACCESS_ATTEMPTS)
                or identity != _PREACCESS_OWNER
                or len(matches) != 1
                or (matches[0].get("id"), matches[0].get("digest"))
                != _PREACCESS_ARTIFACT
            ):
                raise AdapterError(
                    "Incomplete historical recovery chain", "access"
                )
            return (
                identity, result,
                max((item[0] for item in skipped), default=0),
            )
        if artifacts:
            raise AdapterError(
                "Unexpected artifactless attempt outputs", "access"
            )
        prove_no_publisher_access(row, cache)
        skipped.append(row)
    raise AdapterError("No actual owned publisher state remains", "access")


def preserve_recovery_bounds(incoming, finished):
    """Retain elapsed job bounds without fabricating publisher receipts."""
    state = copy.deepcopy(validate_budget(incoming))
    if finished:
        state["until"] = max(
            state["until"],
            finished + max(state["interval"], state["page_gap"]),
        )
        state["observed_at"] = max(state["observed_at"], finished)
    return validate_budget(state)


def wait_startup_quiet():
    """A wall-clock adjustment cannot shorten the first-access quiet floor."""
    if _STARTUP_QUIET_UNTIL is None:
        raise AdapterError("Publisher startup quiet is unproved", "access")
    while time.monotonic() < _STARTUP_QUIET_UNTIL:
        check_deadline()
        time.sleep(max(0, min(1, _STARTUP_QUIET_UNTIL - time.monotonic())))



# Activated only by an independently reviewed public maintenance decision.
_WBI_HANDOFF_DECISION = {
    "schema": 1,
    "source": "be-wbi",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/373"
        "#issuecomment-6942897042"
    ),
    "state_sha256": (
        "afb4dfdf4a796db523e1f5c10cac913c1883bb095f476202f91376e1840def10"
    ),
    "native_sha256": (
        "f2b80e070da2f7023e7519e97d25a657de050f78769db3dfa7f9844a5964908e"
    ),
    "witness_sha256": (
        "34f8daf029205bc7a567fb25eb59caefe06ea3a4d0d512329f10ba49e3fdde4c"
    ),
}
_WBI_HANDOFF_SHA256 = (
    "afb4dfdf4a796db523e1f5c10cac913c1883bb095f476202f91376e1840def10"
)
_WBI_HANDOFF_NATIVE = (
    "f2b80e070da2f7023e7519e97d25a657de050f78769db3dfa7f9844a5964908e"
)
_WBI_HANDOFF_WITNESSES = (
    "34f8daf029205bc7a567fb25eb59caefe06ea3a4d0d512329f10ba49e3fdde4c"
)
_WBI_HANDOFF_BYTES = (
    "eyJibG9ja2VkIjp0cnVlLCJjb21wbGV0ZWQiOjE3OTQ5MDI3ODMuOTEwODg5NiwiZmFt"
    "aWx5IjoieW91dGhvcHBzLWJlLXdiaS1wdWJsaXNoZXItdjEiLCJpbl9mbGlnaHQiOmZh"
    "bHNlLCJpbnRlcnZhbCI6MTIuMiwiaW52ZW50b3J5X2NvbXBsZXRlZCI6MTc5NDkwMjQ3"
    "NC4wNTkyNjEzLCJsZWdhY3lfNDI5X2hpc3RvcnkiOiJ1bmtub3duIiwibGVnYWN5X2Fj"
    "Y2Vzc19oaXN0b3J5X3Vua25vd24iOnRydWUsImxlZ2FjeV9hY2Nlc3Nfb2JzZXJ2YXRp"
    "b25zIjpbXSwibGVnYWN5X2hvbGRfaGlzdG9yeSI6InVubG9nZ2VkX2ZpZWxkc191bmtu"
    "b3duIiwib2JzZXJ2ZWRfNDI5X3NpbmNlX3RyYW5zaXRpb24iOjAsIm9ic2VydmVkX2F0"
    "IjoxNzk0OTAyNzgzLjkxMDg4OTYsInBhZ2VfZ2FwIjozMCwicmF0ZV9saW1pdF91bnRp"
    "bCI6MCwicmVmdXNhbF9zY29wZSI6eyJhZGRpdGlvbmFsX2hvbGRzIjpbXSwiZGVjaXNp"
    "b24iOnsiZGV0YWlsX3ByZWZpeGVzIjpbIi9mci9hY3R1YWxpdGVzLyIsIi9mci9haWRl"
    "LXNlcnZpY2UvIl0sImV2aWRlbmNlIjoiaHR0cHM6Ly9naXRodWIuY29tL1lvdXRoT3Bw"
    "cy9kYXRhLXBpcGVsaW5lL2lzc3Vlcy8zNzMjaXNzdWVjb21tZW50LTY5MjU2MDAxMzci"
    "LCJoZWxkX3NoYTI1NiI6ImFhNTQ0MTAxNzY1ZTY1YzdmMzEyNjUwMWNjZThlNjc4ODEw"
    "NjJjY2JlZmVjNzdmYjE5MzBjZDVlZmFiMjZlMGMiLCJtaWdyYXRpb25fcmV2aXNpb24i"
    "OiJ3YmkzNzMtY29udGVudC1zY29wZS12MSIsIm5hdGl2ZV9yZXZpc2lvbiI6IjA2Y2M4"
    "YjBkZTgzMmI0ZjA1NWMzNGE0N2JmMTZjNDM1ZGM3NTM2NWQwMjYwZWUyNjBjZDM3YTg3"
    "NjYwMzQwODYiLCJwYXRocyI6WyIvcm9ib3RzLnR4dCIsIi9lbi9ib3Vyc2VzIiwiL2Vu"
    "L3RheG9ub215L3Rlcm0vODQzIiwiL2VuL3RheG9ub215L3Rlcm0vODQ0IiwiL2VuL3Rh"
    "eG9ub215L3Rlcm0vODQ2IiwiL2VuL3RheG9ub215L3Rlcm0vODQ3IiwiL2ZyL2FwcGVs"
    "cy1hLXByb2pldHMiLCIvYWN0dWFsaXRlcyIsIi9mci9hY3R1YWxpdGVzIiwiL2ZyL3Jz"
    "cy1mZWVkcyJdLCJyZXNwb25zZV9zaGEyNTYiOiJiZWZkNGU0YTg0M2E3MGI0MTVlOGI5"
    "OWQ0Nzk4MjA1OThhMzIyZTRiNDA0YzhjNjNlMjQ2NGUyYmFiZTZkOTE3Iiwic2NoZW1h"
    "IjoxLCJzb3VyY2UiOiJiZS13YmkifSwiZXh0ZW5zaW9ucyI6W3siZXZpZGVuY2UiOiJo"
    "dHRwczovL2dpdGh1Yi5jb20vWW91dGhPcHBzL2RhdGEtcGlwZWxpbmUvaXNzdWVzLzM3"
    "MyNpc3N1ZWNvbW1lbnQtNjk0MTQxNzYzOCIsImhlbGRfc2hhMjU2IjoiYWE1NDQxMDE3"
    "NjVlNjVjN2YzMTI2NTAxY2NlOGU2Nzg4MTA2MmNjYmVmZWM3N2ZiMTkzMGNkNWVmYWIy"
    "NmUwYyIsIm1pZ3JhdGlvbl9yZXZpc2lvbiI6IndiaTM3My1jb250ZW50LXNjb3BlLXYy"
    "IiwicGFyZW50X2RlY2lzaW9uX3NoYTI1NiI6IjA0ZWJjOTNmYjRkZDc4ODc4MmRiYWE0"
    "NDY4NDE0ZjQ4Mjc2MDM5ODg5ZTkzZWY0ZDI1NTE4MzdkM2JhZDUyNmEiLCJwYXRocyI6"
    "WyIvZnIvV0JJLVdvcmxkIl0sInByaW9yX2pvdXJuYWxfc2hhMjU2IjoiMjcxNjY3ZjJm"
    "Mzk1OGFmNWNjMjE3ZjExNDNjZjNmZTU2ZDUzYzU4NWZhYjYyMGJjYWYyYWZmMmNmMTQ1"
    "OWM1MyIsInJlc3BvbnNlX3NoYTI1NiI6ImJlZmQ0ZTRhODQzYTcwYjQxNWU4Yjk5ZDQ3"
    "OTgyMDU5OGEzMjJlNGI0MDRjOGM2M2UyNDY0ZTJiYWJlNmQ5MTciLCJzY2hlbWEiOjEs"
    "InNvdXJjZSI6ImJlLXdiaSJ9XSwiaW5pdGlhbF9yZWZ1c2FsIjp7ImJvZHlfc2hhMjU2"
    "IjoiYmVmZDRlNGE4NDNhNzBiNDE1ZThiOTlkNDc5ODIwNTk4YTMyMmU0YjQwNGM4YzYz"
    "ZTI0NjRlMmJhYmU2ZDkxNyIsImNvbXBsZXRlZCI6MTc5NDg1MTc5My43NDQyODY4LCJu"
    "YXRpdmVfcmV2aXNpb24iOiIwNmNjOGIwZGU4MzJiNGYwNTVjMzRhNDdiZjE2YzQzNWRj"
    "NzUzNjVkMDI2MGVlMjYwY2QzN2E4NzY2MDM0MDg2Iiwibm9kZV9pZCI6MjUxMDgsIm9y"
    "aWdpbnMiOlsiaHR0cHM6Ly93YmkuYmUiLCJodHRwczovL3d3dy53YmkuYmUiXSwicmVz"
    "b3VyY2Vfc2x1ZyI6Im9mZnJlLXN0YWdlLWNlbnRyZS1pbnRlcm5hdGlvbmFsLXJlY2hl"
    "cmNoZS1jYW5jZXItY2lyYyIsInN0YXR1cyI6NDAzLCJ1cmwiOiJodHRwczovL3d3dy53"
    "YmkuYmUvZnIvYWN0dWFsaXRlcy9vZmZyZS1zdGFnZS1jZW50cmUtaW50ZXJuYXRpb25h"
    "bC1yZWNoZXJjaGUtY2FuY2VyLWNpcmMifSwib3JpZ2luYWxfam91cm5hbF9iYXNlNjQi"
    "OiJleUp6WTJobGJXRWlPak1zSW1aaGJXbHNlU0k2SW5sdmRYUm9iM0J3Y3kxaVpTMTNZ"
    "bWt0Y0hWaWJHbHphR1Z5TFhZeElpd2liR1ZuWVdONVgyaHZiR1JmYUdsemRHOXllU0k2"
    "SW5WdWJHOW5aMlZrWDJacFpXeGtjMTkxYm10dWIzZHVJaXdpYkdWbllXTjVYMkZqWTJW"
    "emMxOW9hWE4wYjNKNVgzVnVhMjV2ZDI0aU9uUnlkV1VzSW14bFoyRmplVjloWTJObGMz"
    "TmZiMkp6WlhKMllYUnBiMjV6SWpwYlhTd2ljbVZ6WldGeVkyaGZjSEp2ZG1WdVlXNWpa"
    "U0k2ZXlKaVlYTmxiR2x1WlY5amIyeHNaV04wYVc5dVgyeHZZMnNpT2lKT1QxOVBVa2xI"
    "U1U1QlRGOURUMHhNUlVOVVNVOU9YMHhQUTB0ZlNVNWZRa0ZUUlV4SlRrVWlMQ0pqYjIx"
    "d2JHVjBaVjloZEhSbGJYQjBjeUk2TWpjc0ltUmhkR0ZmYzI5MWNtTmxYM0JwYmlJNklt"
    "RTJOekV6WlRReE9XRXhabVJqWVRNeU9UbGtOakJqWlRneE1EZ3daRFJrTXpJeU0ySmpN"
    "RGNpTENKbGVHVmpkWFJsWkY5cWIySmZZMjl0Y0d4bGRHbHZibDkxY0hCbGNsOWliM1Z1"
    "WkhNaU9sdDdJbUYwZEdWdGNIUWlPakVzSW1wdllsOXBaQ0k2TVRFMk5USXdNek14TVRn"
    "ekxDSnJhVzVrSWpvaVlXTjBkV0ZzWDJwdllsOWpiMjF3YkdWMGFXOXVYMVZRVUVWU1gw"
    "SlBWVTVFWDI1dmRGOXdkV0pzYVhOb1pYSmZkMmx5WlNJc0luSjFibDlwWkNJNk16ZzNO"
    "ak0wTVRrME9UTXNJblJwYldVaU9qRTNPVEl3T0RBeE1Ea3VNSDBzZXlKaGRIUmxiWEIw"
    "SWpveExDSnFiMkpmYVdRaU9qRXhOak0zTmpFd05qRTJNaXdpYTJsdVpDSTZJbUZqZEhW"
    "aGJGOXFiMkpmWTI5dGNHeGxkR2x2Ymw5VlVGQkZVbDlDVDFWT1JGOXViM1JmY0hWaWJH"
    "bHphR1Z5WDNkcGNtVWlMQ0p5ZFc1ZmFXUWlPak00TnpJeE1qVTVOakV5TENKMGFXMWxJ"
    "am94TnpreU1EWXhNRFF6TGpCOUxIc2lZWFIwWlcxd2RDSTZNU3dpYW05aVgybGtJam94"
    "TVRZME1qSXhPVE0wTnpnc0ltdHBibVFpT2lKaFkzUjFZV3hmYW05aVgyTnZiWEJzWlhS"
    "cGIyNWZWVkJRUlZKZlFrOVZUa1JmYm05MFgzQjFZbXhwYzJobGNsOTNhWEpsSWl3aWNu"
    "VnVYMmxrSWpvek9EY3lNREE0TkRFeU9Td2lkR2x0WlNJNk1UYzVNakEyTnpZeU15NHdm"
    "U3g3SW1GMGRHVnRjSFFpT2pFc0ltcHZZbDlwWkNJNk1URTJNekEzTkRZNU5UY3pMQ0py"
    "YVc1a0lqb2lZV04wZFdGc1gycHZZbDlqYjIxd2JHVjBhVzl1WDFWUVVFVlNYMEpQVlU1"
    "RVgyNXZkRjl3ZFdKc2FYTm9aWEpmZDJseVpTSXNJbkoxYmw5cFpDSTZNemcyT1RrMU16"
    "VTNOallzSW5ScGJXVWlPakUzT1RJd05UQXpOakF1TUgwc2V5SmhkSFJsYlhCMElqb3hM"
    "Q0pxYjJKZmFXUWlPakV4TmpJMU5UVTNNemMzT1N3aWEybHVaQ0k2SW1GamRIVmhiRjlx"
    "YjJKZlkyOXRjR3hsZEdsdmJsOVZVRkJGVWw5Q1QxVk9SRjl1YjNSZmNIVmliR2x6YUdW"
    "eVgzZHBjbVVpTENKeWRXNWZhV1FpT2pNNE5qZzBNak0xT0RRd0xDSjBhVzFsSWpveE56"
    "a3lNRFF3TmprekxqQjlMSHNpWVhSMFpXMXdkQ0k2TVN3aWFtOWlYMmxrSWpveE1UWXhO"
    "VFkyTlRVNU9EQXNJbXRwYm1RaU9pSmhZM1IxWVd4ZmFtOWlYMk52YlhCc1pYUnBiMjVm"
    "VlZCUVJWSmZRazlWVGtSZmJtOTBYM0IxWW14cGMyaGxjbDkzYVhKbElpd2ljblZ1WDJs"
    "a0lqb3pPRFkxTXpFNE5UazJNaXdpZEdsdFpTSTZNVGM1TWpBeU1EQTRNQzR3ZlN4N0lt"
    "RjBkR1Z0Y0hRaU9qRXNJbXB2WWw5cFpDSTZNVEUyTURNMk1UZ3lPVFEyTENKcmFXNWtJ"
    "am9pWVdOMGRXRnNYMnB2WWw5amIyMXdiR1YwYVc5dVgxVlFVRVZTWDBKUFZVNUVYMjV2"
    "ZEY5d2RXSnNhWE5vWlhKZmQybHlaU0lzSW5KMWJsOXBaQ0k2TXpnMk1UYzBOemszTWpJ"
    "c0luUnBiV1VpT2pFM09USXdNRE0xT0RJdU1IMHNleUpoZEhSbGJYQjBJam94TENKcWIy"
    "SmZhV1FpT2pFeE5UZzVNREEyT0RJM01pd2lhMmx1WkNJNkltRmpkSFZoYkY5cWIySmZZ"
    "Mjl0Y0d4bGRHbHZibDlWVUZCRlVsOUNUMVZPUkY5dWIzUmZjSFZpYkdsemFHVnlYM2Rw"
    "Y21VaUxDSnlkVzVmYVdRaU9qTTROVGMxTVRFNU5UQXhMQ0owYVcxbElqb3hOemt4T1Rn"
    "MU9UazRMakI5TEhzaVlYUjBaVzF3ZENJNk1Td2lhbTlpWDJsa0lqb3hNVFUzTmpJNU9U"
    "Z3hPRFlzSW10cGJtUWlPaUpoWTNSMVlXeGZhbTlpWDJOdmJYQnNaWFJwYjI1ZlZWQlFS"
    "VkpmUWs5VlRrUmZibTkwWDNCMVlteHBjMmhsY2w5M2FYSmxJaXdpY25WdVgybGtJam96"
    "T0RVek5EYzFOalF6TlN3aWRHbHRaU0k2TVRjNU1UazJOemN4TUM0d2ZTeDdJbUYwZEdW"
    "dGNIUWlPakVzSW1wdllsOXBaQ0k2TVRFMU56UXhNRE0wTWpVeExDSnJhVzVrSWpvaVlX"
    "TjBkV0ZzWDJwdllsOWpiMjF3YkdWMGFXOXVYMVZRVUVWU1gwSlBWVTVFWDI1dmRGOXdk"
    "V0pzYVhOb1pYSmZkMmx5WlNJc0luSjFibDlwWkNJNk16ZzFNekExTWpFeE5qTXNJblJw"
    "YldVaU9qRTNPVEU1TmpReE9EZ3VNSDBzZXlKaGRIUmxiWEIwSWpveExDSnFiMkpmYVdR"
    "aU9qRXhOVFl3TURFNU56QTBNaXdpYTJsdVpDSTZJbUZqZEhWaGJGOXFiMkpmWTI5dGNH"
    "eGxkR2x2Ymw5VlVGQkZVbDlDVDFWT1JGOXViM1JmY0hWaWJHbHphR1Z5WDNkcGNtVWlM"
    "Q0p5ZFc1ZmFXUWlPak00TkRnMk1UVTJNemsxTENKMGFXMWxJam94TnpreE9UTTFNek0z"
    "TGpCOUxIc2lZWFIwWlcxd2RDSTZNU3dpYW05aVgybGtJam94TVRVMU1UYzNOekF6T0Rr"
    "c0ltdHBibVFpT2lKaFkzUjFZV3hmYW05aVgyTnZiWEJzWlhScGIyNWZWVkJRUlZKZlFr"
    "OVZUa1JmYm05MFgzQjFZbXhwYzJobGNsOTNhWEpsSWl3aWNuVnVYMmxrSWpvek9EUTJN"
    "VFUyTnpVek1Td2lkR2x0WlNJNk1UYzVNVGt5TWpnek15NHdmU3g3SW1GMGRHVnRjSFFp"
    "T2pFc0ltcHZZbDlwWkNJNk1URTFNakk0TURjNU9UQTNMQ0pyYVc1a0lqb2lZV04wZFdG"
    "c1gycHZZbDlqYjIxd2JHVjBhVzl1WDFWUVVFVlNYMEpQVlU1RVgyNXZkRjl3ZFdKc2FY"
    "Tm9aWEpmZDJseVpTSXNJbkoxYmw5cFpDSTZNemd6Tnpjd09Ea3dNamdzSW5ScGJXVWlP"
    "akUzT1RFNE9EUXhOemd1TUgwc2V5SmhkSFJsYlhCMElqb3hMQ0pxYjJKZmFXUWlPakV4"
    "TlRBeU9UVTFNVEEyTXl3aWEybHVaQ0k2SW1GamRIVmhiRjlxYjJKZlkyOXRjR3hsZEds"
    "dmJsOVZVRkJGVWw5Q1QxVk9SRjl1YjNSZmNIVmliR2x6YUdWeVgzZHBjbVVpTENKeWRX"
    "NWZhV1FpT2pNNE16RTFNRFF6TVRNMkxDSjBhVzFsSWpveE56a3hPRFF6TWpBeExqQjlM"
    "SHNpWVhSMFpXMXdkQ0k2TVN3aWFtOWlYMmxrSWpveE1UUTNORFV6TURjM01UWXNJbXRw"
    "Ym1RaU9pSmhZM1IxWVd4ZmFtOWlYMk52YlhCc1pYUnBiMjVmVlZCUVJWSmZRazlWVGtS"
    "ZmJtOTBYM0IxWW14cGMyaGxjbDkzYVhKbElpd2ljblZ1WDJsa0lqb3pPREl5T1RVek5E"
    "VXdNQ3dpZEdsdFpTSTZNVGM1TVRjNU56RTVOUzR3ZlN4N0ltRjBkR1Z0Y0hRaU9qRXNJ"
    "bXB2WWw5cFpDSTZNVEUwTmpBMk5UTTROVGcxTENKcmFXNWtJam9pWVdOMGRXRnNYMnB2"
    "WWw5amIyMXdiR1YwYVc5dVgxVlFVRVZTWDBKUFZVNUVYMjV2ZEY5d2RXSnNhWE5vWlhK"
    "ZmQybHlaU0lzSW5KMWJsOXBaQ0k2TXpneE9EVTBNRFV6TURBc0luUnBiV1VpT2pFM09U"
    "RTNOakkwTnpRdU1IMHNleUpoZEhSbGJYQjBJam94TENKcWIySmZhV1FpT2pFeE5EVTBP"
    "RGt5TkRNNE9Td2lhMmx1WkNJNkltRmpkSFZoYkY5cWIySmZZMjl0Y0d4bGRHbHZibDlW"
    "VUZCRlVsOUNUMVZPUkY5dWIzUmZjSFZpYkdsemFHVnlYM2RwY21VaUxDSnlkVzVmYVdR"
    "aU9qTTRNVFkyTVRFMk9URTNMQ0owYVcxbElqb3hOemt4TnpRMU16Z3dMakI5TEhzaVlY"
    "UjBaVzF3ZENJNk1Td2lhbTlpWDJsa0lqb3hNVFEwTXpNd09ETXhPVFFzSW10cGJtUWlP"
    "aUpoWTNSMVlXeGZhbTlpWDJOdmJYQnNaWFJwYjI1ZlZWQlFSVkpmUWs5VlRrUmZibTkw"
    "WDNCMVlteHBjMmhsY2w5M2FYSmxJaXdpY25WdVgybGtJam96T0RFeU5qa3hOakV4TXl3"
    "aWRHbHRaU0k2TVRjNU1UY3dPVFV6TVM0d2ZTeDdJbUYwZEdWdGNIUWlPakVzSW1wdlls"
    "OXBaQ0k2TVRFME16UXhNVGMxTkRNNUxDSnJhVzVrSWpvaVlXTjBkV0ZzWDJwdllsOWpi"
    "MjF3YkdWMGFXOXVYMVZRVUVWU1gwSlBWVTVFWDI1dmRGOXdkV0pzYVhOb1pYSmZkMmx5"
    "WlNJc0luSjFibDlwWkNJNk16Z3dPVFUzTlRNeE1UWXNJblJwYldVaU9qRTNPVEUyTnpV"
    "M05EUXVNSDBzZXlKaGRIUmxiWEIwSWpveExDSnFiMkpmYVdRaU9qRXhOREk0TlRNMk9U"
    "UTFNaXdpYTJsdVpDSTZJbUZqZEhWaGJGOXFiMkpmWTI5dGNHeGxkR2x2Ymw5VlVGQkZV"
    "bDlDVDFWT1JGOXViM1JmY0hWaWJHbHphR1Z5WDNkcGNtVWlMQ0p5ZFc1ZmFXUWlPak00"
    "TURjMk9EQXhOREUwTENKMGFXMWxJam94TnpreE5qVTNPVEExTGpCOUxIc2lZWFIwWlcx"
    "d2RDSTZNU3dpYW05aVgybGtJam94TVRReU56UTVOVEEyTnprc0ltdHBibVFpT2lKaFkz"
    "UjFZV3hmYW05aVgyTnZiWEJzWlhScGIyNWZWVkJRUlZKZlFrOVZUa1JmYm05MFgzQjFZ"
    "bXhwYzJobGNsOTNhWEpsSWl3aWNuVnVYMmxrSWpvek9EQTNNVGcwTURJNE1Td2lkR2x0"
    "WlNJNk1UYzVNVFkxTkRjMk5DNHdmU3g3SW1GMGRHVnRjSFFpT2pFc0ltcHZZbDlwWkNJ"
    "Nk1URTBNVGMwTURnNE56YzJMQ0pyYVc1a0lqb2lZV04wZFdGc1gycHZZbDlqYjIxd2JH"
    "VjBhVzl1WDFWUVVFVlNYMEpQVlU1RVgyNXZkRjl3ZFdKc2FYTm9aWEpmZDJseVpTSXNJ"
    "bkoxYmw5cFpDSTZNemd3TXpnMU56STJNRFlzSW5ScGJXVWlPakUzT1RFMk1qRTNOVEF1"
    "TUgwc2V5SmhkSFJsYlhCMElqb3hMQ0pxYjJKZmFXUWlPakV4TkRBM09UUTRNRFl3T1N3"
    "aWEybHVaQ0k2SW1GamRIVmhiRjlxYjJKZlkyOXRjR3hsZEdsdmJsOVZVRkJGVWw5Q1Qx"
    "Vk9SRjl1YjNSZmNIVmliR2x6YUdWeVgzZHBjbVVpTENKeWRXNWZhV1FpT2pNNE1EQTNO"
    "RGN3TVRBMExDSjBhVzFsSWpveE56a3hOVGt3T1RBNExqQjlMSHNpWVhSMFpXMXdkQ0k2"
    "TVN3aWFtOWlYMmxrSWpveE1UTTVPVEUyTkRVME9ETXNJbXRwYm1RaU9pSmhZM1IxWVd4"
    "ZmFtOWlYMk52YlhCc1pYUnBiMjVmVlZCUVJWSmZRazlWVGtSZmJtOTBYM0IxWW14cGMy"
    "aGxjbDkzYVhKbElpd2ljblZ1WDJsa0lqb3pOems0TVRFek5UWTFPQ3dpZEdsdFpTSTZN"
    "VGM1TVRVM05EWTNNUzR3ZlN4N0ltRjBkR1Z0Y0hRaU9qRXNJbXB2WWw5cFpDSTZNVEV6"
    "TnpVME56azFNalU1TENKcmFXNWtJam9pWVdOMGRXRnNYMnB2WWw5amIyMXdiR1YwYVc5"
    "dVgxVlFVRVZTWDBKUFZVNUVYMjV2ZEY5d2RXSnNhWE5vWlhKZmQybHlaU0lzSW5KMWJs"
    "OXBaQ0k2TXpjNU1EazVPREF6TVRJc0luUnBiV1VpT2pFM09URTFNemM0T1RVdU1IMHNl"
    "eUpoZEhSbGJYQjBJam94TENKcWIySmZhV1FpT2pFeE16WXlORGc0TmpRNE5Td2lhMmx1"
    "WkNJNkltRmpkSFZoYkY5cWIySmZZMjl0Y0d4bGRHbHZibDlWVUZCRlVsOUNUMVZPUkY5"
    "dWIzUmZjSFZpYkdsemFHVnlYM2RwY21VaUxDSnlkVzVmYVdRaU9qTTNPRFk1TkRrNU5q"
    "TXpMQ0owYVcxbElqb3hOemt4TlRBNU16Z3dMakI5TEhzaVlYUjBaVzF3ZENJNk1Td2lh"
    "bTlpWDJsa0lqb3hNVE0yTWpNNU9UZzJNek1zSW10cGJtUWlPaUpoWTNSMVlXeGZhbTlp"
    "WDJOdmJYQnNaWFJwYjI1ZlZWQlFSVkpmUWs5VlRrUmZibTkwWDNCMVlteHBjMmhsY2w5"
    "M2FYSmxJaXdpY25WdVgybGtJam96TnpnMk9UUTNPRE15TlN3aWRHbHRaU0k2TVRjNU1U"
    "VXdPVEUzTWk0d2ZWMHNJbVp5WlhOb1gyaHBjM1J2Y25sZmMyaGhNalUySWpvaU1HUmxP"
    "REUyTURZNFlqWTVNRE5qTm1Sa1ptRTBaR1U1WWpZMVltWTROV1psTXpkak9HTTVObVUw"
    "Wmpsa05XUTVNV016TlRjMU0yTXdOVEUxWmpKaE5pSXNJbTFsZEdGa1lYUmhYM05vWVRJ"
    "MU5pSTZJakEyTkRoaFl6STRZVFZsTVdVMVpqUTJaamMzWVRZNU9XSmhOVFEyWXpKall6"
    "WmtNamMwTkRnek1URXpOemcwTmpnM1pXRXpZalpsTTJKbE1EZ3lPVElpTENKdmNtbG5h"
    "VzVoYkY5c1pXZGhZM2xmYjJKelpYSjJZWFJwYjI0aU9uc2lZMjlzYkdWamRHbHZibDlz"
    "YjJOclgySmxabTl5WlY5bGJuUnllU0k2ZXlKemRHRjBkWE1pT2lKT1QxOVBVa2xIU1U1"
    "QlRGOURUMHhNUlVOVVNVOU9YMHhQUTB0ZlNVNWZRa0ZUUlV4SlRrVWlmU3dpYm1GdFpY"
    "TndZV05sSWpvaWQySnBMWEIxWW14cGMyaGxjaTF3WVdOcGJtY2lMQ0p6ZEdGMGRYTWlP"
    "aUpCUWxORlRsUmZUazlVWDFwRlVrOWZTRWxUVkU5U1dTSjlMQ0p6ZEdGMGRYTWlPaUpT"
    "VDA5VVgxSkZRVVJQVGt4WlgwWlZURXd5TjE5S1QwSlRYMHhGUjBGRFdWOU5SVlJCUkVG"
    "VVFWOU5RVmhmVUZKUFVFOVRRVXdpTENKMWJtdHViM2R1WDJOdmJuTjBjbUZwYm5Seklq"
    "b2lkVzVzYjJkblpXUWdhR2x6ZEc5eWFXTmhiQ0J3ZFdKc2FYTm9aWElnWVdOalpYTnpM"
    "M0psWm5WellXd3ZOREk1SUdobFlXUmxjbk1nY21WdFlXbHVJSFZ1YTI1dmQyNDdJSHBs"
    "Y204Z2IySnpaWEoyWldRZ2MzUnlhV3RsY3lCdmJteDVJR0ZtZEdWeUlHVjRjR3hwWTJs"
    "MElIUnlZVzV6YVhScGIyNGlmU3dpYzNSaGNuUnpJanBiTVRjNU5EZzFNVGMzTmk0d05E"
    "RTBNakk0TERFM09UUTROVEUzT1RJdU5USTVNREk0WFN3aWFXNTBaWEoyWVd3aU9qRXlM"
    "aklzSW5WdWRHbHNJam94TnprME9EVXhOekExTGpBMk1qWXhOelVzSW5CaFoyVmZaMkZ3"
    "SWpvek1Dd2lZMjl0Y0d4bGRHVmtJam94TnprME9EVXhOemt6TGpjME5ESTROamdzSW1s"
    "dWRtVnVkRzl5ZVY5amIyMXdiR1YwWldRaU9qRTNPVE14T1RZMU1URXVNRFExTmpNMkxD"
    "SmliRzlqYTJWa0lqcDBjblZsTENKcGJsOW1iR2xuYUhRaU9tWmhiSE5sTENKc1pXZGhZ"
    "M2xmTkRJNVgyaHBjM1J2Y25raU9pSjFibXR1YjNkdUlpd2liMkp6WlhKMlpXUmZOREk1"
    "WDNOcGJtTmxYM1J5WVc1emFYUnBiMjRpT2pBc0luSmhkR1ZmYkdsdGFYUmZkVzUwYVd3"
    "aU9qQXNJbTlpYzJWeWRtVmtYMkYwSWpveE56azBPRFV4TnprekxqYzBOREk0TmpoOSJ9"
    "LCJyZXNlYXJjaF9wcm92ZW5hbmNlIjp7ImJhc2VsaW5lX2NvbGxlY3Rpb25fbG9jayI6"
    "Ik5PX09SSUdJTkFMX0NPTExFQ1RJT05fTE9DS19JTl9CQVNFTElORSIsImNvbXBsZXRl"
    "X2F0dGVtcHRzIjoyNywiZGF0YV9zb3VyY2VfcGluIjoiYTY3MTNlNDE5YTFmZGNhMzI5"
    "OWQ2MGNlODEwODBkNGQzMjIzYmMwNyIsImV4ZWN1dGVkX2pvYl9jb21wbGV0aW9uX3Vw"
    "cGVyX2JvdW5kcyI6W3siYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTY1MjAzMzExODMsImtp"
    "bmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hl"
    "cl93aXJlIiwicnVuX2lkIjozODc2MzQxOTQ5MywidGltZSI6MTc5MjA4MDEwOS4wfSx7"
    "ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE2Mzc2MTA2MTYyLCJraW5kIjoiYWN0dWFsX2pv"
    "Yl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9p"
    "ZCI6Mzg3MjEyNTk2MTIsInRpbWUiOjE3OTIwNjEwNDMuMH0seyJhdHRlbXB0IjoxLCJq"
    "b2JfaWQiOjExNjQyMjE5MzQ3OCwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9V"
    "UFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4NzIwMDg0MTI5"
    "LCJ0aW1lIjoxNzkyMDY3NjIzLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTYzMDc0"
    "Njk1NzMsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90"
    "X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODY5OTUzNTc2NiwidGltZSI6MTc5MjA1"
    "MDM2MC4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE2MjU1NTczNzc5LCJraW5kIjoi"
    "YWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2ly"
    "ZSIsInJ1bl9pZCI6Mzg2ODQyMzU4NDAsInRpbWUiOjE3OTIwNDA2OTMuMH0seyJhdHRl"
    "bXB0IjoxLCJqb2JfaWQiOjExNjE1NjY1NTk4MCwia2luZCI6ImFjdHVhbF9qb2JfY29t"
    "cGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4"
    "NjUzMTg1OTYyLCJ0aW1lIjoxNzkyMDIwMDgwLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lk"
    "IjoxMTYwMzYxODI5NDYsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJf"
    "Qk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODYxNzQ3OTcyMiwidGlt"
    "ZSI6MTc5MjAwMzU4Mi4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE1ODkwMDY4Mjcy"
    "LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJs"
    "aXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzg1NzUxMTk1MDEsInRpbWUiOjE3OTE5ODU5OTgu"
    "MH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNTc2Mjk5ODE4Niwia2luZCI6ImFjdHVh"
    "bF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJy"
    "dW5faWQiOjM4NTM0NzU2NDM1LCJ0aW1lIjoxNzkxOTY3NzEwLjB9LHsiYXR0ZW1wdCI6"
    "MSwiam9iX2lkIjoxMTU3NDEwMzQyNTEsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRp"
    "b25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODUzMDUy"
    "MTE2MywidGltZSI6MTc5MTk2NDE4OC4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE1"
    "NjAwMTk3MDQyLCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5E"
    "X25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzg0ODYxNTYzOTUsInRpbWUiOjE3"
    "OTE5MzUzMzcuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNTUxNzc3MDM4OSwia2lu"
    "ZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVy"
    "X3dpcmUiLCJydW5faWQiOjM4NDYxNTY3NTMxLCJ0aW1lIjoxNzkxOTIyODMzLjB9LHsi"
    "YXR0ZW1wdCI6MSwiam9iX2lkIjoxMTUyMjgwNzk5MDcsImtpbmQiOiJhY3R1YWxfam9i"
    "X2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lk"
    "IjozODM3NzA4OTAyOCwidGltZSI6MTc5MTg4NDE3OC4wfSx7ImF0dGVtcHQiOjEsImpv"
    "Yl9pZCI6MTE1MDI5NTUxMDYzLCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQ"
    "UEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6MzgzMTUwNDMxMzYs"
    "InRpbWUiOjE3OTE4NDMyMDEuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNDc0NTMw"
    "NzcxNiwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3Rf"
    "cHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MjI5NTM0NTAwLCJ0aW1lIjoxNzkxNzk3"
    "MTk1LjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTQ2MDY1Mzg1ODUsImtpbmQiOiJh"
    "Y3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJl"
    "IiwicnVuX2lkIjozODE4NTQwNTMwMCwidGltZSI6MTc5MTc2MjQ3NC4wfSx7ImF0dGVt"
    "cHQiOjEsImpvYl9pZCI6MTE0NTQ4OTI0Mzg5LCJraW5kIjoiYWN0dWFsX2pvYl9jb21w"
    "bGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzgx"
    "NjYxMTY5MTcsInRpbWUiOjE3OTE3NDUzODAuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQi"
    "OjExNDQzMzA4MzE5NCwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9C"
    "T1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MTI2OTE2MTEzLCJ0aW1l"
    "IjoxNzkxNzA5NTMxLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTQzNDExNzU0Mzks"
    "ImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxp"
    "c2hlcl93aXJlIiwicnVuX2lkIjozODA5NTc1MzExNiwidGltZSI6MTc5MTY3NTc0NC4w"
    "fSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE0Mjg1MzY5NDUyLCJraW5kIjoiYWN0dWFs"
    "X2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1"
    "bl9pZCI6MzgwNzY4MDE0MTQsInRpbWUiOjE3OTE2NTc5MDUuMH0seyJhdHRlbXB0Ijox"
    "LCJqb2JfaWQiOjExNDI3NDk1MDY3OSwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlv"
    "bl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MDcxODQw"
    "MjgxLCJ0aW1lIjoxNzkxNjU0NzY0LjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTQx"
    "NzQwODg3NzYsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRf"
    "bm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODAzODU3MjYwNiwidGltZSI6MTc5"
    "MTYyMTc1MC4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE0MDc5NDgwNjA5LCJraW5k"
    "IjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJf"
    "d2lyZSIsInJ1bl9pZCI6MzgwMDc0NzAxMDQsInRpbWUiOjE3OTE1OTA5MDguMH0seyJh"
    "dHRlbXB0IjoxLCJqb2JfaWQiOjExMzk5MTY0NTQ4Mywia2luZCI6ImFjdHVhbF9qb2Jf"
    "Y29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQi"
    "OjM3OTgxMTM1NjU4LCJ0aW1lIjoxNzkxNTc0NjcxLjB9LHsiYXR0ZW1wdCI6MSwiam9i"
    "X2lkIjoxMTM3NTQ3OTUyNTksImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQ"
    "RVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozNzkwOTk4MDMxMiwi"
    "dGltZSI6MTc5MTUzNzg5NS4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTEzNjI0ODg2"
    "NDg1LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9w"
    "dWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzc4Njk0OTk2MzMsInRpbWUiOjE3OTE1MDkz"
    "ODAuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExMzYyMzk5ODYzMywia2luZCI6ImFj"
    "dHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUi"
    "LCJydW5faWQiOjM3ODY5NDc4MzI1LCJ0aW1lIjoxNzkxNTA5MTcyLjB9XSwiZnJlc2hf"
    "aGlzdG9yeV9zaGEyNTYiOiIwZGU4MTYwNjhiNjkwM2M2ZGRmYTRkZTliNjViZjg1ZmUz"
    "N2M4Yzk2ZTRmOWQ1ZDkxYzM1NzUzYzA1MTVmMmE2IiwibWV0YWRhdGFfc2hhMjU2Ijoi"
    "MDY0OGFjMjhhNWUxZTVmNDZmNzdhNjk5YmE1NDZjMmNjNmQyNzQ0ODMxMTM3ODQ2ODdl"
    "YTNiNmUzYmUwODI5MiIsIm9yaWdpbmFsX2xlZ2FjeV9vYnNlcnZhdGlvbiI6eyJjb2xs"
    "ZWN0aW9uX2xvY2tfYmVmb3JlX2VudHJ5Ijp7InN0YXR1cyI6Ik5PX09SSUdJTkFMX0NP"
    "TExFQ1RJT05fTE9DS19JTl9CQVNFTElORSJ9LCJuYW1lc3BhY2UiOiJ3YmktcHVibGlz"
    "aGVyLXBhY2luZyIsInN0YXR1cyI6IkFCU0VOVF9OT1RfWkVST19ISVNUT1JZIn0sInN0"
    "YXR1cyI6IlJPT1RfUkVBRE9OTFlfRlVMTDI3X0pPQlNfTEVHQUNZX01FVEFEQVRBX01B"
    "WF9QUk9QT1NBTCIsInVua25vd25fY29uc3RyYWludHMiOiJ1bmxvZ2dlZCBoaXN0b3Jp"
    "Y2FsIHB1Ymxpc2hlciBhY2Nlc3MvcmVmdXNhbC80MjkgaGVhZGVycyByZW1haW4gdW5r"
    "bm93bjsgemVybyBvYnNlcnZlZCBzdHJpa2VzIG9ubHkgYWZ0ZXIgZXhwbGljaXQgdHJh"
    "bnNpdGlvbiJ9LCJzY2hlbWEiOjQsInN0YXJ0cyI6WzE3OTMyODMyMTIuOTYxNzU1OCwx"
    "NzkzMjgzMjU2LjAxODQ3ODQsMTc5NDg1MTcwNS4wNjQ2NzAzLDE3OTQ4NTE3NzYuMDQx"
    "NDIyOCwxNzk0ODUxNzkyLjUyOTAyOCwxNzk0ODU0NjYyLjM4MTg3MzQsMTc5NDg1NDcy"
    "OS4zNDQ2OTYsMTc5NDg1NDc2My42NzQyMTk2LDE3OTQ4NTQ3OTYuMjg2MjcxNiwxNzk0"
    "ODk5OTEzLjQ0MjI5NjUsMTc5NDg5OTk3Mi4wNTE3NTQ3LDE3OTQ4OTk5ODcuMTU0NjE2"
    "NiwxNzk0OTAwMDAyLjEyMzA3MSwxNzk0OTAwMDE4LjAxMjY5NTYsMTc5NDkwMDAzMi4y"
    "MTA5NzM3LDE3OTQ5MDAwNDYuMTEyMDQxLDE3OTQ5MDAwNjIuNjAwNzQ4LDE3OTQ5MDAw"
    "NzYuNjMxODY5NiwxNzk0OTAwNjYxLjUzMzA1MTUsMTc5NDkwMDcyMi42MzMzNzQ3LDE3"
    "OTQ5MDA3MzguMjE5MTA0LDE3OTQ5MDA3NTIuOTQyOTQ4OCwxNzk0OTAwNzY4LjYxODY0"
    "NCwxNzk0OTAwNzg1LjIwNjA1MSwxNzk0OTAwODAwLjc2NzE5NTIsMTc5NDkwMDgxNS4x"
    "MTcwNjY0LDE3OTQ5MDA4MjguODgxOTY2NCwxNzk0OTAwODQzLjExMzYyNDgsMTc5NDkw"
    "MDg1Ny4xMjkyMjIyLDE3OTQ5MDA4NzIuMDI5MjI0OSwxNzk0OTAwODg1LjgyNzgzODcs"
    "MTc5NDkwMDg5OS43NTE2MjgsMTc5NDkwMTEwNi41MzE1ODM1LDE3OTQ5MDExNjQuOTgz"
    "Nzc2LDE3OTQ5MDIzNDguODIyNzM5NiwxNzk0OTAyNDA3LjQ3NTQxMzYsMTc5NDkwMjQ0"
    "MC45MzM2NDQzLDE3OTQ5MDI0NzIuMzkwNzY4LDE3OTQ5MDI0ODYuMjU5NDg4LDE3OTQ5"
    "MDI1MDEuMDM4MjE1NiwxNzk0OTAyNTE0LjkwMDE3MTUsMTc5NDkwMjUzMC44MjIxODUz"
    "LDE3OTQ5MDI1NDUuMDgyOTE0NCwxNzk0OTAyNTU5LjYxNDgyOTMsMTc5NDkwMjU3NC42"
    "NDYyNDU3LDE3OTQ5MDI1OTAuMTQ1MDQwMywxNzk0OTAyNjA4LjIyMzI0MTMsMTc5NDkw"
    "MjYyNS4wOTA4MTY1LDE3OTQ5MDI2NDAuODkzOTc4LDE3OTQ5MDI2NTcuNTMwMjg0Niwx"
    "Nzk0OTAyNjcxLjMyNDI1MzYsMTc5NDkwMjY4NS4zMDA4ODE2LDE3OTQ5MDI2OTkuMzY1"
    "NTc5OCwxNzk0OTAyNzEzLjEzNDg1MDcsMTc5NDkwMjcyNy4zMTU2Mzc4LDE3OTQ5MDI3"
    "NDEuMTI0NDEwNCwxNzk0OTAyNzU0LjgzMDU4MjYsMTc5NDkwMjc2OC42OTE0NzEzLDE3"
    "OTQ5MDI3ODIuNTM5ODY4XSwidW50aWwiOjE3OTQ5MDIzNDguODIwMDc3NH0="
)


def reviewed_wbi_handoff():
    """Decode only the exact independently reviewed complete native MAX."""
    decision = _WBI_HANDOFF_DECISION
    if (
        not isinstance(decision, dict)
        or set(decision) != {
            "schema", "source", "evidence", "state_sha256",
            "native_sha256", "witness_sha256",
        }
        or type(decision["schema"]) is not int
        or decision["schema"] != 1
        or decision["source"] != SOURCE_ID
        or decision["state_sha256"] != _WBI_HANDOFF_SHA256
        or decision["native_sha256"] != _WBI_HANDOFF_NATIVE
        or decision["witness_sha256"] != _WBI_HANDOFF_WITNESSES
        or not isinstance(decision["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/373"
            r"#issuecomment-[1-9][0-9]*", decision["evidence"]
        )
    ):
        raise AdapterError("WBI native MAX handoff is unapproved", "access")
    try:
        raw = base64.b64decode(_WBI_HANDOFF_BYTES, validate=True)
        if (
            not 0 < len(raw) <= 16384
            or hashlib.sha256(raw).hexdigest() != _WBI_HANDOFF_SHA256
        ):
            raise ValueError()
        state = validate_budget(json.loads(raw))
        if (
            state["schema"] != 4 or not state["blocked"]
            or state["in_flight"]
            or state["refusal_scope"]["additional_holds"]
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AdapterError("WBI native MAX payload changed", "access") from None
    return state


_WBI_RESIDENCY_HANDOFF = {'decision': 'https://github.com/YouthOpps/data-pipeline/issues/482#issuecomment-7060577352', 'proof_sha256': '06b1caa16db717f9b9806ee15960b2a1e9d12bac61ae31047ce5f7a3dab84360', 'predecessor': [44770298747, 1, 'be-wbi'], 'predecessor_state_sha256': '012a6818ab8a3cc60a893ad3cecfdc141a132d4cf111dece2f17f0689392980c', 'native': {'artifacts': [{'digest': 'sha256:94befe8d5088a3664394d6d619478e50a0a2f1cf7a5328f9fed9308f6a62f245', 'id': 14701371262, 'name': 'wbi482-recovery-key-44888538876-1', 'size_in_bytes': 626}, {'digest': 'sha256:a0f522cf3afc725f79d7252ae7eb64fcd90c5151a29498d3bfd3be241f1b557c', 'id': 14700508837, 'name': 'wbi482-native-44888538876-1', 'size_in_bytes': 1631238}], 'branch': 'qa/482-wbi-residency-native', 'head': '95be85b99b0ef8904d5656b78881bdbfcc1b58a8', 'job': 136799059881, 'path': '.github/workflows/verify-wbi-residency-native.yml', 'run': 44888538876, 'workflow': 425833605}, 'state_sha256': '0838cdb3372f5a9d73a2e1c9750f255d0dfefad7ecb28f113879eba27cfe4117', 'state_base64': 'eyJzY2hlbWEiOjQsImZhbWlseSI6InlvdXRob3Bwcy1iZS13YmktcHVibGlzaGVyLXYxIiwibGVnYWN5X2hvbGRfaGlzdG9yeSI6InVubG9nZ2VkX2ZpZWxkc191bmtub3duIiwibGVnYWN5X2FjY2Vzc19oaXN0b3J5X3Vua25vd24iOnRydWUsImxlZ2FjeV9hY2Nlc3Nfb2JzZXJ2YXRpb25zIjpbXSwicmVzZWFyY2hfcHJvdmVuYW5jZSI6eyJiYXNlbGluZV9jb2xsZWN0aW9uX2xvY2siOiJOT19PUklHSU5BTF9DT0xMRUNUSU9OX0xPQ0tfSU5fQkFTRUxJTkUiLCJjb21wbGV0ZV9hdHRlbXB0cyI6MjcsImRhdGFfc291cmNlX3BpbiI6ImE2NzEzZTQxOWExZmRjYTMyOTlkNjBjZTgxMDgwZDRkMzIyM2JjMDciLCJleGVjdXRlZF9qb2JfY29tcGxldGlvbl91cHBlcl9ib3VuZHMiOlt7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE2NTIwMzMxMTgzLCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzg3NjM0MTk0OTMsInRpbWUiOjE3OTIwODAxMDkuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNjM3NjEwNjE2Miwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4NzIxMjU5NjEyLCJ0aW1lIjoxNzkyMDYxMDQzLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTY0MjIxOTM0NzgsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODcyMDA4NDEyOSwidGltZSI6MTc5MjA2NzYyMy4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE2MzA3NDY5NTczLCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzg2OTk1MzU3NjYsInRpbWUiOjE3OTIwNTAzNjAuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNjI1NTU3Mzc3OSwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4Njg0MjM1ODQwLCJ0aW1lIjoxNzkyMDQwNjkzLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTYxNTY2NTU5ODAsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODY1MzE4NTk2MiwidGltZSI6MTc5MjAyMDA4MC4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE2MDM2MTgyOTQ2LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzg2MTc0Nzk3MjIsInRpbWUiOjE3OTIwMDM1ODIuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNTg5MDA2ODI3Miwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4NTc1MTE5NTAxLCJ0aW1lIjoxNzkxOTg1OTk4LjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTU3NjI5OTgxODYsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODUzNDc1NjQzNSwidGltZSI6MTc5MTk2NzcxMC4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE1NzQxMDM0MjUxLCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzg1MzA1MjExNjMsInRpbWUiOjE3OTE5NjQxODguMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNTYwMDE5NzA0Miwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4NDg2MTU2Mzk1LCJ0aW1lIjoxNzkxOTM1MzM3LjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTU1MTc3NzAzODksImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODQ2MTU2NzUzMSwidGltZSI6MTc5MTkyMjgzMy4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE1MjI4MDc5OTA3LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6MzgzNzcwODkwMjgsInRpbWUiOjE3OTE4ODQxNzguMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNTAyOTU1MTA2Mywia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MzE1MDQzMTM2LCJ0aW1lIjoxNzkxODQzMjAxLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTQ3NDUzMDc3MTYsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODIyOTUzNDUwMCwidGltZSI6MTc5MTc5NzE5NS4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE0NjA2NTM4NTg1LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6MzgxODU0MDUzMDAsInRpbWUiOjE3OTE3NjI0NzQuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNDU0ODkyNDM4OSwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MTY2MTE2OTE3LCJ0aW1lIjoxNzkxNzQ1MzgwLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTQ0MzMwODMxOTQsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODEyNjkxNjExMywidGltZSI6MTc5MTcwOTUzMS4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE0MzQxMTc1NDM5LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6MzgwOTU3NTMxMTYsInRpbWUiOjE3OTE2NzU3NDQuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNDI4NTM2OTQ1Miwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MDc2ODAxNDE0LCJ0aW1lIjoxNzkxNjU3OTA1LjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTQyNzQ5NTA2NzksImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozODA3MTg0MDI4MSwidGltZSI6MTc5MTY1NDc2NC4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTE0MTc0MDg4Nzc2LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6MzgwMzg1NzI2MDYsInRpbWUiOjE3OTE2MjE3NTAuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExNDA3OTQ4MDYwOSwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM4MDA3NDcwMTA0LCJ0aW1lIjoxNzkxNTkwOTA4LjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTM5OTE2NDU0ODMsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozNzk4MTEzNTY1OCwidGltZSI6MTc5MTU3NDY3MS4wfSx7ImF0dGVtcHQiOjEsImpvYl9pZCI6MTEzNzU0Nzk1MjU5LCJraW5kIjoiYWN0dWFsX2pvYl9jb21wbGV0aW9uX1VQUEVSX0JPVU5EX25vdF9wdWJsaXNoZXJfd2lyZSIsInJ1bl9pZCI6Mzc5MDk5ODAzMTIsInRpbWUiOjE3OTE1Mzc4OTUuMH0seyJhdHRlbXB0IjoxLCJqb2JfaWQiOjExMzYyNDg4NjQ4NSwia2luZCI6ImFjdHVhbF9qb2JfY29tcGxldGlvbl9VUFBFUl9CT1VORF9ub3RfcHVibGlzaGVyX3dpcmUiLCJydW5faWQiOjM3ODY5NDk5NjMzLCJ0aW1lIjoxNzkxNTA5MzgwLjB9LHsiYXR0ZW1wdCI6MSwiam9iX2lkIjoxMTM2MjM5OTg2MzMsImtpbmQiOiJhY3R1YWxfam9iX2NvbXBsZXRpb25fVVBQRVJfQk9VTkRfbm90X3B1Ymxpc2hlcl93aXJlIiwicnVuX2lkIjozNzg2OTQ3ODMyNSwidGltZSI6MTc5MTUwOTE3Mi4wfV0sImZyZXNoX2hpc3Rvcnlfc2hhMjU2IjoiMGRlODE2MDY4YjY5MDNjNmRkZmE0ZGU5YjY1YmY4NWZlMzdjOGM5NmU0ZjlkNWQ5MWMzNTc1M2MwNTE1ZjJhNiIsIm1ldGFkYXRhX3NoYTI1NiI6IjA2NDhhYzI4YTVlMWU1ZjQ2Zjc3YTY5OWJhNTQ2YzJjYzZkMjc0NDgzMTEzNzg0Njg3ZWEzYjZlM2JlMDgyOTIiLCJvcmlnaW5hbF9sZWdhY3lfb2JzZXJ2YXRpb24iOnsiY29sbGVjdGlvbl9sb2NrX2JlZm9yZV9lbnRyeSI6eyJzdGF0dXMiOiJOT19PUklHSU5BTF9DT0xMRUNUSU9OX0xPQ0tfSU5fQkFTRUxJTkUifSwibmFtZXNwYWNlIjoid2JpLXB1Ymxpc2hlci1wYWNpbmciLCJzdGF0dXMiOiJBQlNFTlRfTk9UX1pFUk9fSElTVE9SWSJ9LCJzdGF0dXMiOiJST09UX1JFQURPTkxZX0ZVTEwyN19KT0JTX0xFR0FDWV9NRVRBREFUQV9NQVhfUFJPUE9TQUwiLCJ1bmtub3duX2NvbnN0cmFpbnRzIjoidW5sb2dnZWQgaGlzdG9yaWNhbCBwdWJsaXNoZXIgYWNjZXNzL3JlZnVzYWwvNDI5IGhlYWRlcnMgcmVtYWluIHVua25vd247IHplcm8gb2JzZXJ2ZWQgc3RyaWtlcyBvbmx5IGFmdGVyIGV4cGxpY2l0IHRyYW5zaXRpb24ifSwic3RhcnRzIjpbMTc5NTI1Mjk3MC4wNjYwODksMTc5NTI1Mjk4Ni4zNjU2MjU2LDE3OTUyNTMwMDIuODQ4ODIzMywxNzk1MjUzMDE5LjM4NDE3NjVdLCJpbnRlcnZhbCI6MTIuMiwidW50aWwiOjE3OTUyNTI1MzIuODUzMzc0NSwicGFnZV9nYXAiOjMwLCJjb21wbGV0ZWQiOjE3OTUyNTMwMjMuNTAyODgxOCwiaW52ZW50b3J5X2NvbXBsZXRlZCI6MTc5NTI1MjY0Mi42MjA1NzQ1LCJibG9ja2VkIjp0cnVlLCJpbl9mbGlnaHQiOmZhbHNlLCJsZWdhY3lfNDI5X2hpc3RvcnkiOiJ1bmtub3duIiwib2JzZXJ2ZWRfNDI5X3NpbmNlX3RyYW5zaXRpb24iOjAsInJhdGVfbGltaXRfdW50aWwiOjAsIm9ic2VydmVkX2F0IjoxNzk1MjUzMDIzLjUwMjg4MTgsInJlZnVzYWxfc2NvcGUiOnsiYWRkaXRpb25hbF9ob2xkcyI6W10sImRlY2lzaW9uIjp7ImRldGFpbF9wcmVmaXhlcyI6WyIvZnIvYWN0dWFsaXRlcy8iLCIvZnIvYWlkZS1zZXJ2aWNlLyJdLCJldmlkZW5jZSI6Imh0dHBzOi8vZ2l0aHViLmNvbS9Zb3V0aE9wcHMvZGF0YS1waXBlbGluZS9pc3N1ZXMvMzczI2lzc3VlY29tbWVudC02OTI1NjAwMTM3IiwiaGVsZF9zaGEyNTYiOiJhYTU0NDEwMTc2NWU2NWM3ZjMxMjY1MDFjY2U4ZTY3ODgxMDYyY2NiZWZlYzc3ZmIxOTMwY2Q1ZWZhYjI2ZTBjIiwibWlncmF0aW9uX3JldmlzaW9uIjoid2JpMzczLWNvbnRlbnQtc2NvcGUtdjEiLCJuYXRpdmVfcmV2aXNpb24iOiIwNmNjOGIwZGU4MzJiNGYwNTVjMzRhNDdiZjE2YzQzNWRjNzUzNjVkMDI2MGVlMjYwY2QzN2E4NzY2MDM0MDg2IiwicGF0aHMiOlsiL3JvYm90cy50eHQiLCIvZW4vYm91cnNlcyIsIi9lbi90YXhvbm9teS90ZXJtLzg0MyIsIi9lbi90YXhvbm9teS90ZXJtLzg0NCIsIi9lbi90YXhvbm9teS90ZXJtLzg0NiIsIi9lbi90YXhvbm9teS90ZXJtLzg0NyIsIi9mci9hcHBlbHMtYS1wcm9qZXRzIiwiL2FjdHVhbGl0ZXMiLCIvZnIvYWN0dWFsaXRlcyIsIi9mci9yc3MtZmVlZHMiXSwicmVzcG9uc2Vfc2hhMjU2IjoiYmVmZDRlNGE4NDNhNzBiNDE1ZThiOTlkNDc5ODIwNTk4YTMyMmU0YjQwNGM4YzYzZTI0NjRlMmJhYmU2ZDkxNyIsInNjaGVtYSI6MSwic291cmNlIjoiYmUtd2JpIn0sImV4dGVuc2lvbnMiOlt7ImV2aWRlbmNlIjoiaHR0cHM6Ly9naXRodWIuY29tL1lvdXRoT3Bwcy9kYXRhLXBpcGVsaW5lL2lzc3Vlcy8zNzMjaXNzdWVjb21tZW50LTY5NDE0MTc2MzgiLCJoZWxkX3NoYTI1NiI6ImFhNTQ0MTAxNzY1ZTY1YzdmMzEyNjUwMWNjZThlNjc4ODEwNjJjY2JlZmVjNzdmYjE5MzBjZDVlZmFiMjZlMGMiLCJtaWdyYXRpb25fcmV2aXNpb24iOiJ3YmkzNzMtY29udGVudC1zY29wZS12MiIsInBhcmVudF9kZWNpc2lvbl9zaGEyNTYiOiIwNGViYzkzZmI0ZGQ3ODg3ODJkYmFhNDQ2ODQxNGY0ODI3NjAzOTg4OWU5M2VmNGQyNTUxODM3ZDNiYWQ1MjZhIiwicGF0aHMiOlsiL2ZyL1dCSS1Xb3JsZCJdLCJwcmlvcl9qb3VybmFsX3NoYTI1NiI6IjI3MTY2N2YyZjM5NThhZjVjYzIxN2YxMTQzY2YzZmU1NmQ1M2M1ODVmYWI2MjBiY2FmMmFmZjJjZjE0NTljNTMiLCJyZXNwb25zZV9zaGEyNTYiOiJiZWZkNGU0YTg0M2E3MGI0MTVlOGI5OWQ0Nzk4MjA1OThhMzIyZTRiNDA0YzhjNjNlMjQ2NGUyYmFiZTZkOTE3Iiwic2NoZW1hIjoxLCJzb3VyY2UiOiJiZS13YmkifV0sImluaXRpYWxfcmVmdXNhbCI6eyJib2R5X3NoYTI1NiI6ImJlZmQ0ZTRhODQzYTcwYjQxNWU4Yjk5ZDQ3OTgyMDU5OGEzMjJlNGI0MDRjOGM2M2UyNDY0ZTJiYWJlNmQ5MTciLCJjb21wbGV0ZWQiOjE3OTQ4NTE3OTMuNzQ0Mjg2OCwibmF0aXZlX3JldmlzaW9uIjoiMDZjYzhiMGRlODMyYjRmMDU1YzM0YTQ3YmYxNmM0MzVkYzc1MzY1ZDAyNjBlZTI2MGNkMzdhODc2NjAzNDA4NiIsIm5vZGVfaWQiOjI1MTA4LCJvcmlnaW5zIjpbImh0dHBzOi8vd2JpLmJlIiwiaHR0cHM6Ly93d3cud2JpLmJlIl0sInJlc291cmNlX3NsdWciOiJvZmZyZS1zdGFnZS1jZW50cmUtaW50ZXJuYXRpb25hbC1yZWNoZXJjaGUtY2FuY2VyLWNpcmMiLCJzdGF0dXMiOjQwMywidXJsIjoiaHR0cHM6Ly93d3cud2JpLmJlL2ZyL2FjdHVhbGl0ZXMvb2ZmcmUtc3RhZ2UtY2VudHJlLWludGVybmF0aW9uYWwtcmVjaGVyY2hlLWNhbmNlci1jaXJjIn0sIm9yaWdpbmFsX2pvdXJuYWxfYmFzZTY0IjoiZXlKelkyaGxiV0VpT2pNc0ltWmhiV2xzZVNJNklubHZkWFJvYjNCd2N5MWlaUzEzWW1rdGNIVmliR2x6YUdWeUxYWXhJaXdpYkdWbllXTjVYMmh2YkdSZmFHbHpkRzl5ZVNJNkluVnViRzluWjJWa1gyWnBaV3hrYzE5MWJtdHViM2R1SWl3aWJHVm5ZV041WDJGalkyVnpjMTlvYVhOMGIzSjVYM1Z1YTI1dmQyNGlPblJ5ZFdVc0lteGxaMkZqZVY5aFkyTmxjM05mYjJKelpYSjJZWFJwYjI1eklqcGJYU3dpY21WelpXRnlZMmhmY0hKdmRtVnVZVzVqWlNJNmV5SmlZWE5sYkdsdVpWOWpiMnhzWldOMGFXOXVYMnh2WTJzaU9pSk9UMTlQVWtsSFNVNUJURjlEVDB4TVJVTlVTVTlPWDB4UFEwdGZTVTVmUWtGVFJVeEpUa1VpTENKamIyMXdiR1YwWlY5aGRIUmxiWEIwY3lJNk1qY3NJbVJoZEdGZmMyOTFjbU5sWDNCcGJpSTZJbUUyTnpFelpUUXhPV0V4Wm1SallUTXlPVGxrTmpCalpUZ3hNRGd3WkRSa016SXlNMkpqTURjaUxDSmxlR1ZqZFhSbFpGOXFiMkpmWTI5dGNHeGxkR2x2Ymw5MWNIQmxjbDlpYjNWdVpITWlPbHQ3SW1GMGRHVnRjSFFpT2pFc0ltcHZZbDlwWkNJNk1URTJOVEl3TXpNeE1UZ3pMQ0pyYVc1a0lqb2lZV04wZFdGc1gycHZZbDlqYjIxd2JHVjBhVzl1WDFWUVVFVlNYMEpQVlU1RVgyNXZkRjl3ZFdKc2FYTm9aWEpmZDJseVpTSXNJbkoxYmw5cFpDSTZNemczTmpNME1UazBPVE1zSW5ScGJXVWlPakUzT1RJd09EQXhNRGt1TUgwc2V5SmhkSFJsYlhCMElqb3hMQ0pxYjJKZmFXUWlPakV4TmpNM05qRXdOakUyTWl3aWEybHVaQ0k2SW1GamRIVmhiRjlxYjJKZlkyOXRjR3hsZEdsdmJsOVZVRkJGVWw5Q1QxVk9SRjl1YjNSZmNIVmliR2x6YUdWeVgzZHBjbVVpTENKeWRXNWZhV1FpT2pNNE56SXhNalU1TmpFeUxDSjBhVzFsSWpveE56a3lNRFl4TURRekxqQjlMSHNpWVhSMFpXMXdkQ0k2TVN3aWFtOWlYMmxrSWpveE1UWTBNakl4T1RNME56Z3NJbXRwYm1RaU9pSmhZM1IxWVd4ZmFtOWlYMk52YlhCc1pYUnBiMjVmVlZCUVJWSmZRazlWVGtSZmJtOTBYM0IxWW14cGMyaGxjbDkzYVhKbElpd2ljblZ1WDJsa0lqb3pPRGN5TURBNE5ERXlPU3dpZEdsdFpTSTZNVGM1TWpBMk56WXlNeTR3ZlN4N0ltRjBkR1Z0Y0hRaU9qRXNJbXB2WWw5cFpDSTZNVEUyTXpBM05EWTVOVGN6TENKcmFXNWtJam9pWVdOMGRXRnNYMnB2WWw5amIyMXdiR1YwYVc5dVgxVlFVRVZTWDBKUFZVNUVYMjV2ZEY5d2RXSnNhWE5vWlhKZmQybHlaU0lzSW5KMWJsOXBaQ0k2TXpnMk9UazFNelUzTmpZc0luUnBiV1VpT2pFM09USXdOVEF6TmpBdU1IMHNleUpoZEhSbGJYQjBJam94TENKcWIySmZhV1FpT2pFeE5qSTFOVFUzTXpjM09Td2lhMmx1WkNJNkltRmpkSFZoYkY5cWIySmZZMjl0Y0d4bGRHbHZibDlWVUZCRlVsOUNUMVZPUkY5dWIzUmZjSFZpYkdsemFHVnlYM2RwY21VaUxDSnlkVzVmYVdRaU9qTTROamcwTWpNMU9EUXdMQ0owYVcxbElqb3hOemt5TURRd05qa3pMakI5TEhzaVlYUjBaVzF3ZENJNk1Td2lhbTlpWDJsa0lqb3hNVFl4TlRZMk5UVTVPREFzSW10cGJtUWlPaUpoWTNSMVlXeGZhbTlpWDJOdmJYQnNaWFJwYjI1ZlZWQlFSVkpmUWs5VlRrUmZibTkwWDNCMVlteHBjMmhsY2w5M2FYSmxJaXdpY25WdVgybGtJam96T0RZMU16RTROVGsyTWl3aWRHbHRaU0k2TVRjNU1qQXlNREE0TUM0d2ZTeDdJbUYwZEdWdGNIUWlPakVzSW1wdllsOXBaQ0k2TVRFMk1ETTJNVGd5T1RRMkxDSnJhVzVrSWpvaVlXTjBkV0ZzWDJwdllsOWpiMjF3YkdWMGFXOXVYMVZRVUVWU1gwSlBWVTVFWDI1dmRGOXdkV0pzYVhOb1pYSmZkMmx5WlNJc0luSjFibDlwWkNJNk16ZzJNVGMwTnprM01qSXNJblJwYldVaU9qRTNPVEl3TURNMU9ESXVNSDBzZXlKaGRIUmxiWEIwSWpveExDSnFiMkpmYVdRaU9qRXhOVGc1TURBMk9ESTNNaXdpYTJsdVpDSTZJbUZqZEhWaGJGOXFiMkpmWTI5dGNHeGxkR2x2Ymw5VlVGQkZVbDlDVDFWT1JGOXViM1JmY0hWaWJHbHphR1Z5WDNkcGNtVWlMQ0p5ZFc1ZmFXUWlPak00TlRjMU1URTVOVEF4TENKMGFXMWxJam94TnpreE9UZzFPVGs0TGpCOUxIc2lZWFIwWlcxd2RDSTZNU3dpYW05aVgybGtJam94TVRVM05qSTVPVGd4T0RZc0ltdHBibVFpT2lKaFkzUjFZV3hmYW05aVgyTnZiWEJzWlhScGIyNWZWVkJRUlZKZlFrOVZUa1JmYm05MFgzQjFZbXhwYzJobGNsOTNhWEpsSWl3aWNuVnVYMmxrSWpvek9EVXpORGMxTmpRek5Td2lkR2x0WlNJNk1UYzVNVGsyTnpjeE1DNHdmU3g3SW1GMGRHVnRjSFFpT2pFc0ltcHZZbDlwWkNJNk1URTFOelF4TURNME1qVXhMQ0pyYVc1a0lqb2lZV04wZFdGc1gycHZZbDlqYjIxd2JHVjBhVzl1WDFWUVVFVlNYMEpQVlU1RVgyNXZkRjl3ZFdKc2FYTm9aWEpmZDJseVpTSXNJbkoxYmw5cFpDSTZNemcxTXpBMU1qRXhOak1zSW5ScGJXVWlPakUzT1RFNU5qUXhPRGd1TUgwc2V5SmhkSFJsYlhCMElqb3hMQ0pxYjJKZmFXUWlPakV4TlRZd01ERTVOekEwTWl3aWEybHVaQ0k2SW1GamRIVmhiRjlxYjJKZlkyOXRjR3hsZEdsdmJsOVZVRkJGVWw5Q1QxVk9SRjl1YjNSZmNIVmliR2x6YUdWeVgzZHBjbVVpTENKeWRXNWZhV1FpT2pNNE5EZzJNVFUyTXprMUxDSjBhVzFsSWpveE56a3hPVE0xTXpNM0xqQjlMSHNpWVhSMFpXMXdkQ0k2TVN3aWFtOWlYMmxrSWpveE1UVTFNVGMzTnpBek9Ea3NJbXRwYm1RaU9pSmhZM1IxWVd4ZmFtOWlYMk52YlhCc1pYUnBiMjVmVlZCUVJWSmZRazlWVGtSZmJtOTBYM0IxWW14cGMyaGxjbDkzYVhKbElpd2ljblZ1WDJsa0lqb3pPRFEyTVRVMk56VXpNU3dpZEdsdFpTSTZNVGM1TVRreU1qZ3pNeTR3ZlN4N0ltRjBkR1Z0Y0hRaU9qRXNJbXB2WWw5cFpDSTZNVEUxTWpJNE1EYzVPVEEzTENKcmFXNWtJam9pWVdOMGRXRnNYMnB2WWw5amIyMXdiR1YwYVc5dVgxVlFVRVZTWDBKUFZVNUVYMjV2ZEY5d2RXSnNhWE5vWlhKZmQybHlaU0lzSW5KMWJsOXBaQ0k2TXpnek56Y3dPRGt3TWpnc0luUnBiV1VpT2pFM09URTRPRFF4TnpndU1IMHNleUpoZEhSbGJYQjBJam94TENKcWIySmZhV1FpT2pFeE5UQXlPVFUxTVRBMk15d2lhMmx1WkNJNkltRmpkSFZoYkY5cWIySmZZMjl0Y0d4bGRHbHZibDlWVUZCRlVsOUNUMVZPUkY5dWIzUmZjSFZpYkdsemFHVnlYM2RwY21VaUxDSnlkVzVmYVdRaU9qTTRNekUxTURRek1UTTJMQ0owYVcxbElqb3hOemt4T0RRek1qQXhMakI5TEhzaVlYUjBaVzF3ZENJNk1Td2lhbTlpWDJsa0lqb3hNVFEzTkRVek1EYzNNVFlzSW10cGJtUWlPaUpoWTNSMVlXeGZhbTlpWDJOdmJYQnNaWFJwYjI1ZlZWQlFSVkpmUWs5VlRrUmZibTkwWDNCMVlteHBjMmhsY2w5M2FYSmxJaXdpY25WdVgybGtJam96T0RJeU9UVXpORFV3TUN3aWRHbHRaU0k2TVRjNU1UYzVOekU1TlM0d2ZTeDdJbUYwZEdWdGNIUWlPakVzSW1wdllsOXBaQ0k2TVRFME5qQTJOVE00TlRnMUxDSnJhVzVrSWpvaVlXTjBkV0ZzWDJwdllsOWpiMjF3YkdWMGFXOXVYMVZRVUVWU1gwSlBWVTVFWDI1dmRGOXdkV0pzYVhOb1pYSmZkMmx5WlNJc0luSjFibDlwWkNJNk16Z3hPRFUwTURVek1EQXNJblJwYldVaU9qRTNPVEUzTmpJME56UXVNSDBzZXlKaGRIUmxiWEIwSWpveExDSnFiMkpmYVdRaU9qRXhORFUwT0RreU5ETTRPU3dpYTJsdVpDSTZJbUZqZEhWaGJGOXFiMkpmWTI5dGNHeGxkR2x2Ymw5VlVGQkZVbDlDVDFWT1JGOXViM1JmY0hWaWJHbHphR1Z5WDNkcGNtVWlMQ0p5ZFc1ZmFXUWlPak00TVRZMk1URTJPVEUzTENKMGFXMWxJam94TnpreE56UTFNemd3TGpCOUxIc2lZWFIwWlcxd2RDSTZNU3dpYW05aVgybGtJam94TVRRME16TXdPRE14T1RRc0ltdHBibVFpT2lKaFkzUjFZV3hmYW05aVgyTnZiWEJzWlhScGIyNWZWVkJRUlZKZlFrOVZUa1JmYm05MFgzQjFZbXhwYzJobGNsOTNhWEpsSWl3aWNuVnVYMmxrSWpvek9ERXlOamt4TmpFeE15d2lkR2x0WlNJNk1UYzVNVGN3T1RVek1TNHdmU3g3SW1GMGRHVnRjSFFpT2pFc0ltcHZZbDlwWkNJNk1URTBNelF4TVRjMU5ETTVMQ0pyYVc1a0lqb2lZV04wZFdGc1gycHZZbDlqYjIxd2JHVjBhVzl1WDFWUVVFVlNYMEpQVlU1RVgyNXZkRjl3ZFdKc2FYTm9aWEpmZDJseVpTSXNJbkoxYmw5cFpDSTZNemd3T1RVM05UTXhNVFlzSW5ScGJXVWlPakUzT1RFMk56VTNORFF1TUgwc2V5SmhkSFJsYlhCMElqb3hMQ0pxYjJKZmFXUWlPakV4TkRJNE5UTTJPVFExTWl3aWEybHVaQ0k2SW1GamRIVmhiRjlxYjJKZlkyOXRjR3hsZEdsdmJsOVZVRkJGVWw5Q1QxVk9SRjl1YjNSZmNIVmliR2x6YUdWeVgzZHBjbVVpTENKeWRXNWZhV1FpT2pNNE1EYzJPREF4TkRFMExDSjBhVzFsSWpveE56a3hOalUzT1RBMUxqQjlMSHNpWVhSMFpXMXdkQ0k2TVN3aWFtOWlYMmxrSWpveE1UUXlOelE1TlRBMk56a3NJbXRwYm1RaU9pSmhZM1IxWVd4ZmFtOWlYMk52YlhCc1pYUnBiMjVmVlZCUVJWSmZRazlWVGtSZmJtOTBYM0IxWW14cGMyaGxjbDkzYVhKbElpd2ljblZ1WDJsa0lqb3pPREEzTVRnME1ESTRNU3dpZEdsdFpTSTZNVGM1TVRZMU5EYzJOQzR3ZlN4N0ltRjBkR1Z0Y0hRaU9qRXNJbXB2WWw5cFpDSTZNVEUwTVRjME1EZzROemMyTENKcmFXNWtJam9pWVdOMGRXRnNYMnB2WWw5amIyMXdiR1YwYVc5dVgxVlFVRVZTWDBKUFZVNUVYMjV2ZEY5d2RXSnNhWE5vWlhKZmQybHlaU0lzSW5KMWJsOXBaQ0k2TXpnd016ZzFOekkyTURZc0luUnBiV1VpT2pFM09URTJNakUzTlRBdU1IMHNleUpoZEhSbGJYQjBJam94TENKcWIySmZhV1FpT2pFeE5EQTNPVFE0TURZd09Td2lhMmx1WkNJNkltRmpkSFZoYkY5cWIySmZZMjl0Y0d4bGRHbHZibDlWVUZCRlVsOUNUMVZPUkY5dWIzUmZjSFZpYkdsemFHVnlYM2RwY21VaUxDSnlkVzVmYVdRaU9qTTRNREEzTkRjd01UQTBMQ0owYVcxbElqb3hOemt4TlRrd09UQTRMakI5TEhzaVlYUjBaVzF3ZENJNk1Td2lhbTlpWDJsa0lqb3hNVE01T1RFMk5EVTBPRE1zSW10cGJtUWlPaUpoWTNSMVlXeGZhbTlpWDJOdmJYQnNaWFJwYjI1ZlZWQlFSVkpmUWs5VlRrUmZibTkwWDNCMVlteHBjMmhsY2w5M2FYSmxJaXdpY25WdVgybGtJam96TnprNE1URXpOVFkxT0N3aWRHbHRaU0k2TVRjNU1UVTNORFkzTVM0d2ZTeDdJbUYwZEdWdGNIUWlPakVzSW1wdllsOXBaQ0k2TVRFek56VTBOemsxTWpVNUxDSnJhVzVrSWpvaVlXTjBkV0ZzWDJwdllsOWpiMjF3YkdWMGFXOXVYMVZRVUVWU1gwSlBWVTVFWDI1dmRGOXdkV0pzYVhOb1pYSmZkMmx5WlNJc0luSjFibDlwWkNJNk16YzVNRGs1T0RBek1USXNJblJwYldVaU9qRTNPVEUxTXpjNE9UVXVNSDBzZXlKaGRIUmxiWEIwSWpveExDSnFiMkpmYVdRaU9qRXhNell5TkRnNE5qUTROU3dpYTJsdVpDSTZJbUZqZEhWaGJGOXFiMkpmWTI5dGNHeGxkR2x2Ymw5VlVGQkZVbDlDVDFWT1JGOXViM1JmY0hWaWJHbHphR1Z5WDNkcGNtVWlMQ0p5ZFc1ZmFXUWlPak0zT0RZNU5EazVOak16TENKMGFXMWxJam94TnpreE5UQTVNemd3TGpCOUxIc2lZWFIwWlcxd2RDSTZNU3dpYW05aVgybGtJam94TVRNMk1qTTVPVGcyTXpNc0ltdHBibVFpT2lKaFkzUjFZV3hmYW05aVgyTnZiWEJzWlhScGIyNWZWVkJRUlZKZlFrOVZUa1JmYm05MFgzQjFZbXhwYzJobGNsOTNhWEpsSWl3aWNuVnVYMmxrSWpvek56ZzJPVFEzT0RNeU5Td2lkR2x0WlNJNk1UYzVNVFV3T1RFM01pNHdmVjBzSW1aeVpYTm9YMmhwYzNSdmNubGZjMmhoTWpVMklqb2lNR1JsT0RFMk1EWTRZalk1TUROak5tUmtabUUwWkdVNVlqWTFZbVk0TldabE16ZGpPR001Tm1VMFpqbGtOV1E1TVdNek5UYzFNMk13TlRFMVpqSmhOaUlzSW0xbGRHRmtZWFJoWDNOb1lUSTFOaUk2SWpBMk5EaGhZekk0WVRWbE1XVTFaalEyWmpjM1lUWTVPV0poTlRRMll6SmpZelprTWpjME5EZ3pNVEV6TnpnME5qZzNaV0V6WWpabE0ySmxNRGd5T1RJaUxDSnZjbWxuYVc1aGJGOXNaV2RoWTNsZmIySnpaWEoyWVhScGIyNGlPbnNpWTI5c2JHVmpkR2x2Ymw5c2IyTnJYMkpsWm05eVpWOWxiblJ5ZVNJNmV5SnpkR0YwZFhNaU9pSk9UMTlQVWtsSFNVNUJURjlEVDB4TVJVTlVTVTlPWDB4UFEwdGZTVTVmUWtGVFJVeEpUa1VpZlN3aWJtRnRaWE53WVdObElqb2lkMkpwTFhCMVlteHBjMmhsY2kxd1lXTnBibWNpTENKemRHRjBkWE1pT2lKQlFsTkZUbFJmVGs5VVgxcEZVazlmU0VsVFZFOVNXU0o5TENKemRHRjBkWE1pT2lKU1QwOVVYMUpGUVVSUFRreFpYMFpWVEV3eU4xOUtUMEpUWDB4RlIwRkRXVjlOUlZSQlJFRlVRVjlOUVZoZlVGSlBVRTlUUVV3aUxDSjFibXR1YjNkdVgyTnZibk4wY21GcGJuUnpJam9pZFc1c2IyZG5aV1FnYUdsemRHOXlhV05oYkNCd2RXSnNhWE5vWlhJZ1lXTmpaWE56TDNKbFpuVnpZV3d2TkRJNUlHaGxZV1JsY25NZ2NtVnRZV2x1SUhWdWEyNXZkMjQ3SUhwbGNtOGdiMkp6WlhKMlpXUWdjM1J5YVd0bGN5QnZibXg1SUdGbWRHVnlJR1Y0Y0d4cFkybDBJSFJ5WVc1emFYUnBiMjRpZlN3aWMzUmhjblJ6SWpwYk1UYzVORGcxTVRjM05pNHdOREUwTWpJNExERTNPVFE0TlRFM09USXVOVEk1TURJNFhTd2lhVzUwWlhKMllXd2lPakV5TGpJc0luVnVkR2xzSWpveE56azBPRFV4TnpBMUxqQTJNall4TnpVc0luQmhaMlZmWjJGd0lqb3pNQ3dpWTI5dGNHeGxkR1ZrSWpveE56azBPRFV4TnprekxqYzBOREk0Tmpnc0ltbHVkbVZ1ZEc5eWVWOWpiMjF3YkdWMFpXUWlPakUzT1RNeE9UWTFNVEV1TURRMU5qTTJMQ0ppYkc5amEyVmtJanAwY25WbExDSnBibDltYkdsbmFIUWlPbVpoYkhObExDSnNaV2RoWTNsZk5ESTVYMmhwYzNSdmNua2lPaUoxYm10dWIzZHVJaXdpYjJKelpYSjJaV1JmTkRJNVgzTnBibU5sWDNSeVlXNXphWFJwYjI0aU9qQXNJbkpoZEdWZmJHbHRhWFJmZFc1MGFXd2lPakFzSW05aWMyVnlkbVZrWDJGMElqb3hOemswT0RVeE56a3pMamMwTkRJNE5qaDkifX0='}


def apply_wbi_residency_handoff(incoming, latest):
    """Carry the exact reviewed native budget above the original production owner."""
    approved = _WBI_RESIDENCY_HANDOFF
    if not isinstance(approved, dict) or set(approved) != {
        'decision', 'proof_sha256', 'predecessor', 'predecessor_state_sha256',
        'native', 'state_sha256', 'state_base64',
    }:
        raise AdapterError('WBI residency handback approval is absent', 'access')
    if not (re.fullmatch(r'https://github\.com/YouthOpps/data-pipeline/issues/482#issuecomment-[1-9][0-9]*',
                         approved['decision'])
            and re.fullmatch(r'[a-f0-9]{64}', approved['proof_sha256'])):
        raise AdapterError('WBI residency handback decision differs', 'access')
    try:
        raw = base64.b64decode(approved['state_base64'], validate=True)
        if len(raw) > 16384:
            raise ValueError()
        floor = validate_budget(json.loads(raw))
        compact = json.dumps(floor, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(compact).hexdigest() != approved['state_sha256']:
            raise ValueError()
        if (floor['schema'] != 4 or not floor['blocked'] or floor['in_flight']
                or floor['refusal_scope']['additional_holds']):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AdapterError('WBI residency native journal differs', 'access') from None
    if tuple(approved['predecessor']) == latest:
        # The original strict decoder has already verified this production artifact.
        compact = json.dumps(incoming, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(compact).hexdigest() != approved['predecessor_state_sha256']:
            raise AdapterError('Pinned WBI predecessor journal differs', 'access')
        verify_wbi_residency_owner(approved['native'])
        return merge_budget(incoming, floor)
    retained_scope = incoming.get('refusal_scope', {})
    scopes = ('decision', 'initial_refusal', 'original_journal_base64', 'extensions')
    values = ('until', 'interval', 'page_gap', 'completed', 'inventory_completed',
              'observed_429_since_transition', 'rate_limit_until', 'observed_at')
    if (incoming.get('schema') != 4 or not incoming['blocked']
            or any(retained_scope.get(key) != floor['refusal_scope'][key] for key in scopes)
            or any(incoming[key] < floor[key] for key in values)
            or any(stamp > incoming['observed_at'] - 60 and stamp not in incoming['starts']
                   for stamp in floor['starts'])):
        raise AdapterError('Newest canonical WBI artifact has not inherited the native floor', 'access')
    return incoming


def verify_wbi_residency_owner(native):
    """Bind the original, retired QA job and both authentic handback archives."""
    def matches(row, **fields):
        return all(type(row.get(key)) is type(value) and row[key] == value for key, value in fields.items())

    workflow = workflow_api(f'/actions/workflows/{native["workflow"]}')
    if not matches(workflow, id=native['workflow'], path=native['path'], state='disabled_manually'):
        raise AdapterError('WBI native workflow must be retired before handback', 'access')
    inventory = workflow_api(f'/actions/workflows/{native["workflow"]}/runs?per_page=2')
    endpoint = f'/actions/runs/{native["run"]}/attempts/1'
    exact = workflow_api(endpoint)
    if (inventory['total_count'] != 1 or len(inventory['workflow_runs']) != 1
            or inventory['workflow_runs'][0]['id'] != native['run']
            or not matches(exact, id=native['run'], workflow_id=native['workflow'],
                           path=native['path'], run_attempt=1, event='push', status='completed',
                           conclusion='success', head_sha=native['head'], head_branch=native['branch'])
            or exact['actor']['id'] != 12278069 or exact['actor']['type'] != 'User'
            or exact['triggering_actor']['id'] != 12278069 or exact['triggering_actor']['type'] != 'User'
            or exact['repository']['full_name'] != 'YouthOpps/data-pipeline'
            or exact['head_repository']['full_name'] != 'YouthOpps/data-pipeline'):
        raise AdapterError('WBI native original attempt differs', 'access')
    jobs = workflow_api(endpoint + '/jobs?per_page=2')
    if (jobs['total_count'] != 1 or len(jobs['jobs']) != 1
            or not matches(jobs['jobs'][0], id=native['job'], run_id=native['run'], run_attempt=1,
                           head_sha=native['head'], head_branch=native['branch'], status='completed',
                           conclusion='success', name='Verify WBI native residency')):
        raise AdapterError('WBI native original job differs', 'access')
    steps = {item['name']: item['conclusion'] for item in jobs['jobs'][0]['steps']}
    if any(steps.get(name) != 'success' for name in (
        'Upload the before-wire recovery public key',
        'Run the original WBI nonpublishing test under reviewed custody',
        'Upload encrypted result and exact owned state evidence',
    )):
        raise AdapterError('WBI native outcome or custody is incomplete', 'access')
    artifacts = workflow_api(f'/actions/runs/{native["run"]}/artifacts?per_page=3')
    if artifacts['total_count'] != 2 or len(artifacts['artifacts']) != 2:
        raise AdapterError('WBI native artifact inventory differs', 'access')
    for expected in native['artifacts']:
        found = workflow_api(f'/actions/artifacts/{expected["id"]}')
        if (not matches(found, **expected, expired=False) or found not in artifacts['artifacts']
                or not matches(found['workflow_run'], id=native['run'], head_sha=native['head'],
                               head_branch=native['branch'])):
            raise AdapterError('Exact WBI native archive differs', 'access')


def restore_family_artifact():
    """Exact reviewed zero-artifact ancestry, then strict newest owned state."""
    global _RESTORED, _STATE_TRUSTED, _RESTORED_LATEST, _RESTORED_HISTORY
    global _VERIFIED_REMOTE_CREATE, _STARTUP_QUIET_UNTIL
    _STATE_TRUSTED = False
    _STARTUP_QUIET_UNTIL = None
    latest = latest_family_run()
    raw_latest = latest
    run_id, attempt, source = latest
    if run_id in _LEGACY_ATTEMPTS:
        raise AdapterError("Modern owned WBI artifact required", "access")
    else:
        latest, value, recovery_bound = restoration_owner(latest)
        run_id, attempt, source = latest
        artifacts = value.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or value.get("total_count") != len(artifacts)
            or len(artifacts) > 100
        ):
            raise AdapterError("Incomplete newest artifact inventory", "access")
        matches = [
            item
            for item in artifacts
            if item.get("name") == f"{_ARTIFACT_NAME}-{attempt}"
        ]
        if len(matches) != 1:
            raise AdapterError(
                "Newest state missing; no older fallback", "access"
            )
        item = matches[0]
        if (
            type(item.get("id")) is not int
            or item["id"] <= 0
            or item.get("expired") is not False
            or item.get("workflow_run", {}).get("id") != run_id
            or item.get("workflow_run", {}).get("head_sha")
            != _ATTEMPT_HEADS.get((run_id, attempt))
            or type(item.get("size_in_bytes")) is not int
            or not 0 < item["size_in_bytes"] <= 32768
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", ""))
        ):
            raise AdapterError("Newest artifact binding invalid", "access")
        incoming = preserve_recovery_bounds(
            download_inert_artifact(latest, item["id"], item["digest"]),
            recovery_bound,
        )
    # This never replaces ownership: the exact modern decoder ran above.
    incoming = apply_wbi_residency_handoff(incoming, raw_latest)
    incoming = merge_budget(incoming, reviewed_wbi_handoff())
    _VERIFIED_REMOTE_CREATE = incoming
    try:
        with locked_budget():
            local = load_budget()
            if (local["schema"] == 3 and local["blocked"]
                    and _WBI_SCOPE_DECISION is not None):
                local = wbi_migrate_scope(_JOURNAL_ACK[1])
            local = wbi_upgrade_scope(local, _JOURNAL_ACK[1])
            combined = merge_budget(local, incoming)
            combined["until"] = max(combined["until"], time.time() + 60)
            write_budget(combined)
            if combined["in_flight"]:
                raise AdapterError("Unsettled newest publisher state", "access")
    finally:
        _VERIFIED_REMOTE_CREATE = None
    _RESTORED_LATEST = raw_latest
    _RESTORED_HISTORY = (
        copy.deepcopy(_SCANNED_HISTORY),
        copy.deepcopy(_SCANNED_JOB_DIGESTS),
        copy.deepcopy(_ATTEMPT_HEADS),
    )
    _STARTUP_QUIET_UNTIL = time.monotonic() + 60
    _RESTORED = _STATE_TRUSTED = True



class PhaseExpired(AdapterError):
    """A fixed admission deadline cannot be handled as a network retry."""

    def __init__(self):
        super().__init__("Fixed lifecycle admission exhausted", "access")



def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True



def legacy_collection_observation():
    """The exact original WBI collector has no coordination lock."""
    return {"status": "NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE"}


def authenticate_current_job():
    """Authenticate current hosted job before trusting or creating state."""
    check_deadline()
    path = os.environ.get("WBI_PACING_ARTIFACT_PATH")
    if (
        not os.environ.get("GITHUB_TOKEN")
        or not path
        or not os.environ.get("GITHUB_OUTPUT")
    ):
        raise AdapterError("Hosted continuity configuration missing", "access")
    checked_artifact_path(path)
    run, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    workflow = (
        "YouthOpps/data-pipeline/.github/workflows/fetch-"
        + SOURCE_ID + ".yml@refs/heads/main"
    )
    if (
        os.environ.get("GITHUB_JOB") != "collect"
        or os.environ.get("GITHUB_WORKFLOW_REF") != workflow
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise AdapterError("Untrusted current hosted job context", "access")
    value = workflow_api(
        f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = value.get("jobs")
    if (
        type(value.get("total_count")) is not int
        or value["total_count"] != 1
        or not isinstance(jobs, list)
        or len(jobs) != 1
    ):
        raise AdapterError("Incomplete current hosted job", "access")
    job = jobs[0]
    if (
        not isinstance(job, dict)
        or type(job.get("id")) is not int or job["id"] <= 0
        or job.get("name") != "collect"
        or job.get("workflow_name") != "fetch-" + SOURCE_ID
        or type(job.get("run_id")) is not int or job["run_id"] != run
        or type(job.get("run_attempt")) is not int
        or job["run_attempt"] != attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("status") != "in_progress"
        or "conclusion" not in job or job["conclusion"] is not None
        or type(job.get("runner_id")) is not int or job["runner_id"] <= 0
        or not isinstance(job.get("started_at"), str)
    ):
        raise AdapterError("Current hosted job identity mismatch", "access")
    try:
        started = datetime.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.tzinfo is None or started.utcoffset().total_seconds() != 0:
            raise ValueError("Current job start must be UTC")
    except (ValueError, TypeError, OverflowError):
        raise AdapterError(
            "Invalid current hosted job start", "access"
        ) from None
    check_deadline()


def start_source_phase():
    """One source owner reserves original job proof and publication tails."""
    global _SOURCE_END, _PHASE_END, _PHASE_NAME
    if _PHASE_NAME != "source":
        _SOURCE_END = min(
            _SOURCE_END, time.monotonic() + COLLECTION_TIMEOUT,
            _RUN_END - _PUBLICATION_SECONDS - _FAILURE_SECONDS
            - EXPORT_TIMEOUT,
        )
        _PHASE_END, _PHASE_NAME = _SOURCE_END, "source"
    clamp_publication_window()
    check_deadline()


def page(url):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    inventory_paths = {
        urllib.parse.urlsplit(value).path for value in _WBI_DYNAMIC_ROOTS
    }
    _INVENTORY_ACTIVE = previous or (
        urllib.parse.urlsplit(url).path in inventory_paths
    )
    try:
        check_deadline()
        delay = check_robots(url)
        root = PublisherHTML(fetch_source(url, min_interval=delay)).root
        check_deadline()
        return root
    finally:
        _INVENTORY_ACTIVE = previous


def main():
    global _PUBLISHING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish", action="store_true",
        help="Write only this source using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    _PUBLISHING = args.publish
    with execution():
        proof_complete = not args.publish
        try:
            prepare_collection()
            if args.publish:
                enter_phase("proof", 30)
                publication_window()
                clamp_publication_window()
                proof_complete = True
            return run_body(args.publish)
        finally:
            primary = sys.exc_info()[0] is not None
            if not proof_complete:
                fail_publisher_cleanup()
            try:
                enter_phase("export", EXPORT_TIMEOUT)
                export_family_artifact()
            except BaseException:
                fail_publisher_cleanup()
                if not primary:
                    raise


if __name__ == "__main__":
    raise SystemExit(main())
