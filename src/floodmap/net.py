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
