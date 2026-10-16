"""Discover ONEK funding rounds and public information events."""

import argparse
import base64
import contextlib
import copy
import errno
import functools
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import signal
import socket
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "cy-onek-youth-funds"
SOURCE_URL = "https://youthfunds.onek.org.cy/en/services/youth-initiatives/"
WEBSITE_URL = "https://youthfunds.onek.org.cy/en/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "CY"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Youth Board of Cyprus (ONEK), https://youthfunds.onek.org.cy/en/; original factual summaries with canonical publisher links"
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {
    "scholarships",
    "grants",
    "internships",
    "jobs",
    "training",
    "fellowships",
}
FAMILY = "youthopps-onek-publisher-v1"
FAMILY_SOURCES = {"cy-onek-youth-funds"}
COLLECTION_TIMEOUT = 2400
RUN_TIMEOUT = 2790
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 3
_ARTIFACT_NAME = "onek-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None
_PAGE_COMPLETED = None

_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_INITIALIZING = False
_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None
_COLLECTION_LOCK_ID = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False
_LOCAL_FIRST_WIRE_VERIFIED = False
_INVENTORY_COMPLETED_MONOTONIC = None
_RUN_END = _PHASE_END = _PHASE_NAME = None
_ENTRY_MONOTONIC = None
_PHASE_SEEN = set()
_FAILURE_CONTEXT = None
_INITIAL_SNAPSHOT_ATTEMPTED = False
_OPERATION_END = None
_ROBOTS = {}
_REP_TARGETS = {}
_HISTORY_COMPLETED = []
_HISTORY_JOBLESS = []
_ACTIVE_FLIGHT_ID = _COMPLETING_FLIGHT_ID = None
_ACTIVE_REQUEST_ID = None
_TRANSACTION_DEPTH = 0
_REQUEST_FRAMES = []
_FAILED_BUDGET = None
_JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
_JOURNAL_CREATED = _JOURNAL_DIRTY = False
_JOURNAL_LOCKS = {}
_JOURNAL_EXPOSED = {}

