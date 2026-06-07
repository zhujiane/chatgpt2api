from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_CLEARANCE_CONFIG = {
    "mode": "none",
    "target_url": "https://auth.openai.com",
    "flaresolverr_url": "",
    "timeout_sec": 60,
    "refresh_interval": 600,
    "cf_cookies": "",
    "user_agent": "",
}

CLOUDFLARE_COOKIE_PREFIXES = ("__cf", "_cf", "cf_")


@dataclass(frozen=True)
class ClearanceCookie:
    name: str
    value: str
    domain: str
    path: str = "/"


@dataclass(frozen=True)
class ClearanceBundle:
    cookies: tuple[ClearanceCookie, ...]
    cf_cookies: str
    user_agent: str
    affinity_key: str
    clearance_host: str
    created_at: float


def normalize_clearance_config(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    mode = str(source.get("mode") or DEFAULT_CLEARANCE_CONFIG["mode"]).strip().lower()
    if mode not in {"none", "manual", "flaresolverr"}:
        mode = "none"
    return {
        "mode": mode,
        "target_url": str(source.get("target_url") or DEFAULT_CLEARANCE_CONFIG["target_url"]).strip()
        or DEFAULT_CLEARANCE_CONFIG["target_url"],
        "flaresolverr_url": str(source.get("flaresolverr_url") or "").strip().rstrip("/"),
        "timeout_sec": _positive_int(source.get("timeout_sec"), int(DEFAULT_CLEARANCE_CONFIG["timeout_sec"]), 1),
        "refresh_interval": _positive_int(
            source.get("refresh_interval"),
            int(DEFAULT_CLEARANCE_CONFIG["refresh_interval"]),
            1,
        ),
        "cf_cookies": str(source.get("cf_cookies") or "").strip(),
        "user_agent": str(source.get("user_agent") or "").strip(),
    }


def build_sec_ch_headers(user_agent: str) -> dict[str, str]:
    major, full = _chrome_version(user_agent)
    brand = "Google Chrome" if "Chrome/" in user_agent and "Edg/" not in user_agent else "Chromium"
    platform, platform_version, arch = _platform_hints(user_agent)
    return {
        "sec-ch-ua": f'"{brand}";v="{major}", "Not?A_Brand";v="8", "Chromium";v="{major}"',
        "sec-ch-ua-full-version-list": (
            f'"Chromium";v="{full}", "Not:A-Brand";v="99.0.0.0", "{brand}";v="{full}"'
        ),
        "sec-ch-ua-platform": f'"{platform}"',
        "sec-ch-ua-platform-version": f'"{platform_version}"',
        "sec-ch-ua-arch": f'"{arch}"',
        "sec-ch-ua-bitness": '"64"',
    }


def apply_user_agent(headers: dict[str, str], user_agent: str) -> dict[str, str]:
    if not user_agent:
        return headers
    headers["user-agent"] = user_agent
    headers.update(build_sec_ch_headers(user_agent))
    return headers


def apply_bundle_to_session(session: Any, bundle: ClearanceBundle | None) -> None:
    if not bundle:
        return
    jar = getattr(session, "cookies", None)
    if jar is None:
        return
    for item in bundle.cookies:
        try:
            jar.set(item.name, item.value, domain=item.domain, path=item.path or "/")
        except Exception:
            try:
                jar.set(item.name, item.value)
            except Exception:
                pass


def merge_cookie_header(existing: str, extra: str) -> str:
    parts = [part.strip() for part in (existing or "").split(";") if part.strip()]
    seen = {part.split("=", 1)[0].strip() for part in parts if "=" in part}
    for part in [item.strip() for item in (extra or "").split(";") if item.strip()]:
        name = part.split("=", 1)[0].strip()
        if name and name not in seen:
            parts.append(part)
            seen.add(name)
    return "; ".join(parts)


class RegisterClearanceStore:
    def __init__(self) -> None:
        self._bundles: dict[tuple[str, str], ClearanceBundle] = {}
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, settings: dict[str, object], proxy_url: str = "", *, force_refresh: bool = False) -> ClearanceBundle | None:
        normalized = normalize_clearance_config(settings)
        mode = str(normalized["mode"])
        if mode == "none":
            return None
        target_url = str(normalized["target_url"])
        clearance_host = _host_from_url(target_url)
        affinity_key = _affinity_key(proxy_url)
        key = (affinity_key, clearance_host)

        if mode == "manual":
            return self._manual_bundle(normalized, key)

        with self._lock_for(key):
            bundle = self._bundles.get(key)
            if bundle and not force_refresh and not self._is_stale(bundle, normalized):
                return bundle
            bundle = self._solve_flaresolverr(normalized, proxy_url, key)
            self._bundles[key] = bundle
            return bundle

    def invalidate(self, settings: dict[str, object], proxy_url: str = "") -> None:
        normalized = normalize_clearance_config(settings)
        key = (_affinity_key(proxy_url), _host_from_url(str(normalized["target_url"])))
        with self._guard:
            self._bundles.pop(key, None)

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _is_stale(self, bundle: ClearanceBundle, settings: dict[str, object]) -> bool:
        return time.time() - bundle.created_at >= int(settings["refresh_interval"])

    def _manual_bundle(self, settings: dict[str, object], key: tuple[str, str]) -> ClearanceBundle | None:
        cf_cookies = str(settings["cf_cookies"]).strip()
        user_agent = str(settings["user_agent"]).strip()
        if not cf_cookies and not user_agent:
            return None
        cookies = _cookies_from_header(cf_cookies, key[1])
        cf_cookies = _cookie_header(cookies)
        return ClearanceBundle(
            cookies=tuple(cookies),
            cf_cookies=cf_cookies,
            user_agent=user_agent,
            affinity_key=key[0],
            clearance_host=key[1],
            created_at=time.time(),
        )

    def _solve_flaresolverr(
        self,
        settings: dict[str, object],
        proxy_url: str,
        key: tuple[str, str],
    ) -> ClearanceBundle:
        flaresolverr_url = str(settings["flaresolverr_url"]).strip().rstrip("/")
        if not flaresolverr_url:
            raise RuntimeError("register clearance flaresolverr_url is required")
        timeout_sec = int(settings["timeout_sec"])
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": str(settings["target_url"]),
            "maxTimeout": timeout_sec * 1000,
        }
        if proxy_url:
            payload["proxy"] = {"url": proxy_url}
        response = requests.post(f"{flaresolverr_url}/v1", json=payload, timeout=timeout_sec + 10)
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"flaresolverr returned non-json response: HTTP {response.status_code}") from exc
        if response.status_code != 200 or data.get("status") != "ok":
            message = str(data.get("message") or data.get("error") or "")[:300]
            raise RuntimeError(f"flaresolverr solve failed: HTTP {response.status_code}{': ' + message if message else ''}")
        solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
        raw_cookies = solution.get("cookies") if isinstance(solution, dict) else []
        cookies = _normalize_solution_cookies(raw_cookies, key[1])
        cf_cookies = _cookie_header(cookies)
        user_agent = str(solution.get("userAgent") or "").strip()
        if not cf_cookies and not user_agent:
            raise RuntimeError("flaresolverr solution did not include cookies or userAgent")
        return ClearanceBundle(
            cookies=tuple(cookies),
            cf_cookies=cf_cookies,
            user_agent=user_agent,
            affinity_key=key[0],
            clearance_host=key[1],
            created_at=time.time(),
        )


