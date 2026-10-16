"""Collect the reviewed OeAD programme with durable publisher pacing."""

import argparse
import base64
from collections import deque
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html import unescape
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import signal
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "at-oead-ernst-mach"
SOURCE_URL = "https://studyinaustria.at/en/news/article/2026/08/ernst-mach-stipendium-weltweit-bewerbung-bis-1-dezember-2026"
WEBSITE_URL = "https://studyinaustria.at/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "AT"
PUBLISHER_TYPE = "public-institution"
ATTRIBUTION = "© OeAD"
CATEGORIES = ('scholarships', 'internships', 'volunteering', 'fellowships', 'training', 'competitions', 'grants', 'jobs', 'other')
_GITHUB = 'https://api.github.com/repos/YouthOpps/data-source'
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"

_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-at-oead-ernst-mach-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_ID = 378390045
_ARTIFACT_NAME = "at-oead-ernst-mach-pacing-state"
_STATE_SCHEMA = 3
COLLECTION_TIMEOUT = 900
PUBLICATION_RESERVE = 180
RUN_TIMEOUT = 1290
EXPORT_TIMEOUT = 30
_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_INITIALIZING = False
_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False
_FAILED_CONSTRAINTS = None
_PUBLISHER_IN_FLIGHT = False
_INVENTORY_COMPLETED_MONOTONIC = None
_RUN_END = _PHASE_END = _PHASE_NAME = None
_ROBOTS = {}
_HISTORY_COMPLETED = []
_FIRST_PUBLISHER_REQUEST = True
_GITHUB_BUDGETS = {}
# Sole reviewed legacy exception; absent approval never creates source state.
_LEGACY_TRANSITION = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/272#issuecomment-6201692246',
 'family': 'youthopps-at-oead-ernst-mach-publisher-v1',
 'history_sha256': '7cb7397a935a35f3c6f8288853c3fcbf00c4d81646153392617ae5ac74364761',
 'schema': 1,
 'state': {'blocked': False,
           'completed': 1792079337.0,
           'family': 'youthopps-at-oead-ernst-mach-publisher-v1',
           'in_flight': False,
           'interval': 12.2,
           'last_inventory_completed': 1792079337.0,
           'legacy_rate_history_unknown': True,
           'not_before': 1792095959.933868,
           'observed_at': 1792095959.933868,
           'page_interval': 30,
           'rate_limit_strikes': 0,
           'rate_limit_until': 0,
           'schema': 3,
           'starts': [1791460673.0,
                      1791473308.0,
                      1791485297.0,
                      1791525433.0,
                      1791551188.0,
                      1791585253.0,
                      1791610771.0,
                      1791634933.0,
                      1791668009.0,
                      1791696920.0,
                      1791722331.0,
                      1791755308.0,
                      1791783929.0,
                      1791817945.0,
                      1791851568.0,
                      1791900239.0,
                      1791931046.0,
                      1791952980.0,
                      1791969393.0,
                      1791978087.0,
                      1791997375.0,
                      1792013705.0,
                      1792038317.0,
                      1792047060.0,
                      1792060616.0,
                      1792067127.0,
                      1792079337.0]}}

_LEGACY_JOB_PINS = {'37773510557': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '9f4123d4617035c049594bd698b35425273031a7',
                 'jobs': [{'completed_at': '2026-10-08T11:57:53Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '9f4123d4617035c049594bd698b35425273031a7'
                           ),
                           'id': 113298591406,
                           'run_attempt': 1,
                           'run_id': 37773510557,
                           'started_at': '2026-10-08T11:57:29Z',
                           'status': 'completed'}]},
 '37800943195': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'e10cefdc2e0f593b726e6954e12495bbcd3cf0ab',
                 'jobs': [{'completed_at': '2026-10-08T15:28:28Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'e10cefdc2e0f593b726e6954e12495bbcd3cf0ab'
                           ),
                           'id': 113392678721,
                           'run_attempt': 1,
                           'run_id': 37800943195,
                           'started_at': '2026-10-08T15:27:42Z',
                           'status': 'completed'}]},
 '37826996038': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '6429b6913fd6bb208ccabb79105336c7e31e16b7',
                 'jobs': [{'completed_at': '2026-10-08T18:48:17Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '6429b6913fd6bb208ccabb79105336c7e31e16b7'
                           ),
                           'id': 113482284875,
                           'run_attempt': 1,
                           'run_id': 37826996038,
                           'started_at': '2026-10-08T18:47:30Z',
                           'status': 'completed'}]},
 '37890972123': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'c96e27b566a8cf8ee2b8f33515b5a027e4b108fb',
                 'jobs': [{'completed_at': '2026-10-09T05:57:13Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'c96e27b566a8cf8ee2b8f33515b5a027e4b108fb'
                           ),
                           'id': 113691586275,
                           'run_attempt': 1,
                           'run_id': 37890972123,
                           'started_at': '2026-10-09T05:56:26Z',
                           'status': 'completed'}]},
 '37934413741': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa',
                 'jobs': [{'completed_at': '2026-10-09T13:06:28Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa'
                           ),
                           'id': 113832761442,
                           'run_attempt': 1,
                           'run_id': 37934413741,
                           'started_at': '2026-10-09T13:05:41Z',
                           'status': 'completed'}]},
 '37999854979': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                 'jobs': [{'completed_at': '2026-10-09T22:34:13Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '2d6491ddf2a37047efc7b996f190f648e647c0c6'
                           ),
                           'id': 114055003861,
                           'run_attempt': 1,
                           'run_id': 37999854979,
                           'started_at': '2026-10-09T22:33:28Z',
                           'status': 'completed'}]},
 '38028203615': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '6e5922277de22b96874c23a705a7d838512c6caf',
                 'jobs': [{'completed_at': '2026-10-10T05:39:31Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '6e5922277de22b96874c23a705a7d838512c6caf'
                           ),
                           'id': 114143456829,
                           'run_attempt': 1,
                           'run_id': 38028203615,
                           'started_at': '2026-10-10T05:38:46Z',
                           'status': 'completed'}]},
 '38051651761': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488',
                 'jobs': [{'completed_at': '2026-10-10T12:22:13Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488'
                           ),
                           'id': 114211834419,
                           'run_attempt': 1,
                           'run_id': 38051651761,
                           'started_at': '2026-10-10T12:21:27Z',
                           'status': 'completed'}]},
 '38088035236': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '28f9266c98068c3a498a6bdecb2c315cf44a6866',
                 'jobs': [{'completed_at': '2026-10-10T21:33:29Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '28f9266c98068c3a498a6bdecb2c315cf44a6866'
                           ),
                           'id': 114318555435,
                           'run_attempt': 1,
                           'run_id': 38088035236,
                           'started_at': '2026-10-10T21:32:46Z',
                           'status': 'completed'}]},
 '38115226424': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                 'jobs': [{'completed_at': '2026-10-11T05:35:20Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '10d99f8ef021c092851bf189ff67307865b8760c'
                           ),
                           'id': 114398688514,
                           'run_attempt': 1,
                           'run_id': 38115226424,
                           'started_at': '2026-10-11T05:34:37Z',
                           'status': 'completed'}]},
 '38140005662': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                 'jobs': [{'completed_at': '2026-10-11T12:38:51Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '10d99f8ef021c092851bf189ff67307865b8760c'
                           ),
                           'id': 114471740537,
                           'run_attempt': 1,
                           'run_id': 38140005662,
                           'started_at': '2026-10-11T12:38:06Z',
                           'status': 'completed'}]},
 '38177827936': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                 'jobs': [{'completed_at': '2026-10-11T21:48:28Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '10d99f8ef021c092851bf189ff67307865b8760c'
                           ),
                           'id': 114583862783,
                           'run_attempt': 1,
                           'run_id': 38177827936,
                           'started_at': '2026-10-11T21:47:44Z',
                           'status': 'completed'}]},
 '38209276920': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                 'jobs': [{'completed_at': '2026-10-12T05:45:29Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '10d99f8ef021c092851bf189ff67307865b8760c'
                           ),
                           'id': 114679597328,
                           'run_attempt': 1,
                           'run_id': 38209276920,
                           'started_at': '2026-10-12T05:44:41Z',
                           'status': 'completed'}]},
 '38268422208': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                 'jobs': [{'completed_at': '2026-10-12T15:12:25Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
                           ),
                           'id': 114876393422,
                           'run_attempt': 1,
                           'run_id': 38268422208,
                           'started_at': '2026-10-12T15:11:38Z',
                           'status': 'completed'}]},
 '38327710790': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                 'jobs': [{'completed_at': '2026-10-13T00:32:48Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
                           ),
                           'id': 115070134903,
                           'run_attempt': 1,
                           'run_id': 38327710790,
                           'started_at': '2026-10-13T00:32:03Z',
                           'status': 'completed'}]},
 '38410066511': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                 'jobs': [{'completed_at': '2026-10-13T14:03:59Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
                           ),
                           'id': 115339717201,
                           'run_attempt': 1,
                           'run_id': 38410066511,
                           'started_at': '2026-10-13T14:03:13Z',
                           'status': 'completed'}]},
 '38478936036': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                 'jobs': [{'completed_at': '2026-10-13T22:37:26Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
                           ),
                           'id': 115576463970,
                           'run_attempt': 1,
                           'run_id': 38478936036,
                           'started_at': '2026-10-13T22:36:43Z',
                           'status': 'completed'}]},
 '38512751508': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                 'jobs': [{'completed_at': '2026-10-14T04:43:00Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '02792bda730b08eeb1eda86a2cd71af80af3cef7'
                           ),
                           'id': 115684740464,
                           'run_attempt': 1,
                           'run_id': 38512751508,
                           'started_at': '2026-10-14T04:42:13Z',
                           'status': 'completed'}]},
 '38536227281': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '92235b48ad33b9fa377e0736b689010173d7bfab',
                 'jobs': [{'completed_at': '2026-10-14T09:16:33Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '92235b48ad33b9fa377e0736b689010173d7bfab'
                           ),
                           'id': 115775573527,
                           'run_attempt': 1,
                           'run_id': 38536227281,
                           'started_at': '2026-10-14T09:15:45Z',
                           'status': 'completed'}]},
 '38558250558': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '92235b48ad33b9fa377e0736b689010173d7bfab',
                 'jobs': [{'completed_at': '2026-10-14T11:41:27Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '92235b48ad33b9fa377e0736b689010173d7bfab'
                           ),
                           'id': 115832643377,
                           'run_attempt': 1,
                           'run_id': 38558250558,
                           'started_at': '2026-10-14T11:40:40Z',
                           'status': 'completed'}]},
 '38603168877': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '92235b48ad33b9fa377e0736b689010173d7bfab',
                 'jobs': [{'completed_at': '2026-10-14T17:02:55Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '92235b48ad33b9fa377e0736b689010173d7bfab'
                           ),
                           'id': 115987242242,
                           'run_attempt': 1,
                           'run_id': 38603168877,
                           'started_at': '2026-10-14T17:02:06Z',
                           'status': 'completed'}]},
 '38641278934': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'a3a41a05d4965d11b560f64271c5a6e3af1467be',
                 'jobs': [{'completed_at': '2026-10-14T21:35:05Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'a3a41a05d4965d11b560f64271c5a6e3af1467be'
                           ),
                           'id': 116117106488,
                           'run_attempt': 1,
                           'run_id': 38641278934,
                           'started_at': '2026-10-14T21:34:19Z',
                           'status': 'completed'}]},
 '38681296600': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'e1a68725fe923582b5640481e29c7166eb77397c',
                 'jobs': [{'completed_at': '2026-10-15T04:25:17Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'e1a68725fe923582b5640481e29c7166eb77397c'
                           ),
                           'id': 116246227630,
                           'run_attempt': 1,
                           'run_id': 38681296600,
                           'started_at': '2026-10-15T04:24:33Z',
                           'status': 'completed'}]},
 '38684236078': {'attempt': 1,
                 'conclusion': 'success',
                 'head': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                 'jobs': [{'completed_at': '2026-10-15T06:51:00Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               '9b97e13c2e2a56ee77700e40202eb6acd2212cf5'
                           ),
                           'id': 116288834036,
                           'run_attempt': 1,
                           'run_id': 38684236078,
                           'started_at': '2026-10-15T06:50:15Z',
                           'status': 'completed'}]},
 '38720083999': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                 'jobs': [{'completed_at': '2026-10-15T12:25:27Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'a62a88f5cad62f4fb1ef5230358557d37d2b2bba'
                           ),
                           'id': 116419946163,
                           'run_attempt': 1,
                           'run_id': 38720083999,
                           'started_at': '2026-10-15T12:24:41Z',
                           'status': 'completed'}]},
 '38721259331': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                 'jobs': [{'completed_at': '2026-10-15T10:36:56Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f'
                           ),
                           'id': 116376105712,
                           'run_attempt': 1,
                           'run_id': 38721259331,
                           'started_at': '2026-10-15T10:35:30Z',
                           'status': 'completed'}]},
 '38763419545': {'attempt': 1,
                 'conclusion': 'success',
                 'head': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                 'jobs': [{'completed_at': '2026-10-15T15:48:57Z',
                           'conclusion': 'success',
                           'head_branch': 'main',
                           'head_sha': (
                               'f77eed642c4544bbeed009440c406064a4f5c6ad'
                           ),
                           'id': 116520330960,
                           'run_attempt': 1,
                           'run_id': 38763419545,
                           'started_at': '2026-10-15T15:47:30Z',
                           'status': 'completed'}]}}


