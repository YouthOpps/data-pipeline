"""Index minimal programme facts from the official CampusBourses UI."""

import argparse
import base64
import contextlib
import zipfile
import stat
import math
import ipaddress
import io
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import copy
import urllib.robotparser
import signal
import threading
import ssl
import struct
import zlib

SOURCE_ID = "fr-campus-france"
SOURCE_URL = "https://campusbourses.campusfrance.org/?lang=en"
WEBSITE_URL = "https://campusbourses.campusfrance.org/"
LANGUAGE = "und"
PUBLISHER_COUNTRY = "FR"
PUBLISHER_TYPE = "public-institution"
ATTRIBUTION = "Source: CampusBourses / Campus France"
SERVICE_URL = "https://bourses-api.campusfrance.org/"
INDEX_URL = SERVICE_URL + "sgetgrants/en"
SCRIPT_PATH = "js/ilangular.b81726ae.js"
SCRIPT_URL = WEBSITE_URL + SCRIPT_PATH
POLICY_URL = (
    "https://www.campusfrance.org/fr/conditions-generales-d-utilisation"
)
REVIEWED_POLICY_HASH = (
    "2494fb8e6483716f946524b3897fe0596999feb67473e1e99a679db4fbd5bf40"
)
TYPE_LABELS = {
    "1": "Study",
    "2": "Research",
    "3": "Internship",
    "4": "Travel",
    "5": "Partial tuition fee waivers",
    "6": "Partial fee waivers (CVEC, Health insurance)",
}
_ALLOWED_HOSTS = {
    "campusbourses.campusfrance.org",
    "bourses-api.campusfrance.org",
    "www.campusfrance.org",
}
_ROBOTS = {}
_PAGE_COMPLETED = None
_COLLECTION_END = None

_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-campus-france-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 900
_STATE_SCHEMA = 3
_ARTIFACT_NAME = "campus-france-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_PAGE_INTERVAL = 30

RUN_TIMEOUT = 1140
EXPORT_TIMEOUT = 30
PUBLICATION_TIMEOUT = 90
MAX_PUBLISHER_STARTS = 512
MAX_COLLECTION_BYTES = 64 * 1024 * 1024
_RUN_END = _PHASE_END = _PHASE_NAME = _OPERATION_END = None
_REQUEST_STARTS = _COLLECTION_BYTES = _TRANSACTION_DEPTH = 0
_PUBLISHER_INTERVAL = 12.2
_ACTIVE_FLIGHT_ID = _ACTIVE_REQUEST_ID = None
_LAST_RESPONSE_RECEIPT = _LAST_RESPONSE_URL = None
_PUBLISHER_COMPLETED_MONOTONIC = _INVENTORY_COMPLETED_MONOTONIC = None
_STARTUP_QUIET_UNTIL = None
_REQUEST_FRAMES = []
_REP_PROVENANCE = {}
_INVENTORY_DEPTH = 0
_PREPARED = _SOURCE_PATH_ENTERED = _ANY_JOURNAL_MUTATION = False
_METADATA_PHASE = _INFRA_CLOSE_FAILED = False
_LOCAL_FIRST_WIRE_VERIFIED = False
_PREWIRE_ERROR = None
_EXECUTION_USED = False
_IDENTITIES = {}
_CAMPUS417_DECISION = None
_CAMPUS417_LOCAL_SEED = None
_CAMPUS417_HOSTED_FLOOR = None
_CAMPUS417_HOSTED_EVIDENCE = None
_LEGACY_OWNER = (40005091304, 1, "fr-campus-france")
_LEGACY_HEAD = "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b"
_LEGACY_ARTIFACT = 12524221502
_LEGACY_ZIP_SHA = (
    "33ec4b65269b71a2060c62294f2f6bb337d8cceb87cc9082202ee074ffb6ceca"
)
_LEGACY_STATE_SHA = (
    "4d4625da4f3e624fe9d63310ff761cf35958e1f17d605843f6563ab5be84e961"
)
_WORKFLOW_ID = 378390756
_CURRENT_WORKFLOW_BLOB = "eec160b01d516ce2ff7b2950bf614f10ef23c661"

