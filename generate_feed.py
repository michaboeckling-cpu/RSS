#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

BASE_URL = "https://oeffentlichevergabe.de/api/notice-exports"
CONFIG_FILE = Path(__file__).with_name("config.json")
OUTPUT_FILE = Path(__file__).with_name("rss.xml")

USER_AGENT = "VergabeRSS/1.0 (+personal RSS feed generator)"


def load_config() -> dict:
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("feed_title", "Ausschreibungen: Social Media")
    cfg.setdefault("feed_description", "Gefilterte öffentliche Ausschreibungen aus dem Datenservice Öffentlicher Einkauf")
    cfg.setdefault("keywords", ["Social Media", "Social-Media"])
    cfg.setdefault("lookback_days", 30)
    cfg.setdefault("max_items", 100)
    cfg.setdefault("require_all_keywords", False)
    cfg.setdefault("allowed_tags", ["tender", "tenderUpdate"])
    return cfg


def fetch_daily_export(day: date) -> bytes | None:
    # The official API documents bulk notice exports. The pubDay query is used
    # to request a single publication day, with OCDS ZIP output.
    params = urlencode({"pubDay": day.isoformat(), "format": "ocds.zip"})
    url = f"{BASE_URL}?{params}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/zip,*/*"})
    try:
        with urlopen(req, timeout=60) as r:
            return r.read()
    except HTTPError as e:
        if e.code in (400, 404):
            return None
        print(f"HTTP error for {day}: {e}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"Network error for {day}: {e}", file=sys.stderr)
        return None


def iter_json_documents(zip_bytes: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("/") or not name.lower().endswith((".json", ".jsonl", ".ndjson")):
                    continue
                raw = zf.read(name)
                text = raw.decode("utf-8-sig", errors="replace").strip()
                if not text:
                    continue
                if name.lower().endswith((".jsonl", ".ndjson")):
                    for line in text.splitlines():
                        line = line.strip()
                        if line:
                            try:
                                yield json.loads(line)
                            except json.JSONDecodeError:
                                pass
                else:
                    try:
                        yield json.loads(text)
                    except json.JSONDecodeError:
                        pass
    except zipfile.BadZipFile:
        # Defensive fallback in case the server returns JSON directly.
        try:
            yield json.loads(zip_bytes.decode("utf-8-sig", errors="replace"))
        except Exception:
            return


def iter_releases(obj):
    """Yield OCDS-like releases from common package/record shapes."""
    if isinstance(obj, list):
        for item in obj:
            yield from iter_releases(item)
        return

    if not isinstance(obj, dict):
        return

    if isinstance(obj.get("releases"), list):
        for release in obj["releases"]:
            if isinstance(release, dict):
                yield release

    if isinstance(obj.get("records"), list):
        for record in obj["records"]:
            if not isinstance(record, dict):
                continue
            compiled = record.get("compiledRelease")
            if isinstance(compiled, dict):
                yield compiled
            if isinstance(record.get("releases"), list):
                for release in record["releases"]:
                    if isinstance(release, dict):
                        yield release

    # Individual release fallback
    if any(k in obj for k in ("ocid", "tender", "buyer", "tag")) and "records" not in obj and "releases" not in obj:
        yield obj


def flatten_text(value) -> str:
    parts = []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                # Skip very large/non-descriptive fields if present.
                if str(k).lower() in {"document", "binary", "base64"}:
                    continue
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, (str, int, float)):
            parts.append(str(v))

    walk(value)
    return "\n".join(parts)


def normalize(s: str) -> str:
    s = s.casefold()
    s = re.sub(r"[-_/]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def matches_keywords(release: dict, keywords: list[str], require_all: bool) -> bool:
    haystack = normalize(flatten_text(release))
    checks = [normalize(k) in haystack for k in keywords if k.strip()]
    if not checks:
        return True
    return all(checks) if require_all else any(checks)


def tags_of(release: dict) -> set[str]:
    tags = release.get("tag", [])
    if isinstance(tags, str):
        tags = [tags]
    return {str(t) for t in tags if t is not None}


def get_title(release: dict) -> str:
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    for value in (
        tender.get("title"),
        release.get("title"),
        tender.get("description"),
        release.get("description"),
    ):
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:300]
    return "Öffentliche Ausschreibung"


def get_buyer(release: dict) -> str:
    buyer = release.get("buyer")
    if isinstance(buyer, dict):
        name = buyer.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    parties = release.get("parties")
    if isinstance(parties, list):
        for party in parties:
            if not isinstance(party, dict):
                continue
            roles = party.get("roles", [])
            if isinstance(roles, str):
                roles = [roles]
            if "buyer" in roles and isinstance(party.get("name"), str):
                return party["name"].strip()
    return ""


def get_description(release: dict) -> str:
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    desc = tender.get("description")
    if not isinstance(desc, str):
        desc = ""
    buyer = get_buyer(release)
    status = tender.get("status") if isinstance(tender.get("status"), str) else ""
    bits = []
    if buyer:
        bits.append(f"Vergabestelle: {buyer}")
    if status:
        bits.append(f"Status: {status}")
    if desc.strip():
        bits.append(" ".join(desc.split()))
    return " | ".join(bits)[:4000]


def get_datetime(release: dict, fallback_day: date) -> datetime:
    candidates = [
        release.get("date"),
        release.get("publishedDate"),
        release.get("publicationDate"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            s = value.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass
    return datetime.combine(fallback_day, datetime.min.time(), tzinfo=timezone.utc)


def find_urls(value):
    found = []
    def walk(v, key=""):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k).lower())
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and v.startswith(("http://", "https://")):
            found.append((key, v))
    walk(value)
    return found


def get_link(release: dict) -> str:
    # Prefer explicit top-level canonical/source links if present.
    for key in ("url", "uri", "sourceUrl", "sourceURL"):
        value = release.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value

    urls = find_urls(release)
    preferred_words = ("source", "notice", "publication", "tender", "procedure", "project")
    for key, url in urls:
        if any(word in key for word in preferred_words):
            return url
    if urls:
        return urls[0][1]

    # Safe landing page fallback, not a fabricated notice URL.
    return "https://oeffentlichevergabe.de/ui/de/search"


def get_guid(release: dict, link: str) -> str:
    ocid = release.get("ocid")
    rid = release.get("id")
    if ocid or rid:
        return f"{ocid or ''}::{rid or ''}"
    return link


def xml_text(parent, tag, text):
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def build_feed(items: list[dict], cfg: dict):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    xml_text(channel, "title", cfg["feed_title"])
    xml_text(channel, "link", "https://oeffentlichevergabe.de/ui/de/search")
    xml_text(channel, "description", cfg["feed_description"])
    xml_text(channel, "language", "de-de")
    xml_text(channel, "lastBuildDate", format_datetime(datetime.now(timezone.utc)))
    xml_text(channel, "ttl", "180")

    for item in items[: int(cfg["max_items"])]:
        node = ET.SubElement(channel, "item")
        xml_text(node, "title", item["title"])
        xml_text(node, "link", item["link"])
        xml_text(node, "description", item["description"])
        xml_text(node, "pubDate", format_datetime(item["published"]))
        guid = xml_text(node, "guid", item["guid"])
        guid.set("isPermaLink", "false")

    ET.indent(rss, space="  ")
    tree = ET.ElementTree(rss)
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)


def main():
    cfg = load_config()
    keywords = cfg["keywords"]
    allowed_tags = set(cfg.get("allowed_tags", []))
    lookback = int(cfg["lookback_days"])

    seen = set()
    items = []

    today = date.today()
    for offset in range(lookback):
        day = today - timedelta(days=offset)
        payload = fetch_daily_export(day)
        if not payload:
            continue

        for doc in iter_json_documents(payload):
            for release in iter_releases(doc):
                release_tags = tags_of(release)

                # If tags exist and configured allowed tags exist, keep only matching tender releases.
                # If no tags are present, do not discard the record solely for that reason.
                if allowed_tags and release_tags and not (allowed_tags & release_tags):
                    continue

                if not matches_keywords(release, keywords, bool(cfg["require_all_keywords"])):
                    continue

                link = get_link(release)
                guid = get_guid(release, link)
                if guid in seen:
                    continue
                seen.add(guid)

                items.append({
                    "title": get_title(release),
                    "description": get_description(release),
                    "link": link,
                    "guid": guid,
                    "published": get_datetime(release, day),
                })

    items.sort(key=lambda x: x["published"], reverse=True)
    build_feed(items, cfg)
    print(f"Wrote {len(items[:int(cfg['max_items'])])} items to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