EXPECTED_TITLE = 'Ernst Mach Scholarship – Worldwide: Application Deadline 1 December 2026'


class ProgrammeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonicals = []
        self.headings = []
        self.structured = []
        self.heading = None
        self.script = None
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").lower().split():
            self.canonicals.append(attributes.get("href"))
        if tag in ("script", "style"):
            self.ignore += 1
        if tag == "script" and (attributes.get("type") or "").lower() == "application/ld+json":
            self.script = []
        if tag == "h1" and not self.ignore:
            if self.heading is not None:
                raise ValueError("Malformed programme heading")
            self.heading = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.heading is not None and not self.ignore:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self.heading is not None:
            self.headings.append(" ".join(unescape("".join(self.heading)).split()))
            self.heading = None
        if tag == "script" and self.script is not None:
            self.structured.append("".join(self.script))
            self.script = None
        if tag in ("script", "style"):
            self.ignore = max(0, self.ignore - 1)


class AdapterError(Exception):
    def __init__(self, message, stage='parse', status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def safe_error(error):
    text = str(error)
    text = re.sub(r'https?://[^\s<>"\']+', lambda match: urllib.parse.urlunsplit((urllib.parse.urlsplit(match[0]).scheme, urllib.parse.urlsplit(match[0]).hostname or '', urllib.parse.urlsplit(match[0]).path, '', '')), text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+\S+', '[redacted]', text)
    text = re.sub(r'(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+', r'\1=[redacted]', text)
    return ' '.join(text.split())[:500]


def make_record(title, url, categories, kind='opportunity', published_at=None, tags=None, host_countries=None, method='editorial-review', evidence=None):
    now = utc_now()
    return {'id': hashlib.sha256(f'{SOURCE_ID}|{url}'.encode()).hexdigest()[:24], 'title': title,
            'url': url, 'source': SOURCE_ID, 'source_url': SOURCE_URL, 'published_at': published_at,
            'summary': '', 'tags': tags or [], 'location': None, 'deadline': None, 'language': LANGUAGE,
            'category': categories[0], 'categories': categories, 'kind': kind, 'host_countries': host_countries or [],
            'eligible_countries': [], 'publisher_country': PUBLISHER_COUNTRY,
            'created_at': now, 'updated_at': now, 'first_seen_at': now, 'last_seen_at': now, 'last_checked_at': now,
            'status': 'unknown', 'classification': {'method': method, 'status': 'unknown' if categories == ['other'] else 'classified', 'evidence': evidence or []}}


def validate_records(records, *, allow_empty=False):
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError('Empty or invalid opportunity collection', 'validate')
    ids = set()
    for record in records:
        if not isinstance(record, dict) or record.get('source') != SOURCE_ID or record.get('source_url') != SOURCE_URL:
            raise AdapterError('Record source identity mismatch', 'validate')
        for field in ('id', 'title', 'url', 'created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at'):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise AdapterError('Missing record field: ' + field, 'validate')
        if record['id'] in ids:
            raise AdapterError('Duplicate opportunity identifier', 'validate')
        ids.add(record['id'])
        url = urllib.parse.urlsplit(record['url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise AdapterError('Unsafe opportunity URL', 'validate')
        for field in ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at', 'published_at', 'deadline'):
            value = record.get(field)
            if field not in record:
                raise AdapterError('Missing record field: ' + field, 'validate')
            if value is not None:
                try:
                    if (field == "deadline" and isinstance(value, str)
                            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                        datetime.strptime(value, "%Y-%m-%d")
                        continue
                    parsed = datetime.fromisoformat(
                        value.replace('Z', '+00:00')
                    )
                    if parsed.tzinfo is None:
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError('Invalid record timestamp: ' + field, 'validate')
        for field in ('tags', 'host_countries', 'eligible_countries'):
            values = record.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise AdapterError('Invalid record list: ' + field, 'validate')
            if field != 'tags' and any(not re.fullmatch('[A-Z]{2}', value) for value in values):
                raise AdapterError('Invalid country code', 'validate')
        categories = record.get('categories')
        if not isinstance(categories, list) or not categories or any(value not in CATEGORIES for value in categories) or len(set(categories)) != len(categories) or record.get('category') != categories[0]:
            raise AdapterError('Invalid opportunity categories', 'validate')
        if record.get('kind') not in ('opportunity', 'programme-overview', 'institutional-grant', 'unknown') or record.get('status') not in ('open', 'expired', 'unknown'):
            raise AdapterError('Invalid opportunity kind or status', 'validate')
        if not isinstance(record.get('summary'), str) or len(record['summary']) > 600 or re.search('<[^>]+>', record['summary']):
            raise AdapterError('Invalid plain summary', 'validate')
        for field in ('location', 'language', 'publisher_country'):
            if field not in record or record[field] is not None and not isinstance(record[field], str):
                raise AdapterError('Invalid record field: ' + field, 'validate')
        classification = record.get('classification')
        if not isinstance(classification, dict) or not isinstance(classification.get('method'), str) or classification.get('status') not in ('classified', 'unknown') or not isinstance(classification.get('evidence'), list) or any(not isinstance(value, str) for value in classification['evidence']):
            raise AdapterError('Invalid classification evidence', 'validate')


def github(method, path, payload=None, missing_ok=False):
    token = os.environ.get('DATA_SOURCE_TOKEN')
    if not token:
        raise AdapterError('DATA_SOURCE_TOKEN is required for publication', 'publish')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    try:
        status, _, body = request_bytes(_GITHUB + path, method=method, headers=headers,
                                        payload=json.dumps(payload).encode() if payload is not None else None)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError('GitHub connection failed; publication is unconfirmed', 'publish') from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f'GitHub publication API returned HTTP {status}', 'publish', status)
    return json.loads(body)


def read_snapshot():
    head = github('GET', '/git/ref/heads/main')['object']['sha']
    tree = github('GET', '/git/commits/' + head)['tree']['sha']
    files = {}
    for name in ('data.json', 'metadata.json'):
        file = github('GET', f'/contents/datas/{SOURCE_ID}/{name}?ref={head}', missing_ok=True)
        if file is not None and file.get('encoding') != 'base64':
            raise AdapterError('Unsupported existing source file encoding', 'publish')
        files[name] = None if file is None else base64.b64decode(file['content']).decode('utf-8')
    if (files['data.json'] is None) != (files['metadata.json'] is None):
        raise AdapterError('Incomplete remote source snapshot; no files changed', 'publish')
    return {'head': head, 'tree': tree, 'files': files}


def publish_files(snapshot, files):
    expected = snapshot['files'].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        candidate = None
        try:
            tree = github('POST', '/git/trees', {'base_tree': snapshot['tree'], 'tree': [
                {'path': f'datas/{SOURCE_ID}/{name}', 'mode': '100644', 'type': 'blob', 'content': text}
                for name, text in files.items()]})['sha']
            candidate = github('POST', '/git/commits', {'message': f'Update {SOURCE_ID} collection outcome', 'tree': tree, 'parents': [snapshot['head']]})['sha']
            github('PATCH', '/git/refs/heads/main', {'sha': candidate, 'force': False})
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (latest['head'] == candidate or latest['files'] == desired):
                return
            if latest['files'] != expected:
                raise AdapterError('Source changed concurrently; newer remote data was not overwritten', 'publish')
            snapshot = latest
            if attempt == 2:
                raise AdapterError('Publication failed; durable remote outcome could not be updated', 'publish')


def metadata(attempt, previous, records, error=None):
    result = {"display_name": "OeAD", 'source': SOURCE_ID, 'source_url': SOURCE_URL, 'website_url': WEBSITE_URL,
              'language': LANGUAGE, 'publisher_country': PUBLISHER_COUNTRY, 'publisher_type': PUBLISHER_TYPE,
              'status': 'fail' if error else 'success', 'last_attempt_at': attempt,
              'last_success_at': previous.get('last_success_at') if error else attempt,
              'last_checked_at': previous.get('last_checked_at') if error else attempt,
              'record_count': len(records),
              'message': (
                  'Collection failed; last-good data preserved' if error
                  else f'Validated refresh retains {len(records)} records'
              ),
              'error': safe_error(error) if error else None}
    if ATTRIBUTION:
        result['attribution'] = ATTRIBUTION
    if error:
        result['failure_stage'] = getattr(error, 'stage', 'parse')
    return result


def remove_expired_records(records, checked_at):
    """Filter verified deadlines after validating the complete fresh inventory.

    Unzoned closing dates use UTC-12 as a conservative world-wide boundary;
    no publisher timezone is inferred. Explicit offsets retain their instant.
    """
    validate_records(records)
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing = datetime.fromisoformat(deadline).date()
            latest_date = checked.astimezone(
                timezone(timedelta(hours=-12))
            ).date()
            expired = latest_date > closing
        elif deadline:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            if closing.tzinfo is not None:
                expired = checked > closing
        if not expired:
            retained.append(record)
    validate_records(retained, allow_empty=True)
    return retained


_OPENER = urllib.request.build_opener(NoRedirect)


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextlib.contextmanager
def execution():
    global _RUN_END, _COLLECTION_LOCK_FD, _RESTORED, _STATE_TRUSTED
    global _FIRST_PUBLISHER_REQUEST, _HISTORY_COMPLETED, _PUBLISHER_IN_FLIGHT
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    _RESTORED = _STATE_TRUSTED = False
    _FIRST_PUBLISHER_REQUEST = True
    _PUBLISHER_IN_FLIGHT = False
    _HISTORY_COMPLETED = []
    _ROBOTS.clear()
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = None
        journal_finish()


@contextlib.contextmanager
def phase(name, seconds):
    global _PHASE_END, _PHASE_NAME
    if _PHASE_END is not None or _RUN_END is None:
        raise AdapterError("Invalid phase transition", "access")
    _PHASE_NAME = name
    limit = _RUN_END if name == "export" else _RUN_END - EXPORT_TIMEOUT
    if name == "collection":
        limit = min(limit, _RUN_END - EXPORT_TIMEOUT - PUBLICATION_RESERVE)
    _PHASE_END = min(time.monotonic() + seconds, limit)
    try:
        check_deadline()
        arm_alarm(_PHASE_END)
        yield
        check_deadline()
    finally:
        _PHASE_END = _PHASE_NAME = None
        arm_alarm(limit)


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "in_flight": False,
        "interval": 12.2,
        "completed": 0,
        "page_interval": 30,
        "last_inventory_completed": 0,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        leaf = FAMILY + "-" + str(os.getuid())
        created = False
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        linked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_nlink < 2
            or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            os.close(descriptor)
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _STATE_INITIALIZING = created
        _STATE_DIR_FD = descriptor
        _STATE_DIR_ID = (info.st_dev, info.st_ino)
        directory = os.path.join(temporary, leaf)
        _STATE_PATH = os.path.join(directory, "state.json")
        _STATE_LOCK = os.path.join(directory, "state.lock")
        verify_state_directory()
    finally:
        os.close(parent)


def locked_budget():
    global _GUARD_LOCK_ID, _GUARD_CREATED, _JOURNAL_NEW
    budget_file()
    if _GUARD_FATAL:
        raise AdapterError("Publisher persistence gate failed", "access")
    parent = journal_parent()
    descriptor = identity = None
    try:
        if _GUARD_LOCKS:
            original = next(iter(_GUARD_LOCKS))
            journal_owned(original, _GUARD_LOCKS[original])
            descriptor = os.dup(original)
        else:
            name = os.path.basename(_STATE_LOCK)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open(name, flags, dir_fd=parent)
            except FileNotFoundError:
                if not journal_creation_allowed():
                    raise AdapterError(
                        "Mature publisher lock missing", "access"
                    )
                descriptor = os.open(
                    name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent,
                )
                pass
        acquired = os.fstat(descriptor)
        identity = (acquired.st_dev, acquired.st_ino)
        info = journal_owned(descriptor, identity)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        journal_owned(descriptor, identity)
        current = os.stat(
            os.path.basename(_STATE_LOCK), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != identity or (
            _GUARD_LOCK_ID is not None and _GUARD_LOCK_ID != identity
        ):
            raise AdapterError("Publisher journal lock changed", "access")
        _GUARD_LOCK_ID = identity
        result = JournalLock(descriptor, identity)
        try:
            journal_retain(None)
        except BaseException:
            result.close()
            descriptor = None
            raise
        return result
    except BaseException:
        journal_failed()
        if descriptor is not None:
            if identity is not None:
                journal_close(descriptor, identity, True)
        raise


def load_budget():
    raw = journal_read()
    if not raw and _GUARD_CREATED and journal_creation_allowed():
        return empty_budget(time.time())
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher state", "access")
    try:
        state = validate_budget(json.loads(raw))
        if state["in_flight"] and not _PUBLISHER_IN_FLIGHT:
            raise AdapterError(
                "Incomplete prior publisher attempt requires reviewed recovery",
                "access",
            )
        return state
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher state", "access") from None


def invalidate_local_budget():
    journal_invalidate()


def retain_failed_constraints(state):
    """Retain all known restrictions in RAM after a failed write."""
    global _FAILED_CONSTRAINTS
    candidate = validate_budget(state)
    if _FAILED_CONSTRAINTS is None:
        _FAILED_CONSTRAINTS = candidate
    else:
        _FAILED_CONSTRAINTS = merge_budgets(
            _FAILED_CONSTRAINTS, candidate, time.time()
        )


def save_budget(state):
    global _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    try:
        write_budget(state)
    except BaseException:
        _PUBLISHER_GATE_FAILED = True
        _STATE_TRUSTED = False
        with contextlib.suppress(BaseException):
            retain_failed_constraints(state)
        with contextlib.suppress(BaseException):
            invalidate_local_budget()
        raise


def write_budget(state):
    state = validate_budget(state)
    journal_commit(state)


def pace():
    global _PUBLISHER_INTERVAL, _PUBLISHER_IN_FLIGHT
    (
        "Serialize every publisher request start across the complete "
        "origin set."
    )
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    check_deadline()
    with locked_budget():
        state = load_budget()
        if state["rate_limit_strikes"] >= 100:
            raise AdapterError(
                "Rate counter exhausted; state retained", "access"
            )
        if state["blocked"]:
            raise AdapterError(
                ("Publisher backoff requires reviewed recovery"), ("access")
            )
        now = time.time()
        starts = [t for t in state["starts"] if t > now - 60]
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state.get("interval", 6))
        target = max(now, state["not_before"], state["rate_limit_until"])
        target = max(target, state["completed"] + _PUBLISHER_INTERVAL)
        if _PUBLISHER_COMPLETED_MONOTONIC is not None:
            target = max(
                target,
                now
                + max(
                    0,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                ),
            )
        if starts:
            target = max(target, starts[-1] + state.get("interval", 6))
        if len(starts) >= 5:
            target = max(target, starts[-5] + 60)
        if target + 120 > time.time() + (_PHASE_END - time.monotonic()):
            raise AdapterError(
                "Publisher backoff exceeds phase budget", "access"
            )
        if _COLLECTION_STARTED is not None and (
            target + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError(
                ("Publisher backoff exceeds collection budget"), ("access")
            )
        while True:
            check_deadline()
            wait = max(0, target - time.time())
            if _PUBLISHER_COMPLETED_MONOTONIC is not None:
                wait = max(
                    wait,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                )
            if wait <= 0:
                break
            if wait + 120 > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Publisher backoff exceeds phase budget", "access"
                )
            if _COLLECTION_STARTED is not None and (
                time.time(
                ) + wait + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Publisher backoff exceeds collection budget", "access"
                )
            time.sleep(wait)
        now = time.time()
        if now < target:
            raise AdapterError(
                ("Publisher pacing clock moved backwards"), ("access")
            )
        check_deadline()
        if (
            _PUBLISHER_COMPLETED_MONOTONIC is not None
            and time.monotonic(
            ) < _PUBLISHER_COMPLETED_MONOTONIC + _PUBLISHER_INTERVAL
        ):
            raise AdapterError(
                "Publisher physical completion gate not elapsed", "access"
            )
        state["in_flight"] = True
        state["starts"] = [t for t in starts if t > now - 60] + [now]
        state["interval"] = _PUBLISHER_INTERVAL
        state["not_before"] = max(
            state["not_before"],
            now + state.get("interval", 6),
        )
        state["observed_at"] = now
        save_budget(state)
        _PUBLISHER_IN_FLIGHT = True


def record_retry_after(value, status=None):
    """Keep unassessed 429 policy failures pending across process restarts."""
    global _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    try:
        apply_retry_after(value, status)
    except BaseException:
        if status == 429:
            # The pre-wire durable pending barrier must not become settled
            # when the newly observed response cannot be safely assessed.
            _PUBLISHER_GATE_FAILED = True
            _STATE_TRUSTED = False
        raise


def apply_retry_after(value, status=None):
    """Persist timed slowdown before body reads; invalid headers add no hold."""
    now = time.time()
    until = retry_after_until(value, now)
    if status != 429 and until is None:
        return
    with locked_budget():
        state = load_budget()
        if status == 429:
            if state["rate_limit_strikes"] >= 100:
                raise AdapterError(
                    "Rate counter exhausted; state retained", "access"
                )
            strikes = state["rate_limit_strikes"] + 1
            factor = 2 ** strikes
            interval = max(state["interval"] * 2, 12.2 * factor)
            page_interval = max(state["page_interval"] * 2, 30 * factor)
            fallback = now + 1800 * (2 ** (strikes - 1))
            if any(not numeric(v) or v > 253402300799 for v in (
                interval, page_interval, fallback
            )):
                raise AdapterError(
                    "Rate budget exceeds supported clock", "access"
                )
            state["rate_limit_strikes"] = strikes
            state["interval"], state["page_interval"] = interval, page_interval
            state["rate_limit_until"] = max(
                state["rate_limit_until"], fallback, until or 0
            )
        state["not_before"] = max(
            state["not_before"], state["rate_limit_until"], until or 0
        )
        state["observed_at"] = now
        save_budget(state)


def merge_budgets(local, incoming, now):
    """Preserve every known start and numeric MAX, plus both durable holds."""
    local, incoming = validate_budget(local), validate_budget(incoming)
    combined = empty_budget(now)
    for key in (
        "not_before", "interval", "completed", "page_interval",
        "last_inventory_completed", "rate_limit_strikes", "rate_limit_until"
    ):
        combined[key] = max(local[key], incoming[key])
    combined["blocked"] = local["blocked"] or incoming["blocked"]
    combined["in_flight"] = local["in_flight"] or incoming["in_flight"]
    combined["legacy_rate_history_unknown"] = (
        local["legacy_rate_history_unknown"]
        or incoming["legacy_rate_history_unknown"]
    )
    combined["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    return validate_budget(combined)


def verify_state_directory():
    """Bind the mature leaf to its retained descriptor before each operation."""
    info = os.fstat(_STATE_DIR_FD)
    current = os.stat(os.path.dirname(_STATE_PATH), follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_nlink < 2
        or (info.st_dev, info.st_ino) != _STATE_DIR_ID
        or (current.st_dev, current.st_ino) != _STATE_DIR_ID
    ):
        raise AdapterError(
            "Publisher state directory identity changed", "access"
        )


def secure_file(name, flags):
    verify_state_directory()
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=_STATE_DIR_FD
    )
    info = os.fstat(descriptor)
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher state file", "access")
    return descriptor


@contextlib.contextmanager
def publisher_attempt(publisher, inventory=False):
    """Persist completion without ever clearing a prior persistence failure."""
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
    global _INVENTORY_COMPLETED_MONOTONIC, _STATE_TRUSTED, _PUBLISHER_IN_FLIGHT
    if not publisher:
        yield
        return
    if (_COLLECTION_LOCK_FD is None or _PUBLISHER_GATE_FAILED
            or not _PUBLISHER_IN_FLIGHT):
        raise AdapterError(
            "Exclusive healthy reserved publisher collector required",
            "access",
        )
    check_deadline()
    if (_PUBLISHER_COMPLETED_MONOTONIC is not None
            and time.monotonic(
            ) < _PUBLISHER_COMPLETED_MONOTONIC + _PUBLISHER_INTERVAL):
        raise AdapterError(
            "Publisher physical completion gate not elapsed",
            "access",
        )
    try:
        yield
    finally:
        _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
        now = time.time()
        if inventory:
            _INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
        if _PUBLISHER_GATE_FAILED:
            if _FAILED_CONSTRAINTS is not None:
                state = dict(
                    _FAILED_CONSTRAINTS,
                    starts=list(_FAILED_CONSTRAINTS["starts"]),
                )
                state["not_before"] = max(
                    state["not_before"],
                    now + _PUBLISHER_INTERVAL,
                )
                state["completed"] = max(state["completed"], now)
                if inventory:
                    state["last_inventory_completed"] = max(
                        state["last_inventory_completed"],
                        now,
                    )
                state["observed_at"] = now
                retain_failed_constraints(state)
        else:
            try:
                with locked_budget():
                    state = load_budget()
                    state["not_before"] = max(
                        state["not_before"],
                        now + _PUBLISHER_INTERVAL,
                    )
                    state["completed"] = max(state["completed"], now)
                    if inventory:
                        state["last_inventory_completed"] = max(
                            state["last_inventory_completed"],
                            now,
                        )
                    state["observed_at"] = now
                    state["in_flight"] = False
                    save_budget(state)
                    _PUBLISHER_IN_FLIGHT = False
            except BaseException:
                _PUBLISHER_GATE_FAILED = True
                _STATE_TRUSTED = False
                invalidate_local_budget()
                raise


def retry_after_until(value, now):
    """Accept only a finite supported positive delay or aware future date."""
    if not isinstance(value, str):
        return None
    try:
        value = value.strip()
        if re.fullmatch(r"[0-9]+", value):
            if len(value) > 10:
                return None
            delay = int(value)
            until = now + delay
        else:
            parsed = parsedate_to_datetime(value)
            if parsed.utcoffset() is None:
                return None
            until = parsed.timestamp()
        if not numeric(until) or not now < until <= 253402300799:
            return None
        return until
    except (ValueError, TypeError, OverflowError):
        return None


def retain_publisher_hold():
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
        state["observed_at"] = time.time()
        save_budget(state)


class VisibleText(HTMLParser):
    """Inspect primary visible text, preserving nested DOM suppression."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.text = []

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
        suppressed = (
            tag
            in {
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

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.stack or not any(self.stack[-1][1:]):
            self.text.append(data)


def reject_visible_challenge(body, headers, *, challenge_only=False):
    # A challenge redirect must not escape inspection through a wrong MIME.
    prefix = body[:512].decode("utf-8", "replace")
    obvious_html = re.match(
        r"^[\s\ufeff]*(?:<!doctype\s+html\b|"
        r"<(?:html|head|title|body|main|article|section|div|p|form|h[1-6])"
        r"(?:\s|>))", prefix, re.I,
    )
    if (
        "html" not in headers.get("Content-Type", "").lower()
        and not obvious_html
    ):
        return

    class ChallengeText(VisibleText):
        """Keep genuine unsuppressed HEAD titles as separate evidence."""

        def __init__(self):
            super().__init__()
            self.head_title = []
            self.head_seen = False
            self.head_depth = None
            self.body_started = False

        def handle_starttag(self, tag, attrs):
            if tag not in {
                "html", "head", "title", "meta", "link", "base",
                "style", "script", "noscript", "template",
            }:
                self.body_started = True
            if (
                tag == "head" and not self.head_seen
                and not self.body_started
                and [frame[0] for frame in self.stack] in ([], ["html"])
            ):
                self.head_seen = True
                self.head_depth = len(self.stack) + 1
            super().handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            if tag == "head":
                self.head_depth = None

        def handle_data(self, data):
            if (
                data.strip() and self.head_depth is None
                and (not self.stack or not any(self.stack[-1][1:]))
                and not any(
                    frame[0] in {"head", "title"} for frame in self.stack
                )
            ):
                self.body_started = True
            if not any(frame[0] in {"head", "title"} for frame in self.stack):
                super().handle_data(data)
            if (
                self.head_depth is not None and not self.body_started
                and len(self.stack) == self.head_depth + 1
                and self.stack[-1][0] == "title"
                and self.stack[-2][0] == "head"
                and not any(self.stack[-1][1:])
            ):
                self.head_title.append(data)

    parser = ChallengeText()
    parser.feed(body.decode("utf-8", "replace"))
    text = " ".join(
        " ".join(parser.text + parser.head_title).lower().split()
    )
    phrases = (
        "verify you are human",
        "verify that you are human",
        "complete the captcha",
        "enable javascript and cookies to continue",
        "checking your browser",
    )
    if not challenge_only:
        phrases += (
            "access denied",
            "permission denied",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    title = " ".join(" ".join(parser.head_title).lower().split())
    title_refusal = re.search(
        r"just a moment|attention required|access denied|captcha", title, re.I
    )
    if any(phrase in text for phrase in phrases) or title_refusal:
        retain_publisher_hold()
        raise AdapterError("Visible publisher challenge or denial", "access")


def wait_inventory_page():
    """Separate inventory completion from ordinary physical completion."""
    with locked_budget():
        state = load_budget()
        target = state["last_inventory_completed"] + state["page_interval"]
        if _INVENTORY_COMPLETED_MONOTONIC is not None:
            target = max(
                target, time.time() + max(
                    0, _INVENTORY_COMPLETED_MONOTONIC + state["page_interval"]
                    - time.monotonic()
                )
            )
        while target > time.time():
            check_deadline()
            wait = target - time.time()
            if wait + 120 > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Inventory gap exceeds phase budget", "access"
                )
            if (
                time.time() + wait + 120
                > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Inventory gap exceeds collection budget", "access"
                )
            time.sleep(wait)


def validate_family_identity(run, expected=None):
    source = family_source(run)
    identity, attempt = run.get("id"), run.get("run_attempt")
    head = run.get("head_sha")
    if (
        run.get("workflow_id") != _WORKFLOW_ID
        or source != SOURCE_ID
        or type(identity) is not int or identity <= 0
        or type(attempt) is not int or attempt <= 0
        or run.get("repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)
        or expected is not None
        and (identity, attempt, source, head) != expected
    ):
        raise AdapterError("Untrusted family run identity", "access")
    return identity, attempt, source, head


def publisher_transport_error(error, url):
    """Describe a publisher transport failure without logging exception text."""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        category = "TLS certificate verification failure"
    elif isinstance(cause, ssl.SSLError):
        category = "TLS handshake/read failure"
    elif isinstance(cause, TimeoutError):
        category = "Network timeout"
    elif isinstance(cause, ConnectionError):
        category = "Connection failure"
    else:
        category = "Network transport failure"
    parsed = urllib.parse.urlsplit(url)
    hop = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.hostname or "", parsed.path, "", "")
    )
    return AdapterError(
        "Publisher transport failure at " + hop + " (" + category + ")",
        "fetch",
    )


def workflow_api(path):
    """Read real public family metadata using existing authentication."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        separator = "&" if "?" in path else "?"
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path + separator
            + "_fresh=" + str(time.time_ns()),
            headers=headers,
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            "Workflow pacing state unavailable", "access"
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


def completed_identity(run, expected=None):
    """Bind exact attempt/head and actual completed jobs, never updated_at."""
    identity = validate_family_identity(run, expected)
    run_id, attempt, source, head = identity
    exact = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(exact, identity)
    if exact.get("status") != "completed":
        raise AdapterError("Prior family attempt is incomplete", "access")
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        not isinstance(jobs, list) or not jobs or len(jobs) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(jobs)
    ):
        raise AdapterError("Incomplete family attempt jobs", "access")
    starts, ends, seen = [], [], set()
    try:
        for job in jobs:
            if (
                type(job.get("id")) is not int or job["id"] <= 0
                or job["id"] in seen
                or type(job.get("run_id")) is not int
                or job["run_id"] != run_id
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt or job.get("head_sha") != head
                or job.get("head_branch") != "main"
                or job.get("status") != "completed"
            ):
                raise ValueError("Invalid attempt job binding")
            seen.add(job["id"])
            times = [datetime.fromisoformat(job[k].replace("Z", "+00:00"))
                     for k in ("started_at", "completed_at")]
            if any(t.utcoffset() is None for t in times):
                raise ValueError("Missing job timezone")
            began, finished = (t.timestamp() for t in times)
            if not 0 <= began <= finished <= time.time() + 1:
                raise ValueError("Invalid attempt job chronology")
            starts.append(began)
            ends.append(finished)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AdapterError(
            "Untrusted completed family jobs", "access"
        ) from None
    return max(ends), min(starts), *identity


class InventoryRace(AdapterError):
    """A changed advertised total permits a bounded fresh history scan."""


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


def scan_family_history():
    """Exhaust the singleton family and completed queued-rerun history."""
    global _HISTORY_COMPLETED
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    candidates, seen_ids = [], set()
    total, scanned, current_seen = None, 0, False
    for page in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-at-oead-ernst-mach.yml/runs"
            f"?per_page=100&page={page}"
        )
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int or count < 0
            or not isinstance(runs, list) or len(runs) > 100
        ):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace(
                "Workflow inventory changed during restore", "access"
            )
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            identity, attempt, source, head = validate_family_identity(run)
            if identity in seen_ids:
                raise AdapterError("Duplicate family workflow run", "access")
            seen_ids.add(identity)
            if current and identity == current[0]:
                if (
                    (identity, attempt) != current
                    or head != os.environ["GITHUB_SHA"]
                ):
                    raise AdapterError(
                        "Current attempt/head mismatch", "access"
                    )
                current_seen = True
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(completed_identity(
                        prior, (identity, attempt - 1, source, head)
                    ))
                continue
            if run.get("status") != "completed":
                if not hosted or run.get("status") not in {
                    "queued", "waiting", "pending", "requested"
                }:
                    raise AdapterError("Another family run is active", "access")
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(completed_identity(
                        prior, (identity, attempt - 1, source, head)
                    ))
                continue
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total or hosted and not current_seen:
                raise AdapterError(
                    "Incomplete current family inventory", "access"
                )
            if not candidates:
                return None
            _HISTORY_COMPLETED = sorted(candidates)
            ordered = _HISTORY_COMPLETED
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError("Ambiguous family chronology", "access")
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def download_inert_artifact(identity, artifact_id, digest=None):
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
            **({"Authorization": "Bearer " + token} if token else {}),
        },
    )
    try:
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
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
            or not parsed.hostname.endswith((
                ".blob.core.windows.net", ".actions.githubusercontent.com"
            ))
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
        status, _, body = request_bytes(destination, byte_limit=32768)
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
            "head_sha",
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "state",
        }:
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source, head = identity
        if (
            type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or envelope["repository"] != "YouthOpps/data-pipeline"
            or type(envelope["run_id"]) is not int
            or type(envelope["run_attempt"]) is not int
            or envelope["run_id"] != run_id
            or envelope["run_attempt"] != attempt
            or envelope["source"] != source
            or envelope["head_sha"] != head
        ):
            raise ValueError("Artifact ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def invalidate_artifact(parent, name):
    """Invalidate only a safely owned artifact, before attempting unlink."""
    try:
        descriptor = os.open(
            name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent
        )
    except FileNotFoundError:
        return
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe existing pacing artifact", "access")
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def artifact_output(path):
    """Keep a pinned output descriptor until export and signaling succeed."""
    global _PUBLISHER_GATE_FAILED
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    name = os.path.basename(path)
    try:
        info = os.fstat(parent)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o022
            or info.st_mode & 0o700 != 0o700
        ):
            raise AdapterError("Unsafe pacing artifact directory", "access")
        invalidate_artifact(parent, name)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion cannot be exported", "access"
            )
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            yield descriptor
            os.fsync(descriptor)
            os.fsync(parent)
        except BaseException:
            _PUBLISHER_GATE_FAILED = True
            # Keep the exact owned descriptor open until failure cleanup.
            # Truncate before unlink: failed unlink still leaves invalid JSON.
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            with contextlib.suppress(Exception):
                signal = os.open(
                    os.environ["GITHUB_OUTPUT"],
                    os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
                )
                with os.fdopen(signal, "a") as output:
                    info = os.fstat(output.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                    ):
                        raise AdapterError("Unsafe readiness output", "access")
                    output.write("pacing_ready=false\n")
                    output.flush()
                    os.fsync(output.fileno())
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            raise

    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def collect_programme():
    delay = check_robots(SOURCE_URL)
    page = ProgrammeHTML()
    page.feed(fetch_source(SOURCE_URL, min_interval=delay))
    page.close()
    if page.canonicals != [SOURCE_URL]:
        raise ValueError("Reviewed programme canonical URL changed or is ambiguous")
    if page.heading is not None or page.headings != [EXPECTED_TITLE]:
        raise ValueError("Expected one exact reviewed programme heading")
    # The verified heading explicitly identifies this application's closing day.
    record = make_record(
        EXPECTED_TITLE, SOURCE_URL, ["scholarships"],
        kind="programme-overview", tags=["programme-overview"],
        method="editorial-review", evidence=[SOURCE_URL],
    )
    record["deadline"] = "2026-12-01"
    return [record]


