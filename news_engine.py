import xml.etree.ElementTree as ET
import urllib.request
import json
import logging
import time
from datetime import datetime, timezone
import email.utils
import db
import config

log = logging.getLogger(__name__)

# --- Keywords mapping for monitored series ---
SERIES_KEYWORDS = {
    "KXCABOUT": ["cabinet", "resign", "resignation", "department head", "fired", "leaves office", "vacate"],
    "KXNEXTAG": ["attorney general", "blanche", "gaetz", "nominee", "nomination", "justice department", "doj"],
    "KXNEXTDEF": ["secretary of defense", "secdef", "pentagon", "hegseth", "defense department"],
    "KXNEXTSTATE": ["secretary of state", "secstate", "rubio", "state department"],
    "KXNEXTODNI": ["director of national intelligence", "dni", "gabbard", "intelligence community"],
    "KXTRUMPPARDONS": ["pardon", "clemency", "commute"],
    "KXTRUMPPARDONFAMILY": ["family pardon", "pardon family"],
    "KXINSURRECTION": ["insurrection act", "military deployment", "domestic deployment"],
    "KXMARTIAL": ["martial law", "military rule"],
    "KXHABEAS": ["habeas corpus", "detention without trial"],
    "KXWITHDRAW": ["withdraw from treaty", "nato withdrawal", "treaty exit"],
    "KXDOED": ["education department", "doed", "abolish education"],
    "KXAGENCYELIM": ["agency elimination", "eliminate agency", "doge", "department of government efficiency"],
    "KXEOTRUMPTERM": ["executive order", "eo count"],
    "KXAMEND25": ["25th amendment", "twenty-fifth amendment"],
    "KXTRUMPRESIGN": ["trump resign", "president resignation"],
    "KXFED": ["fed funds rate", "interest rate", "federal reserve", "fomc", "powell", "rate cut", "rate hike"],
    "KXCPI": ["cpi", "inflation", "consumer price index", "bls", "bureau of labor statistics"],
    "KXGDP": ["gdp", "gross domestic product", "bea", "bureau of economic analysis", "economic growth"],
    "KXGREENTERRITORY": ["greenland", "acquisition", "territory"],
    "KXCANTERRITORY": ["canada", "annex", "territory"],
    "KXCANAL": ["panama canal", "canal control"],
    "KXRECOGROC": ["recognize taiwan", "taiwan recognition", "republic of china"],
    "KXZELENSKYPUTIN": ["zelensky", "putin", "talks", "peace negotiation"],
    "KXUSAKIM": ["kim jong-un", "north korea visit"],
    "KXABRAHAMSA": ["israel-saudi", "abraham accords"],
    "KXABRAHAMSY": ["israel-syria", "peace treaty"],
    "KXHOUSE": ["house control", "house speaker", "house majority"],
    "KXSENATE": ["senate control", "senate majority", "senate leader"],
    "KXIMPEACH": ["impeachment", "impeach"],
    "KXVETOOVERRIDE": ["veto override", "override veto"],
    "KXGOVSHUT": ["government shutdown", "shutdown"],
    "KXCR": ["continuing resolution", "cr budget", "stopgap funding"],
    "KXSCOTUSRESIGN": ["supreme court", "scotus", "resign", "justice", "retirement", "vacancy", "alito", "thomas", "sotomayor"],
    "KXSCOTUSCHANGE": ["court size", "court packing"],
    "KXSCOURT": ["next justice", "supreme court nominee"],
    "KXTARIFFS": ["tariffs case", "court tariffs"],
    "KXSCOTUSPOWER": ["court composition", "judicial reform"]
}

