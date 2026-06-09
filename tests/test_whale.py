import unittest
import sys
import os
import time

# Add parent directory to path to import collector
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

try:
    import collector
except ImportError:
    collector = None

class TestWhaleTracker(unittest.TestCase):
    def setUp(self):
        # Configure test DB
        self.original_db_path = db.DB_PATH
        self.test_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_pmwatch_whale.db")
        db.DB_PATH = self.test_db_path
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.init_db()

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.DB_PATH = self.original_db_path

    def test_whale_profiling_and_hourly_rollup(self):
        """Verify that whale trades are flagged and aggregated correctly in database rollups."""
        ticker = "KXCABOUT-26DEC"
        
        # 1. Seed trades in test DB
        # We write 100 normal trades (size 10) and 2 whale trades (size 5000)
        trades_to_insert = []
        # Pin now_ts to the middle of the current hour to avoid top-of-the-hour test flakiness
        now_ts = (int(time.time()) // 3600) * 3600 + 1800
        hour_ts = (now_ts // 3600) * 3600
        
        for i in range(100):
            trades_to_insert.append({
                "trade_id": f"t_{i}",
                "ticker": ticker,
                "count_fp": 10.0,
                "yes_price_dollars": 0.50,
                "no_price_dollars": 0.50,
                "taker_side": "yes",
                "is_block_trade": 0,
                "created_time": "2026-06-09T12:00:00Z",
                "created_ts": now_ts - 600 - i
            })
            
        # Add a block trade (size 50, but marked is_block_trade=1, should count as whale)
        trades_to_insert.append({
            "trade_id": "t_whale_block",
            "ticker": ticker,
            "count_fp": 50.0,
            "yes_price_dollars": 0.52,
            "no_price_dollars": 0.48,
            "taker_side": "yes",
            "is_block_trade": 1,
            "created_time": "2026-06-09T12:05:00Z",
            "created_ts": now_ts - 300
        })
        
        # Add a huge trade (size 10000, should exceed 99th percentile and count as whale)
        trades_to_insert.append({
            "trade_id": "t_whale_huge",
            "ticker": ticker,
            "count_fp": 10000.0,
            "yes_price_dollars": 0.55,
            "no_price_dollars": 0.45,
            "taker_side": "no",  # selling/buying NO
            "is_block_trade": 0,
            "created_time": "2026-06-09T12:06:00Z",
            "created_ts": now_ts - 200
        })
        
        db.insert_trades(trades_to_insert)
        
        # Call whale profiling function (to be implemented in collector.py or helper)
        # It should calculate the threshold (around 10.0 for 99th percentile of [10...10, 50, 10000])
        # and create hourly rollups
        collector.rollup_whale_stats_for_market(ticker)
        
        # Check database
        flow = db.get_whale_flow(ticker, limit=5)
        self.assertEqual(len(flow), 1)
        stat = flow[0]
        expected_hour_ts = (trades_to_insert[-1]["created_ts"] // 3600) * 3600
        self.assertEqual(stat["hour_ts"], expected_hour_ts)
        self.assertEqual(stat["block_trade_count"], 1)
        # YES whale trade is the block trade (size 50)
        self.assertEqual(stat["whale_yes_volume"], 50.0)
        # NO whale trade is the huge trade (size 10000)
        self.assertEqual(stat["whale_no_volume"], 10000.0)
        # Net exposure = YES - NO = 50 - 10000 = -9950.0
        self.assertEqual(stat["net_whale_exposure"], -9950.0)

if __name__ == "__main__":
    unittest.main()