def run_body(publishing):
    args = argparse.Namespace(publish=publishing)
    snapshot = read_snapshot() if args.publish else None
    old = json.loads(snapshot['files']['data.json']) if snapshot and snapshot['files']['data.json'] is not None else []
    previous = json.loads(snapshot['files']['metadata.json']) if snapshot and snapshot['files']['metadata.json'] is not None else {}
    if snapshot and snapshot['files']['data.json'] is not None:
        validate_records(old, allow_empty=True)
        if previous.get('source') != SOURCE_ID:
            raise AdapterError('Remote metadata source mismatch; no files changed', 'publish')
    attempt, failure = utc_now(), None
    try:
        records = collect()
        validate_records(records)
        records = remove_expired_records(records, utc_now())
        prior = {record['id']: record for record in old}
        temporal = {'created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at'}
        for record in records:
            before = prior.get(record['id'], {})
            same = {key: value for key, value in before.items() if key not in temporal} == {key: value for key, value in record.items() if key not in temporal}
            record.update(created_at=before.get('created_at', attempt), first_seen_at=before.get('first_seen_at', attempt),
                          updated_at=before.get('updated_at', attempt) if same else attempt, last_seen_at=attempt, last_checked_at=attempt)
        records.sort(key=lambda record: record['id'])
        records.sort(key=lambda record: record['published_at'] or '', reverse=True)
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
    if args.publish and (
        not failure or snapshot["files"]["data.json"] is not None
    ):
        files = {'metadata.json': json.dumps(outcome, ensure_ascii=False, indent=2) + '\n'}
        if not failure:
            files['data.json'] = json.dumps(records, ensure_ascii=False, indent=2) + '\n'
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(attempt, previous, old, AdapterError('Publication failed: ' + safe_error(error), 'publish'))
            try:
                latest = read_snapshot()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError('No safe previous snapshot for failure reporting', 'publish')
                publish_files(latest, {'metadata.json': json.dumps(outcome, ensure_ascii=False, indent=2) + '\n'})
            except Exception:
                outcome['message'] = 'Publication failed; durable failure status could not be recorded. Remote data was not force-overwritten.'
    elif args.publish:
        outcome['message'] = 'First collection failed; no published source folder created'
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