_FINITE_PREWIRE = {
    40116597849: {
        "attempt": 1,
        "completed_at": "2026-10-24T17:33:02Z",
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "job_id": 120939048403,
        "log_sha256": "71a546650f08a520dd4b928b64de9bcbccb89f8489e51f270747eba264d35a6e",
        "run_id": 40116597849,
        "run_started_at": "2026-10-24T17:32:34Z",
        "source_blob": "8b8fcdfd739bcf56ae954d2e6a43c22262f20b4f",
        "source_sha256": "e21e1ac3d96ac3508faf3ca6b764dadebb0bbb2e2204fc1f9177b1875a3fdf9d",
        "started_at": "2026-10-24T17:32:36Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    40218112743: {
        "attempt": 1,
        "completed_at": "2026-10-25T17:48:15Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 121243570689,
        "log_sha256": "9da4a864f7bd4a4afb2153b254439862b28fd41637068bc10211c41f2e0247d5",
        "run_id": 40218112743,
        "run_started_at": "2026-10-25T17:46:55Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-25T17:46:57Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    40392613092: {
        "attempt": 1,
        "completed_at": "2026-10-26T20:07:33Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 121817240912,
        "log_sha256": "0597c645c46d4fd5fcaaf5b8ca8ac1001ffbf6ab0279c58c687de78ccdeb16cb",
        "run_id": 40392613092,
        "run_started_at": "2026-10-26T20:06:02Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-26T20:06:05Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    40570492875: {
        "attempt": 1,
        "completed_at": "2026-10-27T19:37:06Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 122423054177,
        "log_sha256": "a9f9e2d1354ae09811bad286e33f352bc20e2f7a801c98a7b6d5728dec4b6fe1",
        "run_id": 40570492875,
        "run_started_at": "2026-10-27T19:35:29Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-27T19:35:32Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    40757637950: {
        "attempt": 1,
        "completed_at": "2026-10-28T19:50:17Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 123060727995,
        "log_sha256": "4543e1dea4c2454b7c226a550a95d4dcbbcd86e0faba54501f90f51235f16efb",
        "run_id": 40757637950,
        "run_started_at": "2026-10-28T19:48:46Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-28T19:48:49Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    40943349017: {
        "attempt": 1,
        "completed_at": "2026-10-29T19:49:45Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 123692158094,
        "log_sha256": "ff0acdde5ae4b627744f3ffb6c80bce786c5673a2b5ab6cd111fbd5d6a4a9077",
        "run_id": 40943349017,
        "run_started_at": "2026-10-29T19:48:14Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-29T19:48:17Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    41116791858: {
        "attempt": 1,
        "completed_at": "2026-10-30T19:39:09Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 124272992893,
        "log_sha256": "1c233273c4fff559a8cb784ea6f692b25fdf372f74bca68acc672844b18900a8",
        "run_id": 41116791858,
        "run_started_at": "2026-10-30T19:37:42Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-30T19:37:44Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    41230665423: {
        "attempt": 1,
        "completed_at": "2026-10-31T18:43:11Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 124624026004,
        "log_sha256": "df65c320e5b9410db5ddc6301f9b5f8214ee7522a9af9992f9dbbd8c0762b15c",
        "run_id": 41230665423,
        "run_started_at": "2026-10-31T18:41:51Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-10-31T18:41:54Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    41340128122: {
        "attempt": 1,
        "completed_at": "2026-11-01T19:08:57Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 124948163728,
        "log_sha256": "6ebcf915c0f7e49b28271356f124b2c1b69baca98402081d3c74f6d45a8b004e",
        "run_id": 41340128122,
        "run_started_at": "2026-11-01T19:07:40Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-01T19:07:43Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    41540877671: {
        "attempt": 1,
        "completed_at": "2026-11-02T22:54:14Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 125604394210,
        "log_sha256": "339bc731c1166b61f0d805c16b0af9624a3a4415e699b856e41e3b3ad9467b0b",
        "run_id": 41540877671,
        "run_started_at": "2026-11-02T22:52:27Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-02T22:52:36Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    41714047747: {
        "attempt": 1,
        "completed_at": "2026-11-03T20:37:48Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 126189270301,
        "log_sha256": "58d32bab0738c61ba57ea3f8db39fa945d2ed779aab9462d2aa4005665b213b9",
        "run_id": 41714047747,
        "run_started_at": "2026-11-03T20:35:55Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-03T20:35:58Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    41910958080: {
        "attempt": 1,
        "completed_at": "2026-11-04T20:55:37Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 126858060095,
        "log_sha256": "a7bdf1f721fe4956a5a3d329f151a51ee533a9f61e160c736c3d921b36f3782d",
        "run_id": 41910958080,
        "run_started_at": "2026-11-04T20:54:03Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-04T20:54:06Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    42110386975: {
        "attempt": 1,
        "completed_at": "2026-11-05T21:13:37Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 127536070100,
        "log_sha256": "33c06c82ac0a94802f089a34ea8b167c0bc7be5b5519e507c51b21d018106944",
        "run_id": 42110386975,
        "run_started_at": "2026-11-05T21:11:41Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-05T21:11:44Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    42288955587: {
        "attempt": 1,
        "completed_at": "2026-11-06T20:32:32Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 128137038015,
        "log_sha256": "e83e15aeaa58288085487112738b31ee0d8a077f1731d7b4ff3d36d18055c957",
        "run_id": 42288955587,
        "run_started_at": "2026-11-06T20:30:40Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-06T20:30:42Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    42404003228: {
        "attempt": 1,
        "completed_at": "2026-11-07T19:01:24Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 128497700757,
        "log_sha256": "159cc7a159486157c9c4858968685930c7bbb731490c87f061128f6e31c24ee2",
        "run_id": 42404003228,
        "run_started_at": "2026-11-07T18:59:46Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-07T18:59:49Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    42512878473: {
        "attempt": 1,
        "completed_at": "2026-11-08T19:02:53Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 128829543517,
        "log_sha256": "2b1284ef5a443c7c880da183a88d8112e90e07492b5684742952fb1fefea74db",
        "run_id": 42512878473,
        "run_started_at": "2026-11-08T19:01:20Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-08T19:01:22Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    42721225830: {
        "attempt": 1,
        "completed_at": "2026-11-09T23:33:52Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 129515392995,
        "log_sha256": "d96f5c31d241692311e88fb81abd579a7f17827625b3335834e2b561952e8e43",
        "run_id": 42721225830,
        "run_started_at": "2026-11-09T23:32:08Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-09T23:32:11Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    42900397621: {
        "attempt": 1,
        "completed_at": "2026-11-10T21:00:05Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 130124527060,
        "log_sha256": "d8635aa227fc2b1fdd2935a80ee01128a095416516d12de36ec5acf159c4b518",
        "run_id": 42900397621,
        "run_started_at": "2026-11-10T20:58:11Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-10T20:58:13Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    43103607046: {
        "attempt": 1,
        "completed_at": "2026-11-11T21:32:12Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 130801105262,
        "log_sha256": "29dbfc857d87c0077fe7df7607d836a862b552094f7afe900be14392b61c4c17",
        "run_id": 43103607046,
        "run_started_at": "2026-11-11T21:30:16Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-11T21:30:19Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    43311427658: {
        "attempt": 1,
        "completed_at": "2026-11-12T21:35:24Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 131508208130,
        "log_sha256": "184aafc65d1addac8ebae7039fd57399de26b41422adbf4256ba2a4f5868c359",
        "run_id": 43311427658,
        "run_started_at": "2026-11-12T21:33:33Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-12T21:33:35Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    43506306275: {
        "attempt": 1,
        "completed_at": "2026-11-13T20:59:17Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 132164771458,
        "log_sha256": "caf73e5f1343f3091e4c52ade072f38da78e586b892b4e66cb3ddfc0cae37d42",
        "run_id": 43506306275,
        "run_started_at": "2026-11-13T20:57:36Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-13T20:57:39Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    43632046858: {
        "attempt": 1,
        "completed_at": "2026-11-14T19:27:27Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 132560599802,
        "log_sha256": "84c20d77f1590c2e0d05543ad2ac1befd9760fd0cdf44b841cacf1124bef698b",
        "run_id": 43632046858,
        "run_started_at": "2026-11-14T19:25:52Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-14T19:25:54Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    43755117225: {
        "attempt": 1,
        "completed_at": "2026-11-15T19:56:49Z",
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "job_id": 132937396679,
        "log_sha256": "75c98f370a1cb8ba8e1d05c16118c6de9d37e91c4ea9e5d96f417a0f5ed90248",
        "run_id": 43755117225,
        "run_started_at": "2026-11-15T19:55:30Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-15T19:55:32Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
    43979608065: {
        "attempt": 1,
        "completed_at": "2026-11-16T23:30:23Z",
        "head": "9e8e59d64d479829912bc343fc23b526f6519aea",
        "job_id": 133692367769,
        "log_sha256": "baef4d22799dfae5288fd6755335f8278e73ff073dc035f3efa59d27f85e8e90",
        "run_id": 43979608065,
        "run_started_at": "2026-11-16T23:28:41Z",
        "source_blob": "1f84e8e59dc5ae72a37eddd011cd1fbaccdb93ac",
        "source_sha256": "5db43306486f762f9937a17a287cf02cd1bf75b657e9545d23546272876d99cf",
        "started_at": "2026-11-16T23:28:43Z",
        "workflow_blob": "ad2f4dcdfe511f12b121598bfb56847ff9ef9bb1",
    },
}
_FINITE_PREWIRE_SHA = (
    "5be4c82e221471a995441e8f52b0ec95bb449ee71ca90949bfdf0a7fc98628e3"
)
_KNOWN_FLOOR = {
    "blocked": False,
    "completed": 1792781021.5560114,
    "completed_flight_id": None,
    "completed_request_id": None,
    "family": "youthopps-campus-france-publisher-v1",
    "flight_id": None,
    "flight_kind": None,
    "flight_started": 0,
    "in_flight": False,
    "interval": 12.2,
    "last_inventory_completed": 1792781021.5560114,
    "legacy_policy_history_unknown": True,
    "legacy_rate_history_unknown": True,
    "not_before": 1794871943.0,
    "observed_at": 1794871823.0,
    "page_interval": 30,
    "rate_limit_strikes": 0,
    "rate_limit_until": 0,
    "request_id": None,
    "request_inventory": False,
    "request_outcome": None,
    "request_started": 0,
    "schema": 3,
    "starts": [
        1792072947.1247087,
        1792072961.0327752,
        1792072974.048758,
        1792073006.3641808,
        1792074111.3686643,
        1792074126.759418,
        1792074139.7796113,
        1792074170.7606103,
        1792079407.5716066,
        1792079421.2159996,
        1792079433.9842336,
        1792079464.9102907,
        1792089572.4717956,
        1792089585.5273304,
        1792089598.6724257,
        1792089629.5243785,
        1792174915.98388,
        1792174928.9784334,
        1792174942.0981724,
        1792174972.9686923,
        1792258383.7113082,
        1792258396.7270083,
        1792258409.3659015,
        1792258440.2787511,
        1792344812.012178,
        1792344825.1876633,
        1792344860.239571,
        1792440666.8077047,
        1792440680.2187302,
        1792440693.072054,
        1792440724.1853468,
        1792521799.9359195,
        1792521813.1988204,
        1792521826.1427608,
        1792521857.0592084,
        1792608863.5760934,
        1792608876.6802704,
        1792608889.548645,
        1792608920.3708782,
        1792694901.1265004,
        1792694914.5500503,
        1792694927.8036385,
        1792694958.8398376,
        1792780961.6105697,
        1792780977.029896,
        1792780989.817163,
        1792781020.6215267,
    ],
}
_KNOWN_FLOOR_SHA = (
    "7469d5342d9da69817af05e3d47341a24ea208265f2afa6ff149feab6362a825"
)
_EXACT_JOBS = {}
_CODE_CACHE = {}
_FAMILY_HISTORY = []
_PREWIRE_CHAIN_ENDS = []
_LOCAL_CLAIM_BINDINGS = {}
_LAST_EXPORT_RECORD = None
_PREPARE_STAGE = None
_PUBLICATION_END = None

_WORKFLOW_PATH = ".github/workflows/fetch-fr-campus-france.yml"


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
_PUBLISHER_GATE_FAILED = False


class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class WorkflowMetadataError(AdapterError):
    """Carry bounded infrastructure diagnostics without response contents."""

    def __init__(
        self, reason, *, identity=None, attempt=None, route=None, status=None
    ):
        reasons = {
            "credentials",
            "transport",
            "http_status",
            "json",
            "job_inventory",
            "job_identity",
            "job_completion",
            "job_pagination",
            "job_bound",
            "run_identity",
            "run_start",
            "run_chronology",
            "metadata_shape",
        }
        reason = reason if reason in reasons else "metadata_shape"
        self.reason = reason
        self.route = route
        fields = ["reason=" + reason]
        for name, value in (("run", identity), ("attempt", attempt)):
            if type(value) is int and 0 < value < 2**63:
                fields.append(f"{name}={value}")
        if route in {"runs", "attempt", "attempt_jobs", "artifacts"}:
            fields.append("route=" + route)
        if type(status) is int and 100 <= status <= 599:
            fields.append(f"http_status={status}")
        else:
            status = None
        self.http_status = status
        super().__init__(
            "Workflow metadata rejected (" + ", ".join(fields) + ")",
            "access",
        )


def workflow_metadata_route(path):
    """Classify fixed API routes without exposing URLs or query strings."""
    route = path.split("?", 1)[0]
    if route == "/actions/runs":
        return "runs"
    if re.fullmatch(r"/actions/runs/[1-9][0-9]*/artifacts", route):
        return "artifacts"
    if re.fullmatch(
        r"/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*/jobs", route
    ):
        return "attempt_jobs"
    if re.fullmatch(r"/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*", route):
        return "attempt"
    return None


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


def pace(*, inventory=False):
    global _REQUEST_STARTS, _PUBLISHER_INTERVAL, _ACTIVE_REQUEST_ID
    check_deadline()
    if (
        _REQUEST_STARTS >= MAX_PUBLISHER_STARTS
        or _PUBLISHER_GATE_FAILED
        or not _ACTIVE_FLIGHT_ID
    ):
        raise AdapterError(
            "Campus France physical admission unavailable", "access"
        )
    with locked_budget():
        state = load_budget()
        if (
            not state["in_flight"]
            or state["flight_id"] != _ACTIVE_FLIGHT_ID
            or state["blocked"]
            or state["request_outcome"] == "reserved"
            or state["rate_limit_strikes"] >= 100
        ):
            raise AdapterError(
                "Unresolved Campus France physical admission", "access"
            )
        now = time.time()
        starts = [v for v in state["starts"] if v > now - 60]
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        wait_until = max(
            state["not_before"],
            state["rate_limit_until"],
            state["completed"] + _PUBLISHER_INTERVAL,
            now,
        )
        if len(starts) >= 5:
            wait_until = max(wait_until, starts[-5] + 60)
        if _PUBLISHER_COMPLETED_MONOTONIC is not None:
            wait_until = max(
                wait_until,
                now
                + max(
                    0,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                ),
            )
        if _STARTUP_QUIET_UNTIL is not None:
            wait_until = max(
                wait_until,
                now + max(0, _STARTUP_QUIET_UNTIL - time.monotonic()),
            )
        while True:
            check_deadline()
            wait = max(0, wait_until - time.time())
            if _PUBLISHER_COMPLETED_MONOTONIC is not None:
                wait = max(
                    wait,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                )
            if _STARTUP_QUIET_UNTIL is not None:
                wait = max(wait, _STARTUP_QUIET_UNTIL - time.monotonic())
            if wait <= 0:
                break
            if wait + 120 > min(_PHASE_END, _RUN_END) - time.monotonic():
                raise PreRequestDeferral(
                    "Campus France physical guard exceeds phase", "access"
                )
            time.sleep(wait)
        now = time.time()
        ident = os.urandom(16).hex()
        if ident == state["request_id"]:
            raise AdapterError(
                "Campus France physical identity collision", "access"
            )
        state.update(
            request_id=ident,
            request_started=now,
            request_outcome="reserved",
            request_inventory=inventory,
            observed_at=max(state["observed_at"], now),
        )
        state["starts"] = [v for v in starts if v > now - 60] + [now]
        state["interval"] = _PUBLISHER_INTERVAL
        state["not_before"] = max(
            state["not_before"], now + _PUBLISHER_INTERVAL
        )
        write_budget(state)
        _ACTIVE_REQUEST_ID = ident
        _REQUEST_STARTS += 1


def request_bytes(
    url,
    *,
    interval=12.2,
    method="GET",
    headers=None,
    payload=None,
    byte_limit=5_000_000,
    robots=False,
):
    global _SOURCE_PATH_ENTERED
    publisher = urllib.parse.urlsplit(url).hostname in _ALLOWED_HOSTS
    inventory = publisher and bool(_INVENTORY_DEPTH) and not robots
    check_deadline()
    if publisher:
        _SOURCE_PATH_ENTERED = True
        public_url(url)
        if robots:
            p = urllib.parse.urlsplit(url)
            origin = p.scheme + "://" + p.netloc
            _ROBOTS.pop(origin, None)
            _REP_PROVENANCE.pop(origin, None)
        if robots and (
            urllib.parse.urlsplit(url).path != "/robots.txt"
            or urllib.parse.urlsplit(url).query
        ):
            raise AdapterError("Invalid Campus France REP purpose", "access")
        if not robots:
            check_robots(url)
        if inventory:
            wait_inventory_page()
        pace(inventory=inventory)
    else:
        infrastructure_url(url)
    outgoing = {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() not in ("user-agent", "cookie")
    }
    if publisher:
        outgoing = {
            k: v
            for k, v in outgoing.items()
            if k.lower() not in ("authorization", "proxy-authorization")
        }
    outgoing["User-Agent"] = _USER_AGENT
    request = urllib.request.Request(
        url, data=payload, method=method, headers=outgoing
    )
    stop = False
    with publisher_attempt(publisher, inventory) as frame:
        if publisher:
            frame["url"] = url
        with operation_guard(publisher):
            try:
                if publisher:
                    journal_preopener()
                response = _OPENER.open(
                    request, timeout=request_timeout(publisher)
                )
            except urllib.error.HTTPError as error:
                response = error
            except (urllib.error.URLError, OSError) as error:
                cause = (
                    error.reason
                    if isinstance(error, urllib.error.URLError)
                    else error
                )
                if (
                    publisher
                    and isinstance(cause, (ConnectionError, TimeoutError))
                    and not isinstance(cause, ssl.SSLError)
                ):
                    frame["no_response"] = True
                raise
            if publisher:
                frame["received"] = True
            with assessed_response(response, publisher, robots) as status:
                response_headers = response.headers
                if publisher:
                    record_retry_after(
                        response_headers.get_all("Retry-After", []),
                        status,
                        strike=False,
                    )
                body = body_bytes(response, byte_limit)
                if publisher:
                    account_collection_bytes(len(body))
                    try:
                        reject_visible_challenge(
                            body,
                            response_headers,
                            challenge_only=status == 429,
                            unavailable_rep=robots
                            and 400 <= status < 500
                            and status != 429,
                        )
                    except AdapterError:
                        with locked_budget():
                            if not load_budget()["blocked"]:
                                raise
                        stop = True
                    if robots and not stop and status != 429:
                        assess_rep(body, response_headers, url, status)
            if publisher:
                frame["assessed"] = True
            if publisher and (
                stop
                or status == 429
                or not robots
                and status in (401, 403, 451)
            ):
                raise PublisherStop(
                    "Campus France publisher refusal", "access", status
                )
    return status, response_headers, body


def fetch_source(url, min_interval=12.2):
    status, headers, body = source_request(url, interval=min_interval)
    return body.decode(headers.get_content_charset() or "utf-8", "strict")


def check_robots(url):
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if origin not in _ROBOTS:
        request_bytes(origin + "/robots.txt", robots=True, byte_limit=65536)
    return apply_rep(url)


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
    """Filter a validated refresh before its atomic replacement publication."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Cleanup requires an aware check time", "validate")
    retained = []
    for record in records:
        value = record.get("deadline")
        expired = False
        if isinstance(value, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    # An undocumented zone cannot justify an earlier cutoff.
                    closing_date = datetime.strptime(value, "%Y-%m-%d").date()
                    latest_zone = timezone(timedelta(hours=-12))
                    expired = (
                        checked.astimezone(latest_zone).date() > closing_date
                    )
                else:
                    deadline = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = (
                        deadline.tzinfo is not None and checked > deadline
                    )
            except (ValueError, TypeError):
                # Missing or ambiguous deadlines never imply expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, published=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not published):
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
        if (
            not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", record["id"])
            or record["id"].lower() in ids
        ):
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].lower())
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
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
        native = re.fullmatch(
            re.escape(SOURCE_ID) + r"-([1-9][0-9]{0,11})", record["id"]
        )
        if not native or record["url"] != programme_url(int(native[1])):
            raise AdapterError(
                "Native programme identity/link mismatch", "validate"
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
            raise AdapterError(
                "Invalid opportunity kind or status", "validate"
            )
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
                raise AdapterError(
                    "Invalid record field: " + field, "validate"
                )
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
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError(
            "DATA_SOURCE_TOKEN is required for publication", "publish"
        )
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
            "GitHub connection failed; publication is unconfirmed", "publish"
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(
            f"GitHub publication API returned HTTP {status}", "publish", status
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
        if file is None:
            files[name] = None
            continue
        expected = file.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", expected):
            raise AdapterError(
                "Invalid existing source blob identity", "publish"
            )
        if file.get("encoding") != "base64":
            file = github("GET", "/git/blobs/" + expected)
        if file.get("encoding") != "base64" or file.get("sha") != expected:
            raise AdapterError("Unsupported existing source blob", "publish")
        try:
            raw = base64.b64decode(
                re.sub(r"\s+", "", file["content"]), validate=True
            )
            actual = hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()
            if actual != expected:
                raise ValueError("Source blob digest mismatch")
            files[name] = raw.decode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise AdapterError(
                "Invalid existing source blob bytes", "publish"
            ) from None
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish"
        )
    return {"head": head, "tree": tree, "files": files}


def publish_files(snapshot, files):
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


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "display_name": "Campus France",
        "source": SOURCE_ID,
        "name": "CampusBourses / Campus France",
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
                f"Validated refresh retains {len(records)} "
                "CampusBourses financial-support programme records"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(state):
    """Accept only complete modern state; legacy migration is explicit."""
    if (
        type(state) is not dict
        or set(state) != set(empty_budget(0))
        or type(state["schema"]) is not int
        or state["schema"] != 3
        or state["family"] != FAMILY
        or any(
            type(state[k]) is not bool
            for k in ("blocked", "in_flight", "request_inventory")
        )
        or state["legacy_rate_history_unknown"] is not True
        or state["legacy_policy_history_unknown"] is not True
    ):
        raise AdapterError("Untrusted Campus France journal schema", "access")
    numeric_keys = (
        "observed_at",
        "not_before",
        "completed",
        "interval",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_until",
        "flight_started",
        "request_started",
    )
    if (
        any(
            not numeric(state[k]) or state[k] > 253402300799
            for k in numeric_keys
        )
        or state["interval"] < 12.2
        or state["page_interval"] < 30
        or type(state["rate_limit_strikes"]) is not int
        or not 0 <= state["rate_limit_strikes"] <= 100
        or type(state["starts"]) is not list
        or len(state["starts"]) > 512
        or any(
            not numeric(v) or v > state["observed_at"] for v in state["starts"]
        )
        or state["starts"] != sorted(set(state["starts"]))
        or state["completed"] > state["observed_at"]
        or state["last_inventory_completed"] > state["observed_at"]
        or state["observed_at"] > time.time() + 1
    ):
        raise AdapterError(
            "Untrusted Campus France timing constraints", "access"
        )
    for key in (
        "flight_id",
        "completed_flight_id",
        "request_id",
        "completed_request_id",
    ):
        if state[key] is not None and not (
            isinstance(state[key], str)
            and re.fullmatch("[0-9a-f]{32}", state[key])
        ):
            raise AdapterError(
                "Untrusted Campus France transaction identity", "access"
            )
    if state["flight_kind"] not in (None, "policy"):
        raise AdapterError("Untrusted Campus France policy owner", "access")
    if state["in_flight"] and (
        not state["flight_id"] or state["flight_kind"] != "policy"
    ):
        raise AdapterError("Missing Campus France pending owner", "access")
    if state["request_outcome"] not in (
        None,
        "reserved",
        "response_assessed",
        "no_response",
    ):
        raise AdapterError("Untrusted Campus France request outcome", "access")
    if state["request_outcome"] is not None and not state["request_id"]:
        raise AdapterError("Missing Campus France physical owner", "access")
    if (
        state["request_outcome"] == "reserved"
        and not state["in_flight"]
        or state["request_outcome"] == "response_assessed"
        and state["request_id"] != state["completed_request_id"]
    ):
        raise AdapterError("Unresolved Campus France physical owner", "access")
    if (
        not state["in_flight"]
        and state["flight_id"] is not None
        and state["flight_id"] != state["completed_flight_id"]
    ):
        raise AdapterError(
            "Campus France logical owner is unsettled", "access"
        )
    if (
        state["request_outcome"] in ("response_assessed", "no_response")
        and state["request_started"] > state["completed"]
    ):
        raise AdapterError(
            "Campus France physical completion is unproved", "access"
        )
    if state["request_outcome"] is None and (
        state["request_id"] is not None
        or state["completed_request_id"] is not None
    ):
        raise AdapterError(
            "Campus France request identity is inconsistent", "access"
        )
    return copy.deepcopy(state)


def empty_budget(now):
    return {
        "schema": 3,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "completed": 0,
        "page_interval": 30,
        "last_inventory_completed": 0,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "legacy_policy_history_unknown": True,
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


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _GUARD_NEW_DIRECTORY
    if _STATE_PATH and os.path.lexists(os.path.dirname(_STATE_PATH)):
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + ("-") + str(os.getuid())
    )
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")
    if not os.path.lexists(directory) and not _GUARD_REMOTE_INITIALIZING:
        return
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    else:
        _GUARD_NEW_DIRECTORY = _GUARD_REMOTE_INITIALIZING
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AdapterError("Unsafe publisher pacing directory", "access")
    if info.st_mode & 0o077:
        raise AdapterError("Unsafe publisher pacing permissions", "access")
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")


def locked_budget():
    global _GUARD_LOCK_ID
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
        retain_lock(descriptor)
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
        raise AdapterError("Oversized publisher pacing state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError(
            "Corrupt publisher pacing state", "access"
        ) from None


def write_budget(state):
    global _ANY_JOURNAL_MUTATION
    _ANY_JOURNAL_MUTATION = True
    validate_budget(state)
    journal_commit(state)


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    route = workflow_metadata_route(path)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise WorkflowMetadataError("credentials", route=route)
    reason, status = "transport", None
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 200:
            reason = "http_status"
            raise ValueError("Workflow API status")
        reason = "json"
        return json.loads(body)
    except Exception:
        raise WorkflowMetadataError(
            reason, route=route, status=status
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
    """Use actual exact-attempt job completion, never mutable run updated_at."""
    total, jobs_seen = None, set()
    finished = []
    retained_jobs = []
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}"
        )
        count, jobs = result.get("total_count"), result.get("jobs")
        if (
            type(count) is not int
            or count <= 0
            or not isinstance(jobs, list)
            or len(jobs) > 100
            or total is not None
            and total != count
        ):
            raise WorkflowMetadataError(
                "job_inventory", identity=identity, attempt=attempt
            )
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
                or job.get("status") != "completed"
            ):
                raise WorkflowMetadataError(
                    "job_identity", identity=identity, attempt=attempt
                )
            jobs_seen.add(job["id"])
            retained_jobs.append(copy.deepcopy(job))
            try:
                value = datetime.fromisoformat(
                    job["completed_at"].replace("Z", "+00:00")
                )
            except Exception:
                raise WorkflowMetadataError(
                    "job_completion", identity=identity, attempt=attempt
                ) from None
            if value.utcoffset() is None:
                raise WorkflowMetadataError(
                    "job_completion", identity=identity, attempt=attempt
                )
            finished.append(value.timestamp())
        if len(jobs_seen) == total:
            _EXACT_JOBS[(identity, attempt)] = retained_jobs
            return max(finished)
        if len(jobs_seen) > total or len(jobs) != 100:
            raise WorkflowMetadataError(
                "job_pagination", identity=identity, attempt=attempt
            )
    raise WorkflowMetadataError(
        "job_bound", identity=identity, attempt=attempt
    )


def completed_identity(run, expected=None, revision=None):
    """Bind a completed attempt and its chronological publisher interval."""
    identity = attempt = None
    reason = "run_identity"
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
            or run["workflow_id"] <= 0
            or revision is not None
            and (run["head_sha"], run["workflow_id"]) != revision
            or expected
            and (identity, attempt, source) != expected
        ):
            raise ValueError("Untrusted completed attempt")
        reason = "run_start"
        value = datetime.fromisoformat(
            run["run_started_at"].replace("Z", "+00:00")
        )
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        reason = "metadata_shape"
        finished = attempt_completion(identity, attempt, run["head_sha"])
        reason = "run_chronology"
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        return finished, began, identity, attempt, source
    except WorkflowMetadataError as error:
        raise WorkflowMetadataError(
            error.reason,
            identity=identity,
            attempt=attempt,
            route=error.route,
            status=error.http_status,
        ) from None
    except Exception:
        raise WorkflowMetadataError(
            reason, identity=identity, attempt=attempt
        ) from None


def latest_family_run():
    reviewed_transition()
    scan_family_history()
    if not set(_FINITE_PREWIRE) <= set(r[2] for r in _FAMILY_HISTORY):
        raise AdapterError(
            "Campus France finite ancestry incomplete", "access"
        )
    _PREWIRE_CHAIN_ENDS.clear()
    for row in reversed(_FAMILY_HISTORY):
        identity = row[2:]
        if identity[0] in _FINITE_PREWIRE:
            _PREWIRE_CHAIN_ENDS.append(verify_finite_gap(identity))
            continue
        return identity
    raise AdapterError("No exact Campus France predecessor", "access")


def scan_family_history():
    global _FAMILY_HISTORY
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else (None, None)
    all_rows = []
    seen = set()
    total = None
    present = False
    for page in range(1, 101):
        v = workflow_api(
            f"/actions/workflows/{_WORKFLOW_ID}/runs?per_page=100&page={page}"
        )
        count, rows = v.get("total_count"), v.get("workflow_runs")
        if (
            type(count) is not int
            or count <= 0
            or not isinstance(rows, list)
            or len(rows) > 100
            or total is not None
            and total != count
        ):
            raise InventoryRace(
                "Campus France family inventory changed", "access"
            )
        total = count
        for row in rows:
            run, attempt = row.get("id"), row.get("run_attempt")
            if (
                type(run) is not int
                or run <= 0
                or run in seen
                or type(attempt) is not int
                or attempt <= 0
                or row.get("workflow_id") != _WORKFLOW_ID
                or row.get("path") != _WORKFLOW_PATH
                or row.get("repository", {}).get("full_name")
                != "YouthOpps/data-pipeline"
                or row.get("head_repository", {}).get("full_name")
                != "YouthOpps/data-pipeline"
                or row.get("head_branch") != "main"
                or not re.fullmatch("[0-9a-f]{40}", row.get("head_sha", ""))
            ):
                raise AdapterError(
                    "Campus France family identity changed", "access"
                )
            seen.add(run)
            for number in range(1, attempt + 1):
                if (run, number) == current:
                    present = True
                    if number != attempt or row.get(
                        "head_sha"
                    ) != os.environ.get("GITHUB_SHA"):
                        raise AdapterError(
                            "Current Campus France attempt changed", "access"
                        )
                    continue
                exact = workflow_api(f"/actions/runs/{run}/attempts/{number}")
                if exact.get("status") != "completed":
                    raise AdapterError(
                        "Another Campus France attempt is active", "access"
                    )
                finish = completed_identity(
                    exact,
                    (run, number, SOURCE_ID),
                    (row["head_sha"], _WORKFLOW_ID),
                )
                _IDENTITIES[(run, number)] = exact
                all_rows.append(finish)
        if len(seen) == total:
            break
        if len(rows) != 100 or len(seen) > total:
            raise AdapterError(
                "Incomplete Campus France family inventory", "access"
            )
    else:
        raise AdapterError(
            "Campus France family inventory bound exceeded", "access"
        )
    if hosted and not present:
        raise AdapterError("Current Campus France run absent", "access")
    _FAMILY_HISTORY = sorted(all_rows)
    if len(_FAMILY_HISTORY) > 1 and any(
        a[0] == b[0] for a, b in zip(_FAMILY_HISTORY, _FAMILY_HISTORY[1:])
    ):
        raise AdapterError(
            "Ambiguous Campus France completion chronology", "access"
        )
    return _FAMILY_HISTORY[-1][2:] if _FAMILY_HISTORY else None


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
            "Authorization": "Bearer " + token,
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
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
            destinations = response.headers.get_all("Location", [])
            if len(destinations) != 1:
                raise ValueError("Ambiguous artifact redirect")
            destination = destinations[0]
            response.read(1001)
        infrastructure_url(destination)
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
        return decode_owned_payload(identity, artifact_id, envelope)
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED, _ANY_JOURNAL_MUTATION
    if _RESTORED:
        return
    identity = latest_family_run()
    seen = set()
    while True:
        if identity in seen:
            raise AdapterError("Cyclic Campus France receipt chain", "access")
        seen.add(identity)
        incoming = verified_predecessor_artifact(identity)
        if "prewire_receipt" not in incoming:
            break
        current = next(r for r in _FAMILY_HISTORY if r[2:] == identity)
        older = [
            r
            for r in _FAMILY_HISTORY
            if r[0] < current[0] and r[2] not in _FINITE_PREWIRE
        ]
        if not older:
            raise AdapterError(
                "Campus France receipt has no owned ancestry", "access"
            )
        _PREWIRE_CHAIN_ENDS.append(current[0])
        identity = older[-1][2:]
    incoming = apply_reviewed_floor(incoming)
    for end in _PREWIRE_CHAIN_ENDS:
        incoming["not_before"] = max(incoming["not_before"], end + 120)
        incoming["observed_at"] = max(incoming["observed_at"], end)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        with journal_restore_scope(incoming):
            _ANY_JOURNAL_MUTATION = True
            budget_file()
            with locked_budget():
                local = load_budget()
                write_budget(merge_budgets(local, incoming, time.time()))
    else:
        with locked_budget():
            local = load_budget()
            write_budget(merge_budgets(local, incoming, time.time()))
    _RESTORED = _STATE_TRUSTED = True


def export_family_artifact():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    if _GUARD_FATAL or _GUARD_DIRTY:
        invalidate_export()
        raise AdapterError("Campus France IO cannot be exported", "access")
    run, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch("[0-9a-f]{40}", head):
        raise AdapterError("Invalid Campus France export head", "access")
    with open(__file__, "rb") as f:
        source = f.read(1000001)
    envelope = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "source": SOURCE_ID,
        "run_id": run,
        "run_attempt": attempt,
        "head_sha": head,
        "adapter_sha256": hashlib.sha256(source).hexdigest(),
    }
    if _STATE_TRUSTED:
        with locked_budget():
            state = load_budget()
            journal_verify()
            envelope.update(
                state=state,
                state_sha256=hashlib.sha256(
                    canonical_state(state)
                ).hexdigest(),
            )
    elif (
        _PREWIRE_ERROR is not None
        and not _SOURCE_PATH_ENTERED
        and not _ANY_JOURNAL_MUTATION
        and not _INFRA_CLOSE_FAILED
        and _GUARD_FD is None
        and _COLLECTION_LOCK_FD is None
    ):
        receipt_parent = prewire_namespace_absent()
        if receipt_parent is None:
            invalidate_export()
            return
        envelope.update(
            kind="pre-request-metadata-failure",
            zero_publisher_reachability=True,
        )
    else:
        invalidate_export()
        return
    write_export_payload(envelope)
    if "kind" in envelope:
        try:
            if prewire_namespace_absent() != receipt_parent:
                raise AdapterError(
                    "Campus France family appeared during receipt export",
                    "access",
                )
        except BaseException:
            invalidate_export()
            raise


def prewire_namespace_absent():
    """A zero-state receipt cannot hide any unexported local family owner."""
    global _INFRA_CLOSE_FAILED
    if _STATE_PATH is not None:
        return None
    root = tempfile.gettempdir()
    if not os.path.isabs(root) or root != os.path.realpath(root):
        raise AdapterError("Unsafe Campus France temporary parent", "access")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        version = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            stat.S_IMODE(value.st_mode),
        )
        if not stat.S_ISDIR(info.st_mode) or not (
            info.st_uid in (0, os.getuid())
            and not info.st_mode & 0o022
            or stat.S_IMODE(info.st_mode) == 0o1777
        ):
            raise AdapterError(
                "Unsafe Campus France temporary parent", "access"
            )
        absent = False
        try:
            os.stat(
                FAMILY + "-" + str(os.getuid()),
                dir_fd=descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            absent = True
        if version(info) != version(os.fstat(descriptor)) or version(
            info
        ) != version(os.stat(root, follow_symlinks=False)):
            raise AdapterError(
                "Campus France temporary parent changed", "access"
            )
        return version(info) if absent else None
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            _INFRA_CLOSE_FAILED = True
            raise


def prepare_collection():
    global _PREPARED, _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _GUARD_COLLECTION_ID, _GUARD_NEW_DIRECTORY, _STARTUP_QUIET_UNTIL
    global _PREPARE_STAGE, _METADATA_PHASE
    if _PREPARED:
        return
    if _PUBLISHER_GATE_FAILED or _GUARD_FATAL:
        raise AdapterError("Campus France failure remains sticky", "access")
    reviewed_transition()
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if not hosted:
        # Established local state must exist before any metadata fetch.
        budget_file()
        with locked_budget():
            state = load_budget()
            if state["in_flight"] or state["blocked"]:
                raise AdapterError(
                    "Campus France local owner unresolved", "access"
                )
    _METADATA_PHASE = True
    _PREPARE_STAGE = "metadata"
    try:
        workflow_mode_guard()
        if not hosted:
            claim_local_attempt()
        restore_family_artifact()
    finally:
        _METADATA_PHASE = False
    _PREPARE_STAGE = "state"
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        parent = journal_parent()
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open("collection.lock", flags, dir_fd=parent)
        except FileNotFoundError:
            if not _GUARD_NEW_DIRECTORY:
                raise AdapterError(
                    "Mature Campus France collection lock missing", "access"
                )
            fd = os.open(
                "collection.lock",
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
        info = journal_owned(fd)
        identity = (info.st_dev, info.st_ino)
        retain_lock(fd)
        named = os.stat(
            "collection.lock", dir_fd=parent, follow_symlinks=False
        )
        if (named.st_dev, named.st_ino) != identity:
            journal_close(fd, identity, True)
            raise AdapterError(
                "Campus France collection lock changed", "access"
            )
        _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID = fd, identity
        _GUARD_NEW_DIRECTORY = False
    with locked_budget():
        state = load_budget()
        if state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Campus France restored owner unresolved", "access"
            )
    _STATE_TRUSTED = True
    _COLLECTION_STARTED = time.time()
    _STARTUP_QUIET_UNTIL = time.monotonic() + 60
    _PREPARED = True
    _PREPARE_STAGE = "ready"


class InventoryRace(AdapterError):
    """The advertised complete workflow inventory changed during reading."""


def request_timeout(publisher=False):
    check_deadline()
    end = min(_RUN_END, _PHASE_END)
    if _OPERATION_END is not None:
        end = min(end, _OPERATION_END)
    return max(0.001, min(30, end - time.monotonic()))


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


def has_429_head_refusal(body, headers):
    """Recognize a qualified HEAD refusal only for the caller's 429 path."""
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
        return False

    class HeadTitle(VisibleText):
        """Read only the first unsuppressed HEAD's immediate TITLE."""

        def __init__(self):
            super().__init__()
            self.title = []
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
            if (
                self.head_depth is not None
                and not self.body_started
                and len(self.stack) == self.head_depth + 1
                and self.stack[-1][0] == "title"
                and self.stack[-2][0] == "head"
                and not any(self.stack[-1][1:])
            ):
                self.title.append(data)

    parser = HeadTitle()
    parser.feed(body.decode("utf-8", "replace"))
    title = " ".join(" ".join(parser.title).lower().split())
    return bool(
        re.search(
            r"just a moment|attention required|access denied|captcha",
            title,
            re.I,
        )
    )


def reject_visible_challenge(
    body, headers, *, challenge_only=False, unavailable_rep=False
):
    if challenge_only and has_429_head_refusal(body, headers):
        retain_publisher_hold()
        raise AdapterError("Visible publisher challenge or denial", "access")

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
    parser = VisibleText()
    parser.feed(body.decode("utf-8", "replace"))
    text = " ".join(" ".join(parser.text).lower().split())
    phrases = (
        "verify you are human",
        "verify that you are human",
        "complete the captcha",
        "enable javascript and cookies to continue",
        "checking your browser",
    )
    if not challenge_only and not unavailable_rep:
        phrases += (
            "access denied",
            "permission denied",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    if any(phrase in text for phrase in phrases):
        retain_publisher_hold()
        raise AdapterError("Visible publisher challenge or denial", "access")


def source_request(
    url, *, interval=12.2, method="GET", payload=None, robots=False
):
    global _SOURCE_PATH_ENTERED
    _SOURCE_PATH_ENTERED = True
    with policy_operation():
        return original_source_request(
            url,
            interval=interval,
            method=method,
            payload=payload,
            robots=robots,
        )


def fetch_inventory_page(fetch, *args, **kwargs):
    global _INVENTORY_DEPTH
    _INVENTORY_DEPTH += 1
    try:
        return fetch(*args, **kwargs)
    finally:
        _INVENTORY_DEPTH -= 1


class PolicyText(HTMLParser):
    """Read the visible main legal-page body, excluding unrelated chrome."""

    def __init__(self):
        super().__init__()
        self.main = False
        self.sections = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.main = True
            self.sections += 1

    def handle_endtag(self, tag):
        if tag == "main":
            self.main = False

    def handle_data(self, data):
        if self.main:
            self.parts.append(data)


def clean(value):
    if not isinstance(value, str):
        raise AdapterError("Invalid publisher text", "parse")
    text = " ".join(unescape(value).split())
    if not text or re.search(r"<[^>]+>", text):
        raise AdapterError("Empty or nonliteral publisher text", "parse")
    return text


def read_configuration(script):
    if (
        'apiUrl:"' + SERVICE_URL + '"' not in script
        or 'rootUrl:"https://campusbourses.campusfrance.org/"' not in script
        or 'apiUrl+"sgetgrants/"+e' not in script
        or '"ws/cfgrant.php?id="+e.bourse_id+"_"+d.use()' not in script
    ):
        raise AdapterError("Reviewed public UI routes changed", "access")
    match = re.search(
        r'factory\("IwType",\[function\(\)\{var l=\[(.*?)\];return',
        script,
    )
    if not match:
        raise ContentReviewDeferred(
            "Public financial-support taxonomy missing"
        )
    labels = re.findall(
        r'\{id:([1-9][0-9]*),fr:"[^"]*",en:"([^"]*)",es:"[^"]*"\}',
        match[1],
    )
    if len(labels) != 6 or dict(labels) != TYPE_LABELS:
        raise ContentReviewDeferred(
            "Publisher financial-support taxonomy changed"
        )


def read_frontier():
    _, _, body = fetch_inventory_page(source_request, INDEX_URL)
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise AdapterError("Invalid public catalogue JSON", "parse") from None
    if (
        not isinstance(result, dict)
        or set(result) != {"count", "programs", "op", "errmsg"}
        or type(result["op"]) is not int
        or result["op"] != 1
        or type(result["count"]) is not int
        or not 0 < result["count"] <= 5000
        or not isinstance(result["programs"], list)
        or len(result["programs"]) != result["count"]
    ):
        raise AdapterError("Incomplete or empty full catalogue", "parse")
    records = {}
    for row in result["programs"]:
        if (
            not isinstance(row, dict)
            or type(row.get("bourseId")) is not int
            or not 0 < row["bourseId"] < 10**12
            or row["bourseId"] in records
            or not isinstance(row.get("typeListId"), str)
            or not re.fullmatch(r"[1-6](?:,[1-6])*", row["typeListId"])
        ):
            raise AdapterError(
                "Invalid or duplicate native programme", "parse"
            )
        types = row["typeListId"].split(",")
        if len(types) != len(set(types)):
            raise AdapterError("Duplicate native support type", "parse")
        records[row["bourseId"]] = (
            clean(row.get("title")),
            tuple(sorted(types)),
        )
    return records


def collect():
    if _RUN_END is None:
        with execution():
            with phase("restore", 180):
                prepare_collection()
            with phase("collection", COLLECTION_TIMEOUT):
                with policy_operation():
                    return original_collect()
    with phase("collection", COLLECTION_TIMEOUT):
        with policy_operation():
            return original_collect()


def programme_url(identifier):
    """Use the native public client share link without copied prose."""
    return (
        "https://campusbourses.campusfrance.org/ws/cfgrant.php?id="
        + str(identifier)
        + "_en"
    )


def run(args=None):
    args = parse_arguments() if args is None else args
    if args.publish:
        prepare_collection()
    snapshot = read_snapshot_phase() if args.publish else None
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
        validate_records(old, published=True)
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
        validate_records(records, published=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
    if args.publish and (
        not failure or snapshot["files"]["data.json"] is not None
    ):
        files = {
            "metadata.json": json.dumps(outcome, ensure_ascii=False, indent=2)
            + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        try:
            publish_files_phase(snapshot, files)
        except Exception as error:
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
                latest = read_snapshot_phase()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files_phase(
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
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


def main():
    global _PUBLISHING, _METADATA_PHASE, _PREWIRE_ERROR
    args = parse_arguments()
    _PUBLISHING = args.publish
    with execution():
        try:
            if _PUBLISHING:
                _METADATA_PHASE = True
                try:
                    with phase("proof", 30):
                        publication_window()
                finally:
                    _METADATA_PHASE = False
            with phase("restore", 180):
                prepare_collection()
            return run(args)
        except WorkflowMetadataError as error:
            if (
                not _SOURCE_PATH_ENTERED
                and not _ANY_JOURNAL_MUTATION
                and not _INFRA_CLOSE_FAILED
            ):
                _PREWIRE_ERROR = error
            raise
        finally:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                with phase("export", EXPORT_TIMEOUT):
                    export_family_artifact()


_GUARD_PARENT = _GUARD_PARENT_ID = None
_GUARD_ROOT_FD = _GUARD_ROOT_METADATA = None
_GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
_GUARD_LOCK_ID = None
_GUARD_LOCKS = {}
_GUARD_CREATED = _GUARD_DIRTY = _GUARD_FATAL = False
_GUARD_WRITE_ACK = None
_GUARD_BYTES_ACK = None
_GUARD_COLLECTION_ID = None
_GUARD_NEW_DIRECTORY = False
_GUARD_FAILED_STATE = None
_GUARD_REMOTE_INITIALIZING = False


def journal_generation(info):
    """Identify one observed inode and byte generation, not an FS CAS."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        info.st_gid,
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
    root_fd = original_root()
    descriptor = _GUARD_PARENT
    fresh = descriptor is None
    if fresh:
        descriptor = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    identity = None
    try:
        held = os.fstat(descriptor)
        current = os.stat(
            os.path.basename(directory), dir_fd=root_fd, follow_symlinks=False
        )
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
            or fcntl.fcntl(_GUARD_FD, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_RDWR
        ):
            raise AdapterError(
                "Publisher journal generation changed", "access"
            )
        for descriptor, identity in _GUARD_LOCKS.items():
            verify_lock(descriptor, os.path.basename(_STATE_LOCK), identity)
        if _COLLECTION_LOCK_FD is not None:
            verify_lock(
                _COLLECTION_LOCK_FD, "collection.lock", _GUARD_COLLECTION_ID
            )
        if _GUARD_LOCK_ID is not None:
            lock = os.stat(
                os.path.basename(_STATE_LOCK),
                dir_fd=parent,
                follow_symlinks=False,
            )
            if (lock.st_dev, lock.st_ino) != _GUARD_LOCK_ID:
                raise AdapterError("Publisher journal lock changed", "access")
        if (
            check_generation
            and not _GUARD_DIRTY
            and _GUARD_BYTES_ACK is not None
        ):
            if journal_snapshot() != _GUARD_BYTES_ACK:
                raise AdapterError(
                    "Campus France journal bytes changed", "access"
                )
        return info
    except BaseException:
        journal_failed()
        raise


def journal_retain(descriptor=None):
    """Pin a genuine RW journal only under its original stable lock."""
    global _GUARD_FD, _GUARD_ID, _GUARD_VERSION, _GUARD_CREATED, _GUARD_BYTES_ACK
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
                raise AdapterError(
                    "Mature publisher journal missing", "access"
                )
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
    _GUARD_BYTES_ACK = journal_snapshot()
    if _GUARD_CREATED:
        os.fsync(parent)


class JournalLock:
    """Retain the original lock and verify generation before healthy closure."""

    def __init__(self, descriptor, identity):
        self.descriptor, self.identity = descriptor, identity
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
        self.descriptor = None
        try:
            journal_verify()
            journal_owned(descriptor, self.identity)
            if len(_GUARD_LOCKS) == 1:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            _GUARD_LOCKS.pop(descriptor, None)
            os.close(descriptor)
        except BaseException:
            journal_failed()
            with contextlib.suppress(BaseException):
                journal_invalidate()
            _GUARD_LOCKS.pop(descriptor, None)
            raise


def journal_read():
    try:
        if not _GUARD_LOCKS:
            raise AdapterError("Campus France journal lock required", "access")
        journal_verify()
        observed = journal_snapshot()
        if observed != _GUARD_BYTES_ACK:
            raise AdapterError("Campus France journal bytes changed", "access")
        return observed[1]
    except BaseException:
        journal_failed()
        raise


def journal_snapshot():
    parent = journal_parent()
    before = journal_owned(_GUARD_FD, _GUARD_ID)
    size = before.st_size
    if (
        size > 16384
        or fcntl.fcntl(_GUARD_FD, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
    ):
        raise AdapterError(
            "Invalid Campus France journal generation", "access"
        )

    def verify():
        journal_parent()
        held = journal_owned(_GUARD_FD, _GUARD_ID)
        named = os.stat(
            os.path.basename(_STATE_PATH), dir_fd=parent, follow_symlinks=False
        )
        if (named.st_dev, named.st_ino) != _GUARD_ID or journal_generation(
            held
        ) != journal_generation(before):
            raise AdapterError(
                "Campus France journal snapshot changed", "access"
            )
        for fd, identity in _GUARD_LOCKS.items():
            verify_lock(fd, os.path.basename(_STATE_LOCK), identity)
        if _COLLECTION_LOCK_FD is not None:
            verify_lock(
                _COLLECTION_LOCK_FD, "collection.lock", _GUARD_COLLECTION_ID
            )

    def read():
        pieces = []
        offset = 0
        while offset < size:
            verify()
            piece = os.pread(_GUARD_FD, size - offset, offset)
            if not piece or len(piece) > size - offset:
                raise AdapterError(
                    "Incomplete Campus France journal read", "access"
                )
            pieces.append(piece)
            offset += len(piece)
            verify()
        return b"".join(pieces)

    first, second = read(), read()
    verify()
    if first != second:
        raise AdapterError(
            "Campus France journal bytes changed during read", "access"
        )
    return journal_generation(before), first


def journal_require_write():
    """Require the exact last acknowledged own mutation before another."""
    journal_verify(False)
    observed = journal_snapshot()
    journal_verify(False)
    if _GUARD_WRITE_ACK is None or observed != _GUARD_WRITE_ACK:
        raise AdapterError("Publisher journal write changed", "access")


def journal_acknowledge_write(expected):
    """Adopt only the exact bytes produced by our completed syscall."""
    global _GUARD_WRITE_ACK
    journal_verify(False)
    observed = journal_snapshot()
    journal_verify(False)
    if observed[1] != expected:
        raise AdapterError("Publisher journal write changed", "access")
    _GUARD_WRITE_ACK = observed


def journal_commit(state):
    """Rewrite only acknowledged own bytes, checking each partial write."""
    global _GUARD_VERSION, _GUARD_CREATED, _GUARD_DIRTY
    global _GUARD_FAILED_STATE, _GUARD_WRITE_ACK, _GUARD_BYTES_ACK
    if _GUARD_FATAL:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        payload = json.dumps(state, separators=(",", ":")).encode()
        if not _GUARD_LOCKS or len(payload) > 16384:
            raise AdapterError(
                "Bounded locked publisher commit required", "access"
            )
        journal_verify()
        _GUARD_WRITE_ACK = journal_snapshot()
        if _GUARD_WRITE_ACK[0] != _GUARD_VERSION:
            raise AdapterError(
                "Publisher journal generation changed", "access"
            )
        _GUARD_CREATED = False
        journal_mark_mature()
        _GUARD_DIRTY = True
        journal_require_write()
        os.ftruncate(_GUARD_FD, 0)
        journal_acknowledge_write(b"")
        journal_require_write()
        os.fsync(_GUARD_FD)
        journal_require_write()
        offset = 0
        while offset < len(payload):
            journal_require_write()
            written = os.pwrite(_GUARD_FD, payload[offset:], offset)
            if (
                type(written) is not int
                or not 0 < written <= len(payload) - offset
            ):
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += written
            journal_acknowledge_write(payload[:offset])
        journal_require_write()
        os.fsync(_GUARD_FD)
        journal_require_write()
        _GUARD_VERSION = _GUARD_WRITE_ACK[0]
        _GUARD_BYTES_ACK = (_GUARD_VERSION, payload)
        _GUARD_DIRTY = False
        _GUARD_WRITE_ACK = None
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
    observed = journal_snapshot()
    if _GUARD_DIRTY:
        if _GUARD_WRITE_ACK is None or observed != _GUARD_WRITE_ACK:
            return
    elif observed != _GUARD_BYTES_ACK:
        return
    os.ftruncate(_GUARD_FD, 0)
    os.fsync(_GUARD_FD)


def journal_preopener():
    """Final publisher authority check, after request construction."""
    global _LOCAL_FIRST_WIRE_VERIFIED
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        and not _LOCAL_FIRST_WIRE_VERIFIED
    ):
        workflow_mode_guard()
        _LOCAL_FIRST_WIRE_VERIFIED = True
    verify_local_claims()
    try:
        journal_verify()
        if _GUARD_COLLECTION_ID is None or _COLLECTION_LOCK_FD is None:
            raise AdapterError(
                "Publisher collection ownership missing", "access"
            )
        journal_owned(_COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID)
        named = os.stat(
            "collection.lock", dir_fd=journal_parent(), follow_symlinks=False
        )
        if (named.st_dev, named.st_ino) != _GUARD_COLLECTION_ID:
            raise AdapterError("Publisher collection lock changed", "access")
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
    if errors:
        raise errors[0]


def io_verify_current_parent(descriptor, root, expected):
    """Check both the retained directory and its current configured name."""
    io_verify_owned_descriptor(descriptor, expected, True)
    named = os.stat(root, follow_symlinks=False)
    if (named.st_dev, named.st_ino) != expected:
        raise AdapterError("Owned artifact parent changed", "access")


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
    """Close only our acquired object; propagate its own close failure."""
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
        raise


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
        if (
            current is not None
            and (current.st_dev, current.st_ino) == identity
        ):
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


def journal_creation_allowed():
    return _GUARD_NEW_DIRECTORY and _GUARD_REMOTE_INITIALIZING


def journal_mark_mature():
    global _GUARD_CREATED
    _GUARD_CREATED = False


@contextlib.contextmanager
def journal_restore_scope(incoming):
    """Authorize creation only from the original verified incoming state."""
    global _GUARD_REMOTE_INITIALIZING
    validate_budget(incoming)
    if _GUARD_REMOTE_INITIALIZING:
        raise AdapterError("Nested publisher restore refused", "access")
    _GUARD_REMOTE_INITIALIZING = True
    try:
        yield
    finally:
        _GUARD_REMOTE_INITIALIZING = False


def journal_finish():
    global _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID, _GUARD_ROOT_FD, _GUARD_ROOT_METADATA
    errors = []
    if _COLLECTION_LOCK_FD is not None:
        try:
            verify_lock(
                _COLLECTION_LOCK_FD, "collection.lock", _GUARD_COLLECTION_ID
            )
            journal_close(_COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID)
        except BaseException as error:
            journal_failed()
            with contextlib.suppress(BaseException):
                journal_invalidate()
            errors.append(error)
        _COLLECTION_LOCK_FD = _GUARD_COLLECTION_ID = None
    try:
        journal_release()
    except BaseException as error:
        errors.append(error)
    if _GUARD_ROOT_FD is not None:
        fd = _GUARD_ROOT_FD
        try:
            original_root()
            os.close(fd)
        except BaseException as error:
            errors.append(error)
        _GUARD_ROOT_FD = _GUARD_ROOT_METADATA = None
    if errors:
        journal_failed()
        invalidate_export()
        raise errors[0]


def merge_budgets(local, incoming, now):
    """MAX restrictions and union observations without settling any owner."""
    local, incoming = validate_budget(local), validate_budget(incoming)
    if local["in_flight"] or incoming["in_flight"]:
        if local != incoming:
            raise AdapterError(
                "Unresolved Campus France state cannot be merged", "access"
            )
        return local
    combined = copy.deepcopy(
        local if local["observed_at"] >= incoming["observed_at"] else incoming
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
        combined[key] = max(local[key], incoming[key])
    combined["observed_at"] = max(combined["observed_at"], now)
    combined["blocked"] = local["blocked"] or incoming["blocked"]
    combined["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    return validate_budget(combined)


@contextlib.contextmanager
def publisher_attempt(publisher, inventory=False):
    global _ACTIVE_REQUEST_ID, _PUBLISHER_COMPLETED_MONOTONIC
    global _INVENTORY_COMPLETED_MONOTONIC, _LAST_RESPONSE_RECEIPT
    global _LAST_RESPONSE_URL
    if not publisher:
        yield None
        return
    frame = {
        "id": _ACTIVE_REQUEST_ID,
        "assessed": False,
        "received": False,
        "inventory": inventory,
        "no_response": False,
    }
    _REQUEST_FRAMES.append(frame)
    try:
        yield frame
    finally:
        primary = sys.exc_info()[1]
        _REQUEST_FRAMES.pop()
        if not frame["assessed"] and not frame["no_response"]:
            fail_policy()
        elif not _PUBLISHER_GATE_FAILED and not _GUARD_FATAL:
            try:
                with locked_budget():
                    state = load_budget()
                    if (
                        state["flight_id"] != _ACTIVE_FLIGHT_ID
                        or state["request_id"] != frame["id"]
                        or state["request_outcome"] != "reserved"
                    ):
                        raise AdapterError(
                            "Campus France receipt owner changed", "access"
                        )
                    now = time.time()
                    state.update(
                        completed=max(state["completed"], now),
                        observed_at=max(state["observed_at"], now),
                        request_outcome=(
                            "no_response"
                            if frame["no_response"]
                            else "response_assessed"
                        ),
                        completed_request_id=frame["id"],
                    )
                    state["not_before"] = max(
                        state["not_before"],
                        state["completed"] + state["interval"],
                    )
                    if inventory:
                        state["last_inventory_completed"] = max(
                            state["last_inventory_completed"], now
                        )
                    write_budget(state)
                _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
                if inventory:
                    _INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
                _LAST_RESPONSE_RECEIPT = frame["id"]
                _LAST_RESPONSE_URL = frame["url"]
                _ACTIVE_REQUEST_ID = None
            except BaseException:
                fail_policy()
                raise
        if (
            primary is None
            and not frame["assessed"]
            and not frame["no_response"]
        ):
            raise AdapterError(
                "Campus France response was not fully assessed", "access"
            )


def retry_after_until(value, now):
    if not isinstance(value, str):
        raise AdapterError("Unassessed Campus France Retry-After", "access")
    value = value.strip()
    if re.fullmatch("[0-9]+", value):
        if len(value) > 12:
            raise AdapterError("Unsupported positive Retry-After", "access")
        until = now + int(value)
    elif re.fullmatch(_HTTP_DATE_PATTERN, value, re.I):
        try:
            parsed = parse_http_date(value, now)
            if parsed is None:
                raise ValueError()
            until = parsed.timestamp()
        except (ValueError, TypeError, OverflowError):
            raise AdapterError(
                "Unsupported Campus France Retry-After date", "access"
            ) from None
    elif re.fullmatch(r"\+?[0-9]+\.[0-9]+", value):
        raise AdapterError(
            "Unassessed positive Retry-After precision", "access"
        )
    else:
        return None
    if not numeric(until) or until > 253402300799:
        raise AdapterError(
            "Unrepresentable Campus France Retry-After", "access"
        )
    return until if until > now else None


def record_retry_after(values, status=None, *, strike=True):
    """Persist received status first, then MAX every assessable field."""
    now = time.time()
    if status == 429 and strike:
        with locked_budget():
            state = load_budget()
            count = state["rate_limit_strikes"] + 1
            if count > 100:
                raise AdapterError(
                    "Campus France rate counter exhausted", "access"
                )
            current_interval, current_page = (
                state["interval"] * 2,
                state["page_interval"] * 2,
            )
            unsupported = any(
                not numeric(v) or v > 253402300799
                for v in (current_interval, current_page, now + 1800)
            )
            state.update(rate_limit_strikes=count, observed_at=now)
            if numeric(current_interval) and current_interval <= 253402300799:
                state["interval"] = max(state["interval"], current_interval)
            if numeric(current_page) and current_page <= 253402300799:
                state["page_interval"] = max(
                    state["page_interval"], current_page
                )
            state["rate_limit_until"] = max(
                state["rate_limit_until"], state["not_before"], now + 1800
            )
            state["not_before"] = max(
                state["not_before"], state["rate_limit_until"]
            )
            # Commit what the received status already establishes before any
            # stronger exponential calculation or header/body callback.
            write_budget(state)
            interval = 12.2 * (2**count)
            page_interval = 30 * (2**count)
            fallback = now + 1800 * (2 ** (count - 1))
            unsupported = unsupported or any(
                not numeric(v) or v > 253402300799
                for v in (interval, page_interval, fallback)
            )
            changed = False
            for key, value in (
                ("interval", interval),
                ("page_interval", page_interval),
                ("rate_limit_until", fallback),
            ):
                if (
                    numeric(value)
                    and value <= 253402300799
                    and value > state[key]
                ):
                    state[key] = value
                    changed = True
            if changed:
                state["not_before"] = max(
                    state["not_before"], state["rate_limit_until"]
                )
                write_budget(state)
            if unsupported:
                raise AdapterError(
                    "Unsupported Campus France rate arithmetic", "access"
                )
    if isinstance(values, str):
        values = [values]
    if values is None:
        values = []
    if not isinstance(values, (tuple, list)):
        raise AdapterError("Unassessed Retry-After fields", "access")
    maximum, error = 0, None
    for field in values:
        try:
            # A legal HTTP-date contains a comma. Joined dates/delays are
            # ambiguous: retain every visible numeric constraint, then stop.
            if not isinstance(field, str):
                raise AdapterError("Unassessed Retry-After field", "access")
            if re.fullmatch(r"\s*[0-9]+(?:\s*,\s*[0-9]+)+\s*", field):
                pieces = field.split(",")
            elif re.fullmatch(_HTTP_DATE_PATTERN, field.strip(), re.I):
                pieces = [field]
            else:
                dates = list(
                    re.finditer(_HTTP_DATE_COMPONENT_PATTERN, field, re.I)
                )
                if dates or "," in field:
                    pieces = [match.group(1) for match in dates]
                    remainder = re.sub(
                        _HTTP_DATE_COMPONENT_PATTERN, " ", field, flags=re.I
                    )
                    pieces.extend(
                        v for v in remainder.split(",") if v.strip().isdigit()
                    )
                    error = AdapterError(
                        "Ambiguous combined Retry-After", "access"
                    )
                else:
                    pieces = [field]
            for piece in pieces:
                try:
                    maximum = max(maximum, retry_after_until(piece, now) or 0)
                except (AdapterError, OverflowError, ValueError) as problem:
                    error = problem
        except (AdapterError, OverflowError, ValueError) as problem:
            error = problem
    if maximum:
        with locked_budget():
            state = load_budget()
            changed = maximum > state["not_before"]
            state["not_before"] = max(state["not_before"], maximum)
            if status == 429:
                changed = changed or maximum > state["rate_limit_until"]
                state["rate_limit_until"] = max(
                    state["rate_limit_until"], maximum
                )
            if changed:
                state["observed_at"] = max(state["observed_at"], now)
                write_budget(state)
    if error is not None:
        raise error


def retain_publisher_hold():
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
        state["observed_at"] = time.time()
        write_budget(state)


def account_collection_bytes(count):
    """Bound aggregate encoded and decoded publisher bytes for this operation."""
    global _COLLECTION_BYTES
    if (
        type(count) is not int
        or count < 0
        or count > MAX_COLLECTION_BYTES - _COLLECTION_BYTES
    ):
        raise AdapterError(
            "Publisher collection byte budget exceeded", "fetch"
        )
    _COLLECTION_BYTES += count


def begin_transaction():
    global _ACTIVE_FLIGHT_ID
    check_deadline()
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _GUARD_FATAL:
        raise AdapterError(
            "Healthy restored Campus France journal required", "access"
        )
    refusal = None
    with locked_budget():
        state = load_budget()
        if (
            state["in_flight"]
            or state["blocked"]
            or state["rate_limit_strikes"] >= 100
        ):
            raise AdapterError(
                "Campus France journal requires reviewed recovery", "access"
            )
        now = time.time()
        ready = max(
            state["not_before"],
            state["completed"] + state["interval"],
            now
            + max(
                0,
                (_STARTUP_QUIET_UNTIL or time.monotonic()) - time.monotonic(),
            ),
        )
        if state["rate_limit_until"] > now:
            refusal = "Retained Campus France cooldown"
        elif ready + 120 > now + min(_PHASE_END, _RUN_END) - time.monotonic():
            refusal = "Known Campus France guard exceeds phase budget"
        journal_verify()
        if refusal is None:
            ident = os.urandom(16).hex()
            if ident == state["flight_id"]:
                raise AdapterError(
                    "Campus France owner identity collision", "access"
                )
            state.update(
                in_flight=True,
                flight_id=ident,
                flight_kind="policy",
                flight_started=now,
                observed_at=now,
            )
            write_budget(state)
            _ACTIVE_FLIGHT_ID = ident
    if refusal:
        raise PreRequestDeferral(refusal, "access")


@contextlib.contextmanager
def policy_operation():
    global _TRANSACTION_DEPTH, _ACTIVE_FLIGHT_ID
    outer = _TRANSACTION_DEPTH == 0
    if outer:
        begin_transaction()
    _TRANSACTION_DEPTH += 1
    assessed = False
    try:
        yield
        assessed = True
    except PreRequestDeferral:
        assessed = (
            not _PUBLISHER_GATE_FAILED
            and not _GUARD_FATAL
            and _LAST_RESPONSE_RECEIPT is not None
        )
        raise
    except PublisherStop:
        assessed = not _PUBLISHER_GATE_FAILED and not _GUARD_FATAL
        raise
    except ContentReviewDeferred as error:
        assessed = outer and clean_semantic_deferral(error)
        raise
    finally:
        _TRANSACTION_DEPTH -= 1
        if outer:
            if not assessed or _PUBLISHER_GATE_FAILED or _GUARD_FATAL:
                fail_policy()
            else:
                try:
                    with locked_budget():
                        state = load_budget()
                        if (
                            not state["in_flight"]
                            or state["flight_id"] != _ACTIVE_FLIGHT_ID
                            or state["request_outcome"] != "response_assessed"
                            or state["request_id"]
                            != state["completed_request_id"]
                            or state["request_started"]
                            < state["flight_started"]
                            or state["completed"] < state["request_started"]
                            or _ACTIVE_REQUEST_ID is not None
                            or _REQUEST_FRAMES
                        ):
                            raise AdapterError(
                                "Campus France owner changed before completion",
                                "access",
                            )
                        now = time.time()
                        state.update(
                            in_flight=False,
                            completed_flight_id=_ACTIVE_FLIGHT_ID,
                            completed=max(state["completed"], now),
                            observed_at=max(state["observed_at"], now),
                        )
                        state["not_before"] = max(
                            state["not_before"],
                            state["completed"] + state["interval"],
                        )
                        write_budget(state)
                    _ACTIVE_FLIGHT_ID = None
                except BaseException:
                    fail_policy()
                    raise


@contextlib.contextmanager
def operation_guard(publisher=False):
    """Bound headers, body and closure by the earliest operation end."""
    global _OPERATION_END
    check_deadline()
    previous_end = _OPERATION_END
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    began = time.monotonic()
    end = min(_PHASE_END, _RUN_END, began + (60 if publisher else 15))
    if previous_end is not None:
        end = min(end, previous_end)
    if previous_timer[0] > 0:
        end = min(end, began + previous_timer[0])
    if threading.current_thread() is not threading.main_thread():
        raise AdapterError("Main-thread operation timer required", "access")
    _OPERATION_END = end

    def exhausted(signum, frame):
        for request_frame in _REQUEST_FRAMES:
            request_frame["assessed"] = False
        if publisher or _REQUEST_FRAMES:
            fail_policy()
        # Repeat during cancellation: body timeout must also bound slow close.
        signal.setitimer(signal.ITIMER_REAL, 0.05, 0.05)
        raise PhaseExpired()

    try:
        signal.signal(signal.SIGALRM, exhausted)
        remaining = end - time.monotonic()
        if remaining <= 0:
            exhausted(None, None)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield
        check_deadline()
        if time.monotonic() >= end:
            exhausted(None, None)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        _OPERATION_END = previous_end
        if previous_timer[0] > 0:
            remaining = previous_timer[0] - (time.monotonic() - began)
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, remaining),
                previous_timer[1] if remaining > 0 else 0.05,
            )


@contextlib.contextmanager
def assessed_response(response, publisher, robots):
    global _INFRA_CLOSE_FAILED
    primary = None
    try:
        status = response.status
        if publisher:
            if status == 429:
                record_retry_after([], 429)
            elif not robots and status in (401, 403, 451):
                retain_publisher_hold()
        yield status
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            response.close()
        except BaseException:
            _INFRA_CLOSE_FAILED = True
            if publisher:
                fail_policy()
            if primary is None:
                raise


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


class PhaseExpired(BaseException):
    """A non-renewable phase deadline, never a connection retry."""


class PublisherStop(AdapterError):
    """A fully received refusal with durable constraints."""


class PreRequestDeferral(AdapterError):
    """A verified timed admission decision, without a new physical request."""


class ContentReviewDeferred(AdapterError):
    """A specific complete factual input needs review, not transport recovery."""

    def __init__(self, message):
        super().__init__(message, "parse")
        self.receipt = _LAST_RESPONSE_RECEIPT
        self.url = _LAST_RESPONSE_URL


def check_deadline():
    if _PHASE_END is None or _RUN_END is None:
        raise AdapterError(
            "Bounded Campus France execution required", "access"
        )
    if time.monotonic() >= min(_PHASE_END, _RUN_END):
        raise PhaseExpired()


def arm_alarm(end):
    signal.setitimer(signal.ITIMER_REAL, max(0.001, end - time.monotonic()))


def alarm_expired(signum, frame):
    signal.setitimer(signal.ITIMER_REAL, 0.05, 0.05)
    raise PhaseExpired()


@contextlib.contextmanager
def execution():
    global _RUN_END, _REQUEST_STARTS, _COLLECTION_BYTES, _EXECUTION_USED
    if (
        _EXECUTION_USED
        or _RUN_END is not None
        or _GUARD_FATAL
        or _PUBLISHER_GATE_FAILED
    ):
        raise AdapterError(
            "Campus France execution cannot be renewed", "access"
        )
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _EXECUTION_USED = True
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    _REQUEST_STARTS = _COLLECTION_BYTES = 0
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        try:
            journal_finish()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            _RUN_END = None


@contextlib.contextmanager
def phase(name, seconds):
    global _PHASE_END, _PHASE_NAME
    if _RUN_END is None or _PHASE_END is not None:
        raise AdapterError("Invalid Campus France phase transition", "access")
    limit = _RUN_END if name == "export" else _RUN_END - EXPORT_TIMEOUT
    if name == "collection":
        limit -= PUBLICATION_TIMEOUT
    _PHASE_END = min(time.monotonic() + seconds, limit)
    _PHASE_NAME = name
    try:
        check_deadline()
        arm_alarm(_PHASE_END)
        yield
        check_deadline()
    finally:
        _PHASE_NAME = _PHASE_END = None
        arm_alarm(limit)


def fail_policy():
    global _PUBLISHER_GATE_FAILED
    _PUBLISHER_GATE_FAILED = True


def public_url(url):
    p = urllib.parse.urlsplit(url)
    if (
        p.scheme != "https"
        or p.hostname not in _ALLOWED_HOSTS
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise AdapterError("Unreviewed Campus France publisher URL", "access")
    return url


def infrastructure_url(url):
    p = urllib.parse.urlsplit(url)
    allowed = p.hostname in ("api.github.com",) or (
        isinstance(p.hostname, str)
        and p.hostname.endswith(
            (
                ".blob.core.windows.net",
                ".actions.githubusercontent.com",
                ".githubusercontent.com",
            )
        )
    )
    if (
        p.scheme != "https"
        or not allowed
        or p.username
        or p.password
        or p.port is not None
        or p.hostname in _ALLOWED_HOSTS
    ):
        raise AdapterError(
            "Unreviewed Campus France infrastructure URL", "access"
        )
    return url


def body_bytes(response, limit):
    h = response.headers
    lengths, transfers = h.get_all("Content-Length", []), h.get_all(
        "Transfer-Encoding", []
    )
    encodings = h.get_all("Content-Encoding", [])
    chunked = (
        len(transfers) == 1
        and transfers[0].strip().lower() == "chunked"
        and not lengths
        and getattr(response, "chunked", False) is True
    )
    if (
        len(lengths) > 1
        or transfers
        and not chunked
        or len(encodings) > 1
        or any(
            not v.isdigit() or len(v) > 10 or int(v) > limit for v in lengths
        )
    ):
        raise AdapterError("Ambiguous Campus France response framing", "fetch")
    raw = response.read(limit + 1)
    if len(raw) > limit or lengths and len(raw) != int(lengths[0]):
        raise AdapterError("Incomplete Campus France response body", "fetch")
    encoding = encodings[0].strip().lower() if encodings else "identity"
    if encoding not in ("", "identity"):
        raise AdapterError(
            "Unsupported Campus France content encoding", "fetch"
        )
    return raw


def assess_rep(body, headers, url, status):
    origin = urllib.parse.urlsplit(url)
    origin = origin.scheme + "://" + origin.netloc
    parser, digest = None, None
    if 400 <= status < 500 and status != 429:
        pass
    elif status == 200:
        types = headers.get_all("Content-Type", [])
        if (
            len(types) != 1
            or types[0].split(";", 1)[0].strip().lower() != "text/plain"
        ):
            raise AdapterError(
                "Campus France REP requires text/plain", "access"
            )
        text = body.decode("utf-8-sig", "strict")
        if re.search(r"<\s*[A-Za-z!/?]", text):
            raise AdapterError("Markup is not Campus France REP", "access")
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        digest = hashlib.sha256(body).hexdigest()
        # Unsupported positive rates cannot silently disappear in stdlib REP.
        for line in text.splitlines():
            value = line.split("#", 1)[0].strip()
            if re.match(r"(?i)^(crawl-delay|request-rate)\s*:", value):
                key, raw = value.split(":", 1)
                if not re.fullmatch(r"\s*[0-9]+(?:/[0-9]+)?\s*", raw):
                    raise AdapterError(
                        "Unsupported Campus France REP rate", "access"
                    )
    else:
        raise AdapterError("Campus France REP unavailable", "access", status)
    _ROBOTS[origin] = parser
    _REP_PROVENANCE[origin] = {
        "status": status,
        "body_sha256": digest,
        "request_id": _ACTIVE_REQUEST_ID,
        "url": url,
    }


def apply_rep(url):
    p = urllib.parse.urlsplit(url)
    origin = p.scheme + "://" + p.netloc
    if origin not in _REP_PROVENANCE:
        raise AdapterError("Campus France REP provenance missing", "access")
    parser = _ROBOTS[origin]
    interval = 12.2
    if parser is not None:
        policies = [parser]
        for entry in parser.entries:
            if any(
                entry.applies_to(agent)
                for agent in ("YouthOppsBot", "YouthOpps")
            ):
                single = copy.copy(parser)
                single.entries, single.default_entry = [entry], None
                policies.append(single)
        for policy in policies:
            for agent in ("YouthOppsBot", "YouthOpps"):
                if not policy.can_fetch(agent, url):
                    raise AdapterError(
                        "Campus France REP excludes target", "access"
                    )
                delay, rate = policy.crawl_delay(agent), policy.request_rate(
                    agent
                )
                if delay is not None:
                    interval = max(interval, delay)
                if rate:
                    if rate.requests <= 0:
                        raise AdapterError("Invalid REP rate", "access")
                    interval = max(interval, rate.seconds / rate.requests)
    with locked_budget():
        state = load_budget()
        if interval > state["interval"]:
            state["interval"] = interval
            state["observed_at"] = max(state["observed_at"], time.time())
            write_budget(state)
    return interval


def original_source_request(
    url, *, interval=12.2, method="GET", payload=None, robots=False
):
    """Use only literal official public hosts; preserve redirect method/body."""
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError("Robots retrieval requires robots.txt", "access")
    current = url
    for hop in range(4):
        parsed = urllib.parse.urlsplit(current)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise AdapterError("Unreviewed public source URL", "access")
        if not robots:
            interval = max(interval, check_robots(current))
        status, headers, body = request_bytes(
            current,
            interval=interval,
            method=method,
            payload=payload,
            robots=robots,
            headers=(
                {"Content-Type": "application/json; charset=utf-8"}
                if payload is not None
                else None
            ),
        )
        if not robots:
            reject_visible_challenge(
                body, headers, challenge_only=status == 429
            )
        if status in {301, 302, 303, 307, 308}:
            if not headers.get("Location") or hop == 3:
                raise AdapterError("Invalid source redirect", "fetch")
            target = urllib.parse.urljoin(current, headers["Location"])
            if urllib.parse.urlsplit(target).hostname != parsed.hostname:
                raise AdapterError(
                    "Redirect changes reviewed source host", "access"
                )
            if robots and urllib.parse.urlsplit(target).path != "/robots.txt":
                raise AdapterError(
                    "Robots redirect leaves the policy route", "access"
                )
            if method != "GET" and status not in {307, 308}:
                raise AdapterError("Unexpected public POST redirect", "fetch")
            current = target
            continue
        if status != 200 and not robots:
            raise AdapterError(
                "Publisher content returned HTTP " + str(status),
                "fetch",
                status,
            )
        return status, headers, body
    raise AdapterError("Source redirect limit exceeded", "fetch")


def clean_semantic_deferral(error):
    if (
        not isinstance(error, ContentReviewDeferred)
        or error.receipt is None
        or error.receipt != _LAST_RESPONSE_RECEIPT
        or error.url != SCRIPT_URL
        or _REQUEST_FRAMES
        or _ACTIVE_REQUEST_ID
        or _GUARD_FATAL
        or _PUBLISHER_GATE_FAILED
    ):
        return False
    with locked_budget():
        state = load_budget()
        journal_verify()
        valid = (
            state["in_flight"]
            and not state["blocked"]
            and state["rate_limit_until"] <= time.time()
            and state["flight_id"] == _ACTIVE_FLIGHT_ID
            and state["request_outcome"] == "response_assessed"
            and state["request_id"] == state["completed_request_id"]
            and state["request_id"] == error.receipt
            and state["request_started"] >= state["flight_started"]
        )
    return valid and not _GUARD_FATAL


def original_collect():
    """Read two complete anonymous UI inventories, keeping minimal facts."""
    global _COLLECTION_END
    prepare_collection()
    _COLLECTION_END = time.monotonic() + COLLECTION_TIMEOUT
    home = fetch_inventory_page(fetch_source, SOURCE_URL)
    if 'src="' + SCRIPT_PATH + '"' not in home:
        raise AdapterError("Reviewed public application changed", "access")
    read_configuration(fetch_source(SCRIPT_URL))
    policy = PolicyText()
    policy.feed(fetch_source(POLICY_URL))
    policy_text = " ".join(" ".join(policy.parts).split())
    if (
        policy.sections != 1
        or hashlib.sha256(policy_text.encode()).hexdigest()
        != REVIEWED_POLICY_HASH
    ):
        raise AdapterError("Reviewed legal-page scope changed", "access")
    first = read_frontier()
    closing = read_frontier()
    if first != closing:
        raise AdapterError(
            "Closing full catalogue differs; no partial output", "parse"
        )
    records = []
    for identifier, (title, types) in sorted(first.items()):
        record = make_record(
            title,
            programme_url(identifier),
            (
                ["scholarships", "grants"]
                if "1" in types and "2" in types
                else ["grants"] if "2" in types else ["scholarships"]
            ),
            kind="programme-overview",
            tags=[TYPE_LABELS[value] for value in types],
            method="publisher-financial-support-catalogue",
            evidence=["CampusBourses financial-support programme"],
        )
        record.update(
            id=SOURCE_ID + "-" + str(identifier),
            summary_language="en",
            summary=(
                "CampusBourses lists this financial support programme. "
                "Check the original source for current application conditions "
                "and dates."
            ),
        )
        records.append(record)
    validate_records(records)
    print(
        json.dumps(
            {
                "source": SOURCE_ID,
                "records": len(records),
                "full_frontiers": 2,
                "scope": "minimal-programme-facts",
            }
        ),
        file=sys.stderr,
    )
    return records


def canonical_state(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def reviewed_transition():
    value = _CAMPUS417_DECISION
    if (
        type(value) is not dict
        or set(value) != {"evidence", "pins_sha256", "floor_sha256"}
        or value["pins_sha256"] != _FINITE_PREWIRE_SHA
        or value["floor_sha256"] != _KNOWN_FLOOR_SHA
        or not isinstance(value["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/417#issuecomment-[1-9][0-9]*",
            value["evidence"],
        )
        or hashlib.sha256(canonical_state(_FINITE_PREWIRE)).hexdigest()
        != _FINITE_PREWIRE_SHA
        or hashlib.sha256(canonical_state(_KNOWN_FLOOR)).hexdigest()
        != _KNOWN_FLOOR_SHA
    ):
        raise AdapterError(
            "Reviewed Campus France transition is OFF", "access"
        )
    return value["evidence"]


def verified_code(head, path):
    key = (head, path)
    if key in _CODE_CACHE:
        return _CODE_CACHE[key]
    if not re.fullmatch("[0-9a-f]{40}", head):
        raise AdapterError("Invalid immutable Campus France head", "access")
    v = workflow_api("/contents/" + path + "?ref=" + head)
    if (
        v.get("type") != "file"
        or v.get("encoding") != "base64"
        or type(v.get("size")) is not int
        or not 0 < v["size"] <= 1000000
    ):
        raise AdapterError("Invalid immutable Campus France code", "access")
    raw = base64.b64decode("".join(v["content"].split()), validate=True)
    blob = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()
    if len(raw) != v["size"] or blob != v.get("sha"):
        raise AdapterError("Immutable Campus France bytes differ", "access")
    _CODE_CACHE[key] = (raw, blob)
    return raw, blob


def workflow_mode_guard():
    value = workflow_api("/actions/workflows/" + str(_WORKFLOW_ID))
    expected = (
        "active"
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else "disabled_manually"
    )
    if (
        value.get("id") != _WORKFLOW_ID
        or value.get("path") != _WORKFLOW_PATH
        or value.get("state") != expected
    ):
        raise AdapterError(
            "Campus France workflow coordination changed", "access"
        )


def verify_finite_gap(identity):
    run, attempt, source = identity
    pin = _FINITE_PREWIRE[run]
    row = _IDENTITIES[(run, attempt)]
    if (
        attempt != 1
        or source != SOURCE_ID
        or row["head_sha"] != pin["head"]
        or row["run_started_at"] != pin["run_started_at"]
    ):
        raise AdapterError("Campus France finite attempt changed", "access")
    jobs = _EXACT_JOBS[(run, attempt)]
    if len(jobs) != 1 or any(
        jobs[0].get(k) != v
        for k, v in {
            "id": pin["job_id"],
            "started_at": pin["started_at"],
            "completed_at": pin["completed_at"],
            "head_sha": pin["head"],
            "run_id": run,
            "run_attempt": attempt,
            "status": "completed",
            "conclusion": "failure",
        }.items()
    ):
        raise AdapterError("Campus France finite job changed", "access")
    raw, blob = verified_code(
        pin["head"], "adapters/" + SOURCE_ID + "/adapter.py"
    )
    if (
        blob != pin["source_blob"]
        or hashlib.sha256(raw).hexdigest() != pin["source_sha256"]
    ):
        raise AdapterError("Campus France finite source changed", "access")
    if verified_code(pin["head"], _WORKFLOW_PATH)[1] != pin["workflow_blob"]:
        raise AdapterError("Campus France finite workflow changed", "access")
    if not re.fullmatch("[0-9a-f]{64}", pin["log_sha256"]):
        raise AdapterError(
            "Campus France finite causal proof missing", "access"
        )
    result = workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    if (
        type(result.get("total_count")) is not int
        or result["total_count"] != 0
        or result.get("artifacts") != []
    ):
        raise AdapterError(
            "Campus France finite artifact presence changed", "access"
        )
    return datetime.fromisoformat(
        pin["completed_at"].replace("Z", "+00:00")
    ).timestamp()


def upgrade_legacy_budget(value):
    fields = {
        "schema",
        "family",
        "observed_at",
        "not_before",
        "starts",
        "blocked",
        "interval",
        "completed",
        "page_interval",
        "rate_limit_strikes",
        "rate_limit_until",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["family"] != FAMILY
        or type(value["blocked"]) is not bool
        or type(value["rate_limit_strikes"]) is not int
        or not 0 <= value["rate_limit_strikes"] <= 100
        or type(value["starts"]) is not list
        or any(not numeric(v) for v in value["starts"])
        or any(
            not numeric(value[k])
            for k in fields
            - {"schema", "family", "starts", "blocked", "rate_limit_strikes"}
        )
    ):
        raise AdapterError(
            "Invalid scoped legacy Campus France state", "access"
        )
    upgraded = empty_budget(0)
    for key in fields - {"schema", "family"}:
        upgraded[key] = copy.deepcopy(value[key])
    upgraded["observed_at"] = max(
        value["observed_at"], value["completed"], *value["starts"]
    )
    upgraded["last_inventory_completed"] = value["completed"]
    upgraded["starts"] = sorted(set(value["starts"]))
    return validate_budget(upgraded)


def apply_reviewed_floor(incoming):
    reviewed_transition()
    floor = validate_budget(copy.deepcopy(_KNOWN_FLOOR))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if (
            _CAMPUS417_HOSTED_FLOOR is None
            or not isinstance(_CAMPUS417_HOSTED_EVIDENCE, str)
            or _CAMPUS417_HOSTED_EVIDENCE == _CAMPUS417_DECISION["evidence"]
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/417#issuecomment-[1-9][0-9]*",
                _CAMPUS417_HOSTED_EVIDENCE,
            )
        ):
            raise AdapterError("Campus France hosted handoff is OFF", "access")
        floor = merge_budgets(
            floor, validate_budget(_CAMPUS417_HOSTED_FLOOR), time.time()
        )
    return merge_budgets(incoming, floor, time.time())


def verified_predecessor_artifact(identity):
    run, attempt, source = identity
    result = workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    items = result.get("artifacts")
    if (
        type(result.get("total_count")) is not int
        or not isinstance(items, list)
        or result["total_count"] != len(items)
        or len(items) > 100
    ):
        raise AdapterError(
            "Incomplete Campus France artifact inventory", "access"
        )
    matches = [
        v
        for v in items
        if v.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest Campus France owned artifact unavailable", "access"
        )
    v = matches[0]
    head = _IDENTITIES[(run, attempt)]["head_sha"]
    if (
        type(v.get("id")) is not int
        or v["id"] <= 0
        or type(v.get("size_in_bytes")) is not int
        or not 0 < v["size_in_bytes"] <= 32768
        or v.get("workflow_run", {}).get("id") != run
        or v["workflow_run"].get("head_sha") != head
        or v["workflow_run"].get("head_branch") != "main"
        or not re.fullmatch("sha256:[0-9a-f]{64}", v.get("digest", ""))
    ):
        raise AdapterError(
            "Invalid Campus France owned artifact binding", "access"
        )
    if identity == _LEGACY_OWNER and (
        v["id"] != _LEGACY_ARTIFACT
        or v["size_in_bytes"] != 427
        or v["digest"] != "sha256:" + _LEGACY_ZIP_SHA
    ):
        raise AdapterError(
            "Scoped Campus France legacy artifact changed", "access"
        )
    return download_inert_artifact(identity, v["id"], v["digest"])


def local_seed_claim():
    evidence = reviewed_transition()
    value = _CAMPUS417_LOCAL_SEED
    if (
        type(value) is not dict
        or set(value) != {"machine_sha256", "state_sha256"}
        or any(
            not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v)
            for v in value.values()
        )
    ):
        raise AdapterError(
            "Campus France local initialization is OFF", "access"
        )
    with open("/etc/machine-id", "rb") as f:
        actual = hashlib.sha256(f.read(4097)).hexdigest()
    if actual != value["machine_sha256"]:
        raise AdapterError(
            "Campus France initializer machine differs", "access"
        )
    with open(__file__, "rb") as f:
        code = f.read(1000001)
    return {
        "family": FAMILY,
        "source_sha256": hashlib.sha256(code).hexdigest(),
        "state_sha256": value["state_sha256"],
        "machine_sha256": actual,
        "evidence": evidence,
    }


def claim_local_attempt():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    if "DATA_SOURCE_TOKEN" in os.environ:
        raise AdapterError(
            "Local Campus France refuses publication token", "access"
        )
    expected = local_seed_claim()
    path = os.path.join(
        os.path.dirname(_STATE_PATH),
        "..",
        FAMILY + "-issue417-initialized-" + str(os.getuid()) + ".claim",
    )
    parent = original_root()
    name = os.path.basename(path)
    fd = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    try:
        info = journal_owned(fd)
        raw = os.pread(fd, 4097, 0)
        if json.loads(raw) != expected or len(raw) > 4096:
            raise AdapterError(
                "Campus France initializer claim differs", "access"
            )
        _LOCAL_CLAIM_BINDINGS[name] = (parent, (info.st_dev, info.st_ino), raw)
    finally:
        os.close(fd)
    with locked_budget():
        state = load_budget()
        if (
            state["in_flight"]
            or state["blocked"]
            or hashlib.sha256(journal_read()).hexdigest()
            != expected["state_sha256"]
        ):
            raise AdapterError(
                "Initial Campus France generation differs", "access"
            )
        fd = os.open(
            "issue417-native.claim",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=_GUARD_PARENT,
        )
        try:
            raw = canonical_state(expected)
            info = journal_owned(fd)
            if os.write(fd, raw) != len(raw):
                raise OSError("Incomplete native claim")
            os.fsync(fd)
            os.fsync(_GUARD_PARENT)
            if os.pread(fd, len(raw) + 1, 0) != raw:
                raise OSError("Native claim differs")
            _LOCAL_CLAIM_BINDINGS["issue417-native.claim"] = (
                _GUARD_PARENT,
                (info.st_dev, info.st_ino),
                raw,
            )
        finally:
            os.close(fd)


def verify_local_claims():
    for name, (parent, identity, raw) in _LOCAL_CLAIM_BINDINGS.items():
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        try:
            journal_owned(fd, identity)
            if os.pread(fd, len(raw) + 1, 0) != raw:
                raise AdapterError(
                    "Campus France native claim changed", "access"
                )
        finally:
            os.close(fd)


def publication_window():
    run, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    if (
        not re.fullmatch("[0-9a-f]{40}", head)
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or os.environ.get("GITHUB_WORKFLOW_REF")
        != "YouthOpps/data-pipeline/" + _WORKFLOW_PATH + "@refs/heads/main"
    ):
        raise AdapterError(
            "Campus France publication identity changed", "publish"
        )
    if verified_code(head, _WORKFLOW_PATH)[1] != _CURRENT_WORKFLOW_BLOB:
        raise AdapterError("Campus France workflow bytes changed", "publish")
    with open(__file__, "rb") as stream:
        current = stream.read(1000001)
    if (
        verified_code(head, "adapters/" + SOURCE_ID + "/adapter.py")[0]
        != current
    ):
        raise AdapterError("Campus France executing code changed", "publish")
    result = workflow_api(
        f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        type(result.get("total_count")) is not int
        or not isinstance(jobs, list)
        or result["total_count"] != len(jobs)
    ):
        raise AdapterError(
            "Incomplete Campus France publication jobs", "publish"
        )
    rows = [
        j
        for j in jobs
        if j.get("run_id") == run
        and j.get("run_attempt") == attempt
        and j.get("head_sha") == head
        and j.get("status") == "in_progress"
        and j.get("name") == os.environ.get("GITHUB_JOB")
    ]
    if len(rows) != 1:
        raise AdapterError(
            "Exact Campus France executing job missing", "publish"
        )
    start = datetime.fromisoformat(
        rows[0]["started_at"].replace("Z", "+00:00")
    ).timestamp()
    if not 0 <= time.time() - start < 20 * 60:
        raise AdapterError(
            "Campus France publication job budget exhausted", "publish"
        )


def decode_owned_payload(identity, artifact_id, envelope):
    run, attempt, source = identity
    head = _IDENTITIES.get((run, attempt), {}).get("head_sha")
    legacy = {
        "schema",
        "repository",
        "run_id",
        "run_attempt",
        "source",
        "state",
    }
    base = {
        "schema",
        "repository",
        "run_id",
        "run_attempt",
        "source",
        "head_sha",
        "adapter_sha256",
    }
    if (
        type(envelope) is not dict
        or type(envelope.get("schema")) is not int
        or envelope["schema"] != 1
        or type(envelope.get("run_id")) is not int
        or type(envelope.get("run_attempt")) is not int
        or envelope["run_id"] != run
        or envelope["run_attempt"] != attempt
        or envelope.get("source") != source
        or envelope.get("repository") != "YouthOpps/data-pipeline"
    ):
        raise AdapterError("Campus France owned envelope changed", "access")
    if set(envelope) == legacy:
        if (
            identity != _LEGACY_OWNER
            or artifact_id != _LEGACY_ARTIFACT
            or head != _LEGACY_HEAD
            or hashlib.sha256(canonical_state(envelope["state"])).hexdigest()
            != _LEGACY_STATE_SHA
        ):
            raise AdapterError(
                "Unreviewed Campus France legacy envelope", "access"
            )
        return upgrade_legacy_budget(envelope["state"])
    if (
        envelope.get("head_sha") != head
        or not isinstance(envelope.get("adapter_sha256"), str)
        or envelope["adapter_sha256"]
        != hashlib.sha256(
            verified_code(head, "adapters/" + SOURCE_ID + "/adapter.py")[0]
        ).hexdigest()
    ):
        raise AdapterError("Campus France producer code differs", "access")
    if set(envelope) == base | {"state", "state_sha256"}:
        state = validate_budget(envelope["state"])
        if (
            envelope["state_sha256"]
            != hashlib.sha256(canonical_state(state)).hexdigest()
        ):
            raise AdapterError(
                "Campus France owned state digest differs", "access"
            )
        return state
    if set(envelope) == base | {"kind", "zero_publisher_reachability"}:
        if (
            envelope["kind"] != "pre-request-metadata-failure"
            or envelope["zero_publisher_reachability"] is not True
        ):
            raise AdapterError(
                "Invalid Campus France pre-request receipt", "access"
            )
        return {"prewire_receipt": True}
    raise AdapterError("Unknown Campus France owned payload", "access")


def http_date_patterns():
    """Complete HTTP date forms, including obsolete RFC850 and asctime."""
    day = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
    weekday = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    month = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    clock = r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    return (
        day + r", *[0-9]{2} " + month + r" [0-9]{4,} " + clock + r" GMT",
        weekday + r", *[0-9]{2}-" + month + r"-[0-9]{2} " + clock + r" GMT",
        day + " " + month + r" (?:[0-9]{2}| [0-9]) " + clock + r" [0-9]{4,}",
    )


def parse_http_date(value, now):
    """Reject email-parser prefixes; only HTTP asctime may imply UTC."""
    form = next(
        (
            index
            for index, pattern in enumerate(http_date_patterns())
            if re.fullmatch(pattern, value, re.I)
        ),
        None,
    )
    if form is None:
        return None
    parsed = parsedate_to_datetime(value)
    if form == 1:
        year = int(re.search(r"-([0-9]{2}) ", value)[1])
        reference = datetime.fromtimestamp(now, timezone.utc)
        year += reference.year // 100 * 100
        parsed = parsed.replace(year=year)
        if (
            parsed.year,
            parsed.month,
            parsed.day,
            parsed.hour,
            parsed.minute,
            parsed.second,
        ) <= (
            reference.year - 50,
            reference.month,
            reference.day,
            reference.hour,
            reference.minute,
            reference.second,
        ):
            parsed = parsed.replace(year=year + 100)
        if (
            parsed.year,
            parsed.month,
            parsed.day,
            parsed.hour,
            parsed.minute,
            parsed.second,
        ) > (
            reference.year + 50,
            reference.month,
            reference.day,
            reference.hour,
            reference.minute,
            reference.second,
        ):
            parsed = parsed.replace(year=parsed.year - 100)
    if parsed.utcoffset() is None:
        if form != 2:
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.utcoffset() != timedelta(0):
        return None
    return parsed


_HTTP_DATE_PATTERN = "(?:" + "|".join(http_date_patterns()) + ")"
_HTTP_DATE_COMPONENT_PATTERN = (
    r"(?:^|,\s*)(" + _HTTP_DATE_PATTERN + r")(?=\s*(?:,|$))"
)


def export_location():
    root = os.environ.get("RUNNER_TEMP", "")
    path = os.environ.get("CAMPUS_FRANCE_PACING_ARTIFACT_PATH", "")
    if (
        not os.path.isabs(root)
        or root != os.path.realpath(root)
        or os.path.dirname(path) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe Campus France export path", "access")
    return root, path


def invalidate_export():
    if _LAST_EXPORT_RECORD is None:
        return
    root, path, parent_id, identity, raw = _LAST_EXPORT_RECORD
    parent = fd = None
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        io_verify_current_parent(parent, root, parent_id)
        fd = os.open(
            os.path.basename(path),
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        io_artifact_descriptor(fd, identity)
        if os.pread(fd, len(raw) + 1, 0) != raw:
            return
        os.ftruncate(fd, 0)
        os.fsync(fd)
        named = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (named.st_dev, named.st_ino) == identity:
            os.unlink(os.path.basename(path), dir_fd=parent)
            os.fsync(parent)
    except BaseException:
        pass
    finally:
        for value in (fd, parent):
            if value is not None:
                with contextlib.suppress(BaseException):
                    os.close(value)


def write_export_payload(envelope):
    global _LAST_EXPORT_RECORD
    root, path = export_location()
    parent = fd = None
    parent_id = identity = None
    primary = None
    errors = []
    raw = canonical_state(envelope)
    if len(raw) > 16384:
        raise AdapterError("Campus France export oversized", "access")
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(parent)
        parent_id = (info.st_dev, info.st_ino)
        io_verify_owned_descriptor(parent, parent_id, True)
        io_verify_current_parent(parent, root, parent_id)
        old = None
        try:
            old = os.open(
                os.path.basename(path),
                os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            pass
        if old is not None:
            try:
                held = os.fstat(old)
                old_id = (held.st_dev, held.st_ino)
                io_artifact_descriptor(old, old_id)
                named = os.stat(
                    os.path.basename(path),
                    dir_fd=parent,
                    follow_symlinks=False,
                )
                if (named.st_dev, named.st_ino) != old_id:
                    raise AdapterError(
                        "Existing Campus France export replaced", "access"
                    )
                os.ftruncate(old, 0)
                os.fsync(old)
                io_verify_current_parent(parent, root, parent_id)
                named = os.stat(
                    os.path.basename(path),
                    dir_fd=parent,
                    follow_symlinks=False,
                )
                if (named.st_dev, named.st_ino) != old_id:
                    raise AdapterError(
                        "Existing Campus France export replaced", "access"
                    )
                os.unlink(os.path.basename(path), dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(old)
        fd = os.open(
            os.path.basename(path),
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        _LAST_EXPORT_RECORD = (root, path, parent_id, identity, raw)
        offset = 0
        while offset < len(raw):
            io_verify_current_parent(parent, root, parent_id)
            io_artifact_descriptor(fd, identity)
            count = os.write(fd, raw[offset:])
            if type(count) is not int or count <= 0:
                raise OSError("Incomplete export write")
            offset += count
        os.fsync(fd)
        os.fsync(parent)
        io_verify_current_parent(parent, root, parent_id)
        io_artifact_descriptor(fd, identity)
        if os.pread(fd, len(raw) + 1, 0) != raw:
            raise OSError("Campus France export readback differs")
        _LAST_EXPORT_RECORD = (root, path, parent_id, identity, raw)
        if _STATE_TRUSTED:
            with locked_budget():
                journal_verify()
    except BaseException as error:
        primary = error
        journal_failed()
        if fd is not None:
            with contextlib.suppress(BaseException):
                io_artifact_descriptor(fd, identity)
                os.ftruncate(fd, 0)
                os.fsync(fd)
        raise
    finally:
        for value, expected, directory in (
            (fd, identity, False),
            (parent, parent_id, True),
        ):
            if value is not None:
                try:
                    io_close_owned_descriptor(value, expected, directory)
                except BaseException as error:
                    errors.append(error)
        if primary is not None or errors:
            invalidate_export()
        if errors:
            journal_failed()
            if primary is None:
                raise errors[0]


def read_snapshot_phase():
    with phase("snapshot", 60):
        return read_snapshot()


def publish_files_phase(snapshot, files):
    global _PUBLICATION_END
    if _PUBLICATION_END is None:
        _PUBLICATION_END = time.monotonic() + PUBLICATION_TIMEOUT
    with phase("publication", max(0, _PUBLICATION_END - time.monotonic())):
        return publish_files(snapshot, files)


def original_root():
    global _GUARD_ROOT_FD, _GUARD_ROOT_METADATA
    root = os.path.dirname(os.path.dirname(_STATE_PATH))
    if _GUARD_ROOT_FD is None:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        _GUARD_ROOT_FD = fd
        _GUARD_ROOT_METADATA = (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            stat.S_IMODE(info.st_mode),
        )
    fd = _GUARD_ROOT_FD
    info = os.fstat(fd)
    named = os.stat(root, follow_symlinks=False)
    actual = lambda v: (
        v.st_dev,
        v.st_ino,
        v.st_uid,
        v.st_gid,
        stat.S_IMODE(v.st_mode),
    )
    owner, mode = info.st_uid, stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or actual(info) != _GUARD_ROOT_METADATA
        or actual(named) != _GUARD_ROOT_METADATA
        or not (
            (owner in (0, os.getuid()) and not mode & 0o022) or mode == 0o1777
        )
    ):
        journal_failed()
        raise AdapterError(
            "Campus France original temporary parent changed", "access"
        )
    return fd


def lock_struct(kind):
    return struct.pack("hhqqi", kind, os.SEEK_SET, 0, 1, 0)


def retain_lock(fd):
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.fcntl(
        fd, getattr(fcntl, "F_OFD_SETLK", 37), lock_struct(fcntl.F_WRLCK)
    )


def verify_lock(fd, name, identity):
    journal_owned(fd, identity)
    parent = journal_parent()
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != identity:
        raise AdapterError("Campus France lock name changed", "access")
    own = struct.unpack(
        "hhqqi",
        fcntl.fcntl(
            fd, getattr(fcntl, "F_OFD_GETLK", 36), lock_struct(fcntl.F_WRLCK)
        ),
    )
    if own[0] != fcntl.F_UNLCK:
        raise AdapterError("Campus France original OFD lock lost", "access")
    probe = os.open(
        name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    try:
        journal_owned(probe, identity)
        other = struct.unpack(
            "hhqqi",
            fcntl.fcntl(
                probe,
                getattr(fcntl, "F_OFD_GETLK", 36),
                lock_struct(fcntl.F_WRLCK),
            ),
        )
        if other[0] != fcntl.F_WRLCK or other[4] != -1:
            raise AdapterError(
                "Campus France owned lock was released", "access"
            )
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise AdapterError(
                "Campus France original flock was released", "access"
            )
    finally:
        os.close(probe)


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Write only this source to YouthOpps/data-source "
            "using DATA_SOURCE_TOKEN"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "source": SOURCE_ID,
                    "status": "fail",
                    "last_attempt_at": utc_now(),
                    "message": (
                        "Run could not complete; " "durable status unavailable"
                    ),
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
