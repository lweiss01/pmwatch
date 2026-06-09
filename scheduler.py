import logging
import json
import os
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
import db
import collector
import scorer
import cluster_scorer

# Load config
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception:
        return {"scheduler_interval_minutes": 60}

CONFIG = load_config()
SCHEDULER_INTERVAL = CONFIG.get("scheduler_interval_minutes", 60)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/scheduler.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def collect_and_score():
    run_time = datetime.now(timezone.utc).isoformat()
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


if __name__ == "__main__":
    db.init_db()

    # Run once immediately on startup
    log.info("Running initial collection on startup...")
    collect_and_score()

    # Then schedule every N minutes (configurable)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        collect_and_score,
        trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL),
        id="collect_and_score",
        name="Collect and score Kalshi markets",
        misfire_grace_time=60
    )

    log.info(f"Scheduler started -- running every {SCHEDULER_INTERVAL} minutes. Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")