def active_deadline():
    if _RUN_END is None:
        raise AdapterError("Bounded OeAD execution context required", "access")
    return _PHASE_END if _PHASE_END is not None else _RUN_END - EXPORT_TIMEOUT


def check_deadline():
    if time.monotonic() >= min(active_deadline(), _RUN_END):
        raise PhaseExpired()


def request_timeout(publisher=False):
    check_deadline()
    return min(30, active_deadline() - time.monotonic())


def public_url(value):
    """Preserve the original reviewed HTTPS host and redirect frontier."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower().removeprefix(
            "www.",
        ) != "studyinaustria.at"
        or parsed.username or parsed.password or parsed.port is not None
    ):
        raise AdapterError(
            "Source redirect leaves the reviewed HTTPS host",
            "fetch",
        )
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def validate_budget(state):
    """OeAD has no authentic older durable schema to silently upgrade."""
    if (
        not isinstance(state, dict)
        or set(state) != set(empty_budget(0))
        or type(state.get("schema")) is not int
        or state["schema"] != _STATE_SCHEMA
        or state.get("family") != FAMILY
        or type(state.get("blocked")) is not bool
        or type(state.get("in_flight")) is not bool
        or type(state.get("legacy_rate_history_unknown")) is not bool
        or not state["legacy_rate_history_unknown"]
    ):
        raise AdapterError("Invalid OeAD pacing schema/history", "access")
    if (
        any(not numeric(state[key]) for key in (
            "observed_at", "not_before", "interval", "completed",
            "page_interval", "last_inventory_completed", "rate_limit_until",
        ))
        or state["interval"] < 12.2 or state["page_interval"] < 30
        or state["completed"] > state["observed_at"]
        or state["last_inventory_completed"] > state["observed_at"]
        or state["observed_at"] > time.time() + 1
        or type(state["rate_limit_strikes"]) is not int
        or not 0 <= state["rate_limit_strikes"] <= 100
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 512
        or state["starts"] != sorted(set(state["starts"]))
        or any(
            not numeric(t) or t > state["observed_at"]
            for t in state["starts"]
        )
    ):
        raise AdapterError("Invalid OeAD pacing constraints", "access")
    return dict(state, starts=list(state["starts"]))


def workflow_mode_guard():
    """Check workflow state before history, state creation or access."""
    result = workflow_api("/actions/workflows/fetch-at-oead-ernst-mach.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-at-oead-ernst-mach.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "OeAD workflow is not serialized for this mode",
            "access",
        )
    if hosted:
        current_run_identity()
        head = os.environ.get("GITHUB_SHA", "")
        if (
            not re.fullmatch(r"[0-9a-f]{40}", head)
            or os.environ.get("GITHUB_WORKFLOW_SHA") != head
            or os.environ.get("GITHUB_WORKFLOW_REF")
            != "YouthOpps/data-pipeline/.github/workflows/"
            "fetch-at-oead-ernst-mach.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable OeAD workflow head",
                "access",
            )


def legacy_transition_state():
    """Allow only the exact 27 reviewed pre-artifact attempts."""
    approval = _LEGACY_TRANSITION
    if not isinstance(approval, dict) or set(approval) != {
        "schema", "family", "evidence", "history_sha256", "state"
    }:
        raise AdapterError("Explicit OeAD legacy transition required", "access")
    pins = json.dumps(
        _LEGACY_JOB_PINS, sort_keys=True, separators=(",", ":")
    ).encode()
    if (
        type(approval["schema"]) is not int or approval["schema"] != 1
        or approval["family"] != FAMILY
        or not isinstance(approval["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/272"
            r"#issuecomment-[1-9][0-9]*", approval["evidence"]
        )
        or approval["history_sha256"] != hashlib.sha256(pins).hexdigest()
        or len(_HISTORY_COMPLETED) != len(_LEGACY_JOB_PINS)
    ):
        raise AdapterError("Untrusted OeAD legacy approval", "access")
    actual = {str(row[2]): row for row in _HISTORY_COMPLETED}
    if set(actual) != set(_LEGACY_JOB_PINS):
        raise AdapterError("OeAD historical attempt set changed", "access")
    bounds = []
    for key, expected in _LEGACY_JOB_PINS.items():
        row = actual[key]
        finished = max(
            datetime.fromisoformat(j["completed_at"].replace("Z", "+00:00"))
            .timestamp() for j in expected["jobs"]
        )
        began = min(
            datetime.fromisoformat(j["started_at"].replace("Z", "+00:00"))
            .timestamp() for j in expected["jobs"]
        )
        if row != (finished, began, int(key), 1, SOURCE_ID, expected["head"]):
            raise AdapterError("OeAD legacy chronology changed", "access")
        run = workflow_api(f"/actions/runs/{key}/attempts/1")
        if run.get("conclusion") != expected["conclusion"]:
            raise AdapterError("OeAD legacy run conclusion changed", "access")
        jobs = workflow_api(f"/actions/runs/{key}/attempts/1/jobs?per_page=100")
        if (
            jobs.get("total_count") != len(expected["jobs"])
            or not isinstance(jobs.get("jobs"), list)
            or len(jobs["jobs"]) != len(expected["jobs"])
            or any(
                {k: j.get(k) for k in pin} != pin
                for j, pin in zip(
                    sorted(jobs["jobs"], key=lambda x: x.get("id", 0)),
                    sorted(expected["jobs"], key=lambda x: x["id"]),
                )
            )
        ):
            raise AdapterError("OeAD exact legacy jobs changed", "access")
        artifacts = workflow_api(f"/actions/runs/{key}/artifacts?per_page=100")
        if artifacts.get(
            "total_count",
        ) != 0 or artifacts.get("artifacts") != []:
            raise AdapterError(
                "OeAD legacy artifact provenance changed",
                "access",
            )
        bounds.append(finished)
    state = validate_budget(approval["state"])
    if (
        not set(bounds).issubset(state["starts"])
        or state["completed"] < max(bounds)
        or state["last_inventory_completed"] < max(bounds)
        or state["not_before"] < max(bounds) + 60
    ):
        raise AdapterError("OeAD legacy completion MAX missing", "access")
    return state


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("OEAD_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native OeAD MAX approval required",
                "access",
            )
        return incoming
    try:
        approval = json.loads(raw)
        if (
            not isinstance(approval, dict)
            or set(approval) != {"schema", "family", "evidence", "state"}
            or type(approval["schema"]) is not int or approval["schema"] != 1
            or approval["family"] != FAMILY
            or not isinstance(approval["evidence"], str)
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/272"
                r"#issuecomment-[1-9][0-9]*", approval["evidence"]
            )
        ):
            raise ValueError("Untrusted full-native approval")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native OeAD approval",
            "access",
        ) from None


def restored_state():
    """Newest exact artifact is mandatory after the one reviewed legacy set."""
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("OeAD family history missing", "access")
    if all(str(row[2]) in _LEGACY_JOB_PINS for row in _HISTORY_COMPLETED):
        return production_maintenance_state(legacy_transition_state())
    run_id, attempt, source, head = latest
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list) or len(artifacts) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(artifacts)
        or any(not isinstance(a, dict) for a in artifacts)
    ):
        raise AdapterError("Incomplete OeAD artifact inventory", "access")
    matches = [a for a in artifacts if a.get("name")
               == _ARTIFACT_NAME + "-" + str(attempt)]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError("Newest OeAD artifact unavailable", "access")
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != head
        or not numeric(artifact.get("size_in_bytes"))
        or artifact["size_in_bytes"] > 32768
        or not isinstance(artifact.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"])
    ):
        raise AdapterError("Invalid OeAD artifact binding", "access")
    return production_maintenance_state(download_inert_artifact(
        latest, artifact["id"], artifact["digest"]
    ))


def prepare_collection():
    global _GUARD_COLLECTION_ID
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    workflow_mode_guard()
    incoming = restored_state()
    if incoming["in_flight"]:
        raise AdapterError(
            "Newest owned attempt has an incomplete publisher request",
            "access",
        )
    # No directory, lock or empty durable budget exists before reviewed state.
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        acquired_id = None
        descriptor = secure_file(
            "collection.lock",
            os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
        )
        acquired_info = os.fstat(descriptor)
        acquired_id = (acquired_info.st_dev, acquired_info.st_ino)
        try:
            journal_owned(descriptor, acquired_id)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal_owned(descriptor, acquired_id)
            _GUARD_COLLECTION_ID = acquired_id
        except BaseException:
            if acquired_id is not None:
                journal_close(descriptor, acquired_id, True)
            raise AdapterError(
                "Another OeAD collector is active",
                "access",
            ) from None
        _COLLECTION_LOCK_FD = descriptor
    with locked_budget():
        now = time.time()
        combined = merge_budgets(load_budget(), incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        save_budget(combined)
    _COLLECTION_STARTED = time.time()
    _RESTORED = _STATE_TRUSTED = True


def parse_robots_policy(body):
    groups, group = [], {"agents": [], "rules": [], "delay": 0}
    text = body.decode("utf-8", "strict")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise AdapterError("Malformed OeAD REP", "access")
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            if not group["agents"]:
                raise AdapterError("OeAD REP group unavailable", "access")
            group["rules"].append((key, value))
        elif key in ("crawl-delay", "request-rate"):
            try:
                if key == "crawl-delay":
                    delay = float(value)
                else:
                    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", value)
                    if not match:
                        raise ValueError("Invalid request rate")
                    delay = int(match[2]) / int(match[1])
                if not numeric(delay):
                    raise ValueError("Invalid REP delay")
            except (ValueError, TypeError, OverflowError):
                raise AdapterError(
                    "Unsupported OeAD REP limit",
                    "access",
                ) from None
            group["delay"] = max(group["delay"], delay)
    groups.append(group)
    return groups


def selected_robots_policies(url):
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    groups = _ROBOTS[origin]
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [g for g in groups if agent in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    policies = [policy for policy in (current, legacy) if policy]
    if not policies:
        policies = [[g for g in groups if "*" in g["agents"]]]
    return policies


def enforce_robots(url):
    """Persist applicable policy inside a durably reserved physical boundary."""
    if not _PUBLISHER_IN_FLIGHT:
        raise AdapterError(
            "REP enforcement requires a pending physical boundary",
            "access",
        )
    parsed = urllib.parse.urlsplit(public_url(url))
    policies = selected_robots_policies(url)
    for selected in policies:
        matches = []
        for group in selected:
            for action, pattern in group["rules"]:
                if not pattern:
                    continue
                expression = re.escape(pattern).replace(r"\*", ".*")
                if expression.endswith(r"\$"):
                    expression = expression[:-2] + "$"
                if re.match(expression, parsed.path):
                    matches.append(
                        (len(
                            pattern.replace("*", "").removesuffix("$"),
                        ), action == "allow"),
                    )
        if matches and not max(matches)[1]:
            retain_publisher_hold()
            raise AdapterError("OeAD REP excludes required programme", "access")
    delay = max([12.2] + [g["delay"] for policy in policies for g in policy])
    with locked_budget():
        state = load_budget()
        state["interval"] = max(state["interval"], delay)
        if state["starts"]:
            state["not_before"] = max(
                state["not_before"], state["starts"][-1] + state["interval"]
            )
        state["observed_at"] = time.time()
        save_budget(state)
    return delay


def check_robots(url):
    """Retrieve policy with in-boundary assessment; cached reads add no wire."""
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if origin not in _ROBOTS:
        status, _, _ = source_response(
            origin + "/robots.txt", robots=True, robots_target=url
        )
        if origin not in _ROBOTS:
            raise AdapterError("OeAD REP unavailable", "access", status)
    policies = selected_robots_policies(url)
    return max(
        [12.2] + [group["delay"] for policy in policies for group in policy],
    )


def github_pace(url, interval=6):
    host = (urllib.parse.urlsplit(url).hostname or '').lower().removeprefix('www.')
    budget = _GITHUB_BUDGETS.setdefault(host, {'starts': deque(), 'interval': 6, 'until': 0})
    budget['interval'] = max(budget['interval'], interval, 6)
    while True:
        now = time.monotonic()
        while budget['starts'] and now - budget['starts'][0] >= 60:
            budget['starts'].popleft()
        ready = max(budget['until'], budget['starts'][-1] + budget['interval'] if budget['starts'] else now,
                    budget['starts'][0] + 60 if len(budget['starts']) >= 10 else now)
        if ready <= now:
            budget['starts'].append(now)
            return budget
        time.sleep(ready - now)


def github_request_bytes(url, *, interval=6, method='GET', headers=None, payload=None):
    budget = github_pace(url, interval)
    request = urllib.request.Request(url, data=payload, method=method, headers={'User-Agent': _USER_AGENT, **(headers or {})})
    try:
        response = _OPENER.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        retry = response.headers.get('Retry-After')
        if retry:
            try:
                seconds = float(retry) if re.fullmatch(r'\d+(?:\.\d+)?', retry) else parsedate_to_datetime(retry).timestamp() - time.time()
                budget['until'] = max(budget['until'], time.monotonic() + max(0, seconds))
            except (ValueError, TypeError, OverflowError):
                pass
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise AdapterError('Response exceeds 5 MB', 'fetch')
        return response.status, response.headers, body


def workflow_request_bytes(url, method, headers, payload):
    """Bound Actions metadata separately from publication transport."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.github.com"
            or not parsed.path.startswith("/repos/YouthOpps/data-pipeline/")
            or method != "GET" or payload is not None):
        raise AdapterError("Unreviewed workflow metadata request", "access")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": _USER_AGENT, **(headers or {})},
    )
    try:
        response = _OPENER.open(request, timeout=request_timeout())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise AdapterError(
                "Workflow metadata exceeds bounded limit",
                "access",
            )
        return response.status, response.headers, body