_WORKFLOW_ID = 380368061
_WORKFLOW_NAME = "Fetch ONEK youth funding opportunities"
_INVENTORY_ACTIVE = False
_LOCAL_SCHEMA1_REHYDRATION = None
_LEGACY_JOB_PINS = {
    "38822317512": {
        "attempt": 1,
        "head": "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
        "jobs": [
            {
                "id": 116721548023,
                "run_id": 38822317512,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38822317512",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbLSV69w",
                "head_sha": "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116721548023",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38822317512/job/116721548023",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T23:13:58Z",
                "started_at": "2026-10-15T23:14:01Z",
                "completed_at": "2026-10-15T23:17:57Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T23:14:01Z",
                        "completed_at": "2026-10-15T23:14:03Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T23:14:03Z",
                        "completed_at": "2026-10-15T23:14:04Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T23:14:04Z",
                        "completed_at": "2026-10-15T23:14:04Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T23:14:04Z",
                        "completed_at": "2026-10-15T23:14:05Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T23:14:05Z",
                        "completed_at": "2026-10-15T23:17:53Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-15T23:17:53Z",
                        "completed_at": "2026-10-15T23:17:54Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T23:17:54Z",
                        "completed_at": "2026-10-15T23:17:54Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T23:17:54Z",
                        "completed_at": "2026-10-15T23:17:54Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-15T23:17:54Z",
                        "completed_at": "2026-10-15T23:17:54Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-15T23:17:54Z",
                        "completed_at": "2026-10-15T23:17:54Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116721548023",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002642,
                "runner_name": "GitHub Actions 1000002642",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38763419550": {
        "attempt": 1,
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs": [
            {
                "id": 116520330700,
                "run_id": 38763419550,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38763419550",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbISclzA",
                "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116520330700",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38763419550/job/116520330700",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T15:47:28Z",
                "started_at": "2026-10-15T15:47:29Z",
                "completed_at": "2026-10-15T15:51:19Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T15:47:30Z",
                        "completed_at": "2026-10-15T15:47:31Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T15:47:31Z",
                        "completed_at": "2026-10-15T15:47:32Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T15:47:32Z",
                        "completed_at": "2026-10-15T15:47:32Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T15:47:32Z",
                        "completed_at": "2026-10-15T15:47:33Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T15:47:33Z",
                        "completed_at": "2026-10-15T15:51:16Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-15T15:51:16Z",
                        "completed_at": "2026-10-15T15:51:16Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T15:51:16Z",
                        "completed_at": "2026-10-15T15:51:17Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T15:51:17Z",
                        "completed_at": "2026-10-15T15:51:17Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-15T15:51:17Z",
                        "completed_at": "2026-10-15T15:51:17Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-15T15:51:17Z",
                        "completed_at": "2026-10-15T15:51:17Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116520330700",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002517,
                "runner_name": "GitHub Actions 1000002517",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38721259254": {
        "attempt": 1,
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs": [
            {
                "id": 116376105478,
                "run_id": 38721259254,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38721259254",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbGI5yBg",
                "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116376105478",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38721259254/job/116376105478",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T10:34:44Z",
                "started_at": "2026-10-15T10:34:46Z",
                "completed_at": "2026-10-15T10:39:53Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T10:34:47Z",
                        "completed_at": "2026-10-15T10:34:48Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T10:34:48Z",
                        "completed_at": "2026-10-15T10:34:50Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T10:34:50Z",
                        "completed_at": "2026-10-15T10:34:50Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T10:34:50Z",
                        "completed_at": "2026-10-15T10:34:51Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T10:34:51Z",
                        "completed_at": "2026-10-15T10:39:48Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-15T10:39:48Z",
                        "completed_at": "2026-10-15T10:39:49Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T10:39:49Z",
                        "completed_at": "2026-10-15T10:39:50Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T10:39:50Z",
                        "completed_at": "2026-10-15T10:39:50Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-15T10:39:50Z",
                        "completed_at": "2026-10-15T10:39:50Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-15T10:39:50Z",
                        "completed_at": "2026-10-15T10:39:50Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116376105478",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002363,
                "runner_name": "GitHub Actions 1000002363",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38720084083": {
        "attempt": 1,
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs": [
            {
                "id": 116372207558,
                "run_id": 38720084083,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38720084083",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbGFL3xg",
                "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116372207558",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38720084083/job/116372207558",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T10:24:51Z",
                "started_at": "2026-10-15T10:24:53Z",
                "completed_at": "2026-10-15T10:29:59Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T10:24:53Z",
                        "completed_at": "2026-10-15T10:24:54Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T10:24:54Z",
                        "completed_at": "2026-10-15T10:24:55Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T10:24:55Z",
                        "completed_at": "2026-10-15T10:24:55Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T10:24:55Z",
                        "completed_at": "2026-10-15T10:24:56Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T10:24:56Z",
                        "completed_at": "2026-10-15T10:29:57Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-15T10:29:57Z",
                        "completed_at": "2026-10-15T10:29:57Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T10:29:57Z",
                        "completed_at": "2026-10-15T10:29:58Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T10:29:58Z",
                        "completed_at": "2026-10-15T10:29:58Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-15T10:29:58Z",
                        "completed_at": "2026-10-15T10:29:58Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-15T10:29:58Z",
                        "completed_at": "2026-10-15T10:29:58Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116372207558",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002349,
                "runner_name": "GitHub Actions 1000002349",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38684236179": {
        "attempt": 1,
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "id": 116255336259,
                "run_id": 38684236179,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38684236179",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbEVunQw",
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116255336259",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38684236179/job/116255336259",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T04:57:59Z",
                "started_at": "2026-10-15T04:58:01Z",
                "completed_at": "2026-10-15T05:02:50Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T04:58:01Z",
                        "completed_at": "2026-10-15T04:58:02Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T04:58:02Z",
                        "completed_at": "2026-10-15T04:58:03Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T04:58:03Z",
                        "completed_at": "2026-10-15T04:58:03Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T04:58:03Z",
                        "completed_at": "2026-10-15T04:58:04Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T04:58:04Z",
                        "completed_at": "2026-10-15T05:02:47Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-15T05:02:47Z",
                        "completed_at": "2026-10-15T05:02:47Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T05:02:47Z",
                        "completed_at": "2026-10-15T05:02:48Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T05:02:48Z",
                        "completed_at": "2026-10-15T05:02:48Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-15T05:02:48Z",
                        "completed_at": "2026-10-15T05:02:48Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-15T05:02:48Z",
                        "completed_at": "2026-10-15T05:02:48Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116255336259",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002285,
                "runner_name": "GitHub Actions 1000002285",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38534647007": {
        "attempt": 1,
        "head": "5146041d2628fb6f03c28d7ee7a2d58c3db8f443",
        "jobs": [
            {
                "id": 115754463649,
                "run_id": 38534647007,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38534647007",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa84DxoQ",
                "head_sha": "5146041d2628fb6f03c28d7ee7a2d58c3db8f443",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115754463649",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38534647007/job/115754463649",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T08:22:28Z",
                "started_at": "2026-10-14T08:22:30Z",
                "completed_at": "2026-10-14T08:26:23Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T08:22:30Z",
                        "completed_at": "2026-10-14T08:22:31Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T08:22:31Z",
                        "completed_at": "2026-10-14T08:22:32Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T08:22:32Z",
                        "completed_at": "2026-10-14T08:22:32Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T08:22:32Z",
                        "completed_at": "2026-10-14T08:22:33Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T08:22:33Z",
                        "completed_at": "2026-10-14T08:26:20Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-14T08:26:20Z",
                        "completed_at": "2026-10-14T08:26:21Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T08:26:21Z",
                        "completed_at": "2026-10-14T08:26:21Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T08:26:21Z",
                        "completed_at": "2026-10-14T08:26:21Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-14T08:26:21Z",
                        "completed_at": "2026-10-14T08:26:21Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-14T08:26:22Z",
                        "completed_at": "2026-10-14T08:26:22Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115754463649",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002129,
                "runner_name": "GitHub Actions 1000002129",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38507918591": {
        "attempt": 1,
        "head": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "jobs": [
            {
                "id": 115669556741,
                "run_id": 38507918591,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38507918591",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa7nFeBQ",
                "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115669556741",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38507918591/job/115669556741",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T03:45:50Z",
                "started_at": "2026-10-14T03:45:52Z",
                "completed_at": "2026-10-14T03:49:46Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T03:45:53Z",
                        "completed_at": "2026-10-14T03:45:55Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T03:45:55Z",
                        "completed_at": "2026-10-14T03:45:56Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T03:45:56Z",
                        "completed_at": "2026-10-14T03:45:56Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T03:45:56Z",
                        "completed_at": "2026-10-14T03:45:57Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T03:45:57Z",
                        "completed_at": "2026-10-14T03:49:42Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-14T03:49:42Z",
                        "completed_at": "2026-10-14T03:49:43Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T03:49:43Z",
                        "completed_at": "2026-10-14T03:49:43Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T03:49:43Z",
                        "completed_at": "2026-10-14T03:49:43Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-14T03:49:43Z",
                        "completed_at": "2026-10-14T03:49:44Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-14T03:49:44Z",
                        "completed_at": "2026-10-14T03:49:44Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115669556741",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002101,
                "runner_name": "GitHub Actions 1000002101",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38394202618": {
        "attempt": 1,
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115285179440,
                "run_id": 38394202618,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38394202618",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa14g8MA",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115285179440",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38394202618/job/115285179440",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-10-13T12:00:46Z",
                "started_at": "2026-10-13T12:00:49Z",
                "completed_at": "2026-10-13T12:02:57Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-13T12:00:49Z",
                        "completed_at": "2026-10-13T12:00:50Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-13T12:00:50Z",
                        "completed_at": "2026-10-13T12:00:52Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-13T12:00:52Z",
                        "completed_at": "2026-10-13T12:00:52Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-13T12:00:52Z",
                        "completed_at": "2026-10-13T12:00:52Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "failure",
                        "number": 5,
                        "started_at": "2026-10-13T12:00:52Z",
                        "completed_at": "2026-10-13T12:02:53Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-13T12:02:53Z",
                        "completed_at": "2026-10-13T12:02:54Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-13T12:02:54Z",
                        "completed_at": "2026-10-13T12:02:55Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "skipped",
                        "number": 11,
                        "started_at": "2026-10-13T12:02:55Z",
                        "completed_at": "2026-10-13T12:02:55Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-13T12:02:55Z",
                        "completed_at": "2026-10-13T12:02:55Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-13T12:02:55Z",
                        "completed_at": "2026-10-13T12:02:55Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115285179440",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000002040,
                "runner_name": "GitHub Actions 1000002040",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38037226667": {
        "attempt": 1,
        "head": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
        "jobs": [
            {
                "id": 114171094814,
                "run_id": 38037226667,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38037226667",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAalSCnHg",
                "head_sha": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114171094814",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38037226667/job/114171094814",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T08:21:01Z",
                "started_at": "2026-10-10T08:21:04Z",
                "completed_at": "2026-10-10T08:27:00Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T08:21:04Z",
                        "completed_at": "2026-10-10T08:21:06Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T08:21:06Z",
                        "completed_at": "2026-10-10T08:21:07Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T08:21:07Z",
                        "completed_at": "2026-10-10T08:21:07Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T08:21:07Z",
                        "completed_at": "2026-10-10T08:21:08Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T08:21:08Z",
                        "completed_at": "2026-10-10T08:26:56Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-10T08:26:56Z",
                        "completed_at": "2026-10-10T08:26:57Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T08:26:57Z",
                        "completed_at": "2026-10-10T08:26:58Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T08:26:58Z",
                        "completed_at": "2026-10-10T08:26:58Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-10T08:26:58Z",
                        "completed_at": "2026-10-10T08:26:58Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-10T08:26:58Z",
                        "completed_at": "2026-10-10T08:26:58Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114171094814",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000001662,
                "runner_name": "GitHub Actions 1000001662",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
    "38037221453": {
        "attempt": 1,
        "head": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
        "jobs": [
            {
                "id": 114170073055,
                "run_id": 38037221453,
                "workflow_name": "Fetch ONEK youth funding " "opportunities",
                "head_branch": "main",
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38037221453",
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAalREP3w",
                "head_sha": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114170073055",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38037221453/job/114170073055",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T08:15:02Z",
                "started_at": "2026-10-10T08:15:04Z",
                "completed_at": "2026-10-10T08:21:01Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T08:15:05Z",
                        "completed_at": "2026-10-10T08:15:06Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T08:15:06Z",
                        "completed_at": "2026-10-10T08:15:07Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T08:15:07Z",
                        "completed_at": "2026-10-10T08:15:08Z",
                    },
                    {
                        "name": "Authenticate data-source " "publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T08:15:08Z",
                        "completed_at": "2026-10-10T08:15:08Z",
                    },
                    {
                        "name": "Collect and publish " "reviewed source",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T08:15:08Z",
                        "completed_at": "2026-10-10T08:20:57Z",
                    },
                    {
                        "name": "Preserve inert publisher " "pacing state",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 6,
                        "started_at": "2026-10-10T08:20:57Z",
                        "completed_at": "2026-10-10T08:20:58Z",
                    },
                    {
                        "name": "Post Authenticate " "data-source publication",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T08:20:58Z",
                        "completed_at": "2026-10-10T08:20:58Z",
                    },
                    {
                        "name": "Post Run " "actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T08:20:58Z",
                        "completed_at": "2026-10-10T08:20:58Z",
                    },
                    {
                        "name": "Post Run " "actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 12,
                        "started_at": "2026-10-10T08:20:58Z",
                        "completed_at": "2026-10-10T08:20:58Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 13,
                        "started_at": "2026-10-10T08:20:58Z",
                        "completed_at": "2026-10-10T08:20:59Z",
                    },
                ],
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114170073055",
                "labels": ["ubuntu-latest"],
                "runner_id": 1000001661,
                "runner_name": "GitHub Actions 1000001661",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ],
    },
}
_LEGACY_TRANSITION = {
    "schema": 1,
    "family": "youthopps-onek-publisher-v1",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/325#issuecomment-6213562887",
    "history_sha256": "c4f8f6944ce0bad9347314478d84b2b42dddaa1644a95f105a969209807f7b56",
    "state": {
        "schema": 3,
        "family": "youthopps-onek-publisher-v1",
        "observed_at": 1792146277.0898156,
        "not_before": 1792146337.0898156,
        "starts": [
            1791620461.0,
            1791620820.0,
            1791892977.0,
            1791949786.0,
            1791966383.0,
            1792040570.0,
            1792060199.0,
            1792060793.0,
            1792079479.0,
            1792106215.6552544,
            1792106221.6553354,
            1792106227.655434,
            1792106233.6555305,
            1792106239.655675,
            1792106245.655774,
            1792106251.655868,
            1792106257.6559896,
            1792106263.6560829,
            1792106269.656179,
            1792106277.0,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792106277.0,
        "page_interval": 30,
        "last_inventory_completed": 1792106277.0,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "flight_started": 0,
        "flight_kind": None,
        "completed_flight_id": None,
        "request_id": None,
        "request_started": 0,
        "request_inventory": False,
        "completed_request_id": None,
        "request_outcome": None,
    },
    "legacy_original": {
        "local": {
            "status": "ABSENT",
            "path": "/tmp/youthopps-onek-publisher-v1-1000",
            "initialization": {
                "purpose": "PROSPECTIVE_FIRST_LOCAL_INITIALIZATION",
                "nonce": "0d60287c727003cd088bd46f1ce66f56",
                "ancestor": {
                    "path": "/tmp",
                    "identity": [34, 1],
                    "uid": 65534,
                    "mode": 1023,
                    "trusted_parent": {
                        "path": "/",
                        "name": "tmp",
                        "identity": [27, 524289],
                        "uid": 65534,
                        "mode": 493,
                    },
                },
                "leaf": "youthopps-onek-publisher-v1-1000",
                "claim_name": ".youthopps-onek-publisher-v1-init-0d60287c727003cd088bd46f1ce66f56.claim",
                "history_sha256": "c4f8f6944ce0bad9347314478d84b2b42dddaa1644a95f105a969209807f7b56",
                "latest_owned": {
                    "identity": [
                        38822317512,
                        1,
                        "cy-onek-youth-funds",
                        "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
                    ],
                    "artifact_id": 12013682993,
                    "digest": "sha256:75bf72f3ee339f8a43ef919a227b3bfc6bc0a9963504378b09594bb8f6fd28e4",
                },
                "state_sha256": "04ab1666a11ef3102a66ccf974ffe7521f007da2935cdcb02aacc47b46a029b1",
            },
        },
        "artifacts_by_run": {
            "38822317512": [
                {
                    "id": 12013682993,
                    "node_id": "MDg6QXJ0aWZhY3QxMjAxMzY4Mjk5Mw==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 402,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/12013682993",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/12013682993/zip",
                    "expired": False,
                    "digest": "sha256:75bf72f3ee339f8a43ef919a227b3bfc6bc0a9963504378b09594bb8f6fd28e4",
                    "created_at": "2026-10-15T23:17:54Z",
                    "updated_at": "2026-10-15T23:17:54Z",
                    "expires_at": "2027-01-13T23:13:58Z",
                    "workflow_run": {
                        "id": 38822317512,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
                    },
                }
            ],
            "38763419550": [
                {
                    "id": 11986254180,
                    "node_id": "MDg6QXJ0aWZhY3QxMTk4NjI1NDE4MA==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 409,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11986254180",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11986254180/zip",
                    "expired": False,
                    "digest": "sha256:806aad73173b8d76979ffd4821c50e9b78d971e9a6c899f15e18103db0b26423",
                    "created_at": "2026-10-15T15:51:16Z",
                    "updated_at": "2026-10-15T15:51:16Z",
                    "expires_at": "2027-01-13T15:47:27Z",
                    "workflow_run": {
                        "id": 38763419550,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                    },
                }
            ],
            "38721259254": [
                {
                    "id": 11966360972,
                    "node_id": "MDg6QXJ0aWZhY3QxMTk2NjM2MDk3Mg==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 409,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11966360972",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11966360972/zip",
                    "expired": False,
                    "digest": "sha256:ff5c6351623bdbfb1e6912c275179d9b325396fa4676653171e61119100293d4",
                    "created_at": "2026-10-15T10:39:49Z",
                    "updated_at": "2026-10-15T10:39:49Z",
                    "expires_at": "2027-01-13T10:34:43Z",
                    "workflow_run": {
                        "id": 38721259254,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                    },
                }
            ],
            "38720084083": [
                {
                    "id": 11964608158,
                    "node_id": "MDg6QXJ0aWZhY3QxMTk2NDYwODE1OA==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 409,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11964608158",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11964608158/zip",
                    "expired": False,
                    "digest": "sha256:3224a259d4edc499acb8b6a21acbea5dc34da1c80440aed5dceb1e9bb71c62ea",
                    "created_at": "2026-10-15T10:29:57Z",
                    "updated_at": "2026-10-15T10:29:57Z",
                    "expires_at": "2027-01-13T10:24:51Z",
                    "workflow_run": {
                        "id": 38720084083,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                    },
                }
            ],
            "38684236179": [
                {
                    "id": 11948749823,
                    "node_id": "MDg6QXJ0aWZhY3QxMTk0ODc0OTgyMw==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 413,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11948749823",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11948749823/zip",
                    "expired": False,
                    "digest": "sha256:600a142a0c6c7da1afcd4e9995310befc45b0a47a8bcaf9123b6e87670b9d24a",
                    "created_at": "2026-10-15T05:02:47Z",
                    "updated_at": "2026-10-15T05:02:47Z",
                    "expires_at": "2027-01-13T04:57:58Z",
                    "workflow_run": {
                        "id": 38684236179,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                    },
                }
            ],
            "38534647007": [
                {
                    "id": 11880080052,
                    "node_id": "MDg6QXJ0aWZhY3QxMTg4MDA4MDA1Mg==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 406,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11880080052",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11880080052/zip",
                    "expired": False,
                    "digest": "sha256:fe74682d93588fc121596bbcf2a4031a9d218966302c41b2ab6b6706e98bb5bf",
                    "created_at": "2026-10-14T08:26:21Z",
                    "updated_at": "2026-10-14T08:26:21Z",
                    "expires_at": "2027-01-12T08:22:28Z",
                    "workflow_run": {
                        "id": 38534647007,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "5146041d2628fb6f03c28d7ee7a2d58c3db8f443",
                    },
                }
            ],
            "38507918591": [
                {
                    "id": 11868578085,
                    "node_id": "MDg6QXJ0aWZhY3QxMTg2ODU3ODA4NQ==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 409,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11868578085",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11868578085/zip",
                    "expired": False,
                    "digest": "sha256:cedee2471635fc0f1a9322b69edab6fe4f22cf4ab8d9df09ce5d68afad1a6874",
                    "created_at": "2026-10-14T03:49:43Z",
                    "updated_at": "2026-10-14T03:49:43Z",
                    "expires_at": "2027-01-12T03:45:50Z",
                    "workflow_run": {
                        "id": 38507918591,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
                    },
                }
            ],
            "38394202618": [
                {
                    "id": 11809449844,
                    "node_id": "MDg6QXJ0aWZhY3QxMTgwOTQ0OTg0NA==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 367,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11809449844",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11809449844/zip",
                    "expired": False,
                    "digest": "sha256:28cd7c676a40985ffb2ca2c18bb452aecb8afe3d454e99759e63581491c5e760",
                    "created_at": "2026-10-13T12:02:54Z",
                    "updated_at": "2026-10-13T12:02:54Z",
                    "expires_at": "2027-01-11T12:00:46Z",
                    "workflow_run": {
                        "id": 38394202618,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                    },
                }
            ],
            "38037226667": [
                {
                    "id": 11663728733,
                    "node_id": "MDg6QXJ0aWZhY3QxMTY2MzcyODczMw==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 410,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11663728733",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11663728733/zip",
                    "expired": False,
                    "digest": "sha256:45436b44e9ae179aa2e0b6d89bd741fd87fccabd8ee974091460bcf23271add5",
                    "created_at": "2026-10-10T08:26:57Z",
                    "updated_at": "2026-10-10T08:26:57Z",
                    "expires_at": "2027-01-08T08:15:09Z",
                    "workflow_run": {
                        "id": 38037226667,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
                    },
                }
            ],
            "38037221453": [
                {
                    "id": 11663844855,
                    "node_id": "MDg6QXJ0aWZhY3QxMTY2Mzg0NDg1NQ==",
                    "name": "onek-pacing-state-1",
                    "size_in_bytes": 406,
                    "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11663844855",
                    "archive_download_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11663844855/zip",
                    "expired": False,
                    "digest": "sha256:92ec1e574e38bf1bb05ec6c78b2a4242ad0dfab9eb680ed5b67746b7a29b5d44",
                    "created_at": "2026-10-10T08:20:58Z",
                    "updated_at": "2026-10-10T08:20:58Z",
                    "expires_at": "2027-01-08T08:15:02Z",
                    "workflow_run": {
                        "id": 38037221453,
                        "repository_id": 1163601208,
                        "head_repository_id": 1163601208,
                        "head_branch": "main",
                        "head_sha": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
                    },
                }
            ],
        },
        "latest_owned": {
            "identity": [
                38822317512,
                1,
                "cy-onek-youth-funds",
                "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
            ],
            "artifact_id": 12013682993,
            "digest": "sha256:75bf72f3ee339f8a43ef919a227b3bfc6bc0a9963504378b09594bb8f6fd28e4",
            "state": {
                "schema": 1,
                "family": "youthopps-onek-publisher-v1",
                "observed_at": 1792106269.656179,
                "not_before": 1792106149.654027,
                "starts": [
                    1792106215.6552544,
                    1792106221.6553354,
                    1792106227.655434,
                    1792106233.6555305,
                    1792106239.655675,
                    1792106245.655774,
                    1792106251.655868,
                    1792106257.6559896,
                    1792106263.6560829,
                    1792106269.656179,
                ],
                "blocked": False,
            },
            "state_sha256": "48ab7e46a733fe8be9aafb16536c39aeacc559913b81a66a1fa3d5a337671c27",
            "original_producer": {
                "path": "adapters/cy-onek-youth-funds/adapter.py",
                "head": "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
                "source_sha256": "537f52c2049988b01710c0bd582559c644b7c4d6db6b689be53c407aa59069f6",
                "envelope_keys": [
                    "repository",
                    "run_attempt",
                    "run_id",
                    "schema",
                    "source",
                    "state",
                ],
            },
        },
    },
}
_HOSTED_TRANSITION = {
    "schema": 1,
    "family": "youthopps-onek-publisher-v1",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/325#issuecomment-6215811228",
    "history_sha256": "c4f8f6944ce0bad9347314478d84b2b42dddaa1644a95f105a969209807f7b56",
    "state": {
        "schema": 3,
        "family": "youthopps-onek-publisher-v1",
        "observed_at": 1792154578.0641198,
        "not_before": 1792154638.0641198,
        "starts": [
            1791620461.0,
            1791620820.0,
            1791892977.0,
            1791949786.0,
            1791966383.0,
            1792040570.0,
            1792060199.0,
            1792060793.0,
            1792079479.0,
            1792106215.6552544,
            1792106221.6553354,
            1792106227.655434,
            1792106233.6555305,
            1792106239.655675,
            1792106245.655774,
            1792106251.655868,
            1792106257.6559896,
            1792106263.6560829,
            1792106269.656179,
            1792106277.0,
            1792147942.677636,
            1792147943.002609,
            1792147943.074587,
            1792147944.6071737,
            1792147944.625675,
            1792147956.8321118,
            1792147956.8543687,
            1792147956.9255488,
            1792147960.9441826,
            1792147961.4148386,
            1792147991.432594,
            1792147991.4649088,
            1792147991.4812458,
            1792147993.4138787,
            1792147993.8299463,
            1792148023.8631475,
            1792148024.1625414,
            1792148024.240629,
            1792148026.3394368,
            1792148026.7864408,
            1792148056.7976856,
            1792148056.8234003,
            1792148056.8974364,
            1792148058.7119865,
            1792148059.1523192,
            1792148089.1625397,
            1792148089.1909828,
            1792148089.2701738,
            1792148091.1983345,
            1792148091.6300504,
            1792148103.861358,
            1792148103.888388,
            1792148103.9181478,
            1792148105.6644552,
            1792148106.0777376,
            1792148118.292671,
            1792148118.3240929,
            1792148118.3457203,
            1792148120.0618362,
            1792148120.4815853,
            1792148132.7072062,
            1792148132.776343,
            1792148132.8622675,
            1792148134.458817,
            1792148134.8745923,
            1792148147.0960984,
            1792148147.1522777,
            1792148147.172962,
            1792148149.073748,
            1792148149.53909,
            1792148161.7682164,
            1792148161.805808,
            1792148161.850778,
            1792148163.6925077,
            1792148164.1062336,
            1792148176.3371725,
            1792148176.3901641,
            1792148176.4713507,
            1792148178.508945,
            1792148178.6475708,
            1792148190.9554112,
            1792148190.9986675,
            1792148191.0748966,
            1792148192.8577375,
            1792148193.2778182,
            1792148205.4997308,
            1792148205.5369866,
            1792148205.6131318,
            1792148207.355674,
            1792148207.7962952,
            1792148220.0189776,
            1792148220.055385,
            1792148220.1297581,
            1792148221.7913203,
            1792148222.2026763,
            1792148234.4822903,
            1792148246.5029316,
            1792148246.5839214,
            1792148248.434218,
            1792148248.8523538,
            1792148261.0719266,
            1792148261.1256132,
            1792148261.1518548,
            1792148262.9159858,
            1792148263.3282087,
            1792148275.5446522,
            1792148275.5737722,
            1792148275.6444705,
            1792148277.307756,
            1792148277.73673,
            1792148289.9520023,
            1792148289.9782476,
            1792148290.0045803,
            1792148291.6389003,
            1792148292.1031358,
            1792148304.3486366,
            1792148304.4234035,
            1792148304.4692948,
            1792148306.1823993,
            1792148306.5882523,
            1792148336.6057575,
            1792148336.6666796,
            1792148336.885443,
            1792148338.1358402,
            1792148338.1573827,
            1792153099.4907765,
            1792153130.1493013,
            1792153130.224789,
            1792153130.3888898,
            1792153130.4111123,
            1792153142.614553,
            1792153142.684457,
            1792153142.789704,
            1792153144.5915809,
            1792153145.1672323,
            1792153175.1758401,
            1792153175.2082999,
            1792153175.2832408,
            1792153177.3047974,
            1792153177.777043,
            1792153207.7903755,
            1792153207.820303,
            1792153207.9005625,
            1792153209.924064,
            1792153210.368184,
            1792153240.3834348,
            1792153240.413958,
            1792153240.5096226,
            1792153242.561391,
            1792153242.9999866,
            1792153273.0090652,
            1792153273.0395386,
            1792153273.1131287,
            1792153276.2529383,
            1792153276.684763,
            1792153288.9058485,
            1792153288.9375105,
            1792153289.0079358,
            1792153291.1091406,
            1792153291.5193946,
            1792153303.7384953,
            1792153303.7784479,
            1792153303.8015397,
            1792153305.7463226,
            1792153306.981361,
            1792153319.199865,
            1792153319.226087,
            1792153319.3082492,
            1792153321.189054,
            1792153321.6217723,
            1792153333.8386378,
            1792153333.8655705,
            1792153333.9398453,
            1792153335.8941548,
            1792153337.1452076,
            1792153349.3640335,
            1792153349.3983011,
            1792153349.4328315,
            1792153351.2517333,
            1792153351.670211,
            1792153363.886941,
            1792153363.921592,
            1792153363.966226,
            1792153365.5966775,
            1792153366.0129802,
            1792153378.2315857,
            1792153378.2758498,
            1792153378.3133929,
            1792153380.0593512,
            1792153380.511736,
            1792153392.7409136,
            1792153392.7658238,
            1792153392.7914395,
            1792153395.0334516,
            1792153396.1395407,
            1792153408.3679743,
            1792153408.4311957,
            1792153408.452137,
            1792153410.178436,
            1792153410.6142912,
            1792153422.833753,
            1792153422.8662603,
            1792153422.8985507,
            1792153424.632254,
            1792153425.0913737,
            1792153437.4282653,
            1792153437.4561205,
            1792153437.4871476,
            1792153439.2637236,
            1792153439.6969738,
            1792153451.916379,
            1792153451.9440615,
            1792153451.9635715,
            1792153453.7330678,
            1792153454.1665938,
            1792153466.3913536,
            1792153466.4304564,
            1792153466.4648604,
            1792153468.3604405,
            1792153469.6467648,
            1792153481.8723736,
            1792153481.9177744,
            1792153482.0020733,
            1792153483.7484283,
            1792153484.1814907,
            1792153514.1998396,
            1792153514.2305188,
            1792153514.3131616,
            1792153515.7872367,
            1792153516.499736,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792153516.5165486,
        "page_interval": 30,
        "last_inventory_completed": 1792153516.5025828,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "in_flight": False,
        "flight_id": "5004aa896cc8c47676353e52a12b5e31",
        "flight_started": 1792153484.302478,
        "flight_kind": "physical",
        "completed_flight_id": "5004aa896cc8c47676353e52a12b5e31",
        "request_id": "7317c8bc2678f469f0e59356a3819727",
        "request_started": 1792153514.1998396,
        "request_inventory": True,
        "completed_request_id": "7317c8bc2678f469f0e59356a3819727",
        "request_outcome": "response_assessed",
    },
    "legacy_provenance_sha256": "7e4f1d97d8374f8fd70afc6ffb7844d210b1c8c36e7aaf6489665e606b951068",
}
_LOCAL_INITIALIZATION = None
_LOCAL_INIT_AUTHORITY = None
_MATURE_LOCAL_LEGACY_OWNED_REENTRY = {
    "schema": 1,
    "family": "youthopps-onek-publisher-v1",
    "purpose": "MATURE_LOCAL_LEGACY_OWNED_REENTRY",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/325#issuecomment-6215098088",
    "history_sha256": "c4f8f6944ce0bad9347314478d84b2b42dddaa1644a95f105a969209807f7b56",
    "legacy_original_sha256": "7e4f1d97d8374f8fd70afc6ffb7844d210b1c8c36e7aaf6489665e606b951068",
    "latest_owned": {
        "identity": [
            38822317512,
            1,
            "cy-onek-youth-funds",
            "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
        ],
        "artifact_id": 12013682993,
        "digest": "sha256:75bf72f3ee339f8a43ef919a227b3bfc6bc0a9963504378b09594bb8f6fd28e4",
        "state": {
            "schema": 1,
            "family": "youthopps-onek-publisher-v1",
            "observed_at": 1792106269.656179,
            "not_before": 1792106149.654027,
            "starts": [
                1792106215.6552544,
                1792106221.6553354,
                1792106227.655434,
                1792106233.6555305,
                1792106239.655675,
                1792106245.655774,
                1792106251.655868,
                1792106257.6559896,
                1792106263.6560829,
                1792106269.656179,
            ],
            "blocked": False,
        },
        "state_sha256": "48ab7e46a733fe8be9aafb16536c39aeacc559913b81a66a1fa3d5a337671c27",
        "original_producer": {
            "path": "adapters/cy-onek-youth-funds/adapter.py",
            "head": "a9fe27eeabc210f171f4d1109f4fa1a95e008edd",
            "source_sha256": "537f52c2049988b01710c0bd582559c644b7c4d6db6b689be53c407aa59069f6",
            "envelope_keys": [
                "repository",
                "run_attempt",
                "run_id",
                "schema",
                "source",
                "state",
            ],
        },
    },
    "state": {
        "schema": 3,
        "family": "youthopps-onek-publisher-v1",
        "observed_at": 1792151910.4480798,
        "not_before": 1792151970.4480798,
        "starts": [
            1791620461.0,
            1791620820.0,
            1791892977.0,
            1791949786.0,
            1791966383.0,
            1792040570.0,
            1792060199.0,
            1792060793.0,
            1792079479.0,
            1792106215.6552544,
            1792106221.6553354,
            1792106227.655434,
            1792106233.6555305,
            1792106239.655675,
            1792106245.655774,
            1792106251.655868,
            1792106257.6559896,
            1792106263.6560829,
            1792106269.656179,
            1792106277.0,
            1792147942.677636,
            1792147943.002609,
            1792147943.074587,
            1792147944.6071737,
            1792147944.625675,
            1792147956.8321118,
            1792147956.8543687,
            1792147956.9255488,
            1792147960.9441826,
            1792147961.4148386,
            1792147991.432594,
            1792147991.4649088,
            1792147991.4812458,
            1792147993.4138787,
            1792147993.8299463,
            1792148023.8631475,
            1792148024.1625414,
            1792148024.240629,
            1792148026.3394368,
            1792148026.7864408,
            1792148056.7976856,
            1792148056.8234003,
            1792148056.8974364,
            1792148058.7119865,
            1792148059.1523192,
            1792148089.1625397,
            1792148089.1909828,
            1792148089.2701738,
            1792148091.1983345,
            1792148091.6300504,
            1792148103.861358,
            1792148103.888388,
            1792148103.9181478,
            1792148105.6644552,
            1792148106.0777376,
            1792148118.292671,
            1792148118.3240929,
            1792148118.3457203,
            1792148120.0618362,
            1792148120.4815853,
            1792148132.7072062,
            1792148132.776343,
            1792148132.8622675,
            1792148134.458817,
            1792148134.8745923,
            1792148147.0960984,
            1792148147.1522777,
            1792148147.172962,
            1792148149.073748,
            1792148149.53909,
            1792148161.7682164,
            1792148161.805808,
            1792148161.850778,
            1792148163.6925077,
            1792148164.1062336,
            1792148176.3371725,
            1792148176.3901641,
            1792148176.4713507,
            1792148178.508945,
            1792148178.6475708,
            1792148190.9554112,
            1792148190.9986675,
            1792148191.0748966,
            1792148192.8577375,
            1792148193.2778182,
            1792148205.4997308,
            1792148205.5369866,
            1792148205.6131318,
            1792148207.355674,
            1792148207.7962952,
            1792148220.0189776,
            1792148220.055385,
            1792148220.1297581,
            1792148221.7913203,
            1792148222.2026763,
            1792148234.4822903,
            1792148246.5029316,
            1792148246.5839214,
            1792148248.434218,
            1792148248.8523538,
            1792148261.0719266,
            1792148261.1256132,
            1792148261.1518548,
            1792148262.9159858,
            1792148263.3282087,
            1792148275.5446522,
            1792148275.5737722,
            1792148275.6444705,
            1792148277.307756,
            1792148277.73673,
            1792148289.9520023,
            1792148289.9782476,
            1792148290.0045803,
            1792148291.6389003,
            1792148292.1031358,
            1792148304.3486366,
            1792148304.4234035,
            1792148304.4692948,
            1792148306.1823993,
            1792148306.5882523,
            1792148336.6057575,
            1792148336.6666796,
            1792148336.885443,
            1792148338.1358402,
            1792148338.1573827,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792148338.1777883,
        "page_interval": 30,
        "last_inventory_completed": 1792148338.1602144,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "in_flight": False,
        "flight_id": "19237b92de3f750c535450062bb8c1e1",
        "flight_started": 1792148306.6926267,
        "flight_kind": "physical",
        "completed_flight_id": "19237b92de3f750c535450062bb8c1e1",
        "request_id": "88924c2526356176c5b2badfce8434fd",
        "request_started": 1792148336.6057575,
        "request_inventory": True,
        "completed_request_id": "88924c2526356176c5b2badfce8434fd",
        "request_outcome": "response_assessed",
    },
    "local": {
        "path": "/tmp/youthopps-onek-publisher-v1-1000",
        "parent_identity": [34, 544406],
        "state_identity": [34, 544409],
        "state_version": [
            34,
            544409,
            780,
            1792148338187489887,
            1792148338187489887,
            1000,
            384,
            1,
        ],
        "raw_sha256": "31051abc06a0a69b109ba4633a1ec23edad8039e97eb35d4f208cc84384cf053",
        "state": {
            "schema": 3,
            "family": "youthopps-onek-publisher-v1",
            "observed_at": 1792148338.1777883,
            "not_before": 1792148350.3777883,
            "starts": [
                1792148289.9520023,
                1792148304.3486366,
                1792148336.6057575,
            ],
            "blocked": False,
            "interval": 12.2,
            "completed": 1792148338.1777883,
            "page_interval": 30,
            "last_inventory_completed": 1792148338.1602144,
            "rate_limit_strikes": 0,
            "rate_limit_until": 0,
            "legacy_rate_history_unknown": True,
            "in_flight": False,
            "flight_id": "19237b92de3f750c535450062bb8c1e1",
            "flight_started": 1792148306.6926267,
            "flight_kind": "physical",
            "completed_flight_id": "19237b92de3f750c535450062bb8c1e1",
            "request_id": "88924c2526356176c5b2badfce8434fd",
            "request_started": 1792148336.6057575,
            "request_inventory": True,
            "completed_request_id": "88924c2526356176c5b2badfce8434fd",
            "request_outcome": "response_assessed",
        },
        "locks": {"collection.lock": [34, 544407], "state.lock": [34, 544408]},
    },
    "outside_claim": {
        "ancestor": {
            "path": "/tmp",
            "identity": [34, 1],
            "uid": 65534,
            "mode": 1023,
            "trusted_parent": {
                "path": "/",
                "name": "tmp",
                "identity": [27, 524289],
                "uid": 65534,
                "mode": 493,
            },
        },
        "name": ".youthopps-onek-publisher-v1-init-0d60287c727003cd088bd46f1ce66f56.claim",
        "identity": [34, 544405],
        "version": [
            34,
            544405,
            943,
            1792147882670093666,
            1792147882670093666,
            1000,
            384,
            1,
        ],
        "raw_sha256": "8fd288c2544c89f6ab68d05881f1b02093784f3a99e6811fc94240f086f72393",
    },
    "settlement_evidence": {
        "native_receipt_sha256": "4c3aed631912cb9ccbd2196299451acdf737ce757a2619bbe2f9201b2d5a3efc",
        "audit_sha256": "28788f265e0201727a2ec6cc98391b5c10283a5f99bf81c00401423febcae74a",
        "owner_ids": [
            "0192f6128f0b6e47e461d0cc0f3b30c0",
            "07e0d6825918238ce5b03122eecf678b",
            "0d60287c727003cd088bd46f1ce66f56",
            "19237b92de3f750c535450062bb8c1e1",
            "1aff5f527edb3693e989a5c723eb3270",
            "25b7ce4bb1eae4cd0402ebb18e673f44",
            "26cb13a064039c2411452a52a1fed498",
            "2b98de547252bdd9da535918d0bbdd82",
            "65c81670e3da1bb5bcfbc59be64d82c1",
            "696a288522df4801bc6c40092d0f90a5",
            "7a5522e8aa6e574a46283f533af0d13d",
            "7e92e79b1c1a6ec73ec8d698fac8a78e",
            "81fd916174390c5cfde4219002479ecb",
            "91c39b94e038aefd4d53ce09fd42d98b",
            "936c2834b448445ad677fbd9f7470fae",
            "a6d882e9bc35179adcc956517ac3c8f1",
            "bb35313cfd792498a6d83750ac0c267f",
            "c8efe3f83b0b9cc5e92d8ca5ae8fd5ec",
            "df337ea69575dd3108b0dbe104d44362",
            "e1a9ae78a8c38f7bf667b3aa215723b3",
            "f0f3e98aa2f80519a144c19d21712116",
            "ffb952c17d149d910aade6d2baad7cff",
        ],
        "request_ids": [
            "0b53893c7c5c49aea1d0ddf1c89dc8b5",
            "0da46c93c9f1acfa7524ef9973ed7160",
            "11938666806c3af1d30be5946d2510b8",
            "28e1af2a4e8ee2da0633c1396f373d7b",
            "2e206fd98362e798ec59deecc46196c7",
            "3fc45c33e495a8e473df083f388cd9f8",
            "5f916ced0dc4b98eee6e7b7ee8cf936a",
            "631d06ce18e51115157dffcbfbaf1fd3",
            "6b9051a27c7d98219cf06e4e6b822df1",
            "72c458e27b8e26db079cb224e7a6bb6c",
            "83c96e921c9dd4e46491e663c8a284ea",
            "88924c2526356176c5b2badfce8434fd",
            "8a2848c7179c91af36aa9a28f7901e13",
            "9a82ab17c064cd0cd23a8f5b76b45479",
            "c2e6bf446c7dfad3dac5006fb73eaf8f",
            "c3ff09b307291ec48df3419bfa9c54bb",
            "cb8b1321a08c96b998fd164b93a9f143",
            "d1147203a6ad43200954d172e4e081f2",
            "de1898158503225fef7301ba27a630e3",
            "e6ff29c96ac6e28808cfc94b01835963",
            "ebfbedd265c7f228c2de6de09fd7ab7a",
        ],
    },
}
_MATURE_LOCAL_REENTRY_AUTHORITY = None
_VERIFIED_LEGACY_PRODUCERS = set()
_RESTORED_OWNED_IDENTITY = None
_FIRST_HOSTED_PENDING = False
_PUBLICATION_WINDOW = None


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style"):
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


class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


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


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


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
    return min(60 if publisher else 15, remaining)


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


def document_root(text, complete=True):
    if complete and not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I
    ):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(
        r"just a moment|access denied|attention required|"
        r"checking your browser|verify you are human",
        title,
    ):
        raise AdapterError("Publisher access challenge", "access")
    return root


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
        if record.get("summary_language") not in {"en", "el"}:
            raise AdapterError("Invalid summary language", "validate")
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


def github(method, path, payload=None, missing_ok=False):
    check_deadline()
    token = os.environ.get("DATA_SOURCE_TOKEN", "")
    if not token:
        raise AdapterError("Publication credentials missing", "publish")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, _, body = request_bytes(
            _GITHUB + path,
            method=method,
            headers=headers,
            payload=(
                json.dumps(payload).encode() if payload is not None else None
            ),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; outcome unconfirmed", "publish"
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(
            f"GitHub publication HTTP {status}", "publish", status
        )
    return json.loads(body)


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
        if file is not None and file.get("encoding") == "none":
            sha = file.get("sha", "")
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise AdapterError("Invalid existing blob identity", "publish")
            file = github("GET", "/git/blobs/" + sha)
            if file.get("sha") != sha:
                raise AdapterError("Existing blob identity mismatch", "publish")
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
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        check_deadline()
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
        except AdapterError:
            latest = read_snapshot()
            if candidate and (
                latest["head"] == candidate or latest["files"] == desired
            ):
                return
            if latest["files"] != expected:
                raise AdapterError(
                    "Source changed concurrently; "
                    "newer remote data was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; "
                    "durable remote outcome could not be updated",
                    "publish",
                )


def public_url(value):
    """Allow public publisher content, never private or application routes."""
    try:
        parsed = urllib.parse.urlsplit(value)
        path = urllib.parse.unquote(parsed.path)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port or parsed.query
                or parsed.hostname != "youthfunds.onek.org.cy"
                or not path.startswith("/")
                or any(part in {".", ".."} for part in path.split("/"))
                or re.search(r"/(?:wp-admin|wp-json|wp-login|xmlrpc)", path)):
            raise ValueError()
    except ValueError:
        raise AdapterError("Unsafe public source route", "access") from None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def material_root(body, key=None):
    root = document_root(body.decode("utf-8", "strict"))
    return one(nodes(root, "div", "lendiz-content-wrapper"),
               "official material content region")


def read_html(url):
    _, headers, body = fetch_public(url)
    if headers.get("Content-Type", "").split(";")[0] != "text/html":
        raise AdapterError("Expected publisher HTML: " + url, "parse")
    return material_root(body), body


def links(root, base):
    for node in nodes(root, "a"):
        href = node.attrs.get("href", "")
        if href:
            yield node.text(), urllib.parse.urljoin(base, href)


def calendar_parameters(body, page):
    """Read current public form values; do not bind rotating nonces."""
    root = document_root(body.decode("utf-8", "strict"))
    form = one([node for node in nodes(root, "form")
                if node.attrs.get("id") == "event_filters"], "event form")
    values = [(node.attrs["name"], node.attrs.get("value", ""))
              for node in nodes(form, "input") if node.attrs.get("name")]
    nonce = dict(values).get("wpem_filter_nonce")
    if not nonce:
        raise AdapterError("Missing public calendar nonce", "parse")
    return urllib.parse.urlencode([
        ("lang", ""), ("search_keywords", ""), ("search_location", ""),
        ("search_datetimes[]", ""), ("per_page", "10"),
        ("orderby", "meta_value"), ("order", "ASC"), ("page", str(page)),
        ("event_online", ""), ("show_pagination", "false"),
        ("form_data", urllib.parse.urlencode(values)),
        ("wpem_filter_nonce", nonce),
    ]).encode()


def read_pages():
    """Discover the current programme, news and calendar frontiers."""
    base = WEBSITE_URL
    directory, _ = fetch_inventory_page(read_html, base + "programmata/")
    programme_urls = {
        url
        for _, url in links(directory, base)
        if re.fullmatch(re.escape(base) + r"services/[^/]+/", url)
    }
    if not programme_urls:
        raise AdapterError("No funding programmes discovered", "parse")
    pages = {"programmes": {}, "news": {}, "events": {}}
    for url in sorted(programme_urls):
        pages["programmes"][url], _ = fetch_inventory_page(read_html, url)
    pending = [base + "nea-anakoinoseis/"]
    seen = set()
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        if len(seen) >= 40:
            raise AdapterError("News pagination limit exceeded", "parse")
        seen.add(url)
        root, _ = fetch_inventory_page(read_html, url)
        cards = nodes(root, "div", "blog-inner")
        if not cards:
            raise AdapterError("Missing news listing cards: " + url, "parse")
        for card in cards:
            categories = " ".join(
                n.text() for n in nodes(card, "div", "post-category")
            )
            if not re.search(r"\b(?:Calls|Extensions|Events)\b", categories):
                continue
            title = one(nodes(card, "div", "entry-title"), "news title")
            candidates = list(links(title, url))
            _, detail = one(candidates, "news detail link")
            if detail not in pages["news"]:
                pages["news"][detail], _ = read_html(detail)
        for _, target in links(root, url):
            if re.fullmatch(
                re.escape(base) + r"nea-anakoinoseis/(?:page/\d+/)?", target
            ):
                if target not in seen and target not in pending:
                    pending.append(target)
    calendar_url = base + "imerologio-draseon/"
    _, calendar_body = fetch_inventory_page(read_html, calendar_url)
    endpoint = base + "em-ajax/get_listings/"
    seen_events = set()
    for page in range(1, 101):
        check_robots(endpoint)
        status, _, body = fetch_inventory_page(
            request_bytes,
            endpoint,
            method="POST",
            publisher=True,
            payload=calendar_parameters(calendar_body, page),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if status != 200:
            raise AdapterError("Calendar listing failed", "fetch", status)
        result = json.loads(body)
        if (
            type(result.get("found_events")) is not bool
            or not isinstance(result.get("html"), str)
            or type(result.get("max_num_pages")) is not int
        ):
            raise AdapterError("Invalid calendar response", "parse")
        if not result["found_events"]:
            if result["max_num_pages"] != 0:
                raise AdapterError("Inconsistent empty calendar page", "parse")
            break
        fragment = PublisherHTML(result["html"]).root
        urls = {
            url
            for _, url in links(fragment, calendar_url)
            if "/ekdilosi/" in url
        }
        if not urls or urls <= seen_events:
            raise AdapterError("Calendar pagination did not advance", "parse")
        for url in sorted(urls - seen_events):
            pages["events"][url], _ = read_html(url)
        seen_events.update(urls)
    else:
        raise AdapterError("Calendar pagination limit exceeded", "parse")
    print(
        f"Discovery: {len(pages['programmes'])} programmes; "
        f"{len(seen)} news pages; {len(pages['news'])} notices; "
        f"{len(pages['events'])} calendar details",
        file=sys.stderr,
    )
    return pages


_MONTHS = {name.lower(): number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"), 1)}

_DATE_PATTERN = (
    r"\b(?:20\d{2}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})|"
    r"\d{1,2}\s+(?:" + "|".join(_MONTHS) + r")\s+\d{4})\b"
)


def dates(text):
    found = []
    for match in re.finditer(_DATE_PATTERN, text, re.I):
        value = match[0]
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
            year, month, day = map(int, value.split("-"))
        elif "/" in value:
            day, month, year = map(int, value.split("/"))
            if year < 100:
                year += 2000
        else:
            day, month, year = value.split()
            day, month, year = int(day), _MONTHS[month.lower()], int(year)
        try:
            found.append(datetime(year, month, day).date().isoformat())
        except ValueError:
            raise AdapterError("Invalid published date", "parse") from None
    return found


def programme_key(text, inventory=()):
    lower = text.lower()
    for name, key in (("youth initiatives", "youth-initiatives"),
                      ("green volunteering", "green-volunteering"),
                      ("youth and the world", "youth-and-world")):
        if name in lower:
            return key
    matches = [key for name, key in inventory if name.casefold() in text.casefold()]
    if len(set(matches)) == 1:
        return matches[0]
    return None


def round_letter(title):
    """Resolve lettered and ordinal rounds without assuming a first call."""
    ordinal_names = (
        "first second third fourth fifth sixth seventh eighth ninth tenth "
        "eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth "
        "eighteenth nineteenth").split()
    ordinals = {name: index + 1 for index, name in enumerate(ordinal_names)}
    for tens, name in enumerate(
            ("twenty", "thirty", "forty", "fifty", "sixty", "seventy",
             "eighty", "ninety"), 2):
        ordinals[name[:-1] + "ieth"] = tens * 10
        for index, unit in enumerate(ordinal_names[:9], 1):
            ordinals[name + "-" + unit] = tens * 10 + index
    number = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+call\b", title, re.I)
    value = int(number[1]) if number else None
    for name, ordinal in ordinals.items():
        if re.search(r"(?<![\w-])" + name.replace("-", "[- ]")
                     + r"\s+call\b", title, re.I):
            value = ordinal
    if value:
        letters = ""
        while value:
            value, remainder = divmod(value - 1, 26)
            letters = chr(ord("a") + remainder) + letters
        return letters or None
    label = re.search(r"\bCall\s+([A-Z]+)(?:['’]|\s|$)", title)
    return label[1].lower() if label else None


def short_excerpt(text, limit):
    """Bound quoted plain text in UTF-16 units without splitting a character."""
    text = " ".join(text.split())
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    clipped = text.encode("utf-16-le")[:max(0, limit - 3) * 2].decode(
        "utf-16-le", "ignore")
    return clipped.rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def factual_blocks(root):
    """Read body paragraphs and leaf lists, excluding navigation and contacts."""
    blocks = []

    def visit(node):
        if not isinstance(node, Node):
            return
        classes = node.attrs.get("class", "")
        if (node.tag in {"nav", "aside", "footer", "script", "style"}
                or re.search(r"breadcrumbs|timeline|steps__|post-navigation|"
                             r"entry-meta|description-date|description-tags",
                             classes)):
            return
        if node.tag in {"h2", "h3", "h4"}:
            blocks.append((node.tag, node.text()))
            return
        if (node.tag in {"p", "li"} and not any(
                nodes(node, tag) for tag in {"ul", "ol"})
                or "elementor-icon-box-content" in classes
                or "section-description" in classes):
            text = node.text()
            if (len(text) >= 30 and "@" not in text
                    and not re.search(r"newsletter|Contact for questions|"
                                      r"All new training offers", text, re.I)):
                blocks.append(("body", text))
            return
        for child in node.children:
            visit(child)

    visit(root)
    return blocks


def describe(record, blocks, language, timing):
    """Index brief publisher description, audience and funding excerpts."""
    bodies = list(dict.fromkeys(text for tag, text in blocks if tag == "body"))
    selected = bodies[:1]
    patterns = (
        r"ve věku|rezident|žijí|určen|eligible|aged|persons|open to|"
        r"applicants|beneficiar|participants.{0,25}(?:from|who)|ηλικ|δικαι",
        r"\d[\d ,.]*\s*(?:eur|€)|hrazen|bezplat|zdarma|"
        r"participation fee|travel costs|travel expenses|budget|"
        r"accommodation|funding|grants|χρηματοδ",
    )
    priorities = (
        r"aged|persons|\d+[-–]\d+|eligible|beneficiar|ηλικ|δικαι",
        r"\d[\d ,.]*\s*(?:eur|€)|€\s*\d|budget|travel costs|"
        r"travel expenses|accommodation|participation fee|χρηματοδ",
    )
    for pattern, priority in zip(patterns, priorities):
        candidates = [text for text in bodies if text not in selected
                      and re.search(pattern, text, re.I)]
        candidates.sort(key=lambda text: not bool(re.search(priority, text, re.I)))
        if candidates:
            selected.append(candidates[0])
    record["summary_language"] = language
    for text in selected:
        record["classification"]["evidence"].append(
            f"Publisher excerpt ({language}): " + short_excerpt(text, 240))
    timing = short_excerpt(timing, 240)
    budget = 600 - len(timing.encode("utf-16-le")) // 2 - 1
    if selected:
        allowance = (budget - 3 * (len(selected) - 1)) // len(selected)
        body = " / ".join(short_excerpt(text, allowance) for text in selected)
    else:
        body = {"en": "Publisher description not stated.",
                "cs": "Popis nabídky není uveden.",
                "el": "Δεν αναφέρεται περιγραφή."}[language]
    record["summary"] = short_excerpt((timing + " " + body).strip(), 600)


def opportunity(key, title, url, deadline, opens=None, activity=None,
                category="grants", proof=None):
    record = make_record(
        title, url, [category], method="publisher-html",
        evidence=[ATTRIBUTION, "Publisher: " + url] + (proof or []))
    record["id"] = hashlib.sha256(
        (SOURCE_ID + "|" + key).encode()).hexdigest()[:24]
    record["deadline"] = deadline
    record["summary_language"] = "en"
    record["summary"] = (
        ("Applications: " + opens + " to " + deadline + ". "
           if opens and deadline else
           "Application deadline: " + deadline + ". " if deadline else
           "Application deadline is not stated. ")
        + ("Activity period: " + activity + ". " if activity else "")
    )
    today = datetime.now(timezone.utc).date().isoformat()
    record["status"] = (
        "expired" if deadline and today > deadline else
        "open" if deadline and today < deadline
        and not (opens and today < opens) else "unknown")
    if key.startswith("youth-initiatives-"):
        record["host_countries"] = ["CY"]
    return record


def parse_inventory(pages):
    """Extract actual round windows and reconcile notices and extensions."""
    records = {}
    inventory = []
    for url, root in pages["programmes"].items():
        title = one(nodes(root, "h1"), "programme heading").text()
        inventory.append((title, programme_key(title) or url))
    for url, root in pages["programmes"].items():
        title = one(nodes(root, "h1"), "programme heading").text()
        programme = programme_key(title, inventory)
        section = re.search(
            r"Application submission deadlines (.+?)Application Submission",
            root.text())
        if not section:
            raise AdapterError("Missing programme application table: " + url,
                               "parse")
        rows = re.findall(r"\bCall(?!s\b)(.*?)(?=\bCall(?!s\b)|$)",
                          section[1], re.I)
        if not rows:
            raise AdapterError("No programme rounds: " + url, "parse")
        for row in rows:
            window = dates(row)
            if len(window) != 4:
                raise AdapterError("Ambiguous programme date row: " + url,
                                   "parse")
            activity_start, activity_end, opens, deadline = window
            if opens > deadline or activity_start > activity_end:
                raise AdapterError("Reversed programme window", "parse")
            label = re.match(r"\s*([A-Z]+|[ΑΒΓ])(?:['’]|\s|$)", row)
            letter = (label[1].translate(str.maketrans("ΑΒΓ", "ABC")).lower()
                      if label else None)
            year = activity_end[:4]
            if not letter:
                # Joint action tracks share one advertised application window.
                matches = [key for key, record in records.items()
                           if key.startswith((programme or url) + "-" + year)
                           and record["deadline"] == deadline]
                if len(matches) != 1:
                    raise AdapterError("Unidentified programme round", "parse")
                records[matches[0]]["classification"]["evidence"].append(
                    "Additional implementation track: " + row.strip())
                records[matches[0]]["summary"] += (
                    " Additional activity track: " + activity_start
                    + " to " + activity_end + ".")
                continue
            key = (programme or url) + "-" + year + "-" + letter
            records[key] = opportunity(
                key, title, url, deadline, opens,
                activity_start + " to " + activity_end,
                proof=["Published application row: " + row.strip()])
    extensions = []
    for url, root in pages["news"].items():
        title = one(nodes(root, "h1"), "notice heading").text()
        content = one(nodes(root, "div", "entry-content"),
                      "notice content").text()
        programme = programme_key(title, inventory)
        if not programme:
            linked = {
                programme_key(one(nodes(page, "h1"), "programme heading").text(),
                              inventory)
                for source, page in pages["programmes"].items()
                if any(target.rstrip("/") == source.rstrip("/")
                       for _, target in links(one(
                           nodes(root, "div", "entry-content"), "notice content"), url))}
            linked.discard(None)
            if len(linked) == 1:
                programme = linked.pop()
        categories = " ".join(n.text() for n in
                              nodes(root, "div", "post-category"))
        if "Events" in categories:
            if not re.search(r"\binvites you\b|\bRSVP\b", content, re.I):
                continue
            closing = re.search(r"RSVP by (.{0,80})", content, re.I)
            deadline = dates(closing[1])[0] if closing and dates(
                closing[1]) else None
            key = ("funding-presentation-2024" if urllib.parse.urlsplit(
                url).path.endswith(
                    "/41-tropoi-gia-na-kaneis-tin-idea-sou-praxi-"
                    "parousiasi-ton-chrimatodotikon-programmaton-"
                    "tou-organismou-neolaias/") else url)
            records[key] = opportunity(
                key, title, url, deadline, category="training")
            event_dates = re.search(r"presentation.*?on (.{0,80})",
                                    content, re.I)
            if event_dates and dates(event_dates[1]):
                records[key]["summary"] += (
                    " Event date: " + dates(event_dates[1])[0] + ".")
            if "Eleftheria Square Amphitheatre" in content:
                records[key]["location"] = "Eleftheria Square Amphitheatre"
                records[key]["host_countries"] = ["CY"]
            continue
        if not programme:
            # New programmes still get deterministic source-link identities.
            programme = url
        closing = re.search(
            r"(?:extended until|remain open until|Application deadline:)"
            r"(.{0,100})", content, re.I)
        window_match = re.search(
            r"submit their applications from (.+?)(?:,\s*for activities|[.])",
            content, re.I)
        window = dates(window_match[1]) if window_match else []
        deadline = (dates(closing[1])[0]
                    if closing and dates(closing[1]) else
                    window[-1] if window else None)
        if not deadline:
            raise AdapterError("Call has no parseable closing date: " + url,
                               "parse")
        if "Extensions" in categories:
            extensions.append((programme, title, url, deadline, content))
            continue
        year_match = re.search(r"\bfor (20\d{2})\b", title)
        year = year_match[1] if year_match else deadline[:4]
        letter = round_letter(title)
        if not letter and re.search(r"\bA[’']1\b", title):
            letter = "a"
        key = programme + "-" + year + "-" + letter if letter else url
        if (not letter and year == "2025" and urllib.parse.urlsplit(url).path
                == "/en/business-financing-loans/"):
            key = "youth-and-world-2025-a"
        before = records.get(key)
        opens = window[0] if len(window) == 2 else None
        record = opportunity(key, title, url, deadline, opens)
        if before:
            if before["deadline"] != deadline:
                raise AdapterError("Conflicting programme/notice deadlines: "
                                   + url, "parse")
            record["status"] = before["status"]
            record["summary"] = before["summary"]
            record["classification"]["evidence"].extend(
                before["classification"]["evidence"][1:])
        records[key] = record
    for programme, title, url, deadline, content in extensions:
        candidates = [key for key, record in records.items()
                      if key.startswith(programme + "-")
                      and record["deadline"][:4] == deadline[:4]
                      and record["deadline"] <= deadline
                      and (datetime.fromisoformat(deadline) -
                           datetime.fromisoformat(
                               record["deadline"])).days <= 45]
        if len(candidates) > 1:
            raise AdapterError("Ambiguous extension target: " + url, "parse")
        if candidates:
            key = candidates[0]
            record = records[key]
            old_deadline = record["deadline"]
            record["deadline"] = deadline
            record["status"] = opportunity(
                key, title, url, deadline)["status"]
            record["summary"] = record["summary"].replace(old_deadline, deadline)
            record["classification"]["evidence"].append(
                "Deadline extension: " + url)
        else:
            key = ("green-volunteering-2024-b" if
                   urllib.parse.urlsplit(url).path.endswith(
                       "/anakoinosi-gia-to-programma-prasinos-ethelontismos/")
                   else url)
            records[key] = opportunity(key, title, url, deadline)
    for url, root in pages["events"].items():
        text = root.text()
        if "Δοκιμαστική περιγραφή" in text:
            continue
        title = one(nodes(root, "h1"), "calendar event heading").text()
        closing = re.search(
            r"(?:Application deadline|Registration deadline|"
            r"Προθεσμία (?:εγγραφής|εγγραφών|αιτήσεων))\s*:?\s*(.{0,100})",
            text, re.I)
        deadline = (dates(closing[1])[0]
                    if closing and dates(closing[1]) else None)
        schedule = re.search(
            r"Date And Time\s+(.+?)(?=Τοποθεσία|Location|Θεματολογία|"
            r"Event Type|Share With Friends|$)", text, re.I)
        if not schedule or not dates(schedule[1]):
            raise AdapterError("Calendar event lacks a dated schedule: " + url,
                               "parse")
        for date in dates(schedule[1]):
            datetime.fromisoformat(date)
        record = opportunity(url, title, url, deadline, category="training")
        record["summary"] = (
            "Event schedule: " + schedule[1].strip()[:160]
            + ". " + ("Application deadline: " + deadline + ". "
                       if deadline else "No application deadline stated. "))
        location = re.search(
            r"(?:Τοποθεσία|Location)\s+(.+?)(?=Θεματολογία|Event Type|"
            r"Πόλη|Share With Friends|$)", text)
        if location:
            record["location"] = location[1].strip()
        record["classification"]["evidence"].append(
            "Published event schedule: " + schedule[1].strip())
        record["language"] = "el" if re.search("[Α-ω]", title) else "en"
        if record["language"] == "el":
            record["summary"] = record["summary"].replace(
                "Event schedule:", "Πρόγραμμα:").replace(
                "Application deadline:", "Προθεσμία αιτήσεων:").replace(
                "No application deadline stated.", "Δεν αναφέρεται προθεσμία αιτήσεων.")
        records[url] = record
    for record in records.values():
        url = record["url"]
        root = (pages["news"].get(url) or pages["events"].get(url)
                or pages["programmes"].get(url))
        content = nodes(root, "div", "entry-content")
        blocks = factual_blocks(content[0] if content else root)
        programme = programme_key(record["title"], inventory)
        if programme:
            for source, page in pages["programmes"].items():
                name = one(nodes(page, "h1"), "programme heading").text()
                if programme_key(name, inventory) == programme and source != url:
                    blocks.extend(factual_blocks(page))
                    record["classification"]["evidence"].append(
                        "Programme description: " + source)
        describe(record, blocks, record["language"], record["summary"])
    result = list(records.values())
    validate_records(result)
    return result


def collect():
    if _PHASE_END is not None:
        check_deadline()
        return parse_inventory(read_pages())
    with execution():
        with phase("prepublication", COLLECTION_TIMEOUT):
            prepare_collection()
            return parse_inventory(read_pages())


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "display_name": "Youth Board of Cyprus",
        "source": SOURCE_ID,
        "name": "ONEK — youth funding rounds and information session",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "attribution": ATTRIBUTION,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (
            previous.get("last_success_at") if error else attempt
        ),
        "last_checked_at": (
            previous.get("last_checked_at") if error else checked_at
        ),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else f"Validated refresh retains {len(records)} ONEK opportunities"
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


def validate_prior_metadata(previous, records):
    """Accept failure writes only after the complete owned last-good pair."""
    if (
        not isinstance(previous, dict)
        or previous.get("source") != SOURCE_ID
        or previous.get("source_url") != SOURCE_URL
        or previous.get("website_url") != WEBSITE_URL
        or not isinstance(previous.get("message"), str)
        or previous.get("status") not in {"success", "fail"}
        or type(previous.get("record_count")) is not int
        or previous["record_count"] != len(records)
        or previous.get("language") != LANGUAGE
        or previous.get("publisher_country") != PUBLISHER_COUNTRY
    ):
        raise AdapterError(
            "Invalid previous source metadata contract", "publish"
        )
    timestamps = {}
    try:
        for field in ("last_attempt_at", "last_success_at", "last_checked_at"):
            value = previous.get(field)
            if not isinstance(value, str):
                raise ValueError()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            timestamps[field] = parsed
        if timestamps["last_success_at"] > timestamps["last_checked_at"]:
            raise ValueError()
        if previous["status"] == "success":
            if timestamps["last_attempt_at"] != timestamps["last_success_at"]:
                raise ValueError()
        elif timestamps["last_attempt_at"] < timestamps["last_checked_at"]:
            raise ValueError()
        if records and timestamps["last_checked_at"] != max(
            datetime.fromisoformat(
                record["last_checked_at"].replace("Z", "+00:00")
            )
            for record in records
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AdapterError(
            "Invalid previous metadata timestamps", "publish"
        ) from None


def phase_error(name):
    return AdapterError(
        "Whole " + name + " phase deadline exceeded",
        "publish" if name == "publication" else "access",
    )


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def remove_expired_records(records, checked):
    """Filter the validated replacement, leaving prior publication untouched."""
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise AdapterError("Cleanup clock requires a timezone", "validate")
    current = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if isinstance(deadline, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                # Without a documented zone, wait for worldwide day closure.
                expired = (
                    datetime.strptime(deadline, "%Y-%m-%d").date()
                    < now.astimezone(timezone(timedelta(hours=-12))).date()
                )
            else:
                instant = datetime.fromisoformat(
                    deadline.replace("Z", "+00:00")
                )
                expired = instant.tzinfo is not None and instant < now
        if not expired:
            current.append(record)
    # Valid nonempty retrieval may have no remaining eligible records.
    validate_records(current, allow_empty=True)
    return current


def fresh_records(records, old, attempt, checked):
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
        same = {k: v for k, v in before.items() if k not in temporal} == {
            k: v for k, v in record.items() if k not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", attempt) if same else attempt,
            last_seen_at=attempt,
            last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records, allow_empty=True)


def run_body(publishing):
    attempt = utc_now()
    snapshot = None
    old, previous, records, desired = [], {}, [], None
    failure = None
    try:
        with phase("prepublication", COLLECTION_TIMEOUT):
            prepare_collection()
            if publishing:
                candidate = read_snapshot()
                if candidate["files"]["data.json"] is not None:
                    candidate_records = json.loads(
                        candidate["files"]["data.json"]
                    )
                    candidate_metadata = json.loads(
                        candidate["files"]["metadata.json"]
                    )
                    validate_records(candidate_records, allow_empty=True)
                    validate_prior_metadata(
                        candidate_metadata, candidate_records
                    )
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            records = remove_expired_records(records, checked)
            fresh_records(records, old, attempt, checked)
            outcome = metadata(attempt, previous, records, checked_at=checked)
            desired = {
                "data.json": serialize(records),
                "metadata.json": serialize(outcome),
            }
    except PhaseExpired:
        failure = phase_error("prepublication")
    except Exception as error:
        failure = error
    if failure is None and publishing:
        try:
            with phase("publication", 180):
                publish_files(snapshot, desired)
        except PhaseExpired:
            failure = phase_error("publication")
        except Exception as error:
            failure = error
    if failure is not None:
        outcome = metadata(attempt, previous, old, failure)
        if publishing:
            try:
                with phase("failure", 180):
                    if snapshot is None:
                        raise AdapterError(
                            "No validated previous source pair", "publish"
                        )
                    latest = read_snapshot()
                    if desired is not None and latest["files"] == desired:
                        outcome = json.loads(desired["metadata.json"])
                        failure = None
                    elif (
                        snapshot["files"]["data.json"] is not None
                        and latest["files"] == snapshot["files"]
                    ):
                        publish_files(
                            latest,
                            {
                                "metadata.json": serialize(outcome),
                            },
                        )
                    else:
                        raise AdapterError(
                            "No safe unchanged last-good pair", "publish"
                        )
            except (Exception, PhaseExpired):
                outcome["message"] = (
                    "Run failed; durable outcome unconfirmed. "
                    "No newer source pair was overwritten."
                )
        elif not old:
            outcome["message"] = "Collection failed; no publication attempted"
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure is not None else 0


def run_lifecycle(publishing):
    result = None
    try:
        result = run_body(publishing)
    except PhaseExpired:
        result = 1
    finally:
        try:
            with phase("export", EXPORT_TIMEOUT):
                export_family_artifact()
        except (Exception, PhaseExpired):
            try:
                arm_alarm(_RUN_END)
                print(
                    "Timing artifact export failed; next run fail closed",
                    file=sys.stderr,
                )
            except (Exception, PhaseExpired):
                pass
            if result == 0:
                result = 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        with execution():
            return run_lifecycle(args.publish)
    except PhaseExpired:
        return 1


_OPENER = urllib.request.build_opener(NoRedirect)

def legacy_request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    if publisher or is_publisher(url):
        raise AdapterError(
            "Publisher requires durable physical transport", "access"
        )
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        try:
            if publisher:
                journal_preopener()
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, OSError) as error:
            if publisher:
                raise publisher_transport_error(error, current) from None
            raise
        with response:
            status = response.status
            response_headers = response.headers
            retry = response.headers.get("Retry-After")
            if publisher:
                record_retry_after(retry)
            location = response.headers.get("Location")
            if publisher and status in (401, 403, 429):
                if status in (401, 403) or retry is None:
                    with locked_budget():
                        state = load_budget()
                        state["blocked"] = True
                        state["observed_at"] = time.time()
                        save_budget(state)
                refused = urllib.parse.urlsplit(current)
                provenance = urllib.parse.urlunsplit(
                    (
                        refused.scheme,
                        refused.hostname or "",
                        refused.path,
                        "",
                        "",
                    )
                )
                raise AdapterError(
                    f"Publisher refused access: HTTP {status}: " + provenance,
                    "access",
                    status,
                )
            if publisher and status in (301, 302, 303, 307, 308):
                if not location or redirect == 5:
                    raise AdapterError("Invalid redirect chain", "access")
                destination = urllib.parse.urljoin(current, location)
                public_url(destination)
                if urllib.parse.urlsplit(destination).scheme != "https":
                    raise AdapterError(
                        "Publisher redirect transport downgrade", "access"
                    )
                body = b""
            else:
                declared = response.headers.get("Content-Length")
                if declared and (
                    not declared.isdigit() or int(declared) > byte_limit
                ):
                    raise AdapterError(
                        ("Oversized or invalid response length"), ("fetch")
                    )
                try:
                    body = response.read(byte_limit + 1)
                except (urllib.error.URLError, OSError) as error:
                    if publisher:
                        raise publisher_transport_error(
                            error, current
                        ) from None
                    raise
                if (
                    len(body) > byte_limit
                    or declared
                    and len(body) != int(declared)
                ):
                    raise AdapterError(
                        "Incomplete or oversized response", "fetch"
                    )
                encoding = response.headers.get("Content-Encoding", "identity")
                if encoding != "identity":
                    raise AdapterError(
                        ("Unsupported response content encoding"), ("fetch")
                    )
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            old, new = (
                urllib.parse.urlsplit(current),
                urllib.parse.urlsplit(destination),
            )
            if publisher:
                public_url(destination)
                if old.scheme == "https" and new.scheme != "https":
                    raise AdapterError(
                        ("Publisher redirect transport downgrade"), ("access")
                    )
                check_robots(destination)
            else:
                if new.scheme != "https" or new.username or new.password:
                    raise AdapterError(
                        ("Unsafe authenticated redirect"), ("publish")
                    )
                if new.netloc != old.netloc:
                    headers = {
                        key: value
                        for key, value in headers.items()
                        if key.lower() != "authorization"
                    }
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    raise AdapterError(
                        ("Unexpected GitHub API redirect"), ("publish")
                    )
            current = destination
            continue
        return status, response_headers, body
    raise AdapterError("Unresolved redirect", "access")



def bounded_infrastructure(function):
    @functools.wraps(function)
    def invoke(*args, **kwargs):
        with operation_guard():
            return function(*args, **kwargs)

    return invoke


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        and not os.path.lexists(
            os.path.join(temporary, FAMILY + "-" + str(os.getuid()))
        )
        and _LOCAL_INIT_AUTHORITY is None
    ):
        raise AdapterError(
            "Finite LOCAL initialization grant required", "access"
        )
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        leaf = FAMILY + "-" + str(os.getuid())
        created = False
        if _LOCAL_INIT_AUTHORITY is not None:
            consume_local_initialization_grant(parent)
        if _MATURE_LOCAL_REENTRY_AUTHORITY is None:
            try:
                os.mkdir(leaf, 0o700, dir_fd=parent)
                created = True
            except FileExistsError:
                if _LOCAL_INIT_AUTHORITY is not None:
                    raise AdapterError(
                        "Claimed LOCAL namespace exists", "access"
                    )
        linked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_nlink < 2
                or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
                or _MATURE_LOCAL_REENTRY_AUTHORITY is not None
                and [info.st_dev, info.st_ino]
                != _MATURE_LOCAL_LEGACY_OWNED_REENTRY["local"][
                    "parent_identity"
                ]
            ):
                raise AdapterError(
                    "Unsafe publisher pacing directory", "access"
                )
            if _LOCAL_INIT_AUTHORITY is not None:
                verify_initialization_ancestor(parent)
                os.fsync(parent)
            _STATE_INITIALIZING = created
            _STATE_DIR_FD = descriptor
            _STATE_DIR_ID = (info.st_dev, info.st_ino)
            directory = os.path.join(temporary, leaf)
            _STATE_PATH = os.path.join(directory, "state.json")
            _STATE_LOCK = os.path.join(directory, "state.lock")
            if _LOCAL_INIT_AUTHORITY is not None:
                _LOCAL_INIT_AUTHORITY["leaf_identity"] = _STATE_DIR_ID
            verify_state_directory()
        except BaseException:
            journal_failure()
            if _STATE_DIR_FD == descriptor:
                _STATE_DIR_FD = _STATE_DIR_ID = None
                _STATE_PATH = _STATE_LOCK = None
                _STATE_INITIALIZING = False
            with contextlib.suppress(BaseException):
                os.close(descriptor)
            raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        try:
            os.close(parent)
        except BaseException:
            journal_failure()
            if not primary_error:
                raise


def journal_version(info):
    """Describe the bytes and metadata of one held journal generation."""
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


def journal_failure():
    """Close access without adopting a changed named journal."""
    global _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False


def verify_journal(check_generation=True):
    """Require the retained RW inode, current name and trusted generation."""
    try:
        verify_state_directory()
        if _JOURNAL_FD is None:
            raise AdapterError("Publisher journal is not retained", "access")
        info = os.fstat(_JOURNAL_FD)
        current = os.stat(
            "state.json", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != _JOURNAL_ID
            or (current.st_dev, current.st_ino) != _JOURNAL_ID
            or check_generation
            and journal_version(info) != _JOURNAL_VERSION
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        lock = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID:
            raise AdapterError("Publisher journal lock changed", "access")
        verify_journal_locks()
        return info
    except BaseException:
        journal_failure()
        raise


def retain_journal():
    """Pin one journal after acquiring its stable lock; never retarget it."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_CREATED
    if _JOURNAL_FD is not None:
        verify_journal()
        return
    try:
        try:
            descriptor = secure_file("state.json", os.O_RDWR)
        except FileNotFoundError:
            if not _STATE_INITIALIZING:
                raise AdapterError(
                    "Mature publisher state is missing", "access"
                )
            descriptor = secure_file(
                "state.json", os.O_RDWR | os.O_CREAT | os.O_EXCL
            )
            _JOURNAL_CREATED = True
        info = os.fstat(descriptor)
        _JOURNAL_FD = descriptor
        _JOURNAL_ID = (info.st_dev, info.st_ino)
        _JOURNAL_VERSION = journal_version(info)
        verify_journal()
        if _JOURNAL_CREATED:
            os.fsync(_STATE_DIR_FD)
    except BaseException:
        journal_failure()
        raise


def close_journal():
    """Close our retained descriptor; propagate an unhealthy close."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_LOCK_ID
    global _JOURNAL_CREATED, _JOURNAL_DIRTY
    descriptor, identity = _JOURNAL_FD, _JOURNAL_ID
    _JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
    _JOURNAL_CREATED = _JOURNAL_DIRTY = False
    if descriptor is not None:
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Publisher journal FD was reused", "access")
            os.close(descriptor)
        except BaseException:
            journal_failure()
            raise


def verify_lock_description(descriptor, identity):
    """Verify one FD while its independently retained flock stays held."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AdapterError("Publisher lock FD changed", "access")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def verify_journal_locks():
    """Reject reused visible FDs without releasing the private lock pin."""
    for guard, identity in _JOURNAL_LOCKS.items():
        verify_lock_description(guard, identity)
        verify_lock_description(_JOURNAL_EXPOSED[guard], identity)


@contextlib.contextmanager
def locked_budget():
    """Keep an independent original flock description across every context."""
    global _JOURNAL_LOCK_ID
    budget_file()
    descriptor = guard = None
    identity = None
    try:
        if _JOURNAL_LOCKS:
            verify_journal_locks()
            original = next(iter(_JOURNAL_LOCKS))
            descriptor = os.dup(original)
        else:
            descriptor = secure_file(
                "state.lock",
                os.O_RDWR
                | (
                    os.O_CREAT
                    | (os.O_EXCL if _LOCAL_INIT_AUTHORITY is not None else 0)
                    if _STATE_INITIALIZING
                    else 0
                ),
            )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if identity != (current.st_dev, current.st_ino) or (
            _JOURNAL_LOCK_ID is not None and identity != _JOURNAL_LOCK_ID
        ):
            raise AdapterError("Publisher journal lock changed", "access")
        guard = os.dup(descriptor)
        _JOURNAL_LOCK_ID = identity
        _JOURNAL_LOCKS[guard] = identity
        _JOURNAL_EXPOSED[guard] = descriptor
        retain_journal()
        yield descriptor
    except BaseException:
        journal_failure()
        raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        errors, safe = [], []
        # Assess BOTH descriptions before closing either: an independent
        # same-inode replacement cannot acquire the original held flock.
        for candidate in (descriptor, guard):
            if candidate is None:
                continue
            try:
                if identity is None:
                    raise AdapterError("Unproved publisher lock FD", "access")
                verify_lock_description(candidate, identity)
                safe.append(candidate)
            except BaseException as error:
                journal_failure()
                errors.append(error)
        if guard is not None:
            _JOURNAL_LOCKS.pop(guard, None)
            _JOURNAL_EXPOSED.pop(guard, None)
        for candidate in safe:
            try:
                info = os.fstat(candidate)
                if (info.st_dev, info.st_ino) != identity:
                    raise AdapterError("Publisher lock FD was reused", "access")
                os.close(candidate)
            except BaseException as error:
                journal_failure()
                errors.append(error)
        if errors and not primary_error:
            raise errors[0]


def load_budget():
    """Read the retained inode, checking its generation after the read."""
    if not _JOURNAL_LOCKS:
        if _STATE_PATH is None:
            raise AdapterError("Publisher state is not initialized", "access")
        with locked_budget():
            return load_budget()
    try:
        verify_journal()
        raw = os.pread(_JOURNAL_FD, 16385, 0)
        verify_journal()
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher state", "access")
        if not raw and _JOURNAL_CREATED and _STATE_INITIALIZING:
            return empty_budget(time.time())
        try:
            if _LOCAL_SCHEMA1_REHYDRATION is not None:
                expected = _LOCAL_SCHEMA1_REHYDRATION
                if hashlib.sha256(raw).hexdigest() == expected["raw_sha256"]:
                    info = os.fstat(_JOURNAL_FD)
                    if [info.st_dev, info.st_ino] != expected[
                        "state_identity"
                    ] or list(_STATE_DIR_ID) != expected["parent_identity"]:
                        raise AdapterError(
                            "Exact schema1 inode changed", "access"
                        )
                    return validate_budget(
                        copy.deepcopy(expected["modern_state"])
                    )
            return validate_budget(json.loads(raw))
        except (ValueError, TypeError):
            raise AdapterError("Corrupt publisher state", "access") from None
    except BaseException:
        journal_failure()
        raise


def save_budget(state):
    """Commit through the held inode; interrupted rewrites fail closed."""
    global _STATE_INITIALIZING, _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    global _JOURNAL_CREATED, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "ONEK state persistence is already unhealthy",
            "access",
        )
    try:
        state = validate_budget(state)
        if (
            not state["in_flight"]
            and _ACTIVE_FLIGHT_ID is not None
            and (
                _COMPLETING_FLIGHT_ID != _ACTIVE_FLIGHT_ID
                or state["completed_flight_id"] != _ACTIVE_FLIGHT_ID
            )
        ):
            raise AdapterError("Unproved ONEK completion write", "access")
        payload = json.dumps(state, separators=(",", ":")).encode()
        if len(payload) > 16384 or not _JOURNAL_LOCKS:
            raise AdapterError(
                "Bounded locked journal commit required", "access"
            )
        verify_journal()
        _STATE_INITIALIZING = _JOURNAL_CREATED = False
        _JOURNAL_DIRTY = True
        os.ftruncate(_JOURNAL_FD, 0)
        os.fsync(_JOURNAL_FD)
        verify_journal(False)
        offset = 0
        while offset < len(payload):
            verify_journal(False)
            count = os.pwrite(_JOURNAL_FD, payload[offset:], offset)
            if type(count) is not int or not 0 < count <= len(payload) - offset:
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += count
            verify_journal(False)
        os.fsync(_JOURNAL_FD)
        verify_journal(False)
        before = verify_journal(False)
        if os.pread(_JOURNAL_FD, len(payload) + 1, 0) != payload:
            raise AdapterError("Publisher journal bytes changed", "access")
        info = verify_journal(False)
        if journal_version(before) != journal_version(info):
            raise AdapterError(
                "Publisher journal changed during commit read", "access"
            )
        _JOURNAL_VERSION = journal_version(info)
        _JOURNAL_DIRTY = False
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            _FAILED_BUDGET = copy.deepcopy(state)
        with contextlib.suppress(BaseException):
            invalidate_budget()
        raise


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
    try:
        info = os.fstat(descriptor)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe publisher state file", "access")
        return descriptor
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            os.close(descriptor)
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
        r"(?:\s|>))",
        prefix,
        re.I,
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
                "html",
                "head",
                "title",
                "meta",
                "link",
                "base",
                "style",
                "script",
                "noscript",
                "template",
            }:
                self.body_started = True
            if (
                tag == "head"
                and not self.head_seen
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
                data.strip()
                and self.head_depth is None
                and (not self.stack or not any(self.stack[-1][1:]))
                and not any(
                    frame[0] in {"head", "title"} for frame in self.stack
                )
            ):
                self.body_started = True
            if not any(frame[0] in {"head", "title"} for frame in self.stack):
                super().handle_data(data)
            if (
                self.head_depth is not None
                and not self.body_started
                and len(self.stack) == self.head_depth + 1
                and self.stack[-1][0] == "title"
                and self.stack[-2][0] == "head"
                and not any(self.stack[-1][1:])
            ):
                self.head_title.append(data)

    parser = ChallengeText()
    parser.feed(body.decode("utf-8", "replace"))
    text = " ".join(" ".join(parser.text + parser.head_title).lower().split())
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
        raise PolicyStop("Visible publisher challenge or denial", "access")


def wait_inventory_page():
    """Separate inventory completion from ordinary physical completion."""
    with locked_budget():
        state = load_budget()
        target = state["last_inventory_completed"] + state["page_interval"]
        if _INVENTORY_COMPLETED_MONOTONIC is not None:
            target = max(
                target,
                time.time()
                + max(
                    0,
                    _INVENTORY_COMPLETED_MONOTONIC
                    + state["page_interval"]
                    - time.monotonic(),
                ),
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
        or type(identity) is not int
        or identity <= 0
        or type(attempt) is not int
        or attempt <= 0
        or run.get("repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or not isinstance(head, str)
        or not re.fullmatch(r"[0-9a-f]{40}", head)
        or expected is not None
        and (identity, attempt, source, head) != expected
    ):
        raise AdapterError("Untrusted family run identity", "access")
    return identity, attempt, source, head


def workflow_api(path):
    """Read real public family metadata using existing authentication."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        separator = "&" if "?" in path else "?"
        status, _, body = infrastructure_request_bytes(
            _WORKFLOW_GITHUB
            + path
            + separator
            + "_fresh="
            + str(time.time_ns()),
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


@bounded_infrastructure
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
            or not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com")
            )
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
        status, _, body = infrastructure_request_bytes(
            destination, byte_limit=32768
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


def checked_artifact_path(path):
    """Validate the configured root before pinning its exact directory inode."""
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    info = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or info.st_mode & 0o700 != 0o700
    ):
        raise AdapterError("Unsafe pacing artifact directory", "access")
    return root, info


def verify_artifact_directory(root, parent):
    """Require the current pathname to identify the pinned safe directory."""
    current = os.stat(root, follow_symlinks=False)
    held = os.fstat(parent)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o022
        or current.st_mode & 0o700 != 0o700
        or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise AdapterError("Pacing artifact directory changed", "access")


def validate_owned_artifact(info):
    """Refuse unsafe objects without opening a blocking FIFO or changing it."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AdapterError("Unsafe existing pacing artifact", "access")


def verify_artifact_target(path, descriptor):
    """Check the current upload target against the exact open output inode."""
    root, _ = checked_artifact_path(path)
    current = os.stat(path, follow_symlinks=False)
    held = os.fstat(descriptor)
    validate_owned_artifact(current)
    validate_owned_artifact(held)
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        raise AdapterError("Pacing artifact output changed", "access")


def invalidate_artifact(parent, name, root=None):
    """Truncate the verified owned inode before fallible signaling or unlink."""
    if root is not None:
        verify_artifact_directory(root, parent)
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    validate_owned_artifact(before)
    descriptor = os.open(
        name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        validate_owned_artifact(info)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or (
            info.st_dev,
            info.st_ino,
        ) != (current.st_dev, current.st_ino):
            raise AdapterError("Pacing artifact output changed", "access")
        if root is not None:
            verify_artifact_directory(root, parent)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        pacing_not_ready()
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if root is not None:
            verify_artifact_directory(root, parent)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Pacing artifact output changed", "access")
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def artifact_output(path, cleanup_only=False):
    """Pin root and output through readiness and healthy context completion."""
    global _PUBLISHER_GATE_FAILED
    parent = descriptor = None
    try:
        root, before = checked_artifact_path(path)
        parent = os.open(
            root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        held = os.fstat(parent)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Pacing artifact directory changed", "access")
        verify_artifact_directory(root, parent)
        name = os.path.basename(path)
        invalidate_artifact(parent, name, root)
        if cleanup_only:
            yield None
            verify_artifact_directory(root, parent)
            return
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion cannot be exported", "access"
            )
        verify_artifact_directory(root, parent)
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        yield descriptor
        verify_artifact_directory(root, parent)
        verify_artifact_target(path, descriptor)
        os.fsync(descriptor)
        os.fsync(parent)
        verify_artifact_directory(root, parent)
        verify_artifact_target(path, descriptor)
    except BaseException:
        _PUBLISHER_GATE_FAILED = True
        if descriptor is not None:
            # The pinned owned output is invalidated even if its parent moved.
            with contextlib.suppress(Exception):
                validate_owned_artifact(os.fstat(descriptor))
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
        pacing_not_ready()
        if descriptor is not None and parent is not None:
            with contextlib.suppress(Exception):
                verify_artifact_directory(root, parent)
                verify_artifact_target(path, descriptor)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def workflow_mode_guard():
    """Check workflow state before history, journal creation or access."""
    result = workflow_api("/actions/workflows/fetch-cy-onek-youth-funds.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-cy-onek-youth-funds.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "ONEK workflow is not serialized for this mode",
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
            "fetch-cy-onek-youth-funds.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable ONEK workflow head",
                "access",
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("ONEK_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native ONEK MAX approval required", "access"
            )
        return incoming
    try:
        approval = json.loads(raw)
        if (
            not isinstance(approval, dict)
            or set(approval) != {"schema", "family", "evidence", "state"}
            or type(approval["schema"]) is not int
            or approval["schema"] != 1
            or approval["family"] != FAMILY
            or not isinstance(approval["evidence"], str)
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/325"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native ONEK approval", "access"
        ) from None


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    global _COLLECTION_LOCK_ID
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    workflow_mode_guard()
    current_job_context()
    incoming = restored_state()
    # No directory, lock or empty durable budget exists before reviewed state.
    if incoming["in_flight"]:
        raise AdapterError("Owned ONEK artifact is unsettled", "access")
    require_local_initialization_authority(incoming)
    budget_file()
    if _FIRST_HOSTED_PENDING and not _STATE_INITIALIZING:
        raise AdapterError(
            "First-hosted namespace was not exclusively created", "access"
        )
    if _COLLECTION_LOCK_FD is None:
        descriptor = secure_file(
            "collection.lock",
            os.O_RDWR
            | (
                os.O_CREAT
                | (os.O_EXCL if _LOCAL_INIT_AUTHORITY is not None else 0)
                if _STATE_INITIALIZING
                else 0
            ),
        )
        try:
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
        except BaseException:
            journal_failure()
            with contextlib.suppress(BaseException):
                os.close(descriptor)
            raise
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Collection lock FD was reused", "access")
        except BaseException:
            journal_failure()
            with contextlib.suppress(OSError):
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) == identity:
                    os.close(descriptor)
            raise AdapterError(
                "Another ONEK collector is active", "access"
            ) from None
        _COLLECTION_LOCK_ID = identity
        _COLLECTION_LOCK_FD = descriptor
    with locked_budget():
        now = time.time()
        local = load_budget()
        if local["in_flight"]:
            raise AdapterError("Local ONEK flight is unsettled", "access")
        mature_reentry_precommit(local)
        combined = merge_budgets(local, incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        if _LOCAL_INIT_AUTHORITY is not None:
            commit_local_initialization(combined)
        else:
            save_budget(combined)
        if _MATURE_LOCAL_REENTRY_AUTHORITY is not None:
            _MATURE_LOCAL_REENTRY_AUTHORITY["committed"] = True
    _COLLECTION_STARTED = time.time()
    _RESTORED = _STATE_TRUSTED = True


def export_family_artifact():
    path = os.environ.get("ONEK_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED:
        with artifact_output(path, cleanup_only=True):
            pass
        pacing_not_ready()
        return
    with artifact_output(path) as descriptor, locked_budget():
        run_id, attempt = current_run_identity()
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError(
                "Unsettled ONEK flight cannot be exported",
                "access",
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
        with os.fdopen(os.dup(descriptor), "w") as file:
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        verify_artifact_target(path, descriptor)
        verify_journal()
        workflow_output_signal(True)
        verify_journal()
        verify_artifact_target(path, descriptor)


def invalidate_budget():
    """Invalidate only the retained original FD, never a replacement name."""
    if _JOURNAL_FD is None:
        return
    info = os.fstat(_JOURNAL_FD)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink not in (0, 1)
        or (info.st_dev, info.st_ino) != _JOURNAL_ID
        or not _JOURNAL_DIRTY
        and info.st_nlink == 1
        and journal_version(info) != _JOURNAL_VERSION
    ):
        raise AdapterError("Original publisher journal changed", "access")
    os.ftruncate(_JOURNAL_FD, 0)
    os.fsync(_JOURNAL_FD)


def selected_robots_policies(groups):
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [g for g in groups if agent in g["agents"]]
    wildcard = [g for g in groups if "*" in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    # Current identity selection must not be displaced by a legacy grant.
    return [current or wildcard] + ([legacy] if legacy else [])


def workflow_output_signal(ready):
    """Write readiness only to a safe nonblocking regular workflow file."""
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
            raise AdapterError("Unsafe workflow output", "access")
        output.write(f"pacing_ready={str(ready).lower()}\n")
        output.flush()
        os.fsync(output.fileno())


def pacing_not_ready():
    """Best effort signal; pinned artifact truncation always precedes this."""
    with contextlib.suppress(Exception):
        workflow_output_signal(False)


def current_job_context():
    """Bind the executing source job and retain its actual55-minute deadline."""
    global _RUN_END, _PHASE_END
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    run_id, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    if (
        not os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GITHUB_WORKFLOW_REF")
        != (
            "YouthOpps/data-pipeline/.github/workflows/fetch-"
            + SOURCE_ID
            + ".yml@refs/heads/main"
        )
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise AdapterError("Untrusted current hosted workflow proof", "access")
    expected = (run_id, attempt, SOURCE_ID, head)
    run = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(run, expected)
    if (
        run.get("status") != "in_progress"
        or os.environ.get("GITHUB_JOB") != "collect"
    ):
        raise AdapterError("ONEK current job is not executing", "access")
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        type(result.get("total_count")) is not int
        or result["total_count"] != 1
        or not isinstance(jobs, list)
        or len(jobs) != 1
    ):
        raise AdapterError("ONEK current job inventory incomplete", "access")
    job = jobs[0]
    if (
        not isinstance(job, dict)
        or type(job.get("id")) is not int
        or job["id"] <= 0
        or job.get("run_id") != run_id
        or job.get("run_attempt") != attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("name") != "collect"
        or job.get("status") != "in_progress"
        or job.get("workflow_name") != _WORKFLOW_NAME
        or "conclusion" not in job
        or job.get("conclusion") is not None
        or type(job.get("runner_id")) is not int
        or job["runner_id"] <= 0
    ):
        raise AdapterError("ONEK current job ownership mismatch", "access")
    try:
        started = datetime.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.utcoffset() != timedelta(0):
            raise ValueError("Missing UTC job timezone")
        elapsed = time.time() - started.timestamp()
        if not -1 <= elapsed < 3300 - EXPORT_TIMEOUT:
            raise ValueError("Job budget exhausted")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AdapterError("Untrusted ONEK job clock", "access") from None
    _RUN_END = min(_RUN_END, time.monotonic() + 3300 - max(elapsed, 0) - 2)
    if _PHASE_NAME == "prepublication":
        _PHASE_END = min(_PHASE_END, _RUN_END - 390)
        arm_alarm(_PHASE_END)
    check_deadline()


class PolicyStop(AdapterError):
    """A fully assessed response or durable explicit refusal ends this run."""

    policy_retained = True


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": 0,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "completed": 0,
        "page_interval": 30,
        "last_inventory_completed": 0,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "flight_started": 0,
        "flight_kind": None,
        "completed_flight_id": None,
        "request_id": None,
        "request_started": 0,
        "request_inventory": False,
        "completed_request_id": None,
        "request_outcome": None,
    }


def validate_budget(state):
    if (
        not isinstance(state, dict)
        or set(state) != set(empty_budget(0))
        or type(state.get("schema")) is not int
        or state["schema"] != _STATE_SCHEMA
        or state["family"] != FAMILY
        or type(state["blocked"]) is not bool
        or state["legacy_rate_history_unknown"] is not True
        or type(state["in_flight"]) is not bool
        or type(state["request_inventory"]) is not bool
    ):
        raise AdapterError("Untrusted publisher state schema", "access")
    fields = (
        "observed_at",
        "not_before",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_until",
        "flight_started",
        "request_started",
    )
    if any(not numeric(state[k]) or state[k] > 253402300799 for k in fields):
        raise AdapterError("Unsupported publisher state clock", "access")
    if (
        state["interval"] < 12.2
        or state["page_interval"] < 30
        or type(state["rate_limit_strikes"]) is not int
        or not 0 <= state["rate_limit_strikes"] <= 100
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 512
        or any(not numeric(t) or t > 253402300799 for t in state["starts"])
        or state["starts"] != sorted(set(state["starts"]))
    ):
        raise AdapterError("Invalid publisher pacing constraints", "access")
    for key in (
        "flight_id",
        "completed_flight_id",
        "request_id",
        "completed_request_id",
    ):
        if state[key] is not None and (
            not isinstance(state[key], str)
            or not re.fullmatch(r"[0-9a-f]{32}", state[key])
        ):
            raise AdapterError(
                "Invalid publisher transaction identity", "access"
            )
    if (state["flight_id"] is None) != (state["flight_kind"] is None):
        raise AdapterError("Missing publisher owner kind", "access")
    if state["flight_kind"] not in (
        None,
        "policy",
        "physical",
        "initialization",
    ):
        raise AdapterError("Invalid publisher transaction kind", "access")
    if state["request_outcome"] not in (
        None,
        "reserved",
        "response_assessed",
        "no_response",
        "policy_only",
    ):
        raise AdapterError("Invalid request assessment state", "access")
    if state["in_flight"]:
        if state["flight_id"] is None or state["flight_kind"] is None:
            raise AdapterError("Missing pending transaction identity", "access")
    elif (
        state["flight_id"] != state["completed_flight_id"]
        or state["flight_started"] > state["completed"]
        or state["request_id"] != state["completed_request_id"]
        or state["request_started"] > state["completed"]
        or state["request_outcome"] == "reserved"
    ):
        raise AdapterError(
            "Unproved publisher transaction completion", "access"
        )
    return state


def publisher_healthy(state):
    if _PUBLISHER_GATE_FAILED or (
        state["in_flight"] and state["flight_id"] != _ACTIVE_FLIGHT_ID
    ):
        raise AdapterError("Unsettled publisher transaction retained", "access")


def fail_policy(state=None):
    """Never heal an unassessed response, failed calculation or failed save."""
    global _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    _PUBLISHER_GATE_FAILED = True
    if state is not None:
        _FAILED_BUDGET = copy.deepcopy(state)
    elif _FAILED_BUDGET is None:
        with contextlib.suppress(BaseException):
            _FAILED_BUDGET = copy.deepcopy(load_budget())
    with contextlib.suppress(BaseException):
        invalidate_budget()


def begin_transaction(kind):
    global _ACTIVE_FLIGHT_ID
    check_deadline()
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Healthy restored publisher state required", "access"
        )
    with locked_budget():
        state = load_budget()
        if state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Publisher state requires reviewed recovery", "access"
            )
        now = time.time()
        if state["rate_limit_until"] > now:
            raise AdapterError("Retained timed publisher cooldown", "access")
        if state["rate_limit_strikes"] >= 100:
            raise AdapterError("Publisher rate counter exhausted", "access")
        ready = max(state["not_before"], state["completed"] + state["interval"])
        if ready + 120 > now + (active_deadline() - time.monotonic()):
            raise AdapterError(
                "Known publisher guard exceeds phase budget", "access"
            )
        ident = os.urandom(16).hex()
        if ident == state["flight_id"]:
            raise AdapterError(
                "Publisher transaction identity collision", "access"
            )
        state.update(
            in_flight=True,
            flight_id=ident,
            flight_started=now,
            flight_kind=kind,
            observed_at=now,
        )
        # This fsynced owner remains True across original no-response retries,
        # all redirect validation and new/cached robots policy assessment.
        save_budget(state)
        _ACTIVE_FLIGHT_ID = ident


def durable_transaction(kind):
    def decorate(function):
        @functools.wraps(function)
        def invoke(url, *args, **kwargs):
            global _TRANSACTION_DEPTH, _ACTIVE_FLIGHT_ID
            global _COMPLETING_FLIGHT_ID
            if not is_publisher(url):
                return function(url, *args, **kwargs)
            if _PUBLISHER_GATE_FAILED:
                raise AdapterError(
                    "Publisher policy failure is sticky", "access"
                )
            outer = _TRANSACTION_DEPTH == 0
            if outer:
                begin_transaction(kind)
            _TRANSACTION_DEPTH += 1
            settled = False
            try:
                result = function(url, *args, **kwargs)
                settled = True
                return result
            except PolicyStop:
                settled = not _PUBLISHER_GATE_FAILED
                raise
            finally:
                _TRANSACTION_DEPTH -= 1
                if outer:
                    try:
                        if not settled or _PUBLISHER_GATE_FAILED:
                            fail_policy()
                            if settled:
                                raise AdapterError(
                                    "Unhealthy policy cannot return data",
                                    "access",
                                )
                        else:
                            with locked_budget():
                                state = load_budget()
                                publisher_healthy(state)
                                if (
                                    not state["in_flight"]
                                    or state["flight_id"] != _ACTIVE_FLIGHT_ID
                                ):
                                    raise AdapterError(
                                        "Publisher owner changed", "access"
                                    )
                                now = time.time()
                                state.update(
                                    in_flight=False,
                                    completed_flight_id=_ACTIVE_FLIGHT_ID,
                                    completed=max(state["completed"], now),
                                    observed_at=now,
                                )
                                state["not_before"] = max(
                                    state["not_before"],
                                    state["completed"] + state["interval"],
                                )
                                _COMPLETING_FLIGHT_ID = _ACTIVE_FLIGHT_ID
                                save_budget(state)
                            _ACTIVE_FLIGHT_ID = None
                    except BaseException:
                        fail_policy()
                        raise
                    finally:
                        _COMPLETING_FLIGHT_ID = None

        return invoke

    return decorate


def pace():
    global _PUBLISHER_INTERVAL, _ACTIVE_REQUEST_ID
    if _PUBLISHER_GATE_FAILED or _ACTIVE_FLIGHT_ID is None:
        raise AdapterError(
            "Reserved publisher policy transaction required", "access"
        )
    check_deadline()
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        if not state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Publisher access state is not healthy", "access"
            )
        now = time.time()
        if (
            state["rate_limit_until"] > now
            or state["rate_limit_strikes"] >= 100
        ):
            raise AdapterError("Publisher cooldown/counter retained", "access")
        starts = [t for t in state["starts"] if t > now - 60]
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"], 12.2)
        target = max(
            now, state["not_before"], state["completed"] + _PUBLISHER_INTERVAL
        )
        if starts:
            target = max(target, starts[-1] + _PUBLISHER_INTERVAL)
        if len(starts) >= 5:
            target = max(target, starts[-5] + 60)
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
            if wait + 120 > active_deadline() - time.monotonic():
                raise AdapterError(
                    "Publisher pacing exceeds remaining phase", "access"
                )
            if wait <= 0:
                break
            time.sleep(wait)
        now = time.time()
        if now < target:
            raise AdapterError("Publisher clock moved backwards", "access")
        ident = os.urandom(16).hex()
        if ident == state["request_id"]:
            raise AdapterError("Physical request identity collision", "access")
        state.update(
            starts=[t for t in starts if t > now - 60] + [now],
            interval=_PUBLISHER_INTERVAL,
            observed_at=now,
            request_id=ident,
            request_started=now,
            request_outcome="reserved",
        )
        state["not_before"] = max(state["not_before"], now + state["interval"])
        save_budget(state)
        _ACTIVE_REQUEST_ID = ident


@contextlib.contextmanager
def publisher_attempt(inventory=False):
    global _PUBLISHER_COMPLETED_MONOTONIC, _INVENTORY_COMPLETED_MONOTONIC
    global _ACTIVE_REQUEST_ID
    frame = {"started": False, "received": False, "assessed": False}
    _REQUEST_FRAMES.append(frame)
    try:
        yield frame
    except PolicyStop:
        frame["assessed"] = True
        raise
    finally:
        if not _REQUEST_FRAMES or _REQUEST_FRAMES.pop() is not frame:
            fail_policy()
        if not frame["assessed"]:
            fail_policy()
        if frame["started"]:
            _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
        if not _PUBLISHER_GATE_FAILED:
            try:
                with locked_budget():
                    state = load_budget()
                    publisher_healthy(state)
                    if state["request_id"] != _ACTIVE_REQUEST_ID:
                        raise AdapterError(
                            "Physical request identity changed", "access"
                        )
                    now = time.time()
                    state.update(
                        completed=now,
                        observed_at=now,
                        completed_request_id=_ACTIVE_REQUEST_ID,
                        request_outcome=(
                            "policy_only"
                            if not frame["started"]
                            else (
                                "response_assessed"
                                if frame["assessed"]
                                else "no_response"
                            )
                        ),
                    )
                    state["not_before"] = max(
                        state["not_before"], now + state["interval"]
                    )
                    if inventory and frame["started"]:
                        state["last_inventory_completed"] = now
                        _INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
                    # Physical completion NEVER clears the enclosing owner.
                    save_budget(state)
                _ACTIVE_REQUEST_ID = None
            except BaseException:
                fail_policy()
                raise


def record_retry_after(value, status=None):
    now = time.time()
    until = retry_after_until(value, now)
    if status != 429 and until is None:
        return
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        if status == 429:
            strikes = state["rate_limit_strikes"] + 1
            if strikes > 100:
                fail_policy(state)
                raise AdapterError("Publisher rate counter exhausted", "access")
            state["rate_limit_strikes"] = strikes
            factor = 2**strikes
            interval = max(state["interval"] * 2, 12.2 * factor)
            page = max(state["page_interval"] * 2, 30 * factor)
            fallback = now + 1800 * 2 ** (strikes - 1)
            if any(
                not numeric(v) or v > 253402300799
                for v in (interval, page, fallback)
            ):
                fail_policy(state)
                raise AdapterError(
                    "Unsupported observed rate constraint", "access"
                )
            state.update(
                rate_limit_strikes=strikes,
                interval=interval,
                page_interval=page,
            )
            state["rate_limit_until"] = max(
                state["rate_limit_until"], fallback, until or 0
            )
        state["not_before"] = max(
            state["not_before"], state["rate_limit_until"], until or 0
        )
        state["observed_at"] = now
        save_budget(state)


def merge_budgets(local, incoming, now):
    local, incoming = validate_budget(local), validate_budget(incoming)
    result = copy.deepcopy(
        max((local, incoming), key=lambda st: st["completed"])
    )
    for key in (
        "observed_at",
        "not_before",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_strikes",
        "rate_limit_until",
    ):
        result[key] = max(local[key], incoming[key])
    result["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    result["blocked"] = local["blocked"] or incoming["blocked"]
    result["legacy_rate_history_unknown"] = (
        local["legacy_rate_history_unknown"]
        or incoming["legacy_rate_history_unknown"]
    )
    pending = [st for st in (local, incoming) if st["in_flight"]]
    if pending:
        if len({st["flight_id"] for st in pending}) != 1:
            raise AdapterError("Conflicting pending publisher owners", "access")
        owner = max(pending, key=lambda st: st["observed_at"])
        for key in (
            "flight_id",
            "flight_started",
            "flight_kind",
            "completed_flight_id",
            "request_id",
            "request_started",
            "request_inventory",
            "completed_request_id",
            "request_outcome",
        ):
            result[key] = owner[key]
    result["in_flight"] = bool(pending)
    return validate_budget(result)


def parse_robots(body):
    text = body.decode("utf-8", "strict")
    groups, group = [], {"agents": [], "rules": [], "delay": 0, "timing": []}
    for raw in text.splitlines():
        key, separator, value = raw.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["timing"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0, "timing": []}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
        elif key in ("crawl-delay", "request-rate"):
            # Raw values in unrelated groups cannot fail our assessment.
            group["timing"].append((key, value))
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


def robots_group_delay(group):
    """Validate only selected timing; retain every equally applicable MAX."""
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    delay = group.get("delay", 0)
    try:
        if not numeric(delay):
            raise ValueError("Invalid retained REP delay")
        for key, value in group.get("timing", []):
            if key == "crawl-delay":
                if not re.fullmatch(decimal, value):
                    raise ValueError("Malformed applicable crawl delay")
                seconds = float(value)
            else:
                match = re.fullmatch(
                    r"([0-9]+)\s*/\s*(" + decimal + r")", value
                )
                if not match:
                    raise ValueError("Malformed applicable request rate")
                requests, period = int(match[1]), float(match[2])
                if requests <= 0 or not numeric(period) or period <= 0:
                    raise ValueError("Invalid applicable rate")
                seconds = period / requests + 0.01
            if not numeric(seconds):
                raise ValueError("Unsupported applicable timing")
            datetime.fromtimestamp(time.time() + seconds, timezone.utc)
            delay = max(delay, seconds)
    except (ValueError, TypeError, OverflowError, OSError):
        raise AdapterError(
            "Unsupported applicable robots timing; assessment incomplete",
            "access",
        ) from None
    return delay


def apply_robots_policy(url, groups):
    global _PUBLISHER_INTERVAL
    if _ACTIVE_FLIGHT_ID is None or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Reserved publisher policy owner required", "access")
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    policies = selected_robots_policies(groups)
    for selected in policies:
        matches = []
        for group in selected:
            for action, pattern in group["rules"]:
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
                            action == "allow",
                        )
                    )
        if matches and not max(matches)[1]:
            retain_publisher_hold()
            raise PolicyStop(
                "Robots policy disallows this source endpoint", "access"
            )
    delay = max(
        [12.2]
        + [robots_group_delay(g) for selected in policies for g in selected]
    )
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"], delay)
        state["interval"] = _PUBLISHER_INTERVAL
        state["observed_at"] = time.time()
        save_budget(state)


def completed_identity(run, expected=None):
    """Use exact immutable attempts/jobs; retain the pinned jobless cancel."""
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
        not isinstance(jobs, list)
        or len(jobs) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(jobs)
    ):
        raise AdapterError("Incomplete exact family jobs", "access")
    if not jobs:
        pin = _LEGACY_JOB_PINS.get(str(run_id))
        if (
            not pin
            or pin["jobs"]
            or attempt != pin["attempt"]
            or head != pin["head"]
            or pin["conclusion"] != "cancelled"
            or exact.get("conclusion") != "cancelled"
        ):
            raise AdapterError("Unreviewed jobless family attempt", "access")
        _HISTORY_JOBLESS.append(identity)
        return None
    began, finished, seen = [], [], set()
    try:
        for job in jobs:
            if (
                type(job.get("id")) is not int
                or job["id"] <= 0
                or job["id"] in seen
                or type(job.get("run_id")) is not int
                or job["run_id"] != run_id
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt
                or job.get("head_sha") != head
                or job.get("head_branch") != "main"
                or job.get("status") != "completed"
            ):
                raise ValueError("Invalid exact attempt binding")
            seen.add(job["id"])
            times = [
                datetime.fromisoformat(job[k].replace("Z", "+00:00"))
                for k in ("started_at", "completed_at")
            ]
            if any(value.utcoffset() is None for value in times):
                raise ValueError("Missing job timezone")
            start, end = (value.timestamp() for value in times)
            if not 0 <= start <= end <= time.time() + 1:
                raise ValueError("Invalid actual job chronology")
            began.append(start)
            finished.append(end)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AdapterError(
            "Untrusted completed family jobs", "access"
        ) from None
    return max(finished), min(began), *identity


def scan_family_history():
    global _HISTORY_COMPLETED, _HISTORY_JOBLESS
    _HISTORY_JOBLESS = []
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    candidates, seen = [], set()
    total, scanned, current_seen = None, 0, False

    def remember(run, expected=None):
        row = completed_identity(run, expected)
        if row is not None:
            candidates.append(row)

    for page in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-cy-onek-youth-funds.yml/runs"
            + f"?per_page=100&page={page}"
        )
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(runs, list)
            or len(runs) > 100
        ):
            raise AdapterError("Invalid family workflow inventory", "access")
        if total is None:
            total = count
        elif count != total:
            raise InventoryRace(
                "Family history changed during restore", "access"
            )
        scanned += len(runs)
        for run in runs:
            identity, attempt, source, head = validate_family_identity(run)
            if identity in seen:
                raise AdapterError("Duplicate workflow run", "access")
            seen.add(identity)
            if current and identity == current[0]:
                if (identity, attempt) != current or head != os.environ[
                    "GITHUB_SHA"
                ]:
                    raise AdapterError(
                        "Current immutable attempt mismatch", "access"
                    )
                current_seen = True
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    remember(prior, (identity, attempt - 1, source, head))
                continue
            if run.get("status") != "completed":
                if not hosted or run.get("status") not in {
                    "queued",
                    "waiting",
                    "pending",
                    "requested",
                }:
                    raise AdapterError(
                        "Another family collector is active", "access"
                    )
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    remember(prior, (identity, attempt - 1, source, head))
                continue
            remember(run)
        if scanned >= total:
            if scanned != total or hosted and not current_seen:
                raise AdapterError("Incomplete family run inventory", "access")
            _HISTORY_COMPLETED = sorted(candidates)
            if (
                len(candidates) > 1
                and _HISTORY_COMPLETED[-1][0] == _HISTORY_COMPLETED[-2][0]
            ):
                raise AdapterError(
                    "Ambiguous actual family chronology", "access"
                )
            return _HISTORY_COMPLETED[-1][2:] if candidates else None
        if len(runs) != 100:
            raise AdapterError("Truncated family history", "access")
    raise AdapterError(
        "Family history exceeds reviewed metadata bound", "access"
    )


def active_deadline():
    if _RUN_END is None or _PHASE_END is None:
        raise AdapterError("Bounded ONEK phase required", "access")
    return min(_RUN_END, _PHASE_END)


@contextlib.contextmanager
def operation_guard():
    """Share the original phase alarm; never install a competing timer."""
    check_deadline()
    yield
    check_deadline()


def nonexport_end():
    return _RUN_END - EXPORT_TIMEOUT


@contextlib.contextmanager
def phase(name, seconds):
    """Admit each original phase once with its downstream reserve intact."""
    global _PHASE_END, _PHASE_NAME
    if _PHASE_END is not None or _RUN_END is None or name in _PHASE_SEEN:
        raise AdapterError("Invalid or renewed phase", "access")
    reserve = {
        "prepublication": 390,
        "publication": 210,
        "failure": 30,
        "export": 0,
    }
    if name not in reserve:
        raise AdapterError("Unknown ONEK phase", "access")
    _PHASE_END = min(time.monotonic() + seconds, _RUN_END - reserve[name])
    _PHASE_NAME = name
    _PHASE_SEEN.add(name)
    try:
        check_deadline()
        arm_alarm(_PHASE_END)
        yield
        check_deadline()
    finally:
        _PHASE_END = _PHASE_NAME = None
        arm_alarm(_RUN_END - (0 if name == "export" else EXPORT_TIMEOUT))


@contextlib.contextmanager
def execution():
    """Use one whole-entry alarm and preserve sticky failures across reentry."""
    global _LOCAL_INIT_AUTHORITY, _VERIFIED_LEGACY_PRODUCERS
    global _MATURE_LOCAL_REENTRY_AUTHORITY
    global _RESTORED_OWNED_IDENTITY
    global _RUN_END, _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
    global _RESTORED, _STATE_TRUSTED, _LOCAL_FIRST_WIRE_VERIFIED
    global _LOCAL_SCHEMA1_REHYDRATION
    if _RUN_END is not None or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Nested or failed ONEK execution", "access")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        and "DATA_SOURCE_TOKEN" in os.environ
    ):
        raise AdapterError(
            "Nonpublishing execution forbids publication token", "access"
        )
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    previous = signal.getsignal(signal.SIGALRM)
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
    _LOCAL_SCHEMA1_REHYDRATION = None
    _LOCAL_INIT_AUTHORITY = None
    _MATURE_LOCAL_REENTRY_AUTHORITY = None
    _VERIFIED_LEGACY_PRODUCERS.clear()
    _RESTORED_OWNED_IDENTITY = None
    _ROBOTS.clear()
    _REP_TARGETS.clear()
    _PHASE_SEEN.clear()
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    signal.signal(signal.SIGALRM, alarm_expired)
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        primary = sys.exc_info()[0] is not None
        errors = []
        try:
            if _COLLECTION_LOCK_FD is not None:
                fd, ident = _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
                _COLLECTION_LOCK_FD = _COLLECTION_LOCK_ID = None
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != ident:
                    raise AdapterError(
                        "Collection lock identity changed", "access"
                    )
                os.close(fd)
        except BaseException as error:
            journal_failure()
            errors.append(error)
        try:
            close_journal()
        except BaseException as error:
            journal_failure()
            errors.append(error)
        if _LOCAL_INIT_AUTHORITY is not None:
            ancestor = _LOCAL_INIT_AUTHORITY.pop("ancestor_fd", None)
            if ancestor is not None:
                try:
                    verify_initialization_ancestor(ancestor)
                    os.close(ancestor)
                except BaseException as error:
                    journal_failure()
                    errors.append(error)
            trusted_parent = _LOCAL_INIT_AUTHORITY.pop(
                "trusted_parent_fd", None
            )
            if trusted_parent is not None:
                try:
                    pins = _LOCAL_INIT_AUTHORITY["grant"]["ancestor"][
                        "trusted_parent"
                    ]
                    info = os.fstat(trusted_parent)
                    if [info.st_dev, info.st_ino] != pins["identity"]:
                        raise AdapterError(
                            "Trusted parent descriptor changed", "access"
                        )
                    os.close(trusted_parent)
                except BaseException as error:
                    journal_failure()
                    errors.append(error)
            _LOCAL_INIT_AUTHORITY = None
        _MATURE_LOCAL_REENTRY_AUTHORITY = None
        if _PUBLISHER_GATE_FAILED:
            path = os.environ.get("ONEK_PACING_ARTIFACT_PATH")
            if path:
                with contextlib.suppress(BaseException):
                    with artifact_output(path, cleanup_only=True):
                        pass
            pacing_not_ready()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = None
        if errors and not primary:
            raise errors[0]


def fetch_inventory_page(fetch, *args, **kwargs):
    """Carry explicit inventory purpose through each physical hop and retry."""
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = True
    try:
        return fetch(*args, **kwargs)
    finally:
        _INVENTORY_ACTIVE = previous


def is_publisher(url):
    try:
        return urllib.parse.urlsplit(url).hostname == "youthfunds.onek.org.cy"
    except (TypeError, ValueError):
        return False


def infrastructure_request_bytes(
    url, *, headers=None, method="GET", payload=None, byte_limit=30000000
):
    """Read one metadata/artifact response without publisher or GH pacing."""
    if is_publisher(url):
        raise AdapterError(
            "Publisher cannot use infrastructure transport", "access"
        )
    check_deadline()
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={**(headers or {}), "User-Agent": _USER_AGENT},
    )
    try:
        response = _OPENER.open(request, timeout=request_timeout(False))
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise AdapterError("Infrastructure response exceeds bound", "fetch")
        return response.status, response.headers, body


@durable_transaction("physical")
def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
):
    """Preserve six hops and original nonpublisher transport without a GH gate."""
    if not publisher:
        if is_publisher(url):
            raise AdapterError("Publisher purpose required", "access")
        return legacy_request_bytes(
            url,
            method=method,
            headers=headers,
            payload=payload,
            publisher=False,
            byte_limit=byte_limit,
        )
    current = public_url(url)
    robots = urllib.parse.urlsplit(current).path == "/robots.txt"
    inventory = bool(_INVENTORY_ACTIVE and not robots)
    for hop in range(6):
        if robots and urllib.parse.urlsplit(current).path != "/robots.txt":
            raise AdapterError("REP redirect cannot become content", "access")
        if not robots:
            check_robots(current)
        status, response_headers, body = publisher_request_once(
            current, method, headers, payload, robots, inventory, byte_limit
        )
        if status not in (301, 302, 303, 307, 308):
            return status, response_headers, body
        location = response_headers.get("Location")
        if not location or hop == 5:
            raise AdapterError("Invalid redirect chain", "access")
        destination = public_url(urllib.parse.urljoin(current, location))
        if robots and urllib.parse.urlsplit(destination).path != "/robots.txt":
            raise AdapterError("REP redirect cannot become content", "access")
        if robots:
            old = urllib.parse.urlsplit(current)
            new = urllib.parse.urlsplit(destination)
            _REP_TARGETS[new.scheme + "://" + new.netloc] = _REP_TARGETS[
                old.scheme + "://" + old.netloc
            ]
        current = destination
    raise AdapterError("Unresolved redirect", "access")


def publisher_request_once(
    url, method, headers, payload, robots, inventory, byte_limit
):
    global _LOCAL_FIRST_WIRE_VERIFIED
    check_deadline()
    if (
        _PHASE_NAME != "prepublication"
        or not _STATE_TRUSTED
        or _PUBLISHER_GATE_FAILED
    ):
        raise AdapterError("Healthy prepublication phase required", "access")
    if (
        method not in ("GET", "POST")
        or robots
        and (method != "GET" or inventory)
    ):
        raise AdapterError("Invalid publisher request purpose", "access")
    if method == "POST" and (
        url != WEBSITE_URL + "em-ajax/get_listings/" or not inventory
    ):
        raise AdapterError("Unreviewed publisher POST", "access")
    origin = urllib.parse.urlsplit(url)
    origin = origin.scheme + "://" + origin.netloc
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        state["request_inventory"] = bool(inventory)
        state["observed_at"] = time.time()
        save_budget(state)
    if inventory:
        wait_inventory_page()
    pace()
    request_headers = {
        k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"
    }
    request_headers["User-Agent"] = _USER_AGENT
    request = urllib.request.Request(
        url, data=payload, method=method, headers=request_headers
    )
    with publisher_attempt(inventory) as frame:
        if not robots:
            apply_robots_policy(url, _ROBOTS[origin])
        if not _LOCAL_FIRST_WIRE_VERIFIED:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                workflow_mode_guard()
                mature_reentry_postquiet()
            _LOCAL_FIRST_WIRE_VERIFIED = True
        frame["started"] = True
        verify_journal()
        try:
            response = _OPENER.open(request, timeout=request_timeout(True))
        except urllib.error.HTTPError as error:
            response = error
        frame["received"] = True
        with response:
            status, response_headers = response.status, response.headers
            record_retry_after(response_headers.get("Retry-After"), status)
            if status == 429:
                raise PolicyStop(
                    "Publisher stopped: HTTP 429", "access", status
                )
            if not robots and status in (401, 403, 451):
                retain_publisher_hold()
            declared = response_headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) > byte_limit
            ):
                raise AdapterError(
                    "Oversized or invalid response length", "fetch"
                )
            body = response.read(byte_limit + 1)
            if (
                len(body) > byte_limit
                or declared
                and len(body) != int(declared)
            ):
                raise AdapterError("Incomplete or oversized response", "fetch")
            if (
                response_headers.get("Content-Encoding", "identity")
                != "identity"
            ):
                raise AdapterError(
                    "Unsupported response content encoding", "fetch"
                )
            if not robots or status in (200, 429):
                reject_visible_challenge(
                    body, response_headers, challenge_only=status == 429
                )
            if robots and status == 200:
                groups = parse_robots(body)
                target_origin, target = _REP_TARGETS[origin]
                apply_robots_policy(target, groups)
                _ROBOTS[target_origin] = groups
            frame["assessed"] = True
            if status == 429 or not robots and status in (401, 403, 451):
                raise PolicyStop(
                    "Publisher stopped: HTTP " + str(status), "access", status
                )
            return status, response_headers, body


@durable_transaction("policy")
def check_robots(url):
    parsed = urllib.parse.urlsplit(public_url(url))
    if parsed.path == "/robots.txt":
        return
    origin = parsed.scheme + "://" + parsed.netloc
    if origin not in _ROBOTS:
        _REP_TARGETS[origin] = (origin, url)
        status, _, body = request_bytes(origin + "/robots.txt", publisher=True)
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = []
        elif status != 200:
            raise PolicyStop(
                "Publisher robots policy unavailable", "access", status
            )
        if origin not in _ROBOTS:
            raise AdapterError("REP was not assessed", "access")
    apply_robots_policy(url, _ROBOTS[origin])


@durable_transaction("policy")
def fetch_public(url):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(reviewed, publisher=True)
        if status == 200:
            return status, headers, body
        if status == 503 and attempt == 0:
            with locked_budget():
                state = load_budget()
                now = time.time()
                state["not_before"] = max(state["not_before"], now + 30)
                state["observed_at"] = now
                save_budget(state)
            continue
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise PolicyStop(
            f"Required publisher input returned HTTP {status}: " + provenance,
            "fetch",
            status,
        )


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_legacy_budget(state):
    if not isinstance(state, dict) or set(state) != {
        "schema",
        "family",
        "observed_at",
        "not_before",
        "starts",
        "blocked",
    }:
        raise AdapterError("Exact schema1 state required", "access")
    if (
        type(state["schema"]) is not int
        or state["schema"] != 1
        or state["family"] != FAMILY
        or type(state["blocked"]) is not bool
        or not numeric(state["observed_at"])
        or not numeric(state["not_before"])
    ):
        raise AdapterError("Invalid legacy state identity", "access")
    starts = state["starts"]
    if (
        not isinstance(starts, list)
        or len(starts) > 10
        or any(not numeric(t) or t > state["observed_at"] for t in starts)
        or starts != sorted(set(starts))
    ):
        raise AdapterError("Invalid legacy starts", "access")
    return state


def verify_original_legacy_producer(expected):
    """Verify an explicitly declared original six-key producer at its head."""
    manifest = expected.get("original_producer")
    identity = expected.get("identity")
    if (
        not isinstance(identity, list)
        or len(identity) != 4
        or not isinstance(manifest, dict)
        or set(manifest) != {"path", "head", "source_sha256", "envelope_keys"}
        or manifest["path"] != "adapters/" + SOURCE_ID + "/adapter.py"
        or manifest["head"] != identity[3]
        or not re.fullmatch(r"[0-9a-f]{40}", manifest["head"])
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["source_sha256"])
        or manifest["envelope_keys"]
        != sorted(
            ["schema", "repository", "run_id", "run_attempt", "source", "state"]
        )
    ):
        raise AdapterError(
            "Exact original producer declaration required", "access"
        )
    key = (manifest["head"], manifest["path"], manifest["source_sha256"])
    if key in _VERIFIED_LEGACY_PRODUCERS:
        return
    result = workflow_api(
        "/contents/" + manifest["path"] + "?ref=" + manifest["head"]
    )
    if (
        not isinstance(result, dict)
        or result.get("type") != "file"
        or result.get("path") != manifest["path"]
        or result.get("encoding") != "base64"
        or not isinstance(result.get("content"), str)
        or len(result["content"]) > 400000
        or not re.fullmatch(r"[0-9a-f]{40}", result.get("sha", ""))
    ):
        raise AdapterError(
            "Original producer contents binding invalid", "access"
        )
    try:
        raw = base64.b64decode(
            re.sub(r"\s+", "", result["content"]), validate=True
        )
    except (ValueError, TypeError):
        raise AdapterError(
            "Original producer encoding invalid", "access"
        ) from None
    if (
        hashlib.sha256(raw).hexdigest() != manifest["source_sha256"]
        or hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw
        ).hexdigest()
        != result["sha"]
    ):
        raise AdapterError("Original producer bytes mismatch", "access")
    _VERIFIED_LEGACY_PRODUCERS.add(key)


def authorize_local_initialization(grant, incoming, approval):
    """Authorize one prospective claim; never infer historical settlement."""
    global _LOCAL_INIT_AUTHORITY
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError(
            "LOCAL grant cannot authorize HOSTED state", "access"
        )
    keys = {
        "purpose",
        "nonce",
        "ancestor",
        "leaf",
        "claim_name",
        "history_sha256",
        "latest_owned",
        "state_sha256",
    }
    if (
        not isinstance(grant, dict)
        or set(grant) != keys
        or not isinstance(approval, dict)
        or type(approval.get("schema")) is not int
        or approval["schema"] != 1
        or approval.get("family") != FAMILY
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/325#issuecomment-[1-9][0-9]*",
            approval.get("evidence", ""),
        )
        or grant["purpose"] != "PROSPECTIVE_FIRST_LOCAL_INITIALIZATION"
        or not isinstance(grant["nonce"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", grant["nonce"])
        or grant["history_sha256"] != approval.get("history_sha256")
        or grant["history_sha256"]
        != (
            canonical_digest(_LEGACY_JOB_PINS)
            if approval is _LEGACY_TRANSITION
            else canonical_digest(
                {"completed": _HISTORY_COMPLETED, "jobless": _HISTORY_JOBLESS}
            )
        )
        or not _HISTORY_COMPLETED
        or grant["state_sha256"] != canonical_digest(incoming)
        or incoming != approval.get("state")
        or incoming["in_flight"]
        or (
            approval is _LEGACY_TRANSITION and incoming["flight_id"] is not None
        )
        or not incoming["legacy_rate_history_unknown"]
        or grant["leaf"] != FAMILY + "-" + str(os.getuid())
        or grant["claim_name"]
        != "." + FAMILY + "-init-" + grant["nonce"] + ".claim"
    ):
        raise AdapterError("Exact prospective LOCAL grant required", "access")
    ancestor = grant["ancestor"]
    owner = grant["latest_owned"]
    if (
        not isinstance(ancestor, dict)
        or set(ancestor)
        not in (
            {"path", "identity", "uid", "mode"},
            {"path", "identity", "uid", "mode", "trusted_parent"},
        )
        or ancestor["path"] != tempfile.gettempdir()
        or type(ancestor["uid"]) is not int
        or ancestor["uid"] < 0
        or not isinstance(ancestor["identity"], list)
        or len(ancestor["identity"]) != 2
        or any(type(v) is not int for v in ancestor["identity"])
        or type(ancestor["mode"]) is not int
        or not isinstance(owner, dict)
        or set(owner) != {"identity", "artifact_id", "digest"}
        or tuple(owner["identity"]) != latest_family_identity()
        or type(owner["artifact_id"]) is not int
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", owner["digest"])
    ):
        raise AdapterError("Prospective LOCAL provenance mismatch", "access")
    trusted = ancestor.get("trusted_parent")
    if ancestor["uid"] == os.getuid() and ancestor["mode"] == 0o700:
        if trusted is not None:
            raise AdapterError("Unexpected private ancestor chain", "access")
    elif (
        ancestor["path"] != "/tmp"
        or ancestor["mode"] != 0o1777
        or not isinstance(trusted, dict)
        or set(trusted) != {"path", "name", "identity", "uid", "mode"}
        or trusted["path"] != "/"
        or trusted["name"] != "tmp"
        or not isinstance(trusted["identity"], list)
        or len(trusted["identity"]) != 2
        or any(type(v) is not int for v in trusted["identity"])
        or type(trusted["uid"]) is not int
        or trusted["uid"] < 0
        or type(trusted["mode"]) is not int
        or trusted["mode"] & 0o022
        or trusted["mode"] & 0o755 != 0o755
    ):
        raise AdapterError(
            "Exact trusted sticky ancestor chain required", "access"
        )
    # The enclosing real restore has already authenticated latest identity,
    # artifact digest, full history and MAX. Pin that exact artifact again here.
    if approval is _LEGACY_TRANSITION:
        latest = approval["legacy_original"]["latest_owned"]
        expected = {k: latest[k] for k in owner}
    else:
        expected = _RESTORED_OWNED_IDENTITY
        if owner != approval.get("latest_owned"):
            raise AdapterError("Modern LOCAL owned grant mismatch", "access")
    if owner != expected:
        raise AdapterError("Prospective owned identity mismatch", "access")
    if os.path.lexists(os.path.join(ancestor["path"], grant["leaf"])):
        raise AdapterError("Prospective LOCAL namespace exists", "access")
    _LOCAL_INIT_AUTHORITY = {
        "grant": copy.deepcopy(grant),
        "approval_sha256": canonical_digest(approval),
        "consumed": False,
    }


def verify_initialization_ancestor(parent):
    """Bind the claimed ancestor and its name throughout initialization."""
    grant = _LOCAL_INIT_AUTHORITY["grant"]
    trusted = grant["ancestor"].get("trusted_parent")
    if trusted is not None:
        descriptor = _LOCAL_INIT_AUTHORITY.get("trusted_parent_fd")
        acquired = descriptor is None
        if acquired:
            descriptor = os.open(
                trusted["path"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        try:
            root = os.fstat(descriptor)
            named_root = os.stat(trusted["path"], follow_symlinks=False)
            for info in (root, named_root):
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or [info.st_dev, info.st_ino] != trusted["identity"]
                    or info.st_uid != trusted["uid"]
                    or stat.S_IMODE(info.st_mode) != trusted["mode"]
                ):
                    raise AdapterError(
                        "Trusted ancestor parent changed", "access"
                    )
        except BaseException:
            if acquired:
                with contextlib.suppress(BaseException):
                    os.close(descriptor)
            raise
        if acquired:
            _LOCAL_INIT_AUTHORITY["trusted_parent_fd"] = descriptor
        named = os.stat(
            trusted["name"], dir_fd=descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISDIR(named.st_mode)
            or [named.st_dev, named.st_ino] != grant["ancestor"]["identity"]
            or named.st_uid != grant["ancestor"]["uid"]
            or stat.S_IMODE(named.st_mode) != grant["ancestor"]["mode"]
        ):
            raise AdapterError(
                "Trusted parent to tmp binding changed", "access"
            )
    before = os.fstat(parent)
    current = os.stat(grant["ancestor"]["path"], follow_symlinks=False)
    for info in (before, current):
        if (
            not stat.S_ISDIR(info.st_mode)
            or [info.st_dev, info.st_ino] != grant["ancestor"]["identity"]
            or info.st_uid != grant["ancestor"]["uid"]
            or stat.S_IMODE(info.st_mode) != grant["ancestor"]["mode"]
        ):
            raise AdapterError("Prospective LOCAL ancestor changed", "access")


def consume_local_initialization_grant(parent):
    """Durably consume a grant outside the deletable family namespace."""
    if _LOCAL_INIT_AUTHORITY.get("consumed"):
        raise AdapterError(
            "LOCAL initialization grant already consumed", "access"
        )
    grant = _LOCAL_INIT_AUTHORITY["grant"]
    descriptor = retained = None
    try:
        verify_initialization_ancestor(parent)
        if os.path.lexists(
            os.path.join(grant["ancestor"]["path"], grant["leaf"])
        ):
            raise AdapterError("Prospective LOCAL leaf appeared", "access")
        descriptor = os.open(
            grant["claim_name"],
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        _LOCAL_INIT_AUTHORITY["consumed"] = True
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe LOCAL consumption record", "access")
        record = {
            "schema": 1,
            "family": FAMILY,
            "source": SOURCE_ID,
            "approval_sha256": _LOCAL_INIT_AUTHORITY["approval_sha256"],
            "grant": grant,
        }
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            if type(count) is not int or not 0 < count <= len(raw) - offset:
                raise AdapterError(
                    "Incomplete LOCAL consumption record", "access"
                )
            offset += count
        os.fsync(descriptor)
        verify_initialization_ancestor(parent)
        os.fsync(parent)
        retained = os.dup(parent)
        _LOCAL_INIT_AUTHORITY["ancestor_fd"] = retained
        retained = None
    except BaseException:
        journal_failure()
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        for fd in (descriptor, retained):
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException:
                    journal_failure()
                    if not primary:
                        raise


def require_local_initialization_authority(incoming):
    """Newest owned state never grants an otherwise missing LOCAL parent."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    path = os.path.join(tempfile.gettempdir(), FAMILY + "-" + str(os.getuid()))
    if os.path.lexists(path):
        if _LOCAL_INIT_AUTHORITY is not None:
            raise AdapterError(
                "Prospective LOCAL leaf already exists", "access"
            )
        return
    if _LOCAL_INIT_AUTHORITY is None:
        approval = _LOCAL_INITIALIZATION
        if not isinstance(approval, dict) or set(approval) != {
            "schema",
            "family",
            "evidence",
            "history_sha256",
            "state",
            "latest_owned",
            "initialization",
        }:
            raise AdapterError(
                "Finite LOCAL absence authority missing", "access"
            )
        authorize_local_initialization(
            approval["initialization"], incoming, approval
        )


def commit_local_initialization(state):
    """Settle only a new two-commit initialization under both fresh locks."""
    global _ACTIVE_FLIGHT_ID, _COMPLETING_FLIGHT_ID
    authority = _LOCAL_INIT_AUTHORITY
    parent = authority.get("ancestor_fd")
    if not authority["consumed"] or parent is None:
        raise AdapterError("Durable LOCAL claim required", "access")
    verify_initialization_ancestor(parent)
    verify_state_directory()
    verify_journal_locks()
    verify_lock_description(_COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID)
    collection = os.stat(
        "collection.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
    )
    if (collection.st_dev, collection.st_ino) != _COLLECTION_LOCK_ID:
        raise AdapterError("Fresh collection lock changed", "access")
    if _STATE_DIR_ID != authority.get("leaf_identity") or not _JOURNAL_CREATED:
        raise AdapterError("Exclusive prospective journal required", "access")
    ident = authority["grant"]["nonce"]
    if state["in_flight"] or state["flight_id"] == ident:
        raise AdapterError(
            "Initialization cannot settle historical state", "access"
        )
    pending = copy.deepcopy(state)
    pending.update(
        in_flight=True,
        flight_id=ident,
        flight_kind="initialization",
        flight_started=time.time(),
        observed_at=time.time(),
    )
    _ACTIVE_FLIGHT_ID = ident
    try:
        save_budget(pending)
        verify_initialization_ancestor(parent)
        verify_state_directory()
        verify_journal_locks()
        verify_lock_description(_COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID)
        collection = os.stat(
            "collection.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (collection.st_dev, collection.st_ino) != _COLLECTION_LOCK_ID:
            raise AdapterError("Fresh collection lock changed", "access")
        if load_budget() != pending:
            raise AdapterError("Prospective pending commit changed", "access")
        complete = copy.deepcopy(pending)
        complete.update(
            in_flight=False,
            completed_flight_id=ident,
            completed=max(pending["completed"], time.time()),
            observed_at=time.time(),
        )
        _COMPLETING_FLIGHT_ID = ident
        save_budget(complete)
        _ACTIVE_FLIGHT_ID = None
    except BaseException:
        fail_policy()
        raise
    finally:
        _COMPLETING_FLIGHT_ID = None


def verify_legacy_local(expected, modern):
    """Permit one exact typed in-place conversion, never arbitrary schema1."""
    global _LOCAL_SCHEMA1_REHYDRATION
    path = os.path.join(tempfile.gettempdir(), FAMILY + "-" + str(os.getuid()))
    if expected["path"] != path:
        raise AdapterError("Legacy namespace mismatch", "access")
    if expected["status"] == "ABSENT":
        if os.path.lexists(path):
            raise AdapterError(
                "Expected absent legacy namespace appeared", "access"
            )
        authorize_local_initialization(
            expected.get("initialization"), modern, _LEGACY_TRANSITION
        )
        return
    if expected["status"] != "PRESENT":
        raise AdapterError("Explicit local provenance required", "access")
    parent = fd = None
    locks = []
    try:
        before = os.stat(path, follow_symlinks=False)
        assert (
            stat.S_ISDIR(before.st_mode)
            and before.st_uid == os.getuid()
            and stat.S_IMODE(before.st_mode) == 0o700
        )
        assert [before.st_dev, before.st_ino] == expected["parent_identity"]
        parent = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        assert (os.fstat(parent).st_dev, os.fstat(parent).st_ino) == tuple(
            expected["parent_identity"]
        )
        for name in ("collection.lock", "state.lock"):
            lock = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            locks.append(lock)
            info = os.fstat(lock)
            assert (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1
            )
            assert [info.st_dev, info.st_ino] == expected["locks"][name]
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fd = os.open(
            "state.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        before_state = os.fstat(fd)
        assert (
            stat.S_ISREG(before_state.st_mode)
            and before_state.st_uid == os.getuid()
            and stat.S_IMODE(before_state.st_mode) == 0o600
            and before_state.st_nlink == 1
        )
        assert [before_state.st_dev, before_state.st_ino] == expected[
            "state_identity"
        ]
        raw = os.read(fd, 16385)
        version = lambda i: (
            i.st_dev,
            i.st_ino,
            i.st_size,
            i.st_mtime_ns,
            i.st_ctime_ns,
        )
        assert len(raw) <= 16384 and version(os.fstat(fd)) == version(
            before_state
        ) == version(
            os.stat("state.json", dir_fd=parent, follow_symlinks=False)
        )
        assert (
            os.stat(path, follow_symlinks=False).st_dev,
            os.stat(path, follow_symlinks=False).st_ino,
        ) == tuple(expected["parent_identity"])
        assert hashlib.sha256(raw).hexdigest() == expected["raw_sha256"]
        original = validate_legacy_budget(json.loads(raw))
        assert original == expected["state"]
        require_legacy_MAX(modern, original)
        _LOCAL_SCHEMA1_REHYDRATION = {
            "raw_sha256": expected["raw_sha256"],
            "modern_state": copy.deepcopy(modern),
            "state_identity": expected["state_identity"],
            "parent_identity": expected["parent_identity"],
        }
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as error:
        raise AdapterError(
            "Exact original local schema1 unavailable", "access"
        ) from error
    finally:
        for descriptor in [fd] + list(reversed(locks)) + [parent]:
            if descriptor is not None:
                os.close(descriptor)


def require_legacy_MAX(modern, old):
    validate_legacy_budget(old)
    if (
        not set(old["starts"]).issubset(modern["starts"])
        or modern["observed_at"] < old["observed_at"]
        or modern["not_before"] < old["not_before"]
        or old["blocked"]
        and not modern["blocked"]
    ):
        raise AdapterError("Approved full legacy MAX missing", "access")


def download_legacy_artifact(expected):
    identity = tuple(expected["identity"])
    run_id, attempt, source, head = identity
    verify_original_legacy_producer(expected)
    status, headers, _ = infrastructure_request_bytes(
        _WORKFLOW_GITHUB + f"/actions/artifacts/{expected['artifact_id']}/zip",
        headers=(
            {"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"]}
            if os.environ.get("GITHUB_TOKEN")
            else {}
        ),
    )
    if status != 302:
        raise AdapterError("Legacy artifact redirect missing", "access")
    url = headers.get("Location", "")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or not parsed.hostname
        or not parsed.hostname.endswith(".blob.core.windows.net")
    ):
        raise AdapterError("Untrusted legacy artifact destination", "access")
    status, _, raw = infrastructure_request_bytes(url, byte_limit=32768)
    if (
        status != 200
        or "sha256:" + hashlib.sha256(raw).hexdigest() != expected["digest"]
    ):
        raise AdapterError("Legacy artifact digest mismatch", "access")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (
            len(entries) != 1
            or entries[0].filename != "pacing-state.json"
            or entries[0].file_size > 16384
            or entries[0].flag_bits & 1
            or entries[0].external_attr >> 16 & 0o170000 == 0o120000
        ):
            raise AdapterError("Invalid legacy artifact archive", "access")
        envelope = json.loads(archive.read(entries[0]))
    if (
        set(envelope)
        != {
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "state",
        }
        or type(envelope["schema"]) is not int
        or envelope["schema"] != 1
        or type(envelope["run_id"]) is not int
        or type(envelope["run_attempt"]) is not int
        or envelope["repository"] != "YouthOpps/data-pipeline"
        or (
            envelope["run_id"],
            envelope["run_attempt"],
            envelope["source"],
        )
        != identity[:3]
    ):
        raise AdapterError("Legacy owned envelope identity mismatch", "access")
    old = validate_legacy_budget(envelope["state"])
    if (
        canonical_digest(old) != expected["state_sha256"]
        or old != expected["state"]
    ):
        raise AdapterError("Legacy owned state mismatch", "access")
    return old


def legacy_transition_state():
    global _FIRST_HOSTED_PENDING
    local = _LEGACY_TRANSITION
    if not isinstance(local, dict) or set(local) != {
        "schema",
        "family",
        "evidence",
        "history_sha256",
        "state",
        "legacy_original",
    }:
        raise AdapterError(
            "Explicit ONEK schema1 transition required", "access"
        )
    if (
        type(local["schema"]) is not int
        or local["schema"] != 1
        or local["family"] != FAMILY
        or local["history_sha256"] != canonical_digest(_LEGACY_JOB_PINS)
        or not _LEGACY_JOB_PINS
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/325#issuecomment-[1-9][0-9]*",
            local["evidence"],
        )
    ):
        raise AdapterError("Untrusted exact ONEK legacy declaration", "access")
    actual = {str(row[2]): row for row in _HISTORY_COMPLETED}
    jobless = {str(row[0]): row for row in _HISTORY_JOBLESS}
    if set(actual) | set(jobless) != set(_LEGACY_JOB_PINS) or set(actual) & set(
        jobless
    ):
        raise AdapterError("Legacy attempt set changed", "access")
    bounds = []
    for key, pin in _LEGACY_JOB_PINS.items():
        jobs = workflow_api(
            f"/actions/runs/{key}/attempts/{pin['attempt']}/jobs?per_page=100"
        )
        if jobs.get("total_count") != len(pin["jobs"]) or len(
            jobs.get("jobs", [])
        ) != len(pin["jobs"]):
            raise AdapterError("Legacy jobs changed", "access")
        selected = [
            {k: job.get(k) for k in old}
            for job, old in zip(
                sorted(jobs["jobs"], key=lambda j: j["id"]),
                sorted(pin["jobs"], key=lambda j: j["id"]),
            )
        ]
        if selected != sorted(pin["jobs"], key=lambda j: j["id"]):
            raise AdapterError("Legacy exact jobs changed", "access")
        artifacts = workflow_api(f"/actions/runs/{key}/artifacts?per_page=100")
        expected = local["legacy_original"]["artifacts_by_run"][key]
        if artifacts.get("total_count") != len(expected) or len(
            artifacts.get("artifacts", [])
        ) != len(expected):
            raise AdapterError("Legacy artifact set changed", "access")
        projection = [
            {k: a.get(k) for k in old}
            for a, old in zip(
                sorted(artifacts["artifacts"], key=lambda a: a["id"]),
                sorted(expected, key=lambda a: a["id"]),
            )
        ]
        if projection != sorted(expected, key=lambda a: a["id"]):
            raise AdapterError("Legacy artifact identities changed", "access")
        starts = [
            datetime.fromisoformat(
                j["started_at"].replace("Z", "+00:00")
            ).timestamp()
            for j in pin["jobs"]
        ]
        ends = [
            datetime.fromisoformat(
                j["completed_at"].replace("Z", "+00:00")
            ).timestamp()
            for j in pin["jobs"]
        ]
        if ends:
            expected_row = (
                max(ends),
                min(starts),
                int(key),
                pin["attempt"],
                SOURCE_ID,
                pin["head"],
            )
            if actual.get(key) != expected_row:
                raise AdapterError(
                    "Legacy attempt chronology changed", "access"
                )
        elif jobless.get(key) != (
            int(key),
            pin["attempt"],
            SOURCE_ID,
            pin["head"],
        ):
            raise AdapterError("Legacy jobless attempt changed", "access")
        bounds.extend(ends)
    approval = local
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if hosted:
        approval = _HOSTED_TRANSITION
        if (
            not isinstance(approval, dict)
            or set(approval)
            != {
                "schema",
                "family",
                "evidence",
                "history_sha256",
                "state",
                "legacy_provenance_sha256",
            }
            or type(approval["schema"]) is not int
            or approval["schema"] != 1
            or approval["family"] != FAMILY
            or approval["history_sha256"] != local["history_sha256"]
            or approval["legacy_provenance_sha256"]
            != canonical_digest(local["legacy_original"])
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/325#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise AdapterError(
                "Separate full-native HOSTED grant required", "access"
            )
    mature = not hosted and _MATURE_LOCAL_LEGACY_OWNED_REENTRY is not None
    if mature:
        approval = _MATURE_LOCAL_LEGACY_OWNED_REENTRY
        if not isinstance(approval, dict) or "state" not in approval:
            raise AdapterError(
                "Exact mature LOCAL reentry grant required", "access"
            )
    state = validate_budget(copy.deepcopy(approval["state"]))
    if (
        state["in_flight"]
        or not state["legacy_rate_history_unknown"]
        or not set(bounds).issubset(state["starts"])
        or bounds
        and (
            state["completed"] < max(bounds)
            or state["last_inventory_completed"] < max(bounds)
            or state["not_before"] < max(bounds) + 60
        )
    ):
        raise AdapterError("Legacy full history MAX missing", "access")
    latest = local["legacy_original"]["latest_owned"]
    if latest_family_identity() != tuple(latest["identity"]):
        raise AdapterError("Newest legacy owned run changed", "access")
    owned = [
        artifact
        for artifact in local["legacy_original"]["artifacts_by_run"].get(
            str(latest["identity"][0]), []
        )
        if artifact.get("id") == latest["artifact_id"]
    ]
    if (
        len(owned) != 1
        or owned[0].get("digest") != latest["digest"]
        or owned[0].get("expired") is not False
        or owned[0].get("name")
        != _ARTIFACT_NAME + "-" + str(latest["identity"][1])
        or owned[0].get("workflow_run", {}).get("id") != latest["identity"][0]
        or owned[0].get("workflow_run", {}).get("head_sha")
        != latest["identity"][3]
        or owned[0].get("workflow_run", {}).get("head_branch") != "main"
    ):
        raise AdapterError(
            "Original latest artifact sidebinding changed", "access"
        )
    require_legacy_MAX(state, download_legacy_artifact(latest))
    (
        require_legacy_MAX(
            state,
            validate_legacy_budget(local["legacy_original"]["local"]["state"]),
        )
        if local["legacy_original"]["local"]["status"] == "PRESENT"
        else None
    )
    if hosted:
        envelope = json.loads(os.environ.get("ONEK_PACING_MAINTENANCE", "null"))
        if envelope != {
            "schema": 1,
            "family": FAMILY,
            "evidence": approval["evidence"],
            "state": state,
        }:
            raise AdapterError(
                "Matching first-HOSTED workflow environment required", "access"
            )
        path = os.path.join(
            tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
        )
        if _STATE_PATH is not None or os.path.lexists(path):
            raise AdapterError("Fresh HOSTED namespace required", "access")
        _FIRST_HOSTED_PENDING = True
    elif mature:
        verify_mature_local_reentry(approval, state)
    else:
        verify_legacy_local(local["legacy_original"]["local"], state)
    return state


def latest_family_identity():
    if not _HISTORY_COMPLETED:
        raise AdapterError("Completed legacy family missing", "access")
    row = max(_HISTORY_COMPLETED)
    return row[2], row[3], row[4], row[5]


def restored_state():
    global _RESTORED_OWNED_IDENTITY
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("ONEK family history missing", "access")
    if _LEGACY_JOB_PINS and all(
        str(row[2]) in _LEGACY_JOB_PINS
        and row[3] == _LEGACY_JOB_PINS[str(row[2])]["attempt"]
        and row[5] == _LEGACY_JOB_PINS[str(row[2])]["head"]
        for row in _HISTORY_COMPLETED
    ):
        return production_maintenance_state(legacy_transition_state())
    run_id, attempt, source, head = latest
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or result.get("total_count") != len(artifacts)
        or len(artifacts) > 100
    ):
        raise AdapterError("Incomplete latest artifact inventory", "access")
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1:
        raise AdapterError("Newest owned artifact missing", "access")
    a = matches[0]
    if (
        a.get("expired") is not False
        or type(a.get("id")) is not int
        or a["id"] <= 0
        or a.get("workflow_run", {}).get("id") != run_id
        or a.get("workflow_run", {}).get("head_sha") != head
        or not numeric(a.get("size_in_bytes"))
        or a["size_in_bytes"] > 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", a.get("digest", ""))
    ):
        raise AdapterError("Newest artifact binding invalid", "access")
    restored = production_maintenance_state(
        download_inert_artifact(latest, a["id"], a["digest"])
    )
    _RESTORED_OWNED_IDENTITY = {
        "identity": list(latest),
        "artifact_id": a["id"],
        "digest": a["digest"],
    }
    return restored


def mature_reentry_MAX(proposed, current):
    """Require the finite seed to retain every exact current restriction."""
    proposed, current = validate_budget(proposed), validate_budget(current)
    numeric_keys = (
        "observed_at",
        "not_before",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_strikes",
        "rate_limit_until",
    )
    identity_keys = (
        "flight_id",
        "flight_started",
        "flight_kind",
        "completed_flight_id",
        "request_id",
        "request_started",
        "request_inventory",
        "completed_request_id",
        "request_outcome",
    )
    if (
        proposed["in_flight"]
        or current["in_flight"]
        or any(proposed[k] < current[k] for k in numeric_keys)
        or not set(current["starts"]).issubset(proposed["starts"])
        or current["blocked"]
        and not proposed["blocked"]
        or not proposed["legacy_rate_history_unknown"]
        or any(proposed[k] != current[k] for k in identity_keys)
        or current["completed_flight_id"] != current["flight_id"]
        or current["completed_request_id"] != current["request_id"]
        or current["request_outcome"] != "response_assessed"
    ):
        raise AdapterError(
            "Exact mature reentry MAX/settlement missing", "access"
        )


def mature_reentry_close(descriptor, identity):
    """Preserve primary errors while releasing a newly acquired descriptor."""
    primary = sys.exc_info()[0] is not None
    try:
        if identity is not None:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError(
                    "Mature verification descriptor changed", "access"
                )
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if not primary:
            raise


def mature_reentry_read(descriptor, expected_version, expected_hash):
    """Read bounded exact bytes through EOF without adopting another generation."""
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or not 0 < before.st_size <= 16384
        or list(journal_version(before)) != expected_version
    ):
        raise AdapterError("Mature verification file changed", "access")
    chunks, length = [], 0
    while True:
        chunk = os.pread(descriptor, min(4096, 16385 - length), length)
        if not chunk:
            break
        chunks.append(chunk)
        length += len(chunk)
        if length > 16384:
            raise AdapterError("Mature verification file oversized", "access")
    raw = b"".join(chunks)
    if (
        len(raw) != before.st_size
        or journal_version(os.fstat(descriptor)) != journal_version(before)
        or hashlib.sha256(raw).hexdigest() != expected_hash
    ):
        raise AdapterError("Mature verification bytes changed", "access")
    return raw


def mature_reentry_claim(marker):
    """Verify the surviving original claim; never create or consume a claim."""
    expected = marker["outside_claim"]
    original = _LEGACY_TRANSITION["legacy_original"]["local"]
    grant = original.get("initialization")
    if (
        original.get("status") != "ABSENT"
        or not isinstance(grant, dict)
        or expected["name"] != grant["claim_name"]
        or expected["ancestor"] != grant["ancestor"]
        or not re.fullmatch(
            r"\." + re.escape(FAMILY) + r"-init-[0-9a-f]{32}\.claim",
            expected["name"],
        )
    ):
        raise AdapterError("Original surviving claim grant missing", "access")
    ancestor = expected["ancestor"]
    if ancestor["path"] != tempfile.gettempdir():
        raise AdapterError("Surviving claim ancestor mismatch", "access")
    parent = trusted = descriptor = None
    identities = {}
    try:
        parent = os.open(
            ancestor["path"],
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        info = os.fstat(parent)
        identities[parent] = (info.st_dev, info.st_ino)
        if (
            list(identities[parent]) != ancestor["identity"]
            or info.st_uid != ancestor["uid"]
            or stat.S_IMODE(info.st_mode) != ancestor["mode"]
        ):
            raise AdapterError("Surviving claim ancestor changed", "access")
        chain = ancestor.get("trusted_parent")
        if chain is not None:
            if (
                ancestor["path"] != "/tmp"
                or ancestor["mode"] != 0o1777
                or chain["path"] != "/"
                or chain["name"] != "tmp"
                or chain["mode"] & 0o022
            ):
                raise AdapterError(
                    "Surviving claim trusted chain invalid", "access"
                )
            trusted = os.open(
                chain["path"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            top = os.fstat(trusted)
            identities[trusted] = (top.st_dev, top.st_ino)
            linked = os.stat(
                chain["name"], dir_fd=trusted, follow_symlinks=False
            )
            if (
                list(identities[trusted]) != chain["identity"]
                or top.st_uid != chain["uid"]
                or stat.S_IMODE(top.st_mode) != chain["mode"]
                or (linked.st_dev, linked.st_ino) != identities[parent]
            ):
                raise AdapterError(
                    "Surviving claim trusted parent changed", "access"
                )
        elif ancestor["uid"] != os.getuid() or ancestor["mode"] != 0o700:
            raise AdapterError(
                "Surviving claim private ancestor required", "access"
            )
        descriptor = os.open(
            expected["name"],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        identities[descriptor] = (info.st_dev, info.st_ino)
        if list(identities[descriptor]) != expected["identity"]:
            raise AdapterError("Surviving claim inode changed", "access")
        raw = mature_reentry_read(
            descriptor, expected["version"], expected["raw_sha256"]
        )
        if json.loads(raw) != {
            "schema": 1,
            "family": FAMILY,
            "source": SOURCE_ID,
            "approval_sha256": canonical_digest(_LEGACY_TRANSITION),
            "grant": grant,
        }:
            raise AdapterError("Surviving claim provenance changed", "access")
        current = os.stat(
            expected["name"], dir_fd=parent, follow_symlinks=False
        )
        named_parent = os.stat(ancestor["path"], follow_symlinks=False)
        if (
            list(journal_version(current)) != expected["version"]
            or (named_parent.st_dev, named_parent.st_ino) != identities[parent]
            or journal_version(os.fstat(descriptor)) != journal_version(current)
        ):
            raise AdapterError("Surviving claim current name changed", "access")
    finally:
        primary = sys.exc_info()[0] is not None
        errors = []
        for fd in (descriptor, trusted, parent):
            if fd is not None:
                try:
                    mature_reentry_close(fd, identities.get(fd))
                except BaseException as error:
                    errors.append(error)
        if errors and not primary:
            raise errors[0]


def verify_mature_local_reentry(marker, state):
    """Select a finite existing schema3 grant without granting initialization."""
    global _MATURE_LOCAL_REENTRY_AUTHORITY
    keys = {
        "schema",
        "family",
        "purpose",
        "evidence",
        "history_sha256",
        "legacy_original_sha256",
        "latest_owned",
        "state",
        "local",
        "outside_claim",
        "settlement_evidence",
    }
    try:
        if (
            os.environ.get("GITHUB_ACTIONS") == "true"
            or not isinstance(marker, dict)
            or set(marker) != keys
            or type(marker["schema"]) is not int
            or marker["schema"] != 1
            or marker["family"] != FAMILY
            or marker["purpose"] != "MATURE_LOCAL_LEGACY_OWNED_REENTRY"
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/325"
                r"#issuecomment-[1-9][0-9]*",
                marker["evidence"],
            )
            or marker["history_sha256"] != canonical_digest(_LEGACY_JOB_PINS)
            or marker["legacy_original_sha256"]
            != canonical_digest(_LEGACY_TRANSITION["legacy_original"])
            or marker["latest_owned"]
            != _LEGACY_TRANSITION["legacy_original"]["latest_owned"]
            or marker["state"] != state
            or _LOCAL_INIT_AUTHORITY is not None
        ):
            raise AdapterError(
                "Exact mature LOCAL reentry grant required", "access"
            )
        local = marker["local"]
        if not isinstance(local, dict) or set(local) != {
            "path",
            "parent_identity",
            "state_identity",
            "state_version",
            "raw_sha256",
            "state",
            "locks",
        }:
            raise AdapterError("Exact mature namespace pins required", "access")
        path = os.path.join(
            tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
        )
        if local["path"] != path or not os.path.lexists(path):
            raise AdapterError("Mature LOCAL parent missing", "access")
        parent = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
            or [parent.st_dev, parent.st_ino] != local["parent_identity"]
            or set(local["locks"]) != {"collection.lock", "state.lock"}
        ):
            raise AdapterError("Mature LOCAL parent/locks changed", "access")
        mature_reentry_MAX(state, local["state"])
        proof = marker["settlement_evidence"]
        if not isinstance(proof, dict) or set(proof) != {
            "native_receipt_sha256",
            "audit_sha256",
            "owner_ids",
            "request_ids",
        }:
            raise AdapterError(
                "Exact retained settlement proof required", "access"
            )
        for name, count in (("owner_ids", 22), ("request_ids", 21)):
            values = proof[name]
            if (
                not isinstance(values, list)
                or len(values) != count
                or len(set(values)) != count
                or any(
                    not isinstance(x, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", x)
                    for x in values
                )
            ):
                raise AdapterError(
                    "Retained settlement identities invalid", "access"
                )
        if (
            any(
                not re.fullmatch(r"[0-9a-f]{64}", proof[k])
                for k in ("native_receipt_sha256", "audit_sha256")
            )
            or local["state"]["flight_id"] not in proof["owner_ids"]
            or local["state"]["request_id"] not in proof["request_ids"]
            or _LEGACY_TRANSITION["legacy_original"]["local"]["initialization"][
                "nonce"
            ]
            not in proof["owner_ids"]
        ):
            raise AdapterError(
                "Retained actual settlement proof mismatch", "access"
            )
        claim = marker["outside_claim"]
        if not isinstance(claim, dict) or set(claim) != {
            "ancestor",
            "name",
            "identity",
            "version",
            "raw_sha256",
        }:
            raise AdapterError("Exact surviving claim pins required", "access")
        mature_reentry_claim(marker)
        _MATURE_LOCAL_REENTRY_AUTHORITY = {
            "marker_sha256": canonical_digest(marker),
            "committed": False,
        }
    except BaseException:
        journal_failure()
        raise


def mature_reentry_precommit(local):
    """Recheck exact current bytes under original collection and state locks."""
    authority = _MATURE_LOCAL_REENTRY_AUTHORITY
    if authority is None:
        return
    try:
        marker = _MATURE_LOCAL_LEGACY_OWNED_REENTRY
        pins = marker["local"]
        if (
            authority["committed"]
            or _STATE_INITIALIZING
            or _LOCAL_INIT_AUTHORITY is not None
            or authority["marker_sha256"] != canonical_digest(marker)
            or _COLLECTION_LOCK_FD is None
            or not _JOURNAL_LOCKS
            or list(_STATE_DIR_ID) != pins["parent_identity"]
            or list(_JOURNAL_ID) != pins["state_identity"]
        ):
            raise AdapterError(
                "Held mature reentry authority changed", "access"
            )
        verify_state_directory()
        verify_journal()
        for name, expected in pins["locks"].items():
            info = os.stat(name, dir_fd=_STATE_DIR_FD, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or [info.st_dev, info.st_ino] != expected
            ):
                raise AdapterError("Pinned mature lock changed", "access")
        collection = os.fstat(_COLLECTION_LOCK_FD)
        if [collection.st_dev, collection.st_ino] != pins["locks"][
            "collection.lock"
        ]:
            raise AdapterError("Held mature collection lock changed", "access")
        raw = mature_reentry_read(
            _JOURNAL_FD, pins["state_version"], pins["raw_sha256"]
        )
        verify_journal()
        if json.loads(raw) != pins["state"] or local != pins["state"]:
            raise AdapterError("Exact approved mature state changed", "access")
        mature_reentry_MAX(marker["state"], local)
        mature_reentry_claim(marker)
    except BaseException:
        journal_failure()
        raise


def mature_reentry_postquiet():
    """Refresh metadata and claim while retaining our resulting generation."""
    authority = _MATURE_LOCAL_REENTRY_AUTHORITY
    if authority is None:
        return
    try:
        marker = _MATURE_LOCAL_LEGACY_OWNED_REENTRY
        if (
            not authority["committed"]
            or not _RESTORED
            or not _STATE_TRUSTED
            or _LOCAL_INIT_AUTHORITY is not None
            or authority["marker_sha256"] != canonical_digest(marker)
        ):
            raise AdapterError("Mature reentry was not committed", "access")
        workflow_mode_guard()
        if latest_family_run() != tuple(marker["latest_owned"]["identity"]):
            raise AdapterError("Mature reentry latest owner changed", "access")
        local = _LEGACY_TRANSITION
        actual = {str(row[2]): row for row in _HISTORY_COMPLETED}
        jobless = {str(row[0]): row for row in _HISTORY_JOBLESS}
        if set(actual) | set(jobless) != set(_LEGACY_JOB_PINS):
            raise AdapterError("Mature reentry history set changed", "access")
        for key, pin in _LEGACY_JOB_PINS.items():
            jobs = workflow_api(
                f"/actions/runs/{key}/attempts/{pin['attempt']}/jobs?per_page=100"
            )
            wanted = sorted(pin["jobs"], key=lambda j: j["id"])
            rows = sorted(jobs.get("jobs", []), key=lambda j: j["id"])
            if (
                jobs.get("total_count") != len(wanted)
                or len(rows) != len(wanted)
                or [
                    {k: row.get(k) for k in old}
                    for row, old in zip(rows, wanted)
                ]
                != wanted
            ):
                raise AdapterError(
                    "Mature reentry exact jobs changed", "access"
                )
            artifacts = workflow_api(
                f"/actions/runs/{key}/artifacts?per_page=100"
            )
            expected = sorted(
                local["legacy_original"]["artifacts_by_run"][key],
                key=lambda a: a["id"],
            )
            rows = sorted(artifacts.get("artifacts", []), key=lambda a: a["id"])
            if (
                artifacts.get("total_count") != len(expected)
                or len(rows) != len(expected)
                or [
                    {k: row.get(k) for k in old}
                    for row, old in zip(rows, expected)
                ]
                != expected
            ):
                raise AdapterError(
                    "Mature reentry exact artifact changed", "access"
                )
        if latest_family_run() != tuple(marker["latest_owned"]["identity"]):
            raise AdapterError(
                "Mature reentry history changed during check", "access"
            )
        workflow_mode_guard()
        verify_journal()
        if (
            list(_STATE_DIR_ID) != marker["local"]["parent_identity"]
            or list(_JOURNAL_ID) != marker["local"]["state_identity"]
        ):
            raise AdapterError(
                "Mature reentry resulting journal changed", "access"
            )
        mature_reentry_claim(marker)
    except BaseException:
        journal_failure()
        raise


if __name__ == "__main__":
    sys.exit(main())
