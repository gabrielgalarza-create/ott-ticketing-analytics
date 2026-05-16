"""SweatPals API client. Handles auth, pagination, retries."""
from __future__ import annotations

import os
import time
from typing import Any, Iterator
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

load_dotenv()


class SweatPalsClient:
    def __init__(self, api_key: str | None = None, host: str | None = None, page_size: int = 100):
        self.api_key = api_key or os.environ["SWEATPALS_API_KEY"]
        self.host = (host or os.environ.get("SWEATPALS_HOST", "https://api.sweatpals.com")).rstrip("/")
        self.page_size = page_size
        self.session = requests.Session()
        # The Postman collection shows apikey auth with key="<key>" — i.e. the header name is literally "<key>".
        # On first real request we'll detect which header SweatPals actually expects (api-key, x-api-key, Authorization).
        self.session.headers.update({"Accept": "application/json"})
        self._auth_header_name: str | None = None

    def _resolve_auth_header(self) -> str:
        """First call: probe a tiny request with each common header until one works."""
        if self._auth_header_name:
            return self._auth_header_name
        candidates = ["api-key", "x-api-key", "X-API-Key", "Api-Key", "Authorization"]
        url = urljoin(self.host + "/", "api/zapier-provider/new-tickets")
        for name in candidates:
            headers = {name: f"Bearer {self.api_key}"} if name == "Authorization" else {name: self.api_key}
            try:
                r = self.session.get(url, headers=headers, params={"pageSize": 1, "page": 1}, timeout=15)
            except requests.RequestException:
                continue
            if r.status_code == 200:
                self._auth_header_name = name
                self.session.headers[name] = headers[name]
                return name
        raise RuntimeError(
            "Could not authenticate against SweatPals. Tried headers: "
            + ", ".join(candidates)
            + ". Verify SWEATPALS_API_KEY and SWEATPALS_HOST are correct."
        )

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._resolve_auth_header()
        url = urljoin(self.host + "/", path.lstrip("/"))
        for attempt in range(4):
            r = self.session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    def paginate(self, path: str, max_pages: int | None = None) -> Iterator[dict]:
        """Walk every page of a Zapier-style endpoint. Yields one record at a time."""
        page = 1
        while True:
            data = self._request(path, params={"pageSize": self.page_size, "page": page})
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected response shape from {path}: {type(data).__name__}")
            if not data:
                return
            for record in data:
                yield record
            if len(data) < self.page_size:
                return
            page += 1
            if max_pages and page > max_pages:
                return

    # Convenience methods for each endpoint
    def orders(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/orders", max_pages)

    def new_tickets(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/new-tickets", max_pages)

    def used_tickets(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/new-used-tickets", max_pages)

    def claimed_tickets(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/new-claimed-tickets", max_pages)

    def waitlist(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/waitlisted-users", max_pages)

    def new_members(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/new-members", max_pages)

    def cancelled_members(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/cancelled-members", max_pages)

    def renewed_members(self, max_pages: int | None = None) -> Iterator[dict]:
        return self.paginate("/api/zapier-provider/renewed-members", max_pages)
