"""
Refresh scheduled macro event dates in config.json from official sources.

Runs monthly via scheduler (or manually). On fetch/parse failure for a source,
existing dates for that event label are preserved.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

HTTP_USER_AGENT = (
    "pmwatch/1.0 (local transparency research; +https://github.com/lweiss01/pmwatch)"
)

FOMC_CALENDAR_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
)
CPI_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"

MONTH_NAME_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

FOMC_LABEL = "FOMC rate decision"
CPI_LABEL = "CPI release"


@dataclass
class SourceRefreshResult:
    source: str
    status: str
    dates_found: int = 0
    message: str = ""


@dataclass
class RefreshResult:
    status: str
    dry_run: bool
    updated: bool = False
    sources: list[SourceRefreshResult] = field(default_factory=list)
    fomc_dates: list[str] = field(default_factory=list)
    cpi_dates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def _format_date(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _month_name_to_num(name: str) -> int | None:
    cleaned = name.strip().lower().split("/")[-1]
    return MONTH_NAME_TO_NUM.get(cleaned)


def parse_fomc_dates(
    html: str,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
) -> list[str]:
    """Extract FOMC meeting decision dates (last day of each meeting range)."""
    now_year = datetime.now(timezone.utc).year
    min_year = min_year or now_year
    max_year = max_year or (now_year + 1)

    dates: list[str] = []
    section_pattern = re.compile(
        r"####\s+(\d{4})\s+FOMC Meetings(.*?)(?=####\s+\d{4}\s+FOMC Meetings|$)",
        re.DOTALL | re.IGNORECASE,
    )
    month_pattern = re.compile(
        r"^(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan/Feb|Apr/May|Oct/Nov)$",
        re.IGNORECASE,
    )
    range_pattern = re.compile(r"^(\d{1,2})-(\d{1,2})")

    for section_match in section_pattern.finditer(html):
        year = int(section_match.group(1))
        if year < min_year or year > max_year:
            continue

        current_month: int | None = None
        for raw_line in section_match.group(2).splitlines():
            line = raw_line.strip()
            if not line:
                continue

            month_match = month_pattern.match(line)
            if month_match:
                current_month = _month_name_to_num(month_match.group(1))
                continue

            range_match = range_pattern.match(line)
            if not range_match or current_month is None:
                continue

            start_day = int(range_match.group(1))
            end_day = int(range_match.group(2))
            month = current_month
            day = end_day
            if end_day < start_day:
                month += 1
                if month > 12:
                    month = 1
                    year += 1
                day = end_day

            dates.append(_format_date(year, month, day))

    return sorted(set(dates))


def parse_cpi_dates(
    html: str,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
) -> list[str]:
    """Extract CPI release dates from BLS schedule HTML."""
    now_year = datetime.now(timezone.utc).year
    min_year = min_year or now_year
    max_year = max_year or (now_year + 1)

    dates: list[str] = []
    table_pattern = re.compile(
        r"(?:Release Date|release date)[\s\S]{0,80}?"
        r">([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
        re.IGNORECASE,
    )
    for match in table_pattern.finditer(html):
        month_name, day_str, year_str = match.groups()
        year = int(year_str)
        if year < min_year or year > max_year:
            continue
        month = _month_name_to_num(month_name)
        if month is None:
            continue
        dates.append(_format_date(year, month, int(day_str)))

    if not dates:
        fallback_pattern = re.compile(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(\d{1,2}),\s*(\d{4})\b",
            re.IGNORECASE,
        )
        for match in fallback_pattern.finditer(html):
            month_name, day_str, year_str = match.groups()
            year = int(year_str)
            if year < min_year or year > max_year:
                continue
            month = _month_name_to_num(month_name)
            if month is None:
                continue
            dates.append(_format_date(year, month, int(day_str)))

    return sorted(set(dates))


def validate_dates(dates: list[str]) -> list[str]:
    """Drop invalid, duplicate, and very stale dates."""
    today = datetime.now(timezone.utc).date()
    valid: list[str] = []
    for date_str in dates:
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if parsed.year < today.year - 1:
            continue
        valid.append(date_str)
    return sorted(set(valid))


def _update_event_dates(
    events: list[dict],
    label: str,
    new_dates: list[str],
) -> bool:
    changed = False
    for event in events:
        if event.get("label") != label:
            continue
        validated = validate_dates(new_dates)
        if not validated:
            return False
        if event.get("dates") != validated:
            event["dates"] = validated
            changed = True
        return changed
    return False


def merge_calendar_into_config(
    cfg: dict,
    *,
    fomc_dates: list[str] | None,
    cpi_dates: list[str] | None,
) -> dict:
    """Return a copy of cfg with updated scheduled_events dates where provided."""
    merged = json.loads(json.dumps(cfg))
    scheduled = merged.setdefault("scheduled_events", {})
    events = scheduled.setdefault("events", [])

    if fomc_dates:
        _update_event_dates(events, FOMC_LABEL, fomc_dates)
    if cpi_dates:
        _update_event_dates(events, CPI_LABEL, cpi_dates)

    return merged


def _set_refresh_metadata(
    cfg: dict,
    *,
    status: str,
    source_results: list[SourceRefreshResult],
) -> None:
    scheduled = cfg.setdefault("scheduled_events", {})
    refresh = scheduled.setdefault("refresh", {})
    refresh["enabled"] = refresh.get("enabled", True)
    refresh["schedule"] = refresh.get("schedule", "monthly")
    refresh["last_refresh"] = config.utc_now_iso()
    refresh["last_status"] = status
    refresh["sources"] = {
        result.source: {
            "status": result.status,
            "dates_found": result.dates_found,
            "message": result.message,
        }
        for result in source_results
    }


def refresh_event_calendar(dry_run: bool = False) -> RefreshResult:
    """Fetch official calendars and update config.json scheduled event dates."""
    cfg = config.load_config()
    refresh_cfg = cfg.get("scheduled_events", {}).get("refresh", {})
    if not refresh_cfg.get("enabled", True):
        return RefreshResult(
            status="skipped",
            dry_run=dry_run,
            errors=["scheduled_events.refresh.enabled is false"],
        )

    now_year = datetime.now(timezone.utc).year
    min_year = now_year
    max_year = now_year + 1
    source_results: list[SourceRefreshResult] = []
    errors: list[str] = []
    fomc_dates: list[str] | None = None
    cpi_dates: list[str] | None = None

    try:
        fomc_html = _http_get(FOMC_CALENDAR_URL)
        parsed_fomc = parse_fomc_dates(fomc_html, min_year=min_year, max_year=max_year)
        if parsed_fomc:
            fomc_dates = parsed_fomc
            source_results.append(SourceRefreshResult(
                source="fomc",
                status="ok",
                dates_found=len(parsed_fomc),
                message=f"Parsed {len(parsed_fomc)} FOMC dates ({min_year}-{max_year})",
            ))
        else:
            source_results.append(SourceRefreshResult(
                source="fomc",
                status="failed",
                message="No FOMC dates parsed from calendar page",
            ))
            errors.append("FOMC parse returned zero dates")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        source_results.append(SourceRefreshResult(
            source="fomc",
            status="failed",
            message=str(exc),
        ))
        errors.append(f"FOMC fetch failed: {exc}")

    try:
        cpi_html = _http_get(CPI_SCHEDULE_URL)
        parsed_cpi = parse_cpi_dates(cpi_html, min_year=min_year, max_year=max_year)
        if parsed_cpi:
            cpi_dates = parsed_cpi
            source_results.append(SourceRefreshResult(
                source="cpi",
                status="ok",
                dates_found=len(parsed_cpi),
                message=f"Parsed {len(parsed_cpi)} CPI dates ({min_year}-{max_year})",
            ))
        else:
            source_results.append(SourceRefreshResult(
                source="cpi",
                status="failed",
                message="No CPI dates parsed from schedule page",
            ))
            errors.append("CPI parse returned zero dates")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        source_results.append(SourceRefreshResult(
            source="cpi",
            status="failed",
            message=str(exc),
        ))
        errors.append(f"CPI fetch failed: {exc}")

    if fomc_dates is None and cpi_dates is None:
        return RefreshResult(
            status="failed",
            dry_run=dry_run,
            sources=source_results,
            errors=errors,
        )

    merged = merge_calendar_into_config(cfg, fomc_dates=fomc_dates, cpi_dates=cpi_dates)
    overall_status = "partial" if errors else "ok"
    _set_refresh_metadata(merged, status=overall_status, source_results=source_results)

    updated = merged != cfg
    if not dry_run and updated:
        config.save_config(merged)
        log.info(
            "Event calendar refresh complete: status=%s fomc=%s cpi=%s",
            overall_status,
            len(fomc_dates or []),
            len(cpi_dates or []),
        )
    elif dry_run:
        log.info("Event calendar dry-run: status=%s (no write)", overall_status)

    return RefreshResult(
        status=overall_status,
        dry_run=dry_run,
        updated=updated and not dry_run,
        sources=source_results,
        fomc_dates=fomc_dates or [],
        cpi_dates=cpi_dates or [],
        errors=errors,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Refresh FOMC/CPI dates in config.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing config.json",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = refresh_event_calendar(dry_run=args.dry_run)
    print(result)