DEFAULT_FEEDS = [
    {
        "url": "https://www.congress.gov/rss/presented-to-president.xml",
        "source": "Congress.gov Presented to President",
        "source_type": "primary_gov"
    },
    {
        "url": "https://www.congress.gov/rss/house-floor-today.xml",
        "source": "Congress.gov House Floor",
        "source_type": "primary_gov"
    },
    {
        "url": "https://www.congress.gov/rss/senate-floor-today.xml",
        "source": "Congress.gov Senate Floor",
        "source_type": "primary_gov"
    },
    {
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "source": "Federal Reserve Press",
        "source_type": "primary_gov"
    },
    {
        "url": "https://www.treasurydirect.gov/xml/RssOffering.xml",
        "source": "TreasuryDirect Offerings",
        "source_type": "primary_gov"
    },
    {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "source": "NYT Politics",
        "source_type": "mainstream_news"
    },
    {
        "url": "https://rss.politico.com/playbook.xml",
        "source": "Politico Playbook",
        "source_type": "mainstream_news"
    }
]


def match_series(title: str, description: str) -> str | None:
    """Matches content against watchlisted series keywords. Returns first matching series ticker."""
    text = (title + " " + (description or "")).lower()
    for series, keywords in SERIES_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                return series
    return None


def parse_rss_string(xml_str: str, source: str, source_type: str) -> list:
    """Parse RSS XML string and extract normalized articles."""
    articles = []
    try:
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        if channel is None:
            # Fall back to root search if atom or direct root elements are present
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

            # Normalize published time
            if pub_date_str:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date_str)
                    dt_utc = dt.astimezone(timezone.utc)
                    published_time = dt_utc.isoformat().replace("+00:00", "Z")
                    published_ts = int(dt_utc.timestamp())
                except Exception:
                    published_time = config.utc_now_iso()
                    published_ts = int(time.time())
            else:
                published_time = config.utc_now_iso()
                published_ts = int(time.time())

            series_ticker = match_series(title, description)

            articles.append({
                "title": title.strip(),
                "description": description.strip() if description else "",
                "url": link.strip(),
                "published_time": published_time,
                "published_ts": published_ts,
                "source": source,
                "source_type": source_type,
                "series_ticker": series_ticker,
                "ingested_ts": int(time.time())
            })
    except Exception as e:
        log.error(f"Error parsing RSS string for source {source}: {e}")
    return articles


def parse_fed_register_json(json_data: dict, source: str) -> list:
    """Parse Federal Register documents.json results."""
    articles = []
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
                published_time = config.utc_now_iso()
                published_ts = int(time.time())
        else:
            published_time = config.utc_now_iso()
            published_ts = int(time.time())

        series_ticker = match_series(title, description)

        articles.append({
            "title": title.strip(),
            "description": description.strip() if description else "",
            "url": url.strip(),
            "published_time": published_time,
            "published_ts": published_ts,
            "source": source,
            "source_type": "primary_gov",
            "series_ticker": series_ticker,
            "ingested_ts": int(time.time())
        })
    return articles


def calculate_correlation_confidence(anomaly: dict, news_article: dict, time_diff: int, overlap_ratio: float) -> float:
    """Calculate confidence based on anomaly score, source weight, and publication proximity."""
    score = anomaly["anomaly_score"]
    source_type = news_article.get("source_type", "mainstream_news")
    weight = 1.5 if source_type == "primary_gov" else 1.0
    
    max_window = 48 * 3600
    time_penalty = 1.0 - (time_diff / max_window)
    time_penalty = max(0.0, min(1.0, time_penalty))
    
    return round(score * weight * overlap_ratio * time_penalty, 2)


