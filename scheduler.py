import logging
from pathlib import Path
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import db
import event_calendar_refresh
import collector
import scorer
import cluster_scorer
import cross_market_scorer
import config
import news_engine
import microstructure_watcher

# Load config
SCHEDULER_INTERVAL = config.get_scheduler_interval()
LOG_PATH = Path(__file__).parent / "logs" / "scheduler.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ],
    force=True,
)
log = logging.getLogger(__name__)


def collect_and_score():
    run_time = config.utc_now_iso()
    log.info(f"=== Scheduled run: {run_time} ===")
    try:
        collector.run_collection()
    except Exception as e:
        log.error(f"Collector failed: {e}")
        return
    try:
        flagged = scorer.run_scorer()
        if flagged:
            log.info(f"*** {flagged} anomalies flagged this run ***")
    except Exception as e:
        log.error(f"Scorer failed: {e}")
        return
    try:
        clusters = cluster_scorer.run_cluster_scorer()
        if clusters:
            log.info(f"*** {clusters} clusters updated ***")
    except Exception as e:
        log.error(f"Cluster scorer failed: {e}")
    try:
        cross_market = cross_market_scorer.run_cross_market_scorer()
        if cross_market:
            log.info(f"*** {cross_market} cross-market clusters updated ***")
    except Exception as e:
        log.error(f"Cross-market scorer failed: {e}")


if __name__ == "__main__":
    db.init_db()

    # Serialize all scheduler jobs so SQLite writes from collection, feeds, and
    # microstructure never overlap (avoids database is locked under WAL).
    executors = {"default": ThreadPoolExecutor(max_workers=1)}
    scheduler = BlockingScheduler(executors=executors, timezone="UTC")
    scheduler.add_job(
        collect_and_score,
        trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL),
        id="collect_and_score",
        name="Collect and score Kalshi markets",
        next_run_time=datetime.now(timezone.utc),  # Run once immediately, then every interval.
        max_instances=1,                           # Never overlap long collection runs.
        coalesce=True,                             # Collapse missed ticks into one catch-up run.
        misfire_grace_time=300,
    )

    scheduler.add_job(
        news_engine.fetch_and_ingest_feeds,
        trigger=IntervalTrigger(minutes=15),
        id="fetch_and_ingest_feeds",
        name="Ingest political RSS and Federal Register feeds",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        microstructure_watcher.run_microstructure_analysis,
        trigger=IntervalTrigger(seconds=15),
        id="run_microstructure_analysis",
        name="Poll and analyze order book microstructure",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=5,
    )

    scheduler.add_job(
        lambda: db.prune_historical_data(order_book_days=14),
        trigger=IntervalTrigger(days=1),
        id="prune_historical_data",
        name="Prune historical data",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        lambda: event_calendar_refresh.refresh_event_calendar(dry_run=False),
        trigger=CronTrigger(day=1, hour=6, minute=0),
        id="refresh_event_calendar",
        name="Refresh FOMC/CPI scheduled event dates",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    log.info(
        "Scheduler started -- running every %s minutes. First run starts immediately. Ctrl+C to stop.",
        SCHEDULER_INTERVAL,
    )
    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")
