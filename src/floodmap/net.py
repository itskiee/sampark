"""The only place the pipeline makes HTTP requests.

Every URL is checked against the data-rules allowlist before any connection is opened,
requests always have a timeout (a hung request must not freeze a live demo), and transient
failures (timeouts, HTTP 429/5xx) are retried with exponential backoff.
tests/test_data_rules.py fails if any other pipeline module imports an HTTP library.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from floodmap import __version__
from floodmap.data_rules import check_http_url

log = logging.getLogger("floodmap.net")

USER_AGENT = f"floodmap/{__version__} (Multimodal AI Hackathon 2026 educational prototype)"
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Broken / timed-out connections worth retrying; shared with cdse_client.download so that
# only this module imports the HTTP library (data-rules scanner).
TRANSIENT_NETWORK_ERRORS = (requests.exceptions.ConnectionError,
                            requests.exceptions.ChunkedEncodingError,
                            requests.exceptions.Timeout)


class NetworkError(RuntimeError):
    """A request failed after all retries; message is safe to show to the user."""


def request_json(
    method: str,
    url: str,
    *,
    timeout_s: float,
    retries: int,
    data: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    check_http_url(url)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(
                method,
                url,
                data=data,
                params=params,
                json=json_body,
                timeout=timeout_s,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            if response.status_code in _RETRY_STATUS:
                raise NetworkError(f"HTTP {response.status_code} from {url}")
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError, NetworkError) as exc:
            last_error = exc
            if attempt < retries:
                wait = 2**attempt
                log.warning("Request to %s failed (%s); retrying in %ss", url, exc, wait)
                time.sleep(wait)
        except requests.HTTPError as exc:  # 4xx other than 429: retrying will not help
            raise NetworkError(f"HTTP error from {url}: {exc}") from exc
        except ValueError as exc:  # body was not JSON
            raise NetworkError(f"Response from {url} was not valid JSON") from exc
    raise NetworkError(f"Request to {url} failed after {retries + 1} attempts: {last_error}")


# --- Training data (Sen1Floods11) -------------------------------------------------------
# Separate from request_json on purpose: the only host is the dataset's official bucket,
# and only object names that pass data_rules.check_training_object can be fetched. URLs are
# built here from constants; callers never pass a URL. Used by scripts/, never the pipeline.

def list_training_objects(prefix: str, *, timeout_s: float, retries: int) -> list[dict]:
    """Names, sizes and MD5s of the bucket objects under an allowed prefix."""
    from floodmap.data_rules import SEN1FLOODS11_LIST_URL, check_training_object

    check_training_object(prefix)
    items, token = [], None
    while True:
        params = {"prefix": prefix, "maxResults": 1000,
                  "fields": "items(name,size,md5Hash),nextPageToken"}
        if token:
            params["pageToken"] = token
        page = _get(SEN1FLOODS11_LIST_URL, params, timeout_s, retries).json()
        items += [i for i in page.get("items", []) if not i["name"].endswith("/")]
        token = page.get("nextPageToken")
        if not token:
            return [check_training_object(i["name"]) and i for i in items]


def download_training_object(item: dict, dest, *, timeout_s: float, retries: int) -> bool:
    """Download one allowed object to `dest` (resumable via .part) and verify its MD5.
    Returns False if the file was already present and valid."""
    import base64
    import hashlib
    from pathlib import Path
    from urllib.parse import quote

    from floodmap.data_rules import SEN1FLOODS11_BUCKET, TRAINING_DOWNLOAD_HOST

    name = item["name"]
    from floodmap.data_rules import check_training_object

    check_training_object(name)
    dest = Path(dest)
    expected = base64.b64decode(item["md5Hash"]).hex()

    def md5_of(path: Path) -> str:
        h = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    if dest.is_file() and md5_of(dest) == expected:
        return False
    url = f"https://{TRAINING_DOWNLOAD_HOST}/{SEN1FLOODS11_BUCKET}/{quote(name)}"
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = _get(url, None, timeout_s, retries)
    part.write_bytes(response.content)
    if md5_of(part) != expected:
        part.unlink(missing_ok=True)
        raise NetworkError(f"Checksum mismatch for {name}")
    part.replace(dest)
    return True


def _get(url: str, params, timeout_s: float, retries: int):
    from floodmap.data_rules import TRAINING_DOWNLOAD_HOST

    if not url.startswith(f"https://{TRAINING_DOWNLOAD_HOST}/"):
        raise NetworkError(f"Refusing training download from {url}")
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout_s,
                                    headers={"User-Agent": USER_AGENT})
            if response.status_code in _RETRY_STATUS:
                raise NetworkError(f"HTTP {response.status_code} from {url}")
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError, NetworkError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2**attempt)
        except requests.HTTPError as exc:
            raise NetworkError(f"HTTP error from {url}: {exc}") from exc
    raise NetworkError(f"Request to {url} failed after {retries + 1} attempts: {last}")
