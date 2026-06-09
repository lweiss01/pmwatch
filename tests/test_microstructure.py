import unittest
import sys
import os
import time

# Add parent directory to path to import microstructure_watcher
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

try:
    import microstructure_watcher
except ImportError:
    microstructure_watcher = None  # Expected during Red phase

class TestMicrostructureWatcher(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(microstructure_watcher, "microstructure_watcher module does not exist yet (expected in RED phase)")
        # Set up a baseline depth for testing
        self.ticker = "KXCPI-26JUN-T0.5"
        
    def test_spoofing_detection(self):
        """Verify that a large wall addition followed by cancellation and opposite trades triggers spoofing."""
        # 1. Setup mock orderbook histories
        # 95th percentile historical depth threshold for testing is, say, 1000 contracts.
        # So we add a wall of 1500 contracts.
        
        # t = 0: normal state
        book_0 = {
            "yes_bids": {0.45: 100.0, 0.44: 200.0},
            "no_bids": {0.55: 100.0, 0.54: 200.0}
        }
        
        # t = 5: Bid wall of 1500 contracts added at price 0.45
        book_5 = {
            "yes_bids": {0.45: 1600.0, 0.44: 200.0},
            "no_bids": {0.55: 100.0, 0.54: 200.0}
        }
        
        # t = 10: Bid wall is canceled (yes_bids drops back to 100 at price 0.45)
        book_10 = {
            "yes_bids": {0.45: 100.0, 0.44: 200.0},
            "no_bids": {0.55: 100.0, 0.54: 200.0}
        }
        
        # Trades during this period:
        # No trades at 0.45 YES. But there were large taker NO trades (opposite side pressure) of 800 contracts.
        class MockTrade:
            def __init__(self, count, price, side, ts):
                self.count = count
                self.price = price
                self.side = side
                self.ts = ts
                
        trades = [
            MockTrade(count=800.0, price=0.55, side="no", ts=7)
        ]
        
        # Run detection manually
        # First, process book_0
        alerts = microstructure_watcher.detect_spoofing(
            self.ticker, book_5, book_0, recent_trades=[], threshold=1000.0, now_ts=5
        )
        self.assertEqual(len(alerts), 0, "No alerts expected on wall insertion")
        
        # Then, process cancellation at t = 10
        alerts_cancel = microstructure_watcher.detect_spoofing(
            self.ticker, book_10, book_5, recent_trades=trades, threshold=1000.0, now_ts=10
        )
        
        self.assertEqual(len(alerts_cancel), 1, "Expected 1 spoofing alert to be generated")
        alert = alerts_cancel[0]
        self.assertEqual(alert["alert_type"], "spoofing")
        self.assertEqual(alert["ticker"], self.ticker)
        self.assertGreater(alert["severity_score"], 0)
        self.assertEqual(alert["details"]["price"], 0.45)
        self.assertEqual(alert["details"]["side"], "yes")

    def test_wash_trading_detection(self):
        """Verify that identical/matching trade sizes within a 30s window on opposite sides trigger wash trading."""
        class MockTrade:
            def __init__(self, id, count_fp, yes_price_dollars, taker_side, created_ts):
                self.id = id
                self.count_fp = count_fp
                self.yes_price_dollars = yes_price_dollars
                self.taker_side = taker_side
                self.created_ts = created_ts

        # Mock trade sequence:
        # Trade 1: Buy 500 contracts at 0.45 YES at t = 100
        # Trade 2: Buy 500 contracts at 0.45 NO at t = 115 (opposite taker side, matching size, time diff = 15s)
        recent_trades = [
            MockTrade(id=1, count_fp=500.0, yes_price_dollars=0.45, taker_side="yes", created_ts=100),
            MockTrade(id=2, count_fp=500.0, yes_price_dollars=0.45, taker_side="no", created_ts=115)
        ]
        
        alerts = microstructure_watcher.detect_wash_trading(self.ticker, recent_trades, window_seconds=30)
        
        self.assertEqual(len(alerts), 1, "Expected 1 wash trading alert to be generated")
        alert = alerts[0]
        self.assertEqual(alert["alert_type"], "wash_trading")
        self.assertEqual(alert["ticker"], self.ticker)
        self.assertEqual(alert["details"]["qty"], 500.0)
        self.assertEqual(alert["details"]["trade_1_id"], 1)
        self.assertEqual(alert["details"]["trade_2_id"], 2)

    def test_trade_cache(self):
        """Verify that trades can be pushed to and retrieved from the in-memory cache."""
        ticker = "KXCACHE-TEST"
        trades = [
            {"id": "T1", "qty": 100.0, "price": 0.50, "side": "yes", "ts": int(time.time()) - 10},
            {"id": "T2", "qty": 200.0, "price": 0.51, "side": "no", "ts": int(time.time()) - 5}
        ]
        
        # Test push
        microstructure_watcher.push_trades(ticker, trades)
        
        # Test get_cached_trades
        cached = microstructure_watcher.get_cached_trades(ticker, max_age_seconds=60)
        self.assertEqual(len(cached), 2)
        self.assertEqual(cached[0]["id"], "T1")
        self.assertEqual(cached[1]["id"], "T2")
        self.assertEqual(cached[0]["count"], 100.0)
        
        # Test duplicates are ignored
        duplicate_trades = [
            {"id": "T2", "qty": 200.0, "price": 0.51, "side": "no", "ts": int(time.time()) - 5},
            {"id": "T3", "qty": 300.0, "price": 0.52, "side": "yes", "ts": int(time.time())}
        ]
        microstructure_watcher.push_trades(ticker, duplicate_trades)
        cached_after = microstructure_watcher.get_cached_trades(ticker, max_age_seconds=60)
        self.assertEqual(len(cached_after), 3, "Duplicate T2 should have been ignored; only T3 added")
        self.assertEqual(cached_after[2]["id"], "T3")

    def test_orderbook_garbage_collection(self):
        """Verify that orderbooks older than 120s are evicted from memory buffer."""
        ticker = "KXOB-TEST"
        microstructure_watcher.clear_orderbook_buffers()
        
        now = int(time.time())
        # Book at now - 150s (older than 120s)
        book_old = {
            "yes_bids": {0.45: 100.0},
            "no_bids": {0.55: 100.0}
        }
        # Book at now - 30s (fresh)
        book_fresh = {
            "yes_bids": {0.45: 150.0},
            "no_bids": {0.55: 150.0}
        }
        
        # Push old book
        microstructure_watcher.push_orderbook(ticker, book_old, timestamp=now - 150)
        buffer = microstructure_watcher._orderbook_buffers[ticker]
        self.assertEqual(len(buffer), 1)
        
        # Push fresh book, should evict the old book because time diff > 120s
        microstructure_watcher.push_orderbook(ticker, book_fresh, timestamp=now - 30)
        buffer = microstructure_watcher._orderbook_buffers[ticker]
        self.assertEqual(len(buffer), 1, "Old book should have been evicted")
        self.assertEqual(buffer[0][0], now - 30, "The remaining book should be the fresh one")

if __name__ == "__main__":
    unittest.main()
