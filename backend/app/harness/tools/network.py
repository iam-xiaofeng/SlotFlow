"""SlotFlow read-only network tools."""

from __future__ import annotations

import asyncio
import base64
import html
import ipaddress
import json
import re
import socket
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from langchain_core.tools import BaseTool, StructuredTool

from app.harness.sandbox import SlotFlowSandboxConfig

DEFAULT_USER_AGENT = "claude-code/2.1.214"
MAX_REDIRECTS = 5
SEARCH_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("bing", "https://www.bing.com/search?q={query}"),
    ("duckduckgo_lite", "https://lite.duckduckgo.com/lite/?q={query}"),
)


class NetworkToolError(ValueError):
    """Raised when a URL is outside the SlotFlow network policy."""


def build_network_tools(
    config: SlotFlowSandboxConfig | None = None,
) -> list[BaseTool]:
    """Build read-only network tools bound by SlotFlow sandbox limits."""

    resolved_config = config or SlotFlowSandboxConfig()
    if not resolved_config.network_enabled:
        return []

    def web_fetch(url: str) -> str:
        """Fetch a public HTTP/HTTPS URL and return bounded readable content."""

        return json.dumps(
            fetch_url(url=url, config=resolved_config),
            ensure_ascii=False,
        )

    async def aweb_fetch(url: str) -> str:
        return await asyncio.to_thread(web_fetch, url)

    def web_search(query: str, max_results: int = 5) -> str:
        """Search the public web and return a compact list of result links."""

        return json.dumps(
            search_web(query=query, max_results=max_results, config=resolved_config),
            ensure_ascii=False,
        )

    async def aweb_search(query: str, max_results: int = 5) -> str:
        return await asyncio.to_thread(web_search, query, max_results)

    return [
        StructuredTool.from_function(
            func=web_fetch,
            coroutine=aweb_fetch,
            name="web_fetch",
        ),
        StructuredTool.from_function(
            func=web_search,
            coroutine=aweb_search,
            name="web_search",
        ),
    ]


