"""
Feed fetching and parsing helpers for news_engine.

Handles RSS/Atom syndication, House PTR index ZIPs, and Senate PTR JSON responses.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

import config
import db
from correlation_engine import correlate_all_recent_anomalies
from keyword_matcher import match_series

log = logging.getLogger(__name__)

HTTP_USER_AGENT = (
    "pmwatch/1.0 (local transparency research; +https://github.com/lweiss01/pmwatch)"
)
HTTP_RSS_ACCEPT = "application/rss+xml, application/xml, text/xml, */*"
SEC_USER_AGENT = "pmwatch lweiss01@users.noreply.github.com"

# TODO: Replace live Senate eFD POST and SEC Form 4 Atom scrape with official bulk
# data sources (SEC EDGAR bulk/API; Senate PTR bulk export) to avoid 403 bot blocks.

HOUSE_FD_ZIP_URL = (
    "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
)
SENATE_PTR_DATA_URL = "https://efdsearch.senate.gov/search/report/data/"


def _http_get(url: str, user_agent: str = HTTP_USER_AGENT, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _parse_pub_date(pub_date_str: str | None) -> tuple[str, int]:
    if not pub_date_str:
        now_ts = int(time.time())
        return config.utc_now_iso(), now_ts
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(pub_date_str)
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.isoformat().replace("+00:00", "Z"), int(dt_utc.timestamp())
    except Exception:
        now_ts = int(time.time())
        return config.utc_now_iso(), now_ts


def _parse_iso_date(date_str: str) -> tuple[str, int]:
    try:
        dt = datetime.strptime(date_str.strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z"), int(dt.timestamp())
    except Exception:
        now_ts = int(time.time())
        return config.utc_now_iso(), now_ts


def parse_atom_string(xml_str: str, source: str, source_type: str) -> list[dict]:
    """Parse Atom XML into normalized article dicts."""
    articles: list[dict] = []
    try:
        root = ET.fromstring(xml_str)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for entry in entries:
            title_el = entry.find("atom:title", ns)
            if title_el is None:
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            link = ""
            link_els = entry.findall("atom:link", ns)
            if not link_els:
                link_els = entry.findall("{http://www.w3.org/2005/Atom}link")
            for link_el in link_els:
                href = link_el.attrib.get("href", "")
                rel = link_el.attrib.get("rel", "alternate")
                if href and (rel == "alternate" or not link):
                    link = href

            summary_el = entry.find("atom:summary", ns)
            if summary_el is None:
                summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
            description = (
                summary_el.text.strip()
                if summary_el is not None and summary_el.text
                else ""
            )

            updated_el = entry.find("atom:updated", ns)
            if updated_el is None:
                updated_el = entry.find("{http://www.w3.org/2005/Atom}updated")
            published_time, published_ts = _parse_pub_date(
                updated_el.text if updated_el is not None else None
            )

            if not title or not link:
                continue

            articles.append({
                "title": title,
                "description": description,
                "url": link,
                "published_time": published_time,
                "published_ts": published_ts,
                "source": source,
                "source_type": source_type,
                "series_ticker": match_series(title, description, source=source),
                "ingested_ts": int(time.time()),
            })
    except Exception as e:
        log.error("Error parsing Atom feed for %s: %s", source, e)
    return articles


def parse_house_fd_xml(xml_bytes: bytes, year: int, source: str) -> list[dict]:
    """Parse House Financial Disclosure XML index into PTR filing articles."""
    articles: list[dict] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.error("Failed to parse House FD XML: %s", e)
        return articles

    for member in root.findall("Member"):
        if (member.findtext("FilingType") or "").strip() != "P":
            continue

        doc_id = (member.findtext("DocID") or "").strip()
        last_name = (member.findtext("Last") or "").strip()
        first_name = (member.findtext("First") or "").strip()
        filing_date = (member.findtext("FilingDate") or "").strip()
        state_dst = (member.findtext("StateDst") or "").strip()

        if not doc_id or not last_name:
            continue

        name = f"{first_name} {last_name}".strip()
        title = f"House PTR: {name}"
        description = (
            f"Periodic Transaction Report filed {filing_date} "
            f"for {state_dst}. STOCK Act House disclosure filing."
        )
        url = (
            f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/"
            f"{year}/{doc_id}.pdf"
        )
        published_time, published_ts = _parse_iso_date(filing_date)

        articles.append({
            "title": title,
            "description": description,
            "url": url,
            "published_time": published_time,
            "published_ts": published_ts,
            "source": source,
            "source_type": "disclosure_filing",
            "series_ticker": match_series(title, description, source=source),
            "ingested_ts": int(time.time()),
        })

    return articles


def parse_senate_ptr_json(json_data: dict | list, source: str) -> list[dict]:
    """Normalize Senate PTR JSON payloads into article dicts."""
    articles: list[dict] = []
    rows = json_data if isinstance(json_data, list) else json_data.get("data", [])
    if not isinstance(rows, list):
        return articles

    for row in rows:
        if not isinstance(row, dict):
            continue
        first = row.get("first_name") or row.get("firstName") or ""
        last = row.get("last_name") or row.get("lastName") or ""
        name = f"{first} {last}".strip() or row.get("filer", "Unknown Senator")
        filing_date = (
            row.get("date_received")
            or row.get("dateRecieved")
            or row.get("filing_date")
            or ""
        )
        link = row.get("link") or row.get("ptr_link") or ""
        asset = row.get("asset_name") or row.get("asset") or ""
        ticker = row.get("ticker") or row.get("symbol") or ""
        tx_type = row.get("type") or row.get("transaction_type") or ""

        title = f"Senate PTR: {name}"
        if ticker:
            title += f" — {ticker}"
        description = (
            f"Senate Periodic Transaction Report. Asset: {asset}. "
            f"Transaction: {tx_type}. Filed {filing_date}."
        ).strip()

        if not link:
            ptr_id = row.get("ptr_id") or row.get("report_id")
            if ptr_id:
                link = f"https://efdsearch.senate.gov/search/view/ptr/{ptr_id}/"

        if not link:
            continue

        published_time, published_ts = _parse_iso_date(str(filing_date))

        articles.append({
            "title": title,
            "description": description,
            "url": link,
            "published_time": published_time,
            "published_ts": published_ts,
            "source": source,
            "source_type": "disclosure_filing",
            "series_ticker": match_series(title, description, source=source),
            "ingested_ts": int(time.time()),
        })

    return articles


def fetch_house_disclosures(year: int | None = None, source: str = "House Financial Disclosures") -> list[dict]:
    """Download the House PTR index ZIP and emit recent filing articles."""
    year = year or datetime.now(timezone.utc).year
    url = HOUSE_FD_ZIP_URL.format(year=year)
    try:
        zip_bytes = _http_get(url, timeout=60)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_name = next((n for n in zf.namelist() if n.endswith(".xml")), None)
            if not xml_name:
                log.warning("House FD ZIP for %s contained no XML index", year)
                return []
            articles = parse_house_fd_xml(zf.read(xml_name), year, source=source)
        log.info("Parsed %d House PTR filings from %s", len(articles), url)
        return articles
    except Exception as e:
        log.error("Failed to fetch House disclosures (%s): %s", url, e)
        return []


def fetch_senate_disclosures(source: str = "Senate eFD Disclosures") -> list[dict]:
    """Attempt to fetch recent Senate PTR filings. Returns [] if unavailable."""
    payload = json.dumps({
        "report_type": "PTR",
        "from_date": "01/01/2020",
        "to_date": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            SENATE_PTR_DATA_URL,
            data=payload,
            headers={
                "User-Agent": HTTP_USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            json_data = json.loads(response.read().decode("utf-8"))
        articles = parse_senate_ptr_json(json_data, source=source)
        log.info("Parsed %d Senate PTR filings", len(articles))
        return articles
    except Exception as e:
        log.warning("Senate eFD disclosures unavailable (skipping): %s", e)
        return []


DEFAULT_FEEDS = [
    {
        "url": "https://www.congress.gov/rss/presented-to-president.xml",
        "source": "Congress.gov Presented to President",
        "source_type": "primary_gov",
    },
    {
        "url": "https://www.congress.gov/rss/house-floor-today.xml",
        "source": "Congress.gov House Floor",
        "source_type": "primary_gov",
    },
    {
        "url": "https://www.congress.gov/rss/senate-floor-today.xml",
        "source": "Congress.gov Senate Floor",
        "source_type": "primary_gov",
    },
    {
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "source": "Federal Reserve Press",
        "source_type": "primary_gov",
    },
    {
        "url": "https://www.treasurydirect.gov/TA_WS/securities/announced/rss",
        "source": "TreasuryDirect Offerings",
        "source_type": "primary_gov",
    },
    {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "source": "NYT Politics",
        "source_type": "mainstream_news",
    },
    {
        "url": "https://rss.politico.com/playbook.xml",
        "source": "Politico Playbook",
        "source_type": "mainstream_news",
    },
    {
        "url": "https://www.whitehouse.gov/news/feed/",
        "source": "White House Briefings",
        "source_type": "primary_gov",
    },
]


def parse_rss_string(xml_str: str, source: str, source_type: str) -> list[dict]:
    """Parse RSS XML string and extract normalized articles."""
    articles: list[dict] = []
    try:
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        if channel is None:
            items = root.findall(".//item")
        else:
            items = channel.findall("item")

        for item in items:
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            pub_el = item.find("pubDate")

            title = title_el.text if title_el is not None else ""
            link = link_el.text if link_el is not None else ""
            description = desc_el.text if desc_el is not None else ""
            pub_date_str = pub_el.text if pub_el is not None else None

            if not title or not link:
                continue

            published_time, published_ts = _parse_pub_date(pub_date_str)

            articles.append({
                "title": title.strip(),
                "description": description.strip() if description else "",
                "url": link.strip(),
                "published_time": published_time,
                "published_ts": published_ts,
                "source": source,
                "source_type": source_type,
                "series_ticker": match_series(title, description, source=source),
                "ingested_ts": int(time.time()),
            })
    except Exception as e:
        log.error("Error parsing RSS string for source %s: %s", source, e)
    return articles


def parse_fed_register_json(json_data: dict, source: str) -> list[dict]:
    """Parse Federal Register documents.json results."""
    articles: list[dict] = []
    results = json_data.get("results", [])
    for doc in results:
        title = doc.get("title", "")
        description = doc.get("abstract", "")
        url = doc.get("html_url", "")
        pub_date = doc.get("publication_date", "")

        if not title or not url:
            continue

        if pub_date:
            try:
                dt = datetime.fromisoformat(pub_date + "T00:00:00+00:00")
                published_time = dt.isoformat().replace("+00:00", "Z")
                published_ts = int(dt.timestamp())
            except Exception:
                published_time, published_ts = config.utc_now_iso(), int(time.time())
        else:
            published_time, published_ts = config.utc_now_iso(), int(time.time())

        articles.append({
            "title": title.strip(),
            "description": description.strip() if description else "",
            "url": url.strip(),
            "published_time": published_time,
            "published_ts": published_ts,
            "source": source,
            "source_type": "primary_gov",
            "series_ticker": match_series(title, description, source=source),
            "ingested_ts": int(time.time()),
        })
    return articles


def fetch_and_ingest_feeds() -> int:
    """Poll external feeds and API endpoints, persist news, then run correlations."""
    log.info("Starting news feeds ingestion...")
    total_ingested = 0

    for feed in DEFAULT_FEEDS:
        url = feed["url"]
        source = feed["source"]
        source_type = feed["source_type"]
        log.info("Polling feed: %s (%s)", source, url)
        try:
            user_agent = HTTP_USER_AGENT
            if feed.get("user_agent") == "sec":
                user_agent = SEC_USER_AGENT
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": HTTP_RSS_ACCEPT,
                },
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                xml_data = response.read().decode("utf-8", errors="ignore")

            if feed.get("parser") == "atom":
                if "Undeclared Automated Tool" in xml_data:
                    log.warning("SEC EDGAR blocked automated access; skipping %s", source)
                    continue
                articles = parse_atom_string(xml_data, source=source, source_type=source_type)
            else:
                articles = parse_rss_string(xml_data, source=source, source_type=source_type)

            if articles:
                inserted = db.insert_news_articles(articles)
                total_ingested += inserted
                log.info("  Ingested %d new items from %s", inserted, source)
        except Exception as e:
            log.error("Failed to fetch RSS feed %s (%s): %s", source, url, e)

    for fetcher, label in (
        (fetch_house_disclosures, "House Financial Disclosures"),
        (fetch_senate_disclosures, "Senate eFD Disclosures"),
    ):
        try:
            articles = fetcher()
            if articles:
                inserted = db.insert_news_articles(articles)
                total_ingested += inserted
                log.info("  Ingested %d new items from %s", inserted, label)
        except Exception as e:
            log.error("Failed to ingest %s: %s", label, e)

    try:
        sec_articles = fetch_sec_edgar_form4()
        if sec_articles:
            inserted = db.insert_news_articles(sec_articles)
            total_ingested += inserted
            log.info("  Ingested %d new items from SEC EDGAR Form 4", inserted)
    except Exception as e:
        log.error("Failed to ingest SEC EDGAR Form 4: %s", e)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for source_label, extra_params in (
        ("Federal Register", ""),
        (
            "Federal Register Executive Orders",
            "&conditions[presidential_document_type][]=executive_order",
        ),
    ):
        fed_reg_url = (
            "https://www.federalregister.gov/api/v1/documents.json"
            f"?conditions[publication_date][gte]={today_str}&per_page=50{extra_params}"
        )
        log.info("Polling %s: %s", source_label, fed_reg_url)
        try:
            req = urllib.request.Request(fed_reg_url, headers={"User-Agent": HTTP_USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as response:
                json_data = json.loads(response.read().decode("utf-8"))
                articles = parse_fed_register_json(json_data, source=source_label)
                if articles:
                    inserted = db.insert_news_articles(articles)
                    total_ingested += inserted
                    log.info("  Ingested %d new items from %s", inserted, source_label)
        except Exception as e:
            log.error("Failed to fetch %s: %s", source_label, e)

    try:
        correlate_all_recent_anomalies()
    except Exception as e:
        log.error("Correlation pass after feed ingestion failed: %s", e)

    log.info("News feed ingestion complete. %d total items ingested.", total_ingested)
    return total_ingested


def fetch_sec_edgar_form4(source: str = "SEC EDGAR Form 4") -> list[dict]:
    """Fetch recent SEC Form 4 insider filings via Atom feed."""
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
        "&type=4&owner=include&count=40&output=atom"
    )
    try:
        xml_data = _http_get(url, user_agent=SEC_USER_AGENT, timeout=30).decode(
            "utf-8", errors="ignore"
        )
        if "Undeclared Automated Tool" in xml_data:
            log.warning("SEC EDGAR blocked automated access; skipping Form 4 feed")
            return []
        return parse_atom_string(xml_data, source=source, source_type="disclosure_filing")
    except Exception as e:
        log.warning("SEC EDGAR Form 4 feed unavailable (skipping): %s", e)
        return []