def artifact_request_bytes(url, method, headers, payload, byte_limit):
    """Bound signed-artifact reads on the reviewed host."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com"),
            )
            or parsed.username or parsed.password or parsed.port
            or method != "GET" or payload is not None or headers
            or byte_limit != 32768):
        raise AdapterError("Unreviewed inert artifact request", "access")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        response = _OPENER.open(request, timeout=request_timeout())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise AdapterError("Inert artifact exceeds bounded limit", "access")
        return response.status, response.headers, body


def header_rate_limit_failure_guard(function):
    """Preserve a marked HTTP 429 error through failing cleanup."""
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            current, seen = error, set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                if getattr(current, "_header429_unassessed", False):
                    raise current
                current = current.__context__
            raise
    invoke.__name__ = function.__name__
    invoke.__doc__ = function.__doc__
    invoke.__wrapped__ = function
    return invoke


@header_rate_limit_failure_guard
def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    byte_limit=5_000_000,
    robots=False,
    inventory=False,
    robots_target=None,
):
    """Every physical publisher request retains purpose and completion."""
    global _FIRST_PUBLISHER_REQUEST
    host = (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
    if host != "studyinaustria.at":
        if urllib.parse.urlsplit(url).path.startswith(
            "/repos/YouthOpps/data-pipeline/",
        ):
            return workflow_request_bytes(url, method, headers, payload)
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == "api.github.com" and parsed.path.startswith(
            "/repos/YouthOpps/data-source/"
        ):
            return github_request_bytes(
                url,
                interval=interval,
                method=method,
                headers=headers,
                payload=payload,
            )
        return artifact_request_bytes(url, method, headers, payload, byte_limit)
    if type(byte_limit) is not int or not 0 < byte_limit <= 30_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    headers = {
        k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"
    }
    headers["User-Agent"] = _USER_AGENT
    origin_parts = urllib.parse.urlsplit(url)
    robots_origin = urllib.parse.urlunsplit(
        origin_parts._replace(path="", query="", fragment=""),
    )
    policy_target = (
        robots_target
        or robots_origin
        + urllib.parse.urlsplit(
            SOURCE_URL,
        ).path
    )
    current = url
    for redirect in range(4):
        check_deadline()
        publisher = urllib.parse.urlsplit(current).hostname in {
            "www.studyinaustria.at",
            "studyinaustria.at",
        }
        if publisher:
            if _PHASE_NAME != "collection":
                raise AdapterError("OeAD source phase required", "access")
            if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
                raise AdapterError(
                    "Healthy restored OeAD state required",
                    "access",
                )
            public_url(current)
            parsed = urllib.parse.urlsplit(current)
            if (
                method != "GET"
                or payload is not None
                or (robots and (parsed.path != "/robots.txt" or inventory))
                or (robots_target is not None and not robots)
            ):
                raise AdapterError("Invalid OeAD request purpose", "access")
            if not robots:
                interval = max(interval, check_robots(current))
            if not numeric(interval):
                raise AdapterError("Invalid physical interval", "access")
            with locked_budget():
                state = load_budget()
                state["interval"] = max(state["interval"], interval, 12.2)
                state["observed_at"] = time.time()
                save_budget(state)
            if inventory:
                wait_inventory_page()
            pace()
            if _FIRST_PUBLISHER_REQUEST:
                workflow_mode_guard()
                _FIRST_PUBLISHER_REQUEST = False
        request = urllib.request.Request(
            current, data=payload, method=method, headers=headers
        )
        with publisher_attempt(publisher, inventory):
            if publisher and not robots:
                enforce_robots(current)
            try:
                if publisher:
                    journal_preopener()
                response = _OPENER.open(
                    request,
                    timeout=request_timeout(publisher),
                )
            except urllib.error.HTTPError as error:
                response = error
            except (urllib.error.URLError, OSError) as error:
                if publisher:
                    raise publisher_transport_error(error, current) from None
                raise
            with (
                header_rate_limit_response(response)
                if publisher and response.status == 429
                else response
            ):
                status, response_headers = response.status, response.headers
                if publisher:
                    record_retry_after(
                        response_headers.get("Retry-After"),
                        status,
                    )
                    if not robots and status in (401, 403, 451):
                        retain_publisher_hold()
                    if status == 429:
                        raise HeaderRateLimitStop(
                            "Publisher stopped: HTTP 429", "access", 429
                        )
                body = response.read(byte_limit + 1)
                if len(body) > byte_limit:
                    raise AdapterError(
                        "Response exceeds bounded limit",
                        "fetch",
                    )
                declared = response_headers.get("Content-Length")
                if declared and (
                    not declared.isdigit() or len(body) != int(declared)
                ):
                    raise AdapterError(
                        "Incomplete or invalid response length",
                        "fetch",
                    )
                if publisher and (not robots or status in (200, 429)):
                    reject_visible_challenge(
                        body, response_headers, challenge_only=status == 429
                    )
                if publisher and robots:
                    if status == 200:
                        _ROBOTS[robots_origin] = parse_robots_policy(body)
                        enforce_robots(policy_target)
                    elif 400 <= status < 500 and status != 429:
                        _ROBOTS[robots_origin] = []
                if publisher and (
                    status == 429 or not robots and status in (401, 403, 451)
                ):
                    raise AdapterError(
                        "OeAD stopped: HTTP " + str(status),
                        "access",
                        status,
                    )
                location = response_headers.get("Location")
        if not publisher or status not in (301, 302, 303, 307, 308):
            return status, response_headers, body
        if not location or redirect == 3:
            raise AdapterError("Invalid OeAD redirect chain", "access")
        destination = public_url(urllib.parse.urljoin(current, location))
        if robots and urllib.parse.urlsplit(destination).path != "/robots.txt":
            raise AdapterError("Robots purpose cannot follow content", "access")
        current = destination
    raise AdapterError("Unresolved OeAD redirect", "access")


def source_response(url, *, interval=12.2, robots=False, robots_target=None):
    """Retain both baseline connection attempts and four-hop redirect paths."""
    if not numeric(interval):
        raise AdapterError("Invalid source interval", "access")
    for attempt in range(2):
        try:
            return request_bytes(
                url, interval=max(12.2, interval), robots=robots,
                byte_limit=5_000_000, robots_target=robots_target,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if _PUBLISHER_GATE_FAILED:
                raise
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch",
                ) from error
        except AdapterError as error:
            if (attempt or error.stage != "fetch" or error.status is not None
                    or not str(error).startswith(
                        "Publisher transport failure at ",
                    )):
                raise
    raise AdapterError("Publisher could not be read", "fetch")


def fetch_source(url, min_interval=12.2):
    status, headers, body = source_response(
        url, interval=min_interval,
        robots=urllib.parse.urlsplit(url).path == "/robots.txt",
    )
    if status != 200:
        raise AdapterError(
            "Publisher returned HTTP " + str(status),
            "fetch",
            status,
        )
    return body.decode(headers.get_content_charset() or "utf-8", "strict")


def collect():
    """Run the unchanged complete programme parser after durable restoration."""
    if os.environ.get("DATA_SOURCE_TOKEN") and not _PUBLISHING:
        raise AdapterError(
            "Nonpublishing collection refuses publication token",
            "access",
        )
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RUN_END is None:
        with execution():
            return collect()
    if not _RESTORED:
        with phase("restore", 180):
            prepare_collection()
    try:
        with phase("collection", COLLECTION_TIMEOUT):
            return collect_programme()
    except PhaseExpired:
        raise AdapterError("OeAD collection phase exhausted", "fetch") from None


def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("OEAD_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError(
            "Explicit runner temporary directory required", "access"
        )
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    if os.path.basename(path) != "pacing-state.json":
        raise AdapterError("Unsafe pacing artifact path", "access")
    name = os.path.basename(path)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_identity = None
    output_identity = None
    output_handles = ()
    try:
        directory = os.fstat(parent)
        parent_identity = (directory.st_dev, directory.st_ino)
        if (
            directory.st_uid != os.getuid()
            or not stat.S_ISDIR(directory.st_mode)
            or directory.st_mode & 0o022
            or directory.st_mode & 0o700 != 0o700
        ):
            raise AdapterError(
                "Unsafe pacing artifact directory owner", "access"
            )
        existing = None
        try:
            existing = os.open(
                name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            pass
        if existing is not None:
            existing_identity = None
            try:
                info = os.fstat(existing)
                existing_identity = (info.st_dev, info.st_ino)
                if (
                    info.st_uid != os.getuid()
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                ):
                    raise AdapterError(
                        "Unsafe existing pacing artifact owner", "access"
                    )
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (
                    info.st_dev,
                    info.st_ino,
                ):
                    raise AdapterError(
                        "Pacing artifact identity changed", "access"
                    )
                # Invalidate before unlink: an unlink failure cannot leave a
                # valid stale envelope on a writable, owned artifact inode.
                io_artifact_descriptor(existing, existing_identity)
                os.ftruncate(existing, 0)
                os.fsync(existing)
                io_artifact_descriptor(existing, existing_identity)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            finally:
                io_close_owned_descriptor(existing, existing_identity)
        if _PUBLISHER_GATE_FAILED or _GUARD_FATAL:
            raise AdapterError(
                "Publisher completion gate cannot be exported", "access"
            )
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
            "head_sha": os.environ["GITHUB_SHA"],
            "state": state,
        }
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        acquired = os.fstat(descriptor)
        identity = (acquired.st_dev, acquired.st_ino)
        parent_id = parent_identity
        output_identity = identity
        guard = None
        output_handles = (descriptor, guard)
        try:
            io_artifact_descriptor(descriptor, identity)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise AdapterError(
                    "Owned artifact acquisition changed", "access"
                )
            guard = os.dup(descriptor)
            output_handles = (descriptor, guard)
            io_artifact_descriptor(guard, identity)
            with locked_budget():
                writer = os.dup(descriptor)
                try:
                    io_artifact_descriptor(writer, identity)
                    payload = json.dumps(
                        envelope, separators=(",", ":")
                    ).encode()
                    offset = 0
                    while offset < len(payload):
                        io_artifact_descriptor(writer, identity)
                        written = os.write(writer, payload[offset:])
                        if (
                            type(written) is not int
                            or not 0 < written <= len(payload) - offset
                        ):
                            raise AdapterError(
                                "Incomplete owned artifact write", "access"
                            )
                        offset += written
                        io_artifact_descriptor(writer, identity)
                    os.fsync(writer)
                    io_artifact_descriptor(writer, identity)
                finally:
                    io_close_owned_descriptor(writer, identity)
                os.fsync(parent)
                check = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
                try:
                    info = os.fstat(check)
                    current = os.stat(
                        name, dir_fd=parent, follow_symlinks=False
                    )
                    if (
                        info.st_uid != os.getuid()
                        or not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_nlink != 1
                        or (info.st_dev, info.st_ino)
                        != (current.st_dev, current.st_ino)
                    ):
                        raise AdapterError(
                            "Unsafe finalized artifact identity", "access"
                        )
                    reader_identity = (info.st_dev, info.st_ino)
                    before_read = journal_generation(info)
                    raw = os.read(check, 16385)
                    after_read = io_artifact_descriptor(check, reader_identity)
                    current = os.stat(
                        name, dir_fd=parent, follow_symlinks=False
                    )
                    if (
                        journal_generation(after_read) != before_read
                        or (current.st_dev, current.st_ino) != identity
                    ):
                        raise AdapterError(
                            "Artifact changed during read", "access"
                        )
                    if len(raw) > 16384 or json.loads(raw) != envelope:
                        raise AdapterError(
                            "Finalized pacing artifact mismatch", "access"
                        )
                finally:
                    # The known artifact identity is available even if the
                    # reader validation/read failed before reader_identity.
                    io_close_owned_descriptor(check, identity)
                check_deadline()
                journal_verify()
                io_mark_pacing_artifact_ready(parent, root)
                journal_verify()
                io_artifact_descriptor(descriptor, identity)
                io_artifact_descriptor(guard, identity)
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if identity != (named.st_dev, named.st_ino):
                    raise AdapterError("Owned artifact changed", "access")
        except BaseException:
            journal_failed()
            # Cleanup must never replace the primary handoff/body failure.
            with contextlib.suppress(BaseException):
                io_invalidate_owned_artifact(
                    parent, root, name, parent_id, identity, (guard, descriptor)
                )
            raise
        finally:
            primary = sys.exc_info()[1]
            errors = []
            for handle in (descriptor, guard):
                if handle is not None:
                    try:
                        io_artifact_descriptor(handle, identity)
                        os.close(handle)
                    except BaseException as error:
                        journal_failed()
                        errors.append(error)
            if errors:
                with contextlib.suppress(BaseException):
                    io_invalidate_owned_artifact(
                        parent,
                        root,
                        name,
                        parent_id,
                        identity,
                        (guard, descriptor),
                    )
                if primary is None:
                    raise errors[0]
    finally:
        primary = sys.exc_info()[1]
        close_error = None
        try:
            io_verify_owned_descriptor(parent, parent_identity, True)
            os.close(parent)
        except BaseException as error:
            journal_failed()
            close_error = error
        if close_error is not None or _GUARD_FATAL:
            # Keep the original primary even when a parent replacement made
            # earlier output cleanup impossible through its retained FD.
            if output_identity is not None:
                recovery = None
                with contextlib.suppress(BaseException):
                    recovery = os.open(
                        root,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | os.O_NONBLOCK,
                    )
                    io_verify_owned_descriptor(recovery, parent_identity, True)
                    io_invalidate_owned_artifact(
                        recovery,
                        root,
                        name,
                        parent_identity,
                        output_identity,
                        output_handles,
                    )
                if recovery is not None:
                    with contextlib.suppress(BaseException):
                        io_close_owned_descriptor(
                            recovery, parent_identity, True
                        )
        if close_error is not None and primary is None:
            raise close_error


def main():
    global _PUBLISHING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    _PUBLISHING = args.publish
    result = 1
    with execution():
        try:
            with phase("restore", 180):
                prepare_collection()
            result = run_body(args.publish)
        except PhaseExpired:
            result = 1
        finally:
            with phase("export", EXPORT_TIMEOUT):
                export_family_artifact()
    return result




_GUARD_PARENT = _GUARD_PARENT_ID = None
_GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
_GUARD_LOCK_ID = None
_GUARD_LOCKS = {}
_GUARD_CREATED = _GUARD_DIRTY = _GUARD_FATAL = False
_GUARD_COLLECTION_ID = None
_GUARD_NEW_DIRECTORY = False
_GUARD_FAILED_STATE = None


def journal_generation(info):
    """Identify one observed inode and byte generation, not an FS CAS."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    )