def fetch_url(
    *,
    url: str,
    config: SlotFlowSandboxConfig,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Fetch one URL with redirects, size limits, and SSRF checks."""

    if not config.network_enabled:
        return error_result(url, "network tools are disabled")

    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/*, application/json, */*;q=0.2"}
    redirects: list[str] = []

    try:
        current_url = validate_public_url(url, config=config)
        with client_factory(timeout=config.network_timeout_seconds, headers=headers) as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current_url, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return error_result(current_url, "redirect_without_location")
                        next_url = validate_public_url(
                            urljoin(current_url, location),
                            config=config,
                        )
                        redirects.append(next_url)
                        current_url = next_url
                        continue

                    content = read_limited_response(response, config.max_fetch_bytes)
                    text = decode_response_text(response, content)
                    result = {
                        "url": url,
                        "final_url": str(response.url),
                        "status_code": response.status_code,
                        "content_type": response.headers.get("content-type"),
                        "title": extract_html_title(text),
                        "content": readable_text(text, response.headers.get("content-type")),
                        "truncated": len(content) >= config.max_fetch_bytes,
                        "redirects": redirects,
                        "source": "slotflow_network",
                    }
                    if include_raw:
                        result["_raw_content"] = text
                    return result

            return error_result(current_url, "too_many_redirects")
    except (httpx.HTTPError, NetworkToolError, OSError) as exc:
        return error_result(url, str(exc))


def search_web(
    *,
    query: str,
    max_results: int,
    config: SlotFlowSandboxConfig,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> dict[str, Any]:
    """Run a lightweight public web search with provider fallback."""

    stripped_query = re.sub(r"\s+", " ", query).strip()
    if not stripped_query:
        return {"query": query, "results": [], "error": "empty_query", "source": "slotflow_network"}

    safe_limit = max(1, min(max_results, 10))
    encoded_query = quote_plus(stripped_query[:200])
    attempts: list[dict[str, str]] = []
    for provider, url_template in SEARCH_PROVIDERS:
        search_url = url_template.format(query=encoded_query)
        fetched = fetch_url(
            url=search_url,
            config=config,
            include_raw=True,
            client_factory=client_factory,
        )
        if fetched.get("error"):
            attempts.append({"provider": provider, "error": str(fetched["error"])})
            continue

        results = extract_search_results(str(fetched.get("_raw_content") or ""), safe_limit)
        if results:
            return {
                "query": stripped_query,
                "results": results,
                "provider": provider,
                "source": "slotflow_network",
            }
        attempts.append({"provider": provider, "error": "no_results"})

    last_error = attempts[-1]["error"] if attempts else "no_results"
    return {
        "query": stripped_query,
        "results": [],
        "error": last_error,
        "attempts": attempts,
        "source": "slotflow_network",
    }


def validate_public_url(url: str, *, config: SlotFlowSandboxConfig) -> str:
    stripped_url = url.strip()
    parsed = urlparse(stripped_url)
    if parsed.scheme not in {"http", "https"}:
        raise NetworkToolError("only http and https URLs are allowed")
    if not parsed.hostname:
        raise NetworkToolError("URL must include a hostname")
    if not config.allow_private_network:
        assert_public_hostname(parsed.hostname)
    return stripped_url


def assert_public_hostname(hostname: str) -> None:
    if hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise NetworkToolError("private or localhost network targets are blocked")

    try:
        ip = ipaddress.ip_address(hostname)
        if is_private_address(ip):
            raise NetworkToolError("private or localhost network targets are blocked")
        return
    except ValueError:
        pass

    try:
        resolved = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkToolError(f"hostname could not be resolved: {hostname}") from exc

    for item in resolved:
        address = item[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if is_private_address(ip):
            raise NetworkToolError("private or localhost network targets are blocked")


def is_private_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def read_limited_response(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        remaining = max_bytes - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += len(chunk[:remaining])
        if total >= max_bytes:
            break
    return b"".join(chunks)


def decode_response_text(response: httpx.Response, content: bytes) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def readable_text(text: str, content_type: str | None) -> str:
    normalized_type = (content_type or "").lower()
    if "html" in normalized_type:
        return compact_text(strip_html(text))
    return compact_text(text)


def extract_html_title(text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    return compact_text(html.unescape(match.group(1)))[:200] or None


def extract_search_results(content: str, max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen_candidates: set[tuple[str, str]] = set()
    candidate_patterns = (
        r"(?is)<h2[^>]*>\s*<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>\s*</h2>",
        r"(?is)<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    )
    for pattern in candidate_patterns:
        for match in re.finditer(pattern, content):
            candidate = (match.group(1), match.group(2))
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            append_search_result(results, candidate[0], candidate[1], max_results)
            if len(results) >= max_results:
                return results
    return results


def append_search_result(
    results: list[dict[str, str]],
    raw_href: str,
    raw_title: str,
    max_results: int,
) -> None:
    href = html.unescape(raw_href)
    title = clean_search_result_title(compact_text(strip_html(raw_title)))
    if not title:
        return
    url = normalize_search_result_url(href)
    if not url.startswith(("http://", "https://")):
        return
    if not is_external_search_result_url(url):
        return
    if any(item["url"] == url for item in results):
        return
    results.append({"title": title[:200], "url": url})
    if len(results) > max_results:
        del results[max_results:]


def clean_search_result_title(title: str) -> str:
    lines = [line.strip() for line in title.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) >= 2 and looks_like_url_or_domain(lines[0]):
        return compact_text(" ".join(lines[1:]))
    return compact_text(" ".join(lines))


def looks_like_url_or_domain(value: str) -> bool:
    return bool(re.match(r"(?i)^(https?://|www\.|[a-z0-9.-]+\.[a-z]{2,})(?:\s|/|$)", value))


def is_external_search_result_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"www.bing.com", "bing.com", "cn.bing.com"}:
        return False
    if hostname.endswith(".duckduckgo.com") or hostname == "duckduckgo.com":
        return False
    return True


def normalize_search_result_url(url: str) -> str:
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return unquote(target)
    if hostname in {"www.bing.com", "bing.com", "cn.bing.com"} and parsed.path.startswith("/ck/"):
        target = parse_qs(parsed.query).get("u", [None])[0]
        decoded_target = decode_bing_result_target(target)
        if decoded_target:
            return urljoin("https://www.bing.com", decoded_target)
    return url


def decode_bing_result_target(target: str | None) -> str | None:
    if not target:
        return None
    unquoted = unquote(target)
    if unquoted.startswith(("http://", "https://", "/")):
        return unquoted
    encoded = unquoted[2:] if unquoted.startswith("a1") else unquoted
    padding = "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{encoded}{padding}").decode(
            "utf-8",
            errors="replace",
        )
    except (ValueError, OSError):
        return None
    if decoded.startswith(("http://", "https://", "/")):
        return decoded
    return None


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in {"p", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def strip_html(text: str) -> str:
    parser = TextExtractor()
    parser.feed(text)
    return html.unescape(" ".join(parser.parts))


def compact_text(text: str, *, max_chars: int = 12000) -> str:
    compact = re.sub(r"[ \t\r\f\v]+", " ", text)
    compact = re.sub(r"\n\s+", "\n", compact)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 1]}..."


def error_result(url: str, error: str) -> dict[str, Any]:
    return {
        "url": url,
        "error": error,
        "source": "slotflow_network",
    }
