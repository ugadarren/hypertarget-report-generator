from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "HyperTargetReportBot/1.0 (+https://localhost)"
PAGE_TIMEOUT = 12

PRIORITY_LINK_TERMS = [
    "news",
    "newsroom",
    "press",
    "media",
    "about",
    "locations",
    "facility",
    "facilities",
    "expansion",
    "investor",
]


@dataclass
class WebResearchResult:
    text: str
    snippets: list[str]
    discovered_addresses: list[str]
    source_log: list[dict]


def _clean_text(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw)
    return text.strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.lower().strip()
        if key and key not in seen:
            seen.add(key)
            output.append(value.strip())
    return output


def _same_host(url_a: str, url_b: str) -> bool:
    return (urlparse(url_a).netloc or "").lower() == (urlparse(url_b).netloc or "").lower()


def _extract_addresses(soup: BeautifulSoup, visible_text: str) -> list[str]:
    matches: list[str] = []

    for tag in soup.find_all("address"):
        cleaned = _clean_text(tag.get_text(" "))
        if len(cleaned) > 8:
            matches.append(cleaned)

    for link in soup.find_all("a", href=True):
        href = link["href"]
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        for key in ("q", "query", "daddr", "destination"):
            if key in query:
                for value in query[key]:
                    value = _clean_text(unquote(value))
                    if len(value) > 8:
                        matches.append(value)

    pattern = re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.\-#'\s]+,\s*[A-Za-z.\-'\s]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"
    )
    matches.extend(pattern.findall(visible_text))

    return _dedupe(matches)[:20]


def _extract_candidate_links(soup: BeautifulSoup, base_url: str, limit: int = 12) -> list[str]:
    scored: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        absolute = urldefrag(urljoin(base_url, href)).url
        if not absolute.startswith("http"):
            continue
        if not _same_host(base_url, absolute):
            continue

        marker = f"{absolute} {(a.get_text(' ') or '').lower()}"
        score = sum(1 for term in PRIORITY_LINK_TERMS if term in marker.lower())
        if score > 0:
            scored.append((score, absolute))

    scored.sort(key=lambda item: item[0], reverse=True)
    ordered = [url for _, url in scored]
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    fallback_paths = [
        "/news",
        "/newsroom",
        "/press",
        "/media",
        "/about",
        "/locations",
    ]
    ordered.extend(f"{root}{path}" for path in fallback_paths)
    return _dedupe(ordered)[:limit]


def _fetch_and_parse(url: str, session: requests.Session) -> tuple[BeautifulSoup | None, str, str | None]:
    try:
        response = session.get(url, timeout=PAGE_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        return None, "", f"Page fetch failed: {exc}"

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    visible_text = _clean_text(soup.get_text(" "))
    return soup, visible_text, None


def scrape_website(url: str | None, max_snippets: int = 20, max_pages: int = 6) -> WebResearchResult:
    if not url:
        return WebResearchResult(text="", snippets=[], discovered_addresses=[], source_log=[])

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    all_text_parts: list[str] = []
    all_addresses: list[str] = []
    source_log: list[dict] = []

    homepage_soup, homepage_text, homepage_err = _fetch_and_parse(str(url), session)
    if homepage_err:
        return WebResearchResult(
            text="",
            snippets=[],
            discovered_addresses=[],
            source_log=[{"source": str(url), "type": "error", "detail": homepage_err}],
        )

    all_text_parts.append(homepage_text)
    all_addresses.extend(_extract_addresses(homepage_soup, homepage_text))
    source_log.append({"source": str(url), "type": "website_page", "detail": "Parsed homepage"})

    visited = {str(url)}
    candidate_links = _extract_candidate_links(homepage_soup, str(url), limit=max_pages * 2)

    for link in candidate_links:
        if len(visited) >= max_pages:
            break
        if link in visited:
            continue

        soup, page_text, err = _fetch_and_parse(link, session)
        visited.add(link)

        if err:
            source_log.append({"source": link, "type": "error", "detail": err})
            continue

        all_text_parts.append(page_text)
        all_addresses.extend(_extract_addresses(soup, page_text))
        source_log.append({"source": link, "type": "website_page", "detail": "Parsed related page"})

    combined_text = " ".join(all_text_parts)
    sentences = [s.strip() for s in re.split(r"[.!?]", combined_text) if len(s.strip()) > 40]
    snippets = _dedupe(sentences)[:max_snippets]
    discovered_addresses = _dedupe(all_addresses)[:30]

    source_log.append(
        {
            "source": str(url),
            "type": "extract",
            "detail": f"Crawled {len(visited)} page(s), detected {len(discovered_addresses)} address candidate(s)",
        }
    )

    return WebResearchResult(
        text=combined_text.lower(),
        snippets=snippets,
        discovered_addresses=discovered_addresses,
        source_log=source_log,
    )