def journal_failed():
    """Keep storage failures sticky independently of completion flags."""
    global _GUARD_FATAL, _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    _GUARD_FATAL = _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False


def journal_parent():
    """Retain the original safe parent; never follow a replacement."""
    global _GUARD_PARENT, _GUARD_PARENT_ID
    directory = os.path.dirname(_STATE_PATH)
    descriptor = _GUARD_PARENT
    fresh = descriptor is None
    if fresh:
        descriptor = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    identity = None
    try:
        held = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (held.st_dev, held.st_ino)
        if (
            not stat.S_ISDIR(held.st_mode)
            or held.st_uid != os.getuid()
            or stat.S_IMODE(held.st_mode) != 0o700
            or held.st_nlink < 2
            or identity != (current.st_dev, current.st_ino)
            or not fresh
            and identity != _GUARD_PARENT_ID
        ):
            raise AdapterError("Publisher journal parent changed", "access")
    except BaseException:
        journal_failed()
        if fresh and identity is not None:
            io_close_owned_descriptor(descriptor, identity, True)
        raise
    if fresh:
        _GUARD_PARENT, _GUARD_PARENT_ID = descriptor, identity
    return descriptor


def journal_owned(descriptor, identity=None, unlinked=False):
    """Check the actual acquired FD before use or release."""
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink not in ((0, 1) if unlinked else (1,))
            or identity is not None
            and (info.st_dev, info.st_ino) != identity
        ):
            raise AdapterError(
                "Publisher descriptor ownership changed", "access"
            )
        return info
    except BaseException:
        journal_failed()
        raise


