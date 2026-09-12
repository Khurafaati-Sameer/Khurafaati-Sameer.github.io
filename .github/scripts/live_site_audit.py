#!/usr/bin/env python3
"""Live HTTP integrity checks for the deployed GitHub Pages site."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.request import Request, urlopen

BASE = "https://khurafaati-sameer.github.io/"
TIMEOUT = 20

SKIP_SCHEMES = {"mailto", "tel", "javascript", "data", "blob"}

class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self.ids: set[str] = set()
        self.canonical: list[str] = []
        self.noindex = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        for attr in ("href", "src", "poster"):
            if a.get(attr):
                self.urls.append(a[attr])
        if tag == "link" and (a.get("rel") or "").lower() == "canonical" and a.get("href"):
            self.canonical.append(a["href"])
        if tag == "meta" and (a.get("name") or "").lower() == "robots" and "noindex" in (a.get("content") or "").lower():
            self.noindex = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def fetch(url: str):
    req = Request(url, headers={"User-Agent": "Khurafaati-Sameer-Site-Audit/1.0"})
    with urlopen(req, timeout=TIMEOUT) as r:
        body = r.read()
        return r.status, r.geturl(), r.headers.get_content_type(), body


def norm(url: str) -> str:
    return urldefrag(url)[0]


def main() -> int:
    errors: list[str] = []
    print(f"Live site audit: {BASE}")

    status, final_url, ctype, body = fetch(BASE)
    if status != 200:
        errors.append(f"HOME STATUS: {status} {BASE}")
    if ctype != "text/html":
        errors.append(f"HOME CONTENT-TYPE: {ctype}")

    root = Parser()
    root.feed(body.decode("utf-8", errors="replace"))
    pages = {BASE}
    # Crawl only same-origin HTML pages discovered from the homepage, then recursively crawl them.
    queue = [BASE]
    seen = set()
    checked_assets = set()

    while queue:
        page = queue.pop(0)
        page = norm(page)
        if page in seen:
            continue
        seen.add(page)
        try:
            st, final, ct, raw = fetch(page)
        except Exception as exc:
            errors.append(f"FETCH ERROR: {page} -> {exc}")
            continue
        if st < 200 or st >= 400:
            errors.append(f"PAGE STATUS: {st} {page}")
            continue
        if ct != "text/html":
            continue

        p = Parser()
        p.feed(raw.decode("utf-8", errors="replace"))
        if p.noindex:
            continue
        pages.add(page)

        expected = page
        if not p.canonical:
            errors.append(f"LIVE CANONICAL MISSING: {page}")
        elif len(p.canonical) != 1:
            errors.append(f"LIVE CANONICAL COUNT: {page} -> {len(p.canonical)}")
        elif norm(urljoin(page, p.canonical[0])) != expected:
            errors.append(f"LIVE CANONICAL MISMATCH: {page} -> {p.canonical[0]}")

        for raw_url in p.urls:
            raw_url = raw_url.strip()
            if not raw_url or raw_url.startswith("#"):
                continue
            parts = urlsplit(raw_url)
            if parts.scheme.lower() in SKIP_SCHEMES:
                continue
            target = urljoin(page, raw_url)
            target_no_frag = norm(target)
            tparts = urlsplit(target_no_frag)
            if tparts.netloc and tparts.netloc != "khurafaati-sameer.github.io":
                continue

            try:
                st2, final2, ct2, raw2 = fetch(target_no_frag)
            except Exception as exc:
                errors.append(f"LIVE BROKEN: {page} -> {raw_url} -> {exc}")
                continue
            if st2 < 200 or st2 >= 400:
                errors.append(f"LIVE STATUS: {st2}: {page} -> {raw_url}")
                continue

            if tparts.fragment and ct2 == "text/html":
                tp = Parser()
                tp.feed(raw2.decode("utf-8", errors="replace"))
                if tparts.fragment not in tp.ids:
                    errors.append(f"LIVE BROKEN ANCHOR: {page} -> {raw_url}")

            if ct2 == "text/html" and target_no_frag not in seen and target_no_frag not in queue:
                queue.append(target_no_frag)
            elif ct2 != "text/html":
                checked_assets.add(target_no_frag)

    print(f"Live indexable pages discovered: {len(pages)}")
    print(f"Live assets checked: {len(checked_assets)}")
    print(f"Errors: {len(errors)}")
    if errors:
        print("\nFAILURES:")
        for e in errors:
            print(f"- {e}")
        return 1
    print("PASS: live HTTP pages, same-origin assets, anchors and canonicals are healthy.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"AUDIT ERROR: {exc}")
        sys.exit(1)
