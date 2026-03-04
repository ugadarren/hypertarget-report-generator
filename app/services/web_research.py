from __future__ import annotations

import json
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
    weighted_texts: dict[str, str]
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

    for frame in soup.find_all("iframe", src=True):
        src = frame["src"]
        parsed = urlparse(src)
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

    # Variant for addresses that omit commas.
    pattern_no_commas = re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.\-#'\s]{3,60}\s+[A-Za-z.\-'\s]{2,40}\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"
    )
    matches.extend(pattern_no_commas.findall(visible_text))

    # Pull structured addresses from JSON-LD (Organization/LocalBusiness schemas).
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            addr = node.get("address")
            if isinstance(addr, dict):
                parts = [
                    addr.get("streetAddress"),
                    addr.get("addressLocality"),
                    addr.get("addressRegion"),
                    addr.get("postalCode"),
                ]
                assembled = ", ".join([str(p).strip() for p in parts if p])
                if len(assembled) > 8:
                    matches.append(assembled)

    # Parse all inline script text for address-like strings used in hydrated state.
    all_scripts = " ".join([(s.get_text() or "") for s in soup.find_all("script")])
    matches.extend(pattern.findall(all_scripts))
    matches.extend(pattern_no_commas.findall(all_scripts))

    return _dedupe(matches)[:20]


def _extract_summary_candidates(soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []

    for meta_name in ("description",):
        tag = soup.find("meta", attrs={"name": meta_name})
        if tag and tag.get("content"):
            content = _clean_text(str(tag["content"]))
            if 30 <= len(content) <= 320:
                candidates.append(content)

    og_tag = soup.find("meta", attrs={"property": "og:description"})
    if og_tag and og_tag.get("content"):
        content = _clean_text(str(og_tag["content"]))
        if 30 <= len(content) <= 320:
            candidates.append(content)

    for tag in soup.find_all(["p", "h1", "h2"], limit=80):
        text = _clean_text(tag.get_text(" "))
        if 45 <= len(text) <= 320:
            candidates.append(text)

    return _dedupe(candidates)[:30]


def _extract_weighted_fields(soup: BeautifulSoup) -> dict[str, str]:
    title = _clean_text((soup.title.get_text(" ") if soup.title else ""))
    meta_desc = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        meta_desc = _clean_text(str(meta["content"]))
    else:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            meta_desc = _clean_text(str(og["content"]))

    headings = _clean_text(" ".join(h.get_text(" ") for h in soup.find_all(["h1", "h2", "h3"], limit=30)))
    links = _clean_text(" ".join(a.get_text(" ") for a in soup.find_all("a", limit=120)))
    paragraphs = _clean_text(" ".join(p.get_text(" ") for p in soup.find_all("p", limit=120)))

    return {
        "title": title,
        "meta": meta_desc,
        "headings": headings,
        "links": links,
        "paragraphs": paragraphs,
    }


def _extract_sitemap_links(base_url: str, session: requests.Session, limit: int = 30) -> list[str]:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = f"{root}/sitemap.xml"
    try:
        response = session.get(sitemap_url, timeout=PAGE_TIMEOUT)
        response.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(response.text, "xml")
    links = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    filtered: list[str] = []
    for link in links:
        if not link.startswith("http"):
            continue
        if not _same_host(base_url, link):
            continue
        marker = link.lower()
        if any(
            term in marker
            for term in (
                "location",
                "contact",
                "office",
                "about",
                "facility",
                "news",
                "press",
            )
        ):
            filtered.append(link)
    return _dedupe(filtered)[:limit]


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


def scrape_website(url: str | None, max_snippets: int = 30, max_pages: int = 10) -> WebResearchResult:
    if not url:
        return WebResearchResult(text="", snippets=[], discovered_addresses=[], weighted_texts={}, source_log=[])

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    all_text_parts: list[str] = []
    all_addresses: list[str] = []
    all_summary_candidates: list[str] = []
    weighted_buckets: dict[str, list[str]] = {
        "title": [],
        "meta": [],
        "headings": [],
        "links": [],
        "paragraphs": [],
    }
    source_log: list[dict] = []

    homepage_soup, homepage_text, homepage_err = _fetch_and_parse(str(url), session)
    if homepage_err:
        return WebResearchResult(
            text="",
            snippets=[],
            discovered_addresses=[],
            weighted_texts={},
            source_log=[{"source": str(url), "type": "error", "detail": homepage_err}],
        )

    all_text_parts.append(homepage_text)
    all_addresses.extend(_extract_addresses(homepage_soup, homepage_text))
    all_summary_candidates.extend(_extract_summary_candidates(homepage_soup))
    for key, value in _extract_weighted_fields(homepage_soup).items():
        if value:
            weighted_buckets[key].append(value)
    source_log.append({"source": str(url), "type": "website_page", "detail": "Parsed homepage"})

    visited = {str(url)}
    candidate_links = _extract_candidate_links(homepage_soup, str(url), limit=max_pages * 2)
    sitemap_links = _extract_sitemap_links(str(url), session, limit=max_pages * 3)
    candidate_links = _dedupe(candidate_links + sitemap_links)

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
        all_summary_candidates.extend(_extract_summary_candidates(soup))
        for key, value in _extract_weighted_fields(soup).items():
            if value:
                weighted_buckets[key].append(value)
        source_log.append({"source": link, "type": "website_page", "detail": "Parsed related page"})

    combined_text = " ".join(all_text_parts)
    sentences = [s.strip() for s in re.split(r"[.!?]", combined_text) if len(s.strip()) > 40]
    snippets = _dedupe(all_summary_candidates + sentences)[:max_snippets]
    discovered_addresses = _dedupe(all_addresses)[:30]
    weighted_texts = {k: _clean_text(" ".join(v)) for k, v in weighted_buckets.items() if v}

    source_log.append(
        {
            "source": str(url),
            "type": "extract",
            "detail": (
                f"Crawled {len(visited)} page(s), checked {len(candidate_links)} candidate links, "
                f"detected {len(discovered_addresses)} address candidate(s)"
            ),
        }
    )

    return WebResearchResult(
        text=combined_text.lower(),
        snippets=snippets,
        discovered_addresses=discovered_addresses,
        weighted_texts=weighted_texts,
        source_log=source_log,
    )