def journal_close(descriptor, identity, primary=False, unlinked=False):
    """Preserve foreign reused FDs and an already active primary error."""
    try:
        journal_owned(descriptor, identity, unlinked)
        os.close(descriptor)
    except BaseException:
        journal_failed()
        if not primary:
            raise


def journal_verify(check_generation=True):
    """Require original current RW journal and all actually held locks."""
    try:
        if _GUARD_FATAL or _GUARD_FD is None:
            raise AdapterError(
                "Publisher journal authority unavailable", "access"
            )
        parent = journal_parent()
        info = journal_owned(_GUARD_FD, _GUARD_ID)
        current = os.stat(
            os.path.basename(_STATE_PATH),
            dir_fd=parent,
            follow_symlinks=False,
        )
        if (
            (current.st_dev, current.st_ino) != _GUARD_ID
            or check_generation
            and journal_generation(info) != _GUARD_VERSION
            or fcntl.fcntl(_GUARD_FD, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        for descriptor, identity in _GUARD_LOCKS.items():
            journal_owned(descriptor, identity)
        if _GUARD_LOCK_ID is not None:
            lock = os.stat(
                os.path.basename(_STATE_LOCK),
                dir_fd=parent,
                follow_symlinks=False,
            )
            if (lock.st_dev, lock.st_ino) != _GUARD_LOCK_ID:
                raise AdapterError("Publisher journal lock changed", "access")
        return info
    except BaseException:
        journal_failed()
        raise


def journal_retain(descriptor=None):
    """Pin a genuine RW journal only under its original stable lock."""
    global _GUARD_FD, _GUARD_ID, _GUARD_VERSION, _GUARD_CREATED
    if _GUARD_FD is not None:
        journal_verify()
        return
    parent = journal_parent()
    name = os.path.basename(_STATE_PATH)
    if descriptor is not None:
        info = journal_owned(descriptor)
        duplicate = os.dup(descriptor)
        try:
            journal_owned(duplicate, (info.st_dev, info.st_ino))
        except BaseException:
            journal_failed()
            raise
        descriptor = duplicate
    else:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            if not journal_creation_allowed():
                raise AdapterError("Mature publisher journal missing", "access")
            descriptor = os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            _GUARD_CREATED = True
    info = journal_owned(descriptor)
    _GUARD_FD = descriptor
    _GUARD_ID = (info.st_dev, info.st_ino)
    _GUARD_VERSION = journal_generation(info)
    journal_verify()
    if _GUARD_CREATED:
        os.fsync(parent)


class JournalLock:
    """Own an acquired lock FD without implicit file-object destruction."""

    def __init__(self, descriptor, identity):
        self.descriptor = descriptor
        self.identity = identity
        _GUARD_LOCKS[descriptor] = identity

    def fileno(self):
        journal_owned(self.descriptor, self.identity)
        return self.descriptor

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def close(self):
        descriptor = self.descriptor
        if descriptor is None:
            return
        primary = sys.exc_info()[0] is not None
        self.descriptor = None
        _GUARD_LOCKS.pop(descriptor, None)
        try:
            journal_owned(descriptor, self.identity, True)
            if not _GUARD_LOCKS:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            journal_owned(descriptor, self.identity, True)
            os.close(descriptor)
        except BaseException:
            journal_failed()
            if not primary:
                raise


def journal_read():
    """Read and recheck the same current generation before trusting bytes."""
    try:
        if not _GUARD_LOCKS:
            raise AdapterError("Publisher journal lock required", "access")
        journal_verify()
        raw = os.pread(_GUARD_FD, 16385, 0)
        journal_verify()
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher journal", "access")
        return raw
    except BaseException:
        journal_failed()
        raise


def journal_commit(state):
    """Rewrite only our held inode; interrupted bytes sacrifice availability."""
    global _GUARD_VERSION, _GUARD_CREATED, _GUARD_DIRTY
    global _GUARD_FAILED_STATE
    if _GUARD_FATAL:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        payload = json.dumps(state, separators=(",", ":")).encode()
        if not _GUARD_LOCKS or len(payload) > 16384:
            raise AdapterError(
                "Bounded locked publisher commit required", "access"
            )
        journal_verify()
        _GUARD_CREATED = False
        journal_mark_mature()
        _GUARD_DIRTY = True
        os.ftruncate(_GUARD_FD, 0)
        os.fsync(_GUARD_FD)
        journal_verify(False)
        offset = 0
        while offset < len(payload):
            journal_verify(False)
            written = os.pwrite(_GUARD_FD, payload[offset:], offset)
            if (
                type(written) is not int
                or not 0 < written <= len(payload) - offset
            ):
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += written
            journal_verify(False)
        os.fsync(_GUARD_FD)
        before = journal_verify(False)
        raw = os.pread(_GUARD_FD, len(payload) + 1, 0)
        after = journal_verify(False)
        if raw != payload or journal_generation(before) != journal_generation(
            after
        ):
            raise AdapterError("Publisher journal readback changed", "access")
        _GUARD_VERSION = journal_generation(after)
        _GUARD_DIRTY = False
    except BaseException:
        _GUARD_FAILED_STATE = json.loads(json.dumps(state))
        journal_failed()
        with contextlib.suppress(BaseException):
            journal_invalidate()
        raise


def journal_invalidate():
    """Invalidate only our own bytes, never a stronger changed generation."""
    if _GUARD_FD is None:
        return
    journal_parent()
    info = journal_owned(_GUARD_FD, _GUARD_ID, True)
    current = os.stat(
        os.path.basename(_STATE_PATH),
        dir_fd=_GUARD_PARENT,
        follow_symlinks=False,
    )
    if (current.st_dev, current.st_ino) != _GUARD_ID:
        return
    if not _GUARD_DIRTY and journal_generation(info) != _GUARD_VERSION:
        return
    os.ftruncate(_GUARD_FD, 0)
    os.fsync(_GUARD_FD)


def journal_preopener():
    """Final publisher authority check, after request construction."""
    try:
        journal_verify()
        if _GUARD_COLLECTION_ID is None or _COLLECTION_LOCK_FD is None:
            raise AdapterError(
                "Publisher collection ownership missing", "access"
            )
        journal_owned(_COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID)
    except BaseException:
        journal_failed()
        raise


def journal_release():
    """Release retained IO only; never close a recycled foreign FD."""
    global _GUARD_FD, _GUARD_ID, _GUARD_VERSION
    global _GUARD_PARENT, _GUARD_PARENT_ID
    descriptor, identity = _GUARD_FD, _GUARD_ID
    _GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
    primary = sys.exc_info()[1]
    errors = []
    if descriptor is not None:
        try:
            journal_close(descriptor, identity, False, True)
        except BaseException as error:
            errors.append(error)
    parent, parent_id = _GUARD_PARENT, _GUARD_PARENT_ID
    _GUARD_PARENT = _GUARD_PARENT_ID = None
    if parent is not None:
        try:
            io_close_owned_descriptor(parent, parent_id, True)
        except BaseException as error:
            errors.append(error)
    if errors and primary is None:
        raise errors[0]


def journal_creation_allowed():
    return _STATE_INITIALIZING


def journal_mark_mature():
    global _STATE_INITIALIZING
    _STATE_INITIALIZING = False


def io_verify_owned_descriptor(descriptor, expected, directory=False):
    """Verify an acquired IO descriptor without adopting a recycled number."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != expected
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or directory
        and not stat.S_ISDIR(info.st_mode)
        or not directory
        and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
    ):
        raise AdapterError("Owned IO descriptor changed", "access")
    return info


def io_close_owned_descriptor(descriptor, expected, directory=False):
    """Close our acquired object only, preserving an active primary error."""
    primary = sys.exc_info()[1]
    try:
        info = os.fstat(descriptor)
        if not directory and info.st_nlink == 0:
            # An exact acquired file may have been deliberately unlinked.
            # Closing that original inode is safe; live path checks stay strict.
            if (
                (info.st_dev, info.st_ino) != expected
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
            ):
                raise AdapterError("Owned IO descriptor changed", "access")
        else:
            io_verify_owned_descriptor(descriptor, expected, directory)
        os.close(descriptor)
    except BaseException:
        journal_failed()
        if primary is None:
            raise


def io_mark_pacing_artifact_ready(runner_parent, runner_root):
    """Signal readiness while checking every acquired output descriptor."""
    value = os.environ.get("GITHUB_OUTPUT")
    if not value:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Fresh step output required", "access")
        return
    if not os.path.isabs(value):
        raise AdapterError("Unsafe step output path", "access")
    relative = os.path.relpath(value, runner_root)
    parts = relative.split(os.sep)
    if not parts or any(name in {"", ".", ".."} for name in parts):
        raise AdapterError(
            "Step output outside runner temporary root", "access"
        )
    descriptors = []
    parent = runner_parent
    root_info = os.fstat(runner_parent)
    root_id = (root_info.st_dev, root_info.st_ino)

    def verify_outputs():
        io_verify_owned_descriptor(runner_parent, root_id, True)
        named_root = os.stat(runner_root, follow_symlinks=False)
        if (named_root.st_dev, named_root.st_ino) != root_id:
            raise AdapterError("Step output root changed", "access")
        for fd, identity, is_directory, parent_fd, name in descriptors:
            io_verify_owned_descriptor(fd, identity, is_directory)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != identity:
                raise AdapterError("Step output path changed", "access")

    try:
        for name in parts[:-1]:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            descriptors.append((descriptor, identity, True, parent, name))
            verify_outputs()
            parent = descriptor
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        descriptors.append((descriptor, identity, False, parent, parts[-1]))
        verify_outputs()
        line = b"pacing_ready=true\n"
        if os.write(descriptor, line) != len(line):
            raise AdapterError("Incomplete pacing-ready handoff", "access")
        verify_outputs()
        os.fsync(descriptor)
        verify_outputs()
    except BaseException:
        journal_failed()
        raise
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, identity, is_directory, _, _ in reversed(descriptors):
            try:
                io_close_owned_descriptor(fd, identity, is_directory)
            except BaseException as error:
                errors.append(error)
        if errors and primary is None:
            raise errors[0]


def io_artifact_descriptor(descriptor, expected):
    """Require the genuine acquired output inode, not a recycled FD number."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != expected
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink not in (0, 1)
    ):
        raise AdapterError("Owned artifact descriptor changed", "access")
    return info


def io_invalidate_owned_artifact(
    parent, root, name, parent_id, identity, handles
):
    """Invalidate only our held original or an exact known original name."""
    held_parent = os.fstat(parent)
    named_parent = os.stat(root, follow_symlinks=False)
    if (held_parent.st_dev, held_parent.st_ino) != parent_id or (
        named_parent.st_dev,
        named_parent.st_ino,
    ) != parent_id:
        raise AdapterError("Owned artifact parent changed", "access")
    descriptor = None
    owned = False
    for handle in handles:
        if handle is not None:
            try:
                io_artifact_descriptor(handle, identity)
                descriptor = handle
                break
            except (OSError, AdapterError):
                pass
    if descriptor is None:
        # Last-close errors may leave no held FD. Reopening is allowed only
        # for this exact original output identity, never a newer pathname.
        descriptor = os.open(
            name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        owned = True
    try:
        io_artifact_descriptor(descriptor, identity)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=parent)
    finally:
        if owned:
            primary = sys.exc_info()[1]
            try:
                io_artifact_descriptor(descriptor, identity)
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise


def journal_finish():
    """Release both independently; a healthy close failure is fatal."""
    global _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    primary = sys.exc_info()[1]
    errors = []
    descriptor, identity = _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    _COLLECTION_LOCK_FD = _GUARD_COLLECTION_ID = None
    if descriptor is not None:
        try:
            if identity is None:
                raise AdapterError("Collection identity not acquired", "access")
            journal_close(descriptor, identity)
        except BaseException as error:
            journal_failed()
            errors.append(error)
    try:
        journal_release()
    except BaseException as error:
        journal_failed()
        errors.append(error)
    if _GUARD_FATAL:
        with contextlib.suppress(BaseException):
            export_family_artifact()
    if errors and primary is None:
        raise errors[0]


class HeaderRateLimitStop(AdapterError):
    """A header-only HTTP 429 stop assessed after safe closure."""


@contextlib.contextmanager
def header_rate_limit_response(response):
    """Retain existing barriers when closing an HTTP 429 fails."""
    try:
        with response:
            yield
    except HeaderRateLimitStop:
        raise
    except BaseException as error:
        error._header429_unassessed = True
        # Latch before any fallible best-effort diagnostics.
        with contextlib.suppress(BaseException):
            journal_failed()
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, PhaseExpired) as error:
        print(json.dumps({
            "source": SOURCE_ID, "status": "fail", "last_attempt_at": utc_now(),
            "message": "Run could not complete; durable status unavailable",
            "error": safe_error(error),
            "failure_stage": getattr(error, "stage", "publish"),
        }, ensure_ascii=False))
        sys.exit(1)