def _positive_int(value: object, default: int, minimum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(minimum, normalized)


def _host_from_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "auth.openai.com").lower()


def _affinity_key(proxy_url: str) -> str:
    return str(proxy_url or "").strip() or "direct"


def _domain_matches(cookie_domain: str, host: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    return not domain or host == domain or host.endswith(f".{domain}")


def _normalize_solution_cookies(raw_cookies: object, host: str) -> list[ClearanceCookie]:
    if not isinstance(raw_cookies, list):
        return []
    cookies: list[ClearanceCookie] = []
    for raw in raw_cookies:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        value = str(raw.get("value") or "").strip()
        domain = str(raw.get("domain") or host).strip() or host
        path = str(raw.get("path") or "/").strip() or "/"
        if not name or not value or not _is_cloudflare_cookie(name) or not _domain_matches(domain, host):
            continue
        cookies.append(ClearanceCookie(name=name, value=value, domain=domain, path=path))
    return cookies


def _cookies_from_header(cookie_header: str, host: str) -> list[ClearanceCookie]:
    cookies: list[ClearanceCookie] = []
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and value and _is_cloudflare_cookie(name):
            cookies.append(ClearanceCookie(name=name, value=value, domain=host))
    return cookies


def _cookie_header(cookies: list[ClearanceCookie]) -> str:
    return "; ".join(f"{item.name}={item.value}" for item in cookies)


def _is_cloudflare_cookie(name: str) -> bool:
    normalized = name.strip().lower()
    return normalized.startswith(CLOUDFLARE_COOKIE_PREFIXES)


def _chrome_version(user_agent: str) -> tuple[str, str]:
    match = re.search(r"(?:Chrome|Chromium|Edg)/([0-9]+(?:\.[0-9]+){0,3})", user_agent)
    full = match.group(1) if match else "145.0.0.0"
    parts = full.split(".")
    major = parts[0] if parts and parts[0].isdigit() else "145"
    full = ".".join((parts + ["0", "0", "0", "0"])[:4])
    return major, full


def _platform_hints(user_agent: str) -> tuple[str, str, str]:
    if "Windows" in user_agent:
        return "Windows", "10.0.0", "x86_64"
    if "Mac OS X" in user_agent or "Macintosh" in user_agent:
        return "macOS", "15.0.0", "arm" if "Arm" in user_agent or "ARM" in user_agent else "x86"
    if "Linux" in user_agent or "X11" in user_agent:
        return "Linux", "", "x86"
    return "Windows", "10.0.0", "x86_64"


register_clearance_store = RegisterClearanceStore()
