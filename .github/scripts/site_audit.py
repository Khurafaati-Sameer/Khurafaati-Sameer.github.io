#!/usr/bin/env python3
"""Static integrity checks for the Khurafaati Sameer GitHub Pages site."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://khurafaati-sameer.github.io/"
SKIP_SCHEMES = {"http", "https", "mailto", "tel", "javascript", "data", "blob"}
SKIP_DIRS = {".git", "node_modules"}


class HTMLAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.canonical: list[str] = []
        self.noindex = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"] or "")
        if tag == "a" and a.get("href"):
            self.links.append(("href", a["href"] or ""))
        for attr in ("src", "poster"):
            if a.get(attr):
                self.links.append((attr, a[attr] or ""))
        if tag == "link" and a.get("href"):
            self.links.append(("link", a["href"] or ""))
            if (a.get("rel") or "").lower() == "canonical":
                self.canonical.append(a["href"] or "")
        if tag == "meta":
            name = (a.get("name") or "").lower()
            content = (a.get("content") or "").lower()
            if name == "robots" and "noindex" in content:
                self.noindex = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def html_files() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.html")
        if not any(part in SKIP_DIRS for part in p.parts)
    )


def public_url(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if rel == "index.html":
        return BASE
    if rel.endswith("/index.html"):
        return BASE + rel[:-len("index.html")]
    return BASE + rel


def path_from_public_url(url: str) -> Path | None:
    parts = urlsplit(url)
    if parts.scheme and parts.scheme not in {"http", "https"}:
        return None
    if parts.scheme in {"http", "https"} and (parts.netloc or "") not in {"", "khurafaati-sameer.github.io"}:
        return None
    raw = unquote(parts.path or "/")
    if raw == "/":
        return ROOT / "index.html"
    rel = raw.lstrip("/")
    if rel.endswith("/"):
        rel += "index.html"
    return ROOT / rel


def resolve_local_url(source: Path, href: str) -> tuple[Path | None, str | None]:
    raw = href.strip()
    if not raw:
        return None, None
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme in SKIP_SCHEMES:
        return None, None
    if raw.startswith("//") or scheme in {"http", "https"}:
        if parts.netloc and parts.netloc != "khurafaati-sameer.github.io":
            return None, None
        return path_from_public_url(raw), parts.fragment
    if raw.startswith("/"):
        return path_from_public_url(BASE.rstrip("/") + raw), parts.fragment
    if parts.path == "":
        # Query-only or fragment-only URLs stay on the current document.
        return source, parts.fragment
    target_raw = unquote(parts.path)
    target = (source.parent / target_raw).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        return None, parts.fragment
    if target.is_dir():
        target = target / "index.html"
    return target, parts.fragment


def read_parser(path: Path) -> HTMLAuditParser:
    parser = HTMLAuditParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.close()
    return parser


def load_sitemap() -> set[str]:
    sitemap = ROOT / "sitemap.xml"
    if not sitemap.exists():
        raise RuntimeError("sitemap.xml is missing")
    root = ET.fromstring(sitemap.read_text(encoding="utf-8"))
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return {loc.text.strip() for loc in root.findall("sm:url/sm:loc", ns) if loc.text and loc.text.strip()}


def main() -> int:
    errors: list[str] = []
    pages = html_files()
    page_set = {p.resolve() for p in pages}
    parsed: dict[Path, HTMLAuditParser] = {}

    print(f"Site audit: {len(pages)} HTML pages")

    for page in pages:
        parser = read_parser(page)
        parsed[page] = parser
        for kind, raw in parser.links:
            target, fragment = resolve_local_url(page, raw)
            if target is None:
                continue
            if not target.exists():
                errors.append(f"BROKEN {kind}: {page.relative_to(ROOT)} -> {raw}")
                continue
            if target.is_file() and target.suffix.lower() == ".html" and target.resolve() not in page_set:
                errors.append(f"BROKEN HTML: {page.relative_to(ROOT)} -> {raw}")
            if fragment and target.suffix.lower() == ".html":
                target_parser = parsed.get(target) or read_parser(target)
                parsed[target] = target_parser
                if unquote(fragment) not in target_parser.ids:
                    errors.append(f"BROKEN ANCHOR: {page.relative_to(ROOT)} -> {raw}")

    canonical_map: dict[str, list[Path]] = {}
    indexable: set[Path] = set()
    for page in pages:
        parser = parsed[page]
        if parser.noindex:
            continue
        indexable.add(page)
        expected = public_url(page)
        if len(parser.canonical) != 1:
            errors.append(f"CANONICAL COUNT: {page.relative_to(ROOT)} has {len(parser.canonical)}; expected exactly 1")
            continue
        actual = parser.canonical[0].strip()
        if actual != expected:
            errors.append(f"CANONICAL MISMATCH: {page.relative_to(ROOT)} -> {actual} (expected {expected})")
        canonical_map.setdefault(actual, []).append(page)

    for canonical, owners in canonical_map.items():
        if len(owners) > 1:
            names = ", ".join(p.relative_to(ROOT).as_posix() for p in owners)
            errors.append(f"DUPLICATE CANONICAL: {canonical} <- {names}")

    sitemap_urls = load_sitemap()
    expected_sitemap = {public_url(p) for p in indexable}
    for url in sorted(expected_sitemap - sitemap_urls):
        errors.append(f"SITEMAP MISSING INDEXABLE PAGE: {url}")
    for url in sorted(sitemap_urls - expected_sitemap):
        target = path_from_public_url(url)
        if target and target.exists() and target in parsed and parsed[target].noindex:
            errors.append(f"SITEMAP CONTAINS NOINDEX PAGE: {url}")
        elif target is None or not target.exists():
            errors.append(f"SITEMAP URL DOES NOT RESOLVE LOCALLY: {url}")
        else:
            errors.append(f"SITEMAP EXTRA URL: {url}")

    robots = ROOT / "robots.txt"
    if not robots.exists():
        errors.append("robots.txt is missing")
    else:
        robots_text = robots.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?im)^\s*Sitemap:\s*\S+\s*$", robots_text) is None:
            errors.append("robots.txt has no Sitemap directive")
        elif BASE + "sitemap.xml" not in robots_text:
            errors.append("robots.txt Sitemap directive does not point to the expected sitemap.xml")

    print(f"Indexable HTML pages: {len(indexable)}")
    print(f"Sitemap URLs: {len(sitemap_urls)}")
    print(f"Errors: {len(errors)}")

    if errors:
        print("\nFAILURES:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("\nPASS: internal links/assets, anchors, canonicals, sitemap parity and robots sitemap are consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