def fetch_and_ingest_feeds():
    """Poll external feeds and API endpoints and persist news to database."""
    log.info("Starting news feeds ingestion...")
    total_ingested = 0

    # 1. Fetch RSS Feeds
    for feed in DEFAULT_FEEDS:
        url = feed["url"]
        source = feed["source"]
        source_type = feed["source_type"]
        log.info(f"Polling feed: {source} ({url})")
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) pmwatch/1.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read().decode('utf-8', errors='ignore')
                articles = parse_rss_string(xml_data, source=source, source_type=source_type)
                if articles:
                    inserted = db.insert_news_articles(articles)
                    total_ingested += inserted
                    log.info(f"  Ingested {inserted} new items from {source}")
        except Exception as e:
            log.error(f"Failed to fetch RSS feed {source} ({url}): {e}")

    # 2. Fetch Federal Register documents for today
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fed_reg_url = f"https://www.federalregister.gov/api/v1/documents.json?conditions[publication_date][gte]={today_str}&per_page=50"
    log.info(f"Polling Federal Register: {fed_reg_url}")
    try:
        req = urllib.request.Request(
            fed_reg_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) pmwatch/1.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            json_data = json.loads(response.read().decode('utf-8'))
            articles = parse_fed_register_json(json_data, source="Federal Register")
            if articles:
                inserted = db.insert_news_articles(articles)
                total_ingested += inserted
                log.info(f"  Ingested {inserted} new items from Federal Register")
    except Exception as e:
        log.error(f"Failed to fetch Federal Register documents: {e}")

    # 3. Perform Correlations
    correlate_all_recent_anomalies()

    log.info(f"News feed ingestion complete. {total_ingested} total items ingested.")
    return total_ingested


def correlate_all_recent_anomalies(lookback_days: int = 7):
    """Scan recent anomalies and match them to ingested news articles."""
    conn = db.get_conn()
    c = conn.cursor()
    cutoff_ts = int(time.time()) - (lookback_days * 86400)
    
    # Get recent anomalies
    c.execute("""
        SELECT * FROM anomalies
        WHERE detected_ts >= ?
        ORDER BY detected_ts ASC
    """, (cutoff_ts,))
    anomalies = [dict(r) for r in c.fetchall()]
    
    # Get all news in window
    c.execute("""
        SELECT * FROM news_articles
        WHERE published_ts >= ?
        ORDER BY published_ts ASC
    """, (cutoff_ts - 86400,))  # allow slightly wider news window
    news_articles = [dict(r) for r in c.fetchall()]
    conn.close()

    correlations_inserted = 0
    for anomaly in anomalies:
        series_ticker = anomaly["series_ticker"]
        if not series_ticker:
            continue
            
        series_keywords = SERIES_KEYWORDS.get(series_ticker, [])
        if not series_keywords:
            continue

        for article in news_articles:
            # Check timeline: news must be published AFTER or at the same time as anomaly
            time_diff = article["published_ts"] - anomaly["detected_ts"]
            if 0 < time_diff <= (48 * 3600):  # 48-hour leakage window
                text = (article["title"] + " " + (article["description"] or "")).lower()
                matches = [kw for kw in series_keywords if kw.lower() in text]
                
                if matches:
                    overlap_ratio = len(matches) / len(series_keywords)
                    # Add a default lower bound for overlap ratio so single matches still yield score
                    overlap_ratio = max(0.25, overlap_ratio)
                    
                    confidence = calculate_correlation_confidence(
                        anomaly, 
                        article, 
                        time_diff, 
                        overlap_ratio
                    )
                    
                    correlation = {
                        "anomaly_id": anomaly["id"],
                        "cluster_first_seen_ts": anomaly["detected_ts"],
                        "ticker": anomaly["ticker"],
                        "news_id": article["id"],
                        "lead_time_seconds": time_diff,
                        "confidence_score": confidence,
                        "notes": f"Matched keywords: {', '.join(matches)} | Source weight: {'1.5x' if article['source_type'] == 'primary_gov' else '1.0x'}"
                    }
                    try:
                        db.insert_correlation(correlation)
                        correlations_inserted += 1
                    except Exception:
                        pass # Ignore duplicate inserts

    if correlations_inserted > 0:
        log.info(f"Created {correlations_inserted} news-to-anomaly correlations.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db.init_db()
    fetch_and_ingest_feeds()
