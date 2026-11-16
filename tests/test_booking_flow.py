import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from booking_flow import BookingRequest, CandidateSlot, Plan, choose_plan


def slot(court, start, price):
    base = 1_900_000_000_000
    return CandidateSlot(court, court, str(base + start * 3600000),
                         str(base + (start + 1) * 3600000), "网球", Decimal(price), True)


class BookingFlowTests(unittest.TestCase):
    def choose(self, slots, plans, budget):
        request = BookingRequest(tuple(item.key for item in slots), tuple(plans), Decimal(budget))
        return choose_plan(request, slots)

    def test_single_skips_expensive_first_court(self):
        result = self.choose([slot("A", 0, "300"), slot("B", 0, "100")], [Plan("single_slot", 1)], "150")
        self.assertEqual(result[1][0].ground_id, "B")

    def test_contiguous_skips_expensive_first_bundle(self):
        slots = [slot("A", 0, "200"), slot("A", 1, "200"), slot("B", 0, "100"), slot("B", 1, "100")]
        result = self.choose(slots, [Plan("contiguous_bundle", 2)], "250")
        self.assertEqual([item.ground_id for item in result[1]], ["B", "B"])

    def test_contiguous_does_not_join_different_courts(self):
        result = self.choose([slot("A", 0, "100"), slot("B", 1, "100")], [Plan("contiguous_bundle", 2)], "300")
        self.assertIsNone(result)

    def test_fallback_only_after_primary_has_no_match(self):
        plans = [Plan("contiguous_bundle", 2), Plan("single_slot", 1)]
        result = self.choose([slot("A", 0, "100")], plans, "300")
        self.assertEqual(result[0].kind, "single_slot")

    def test_split_does_not_book_overlapping_courts(self):
        result = self.choose([slot("A", 0, "100"), slot("B", 0, "100")], [Plan("split_bundle", 2)], "300")
        self.assertIsNone(result)

    def test_split_skips_expensive_early_slot_but_keeps_time_priority(self):
        slots = [slot("A", 0, "250"), slot("B", 0, "100"), slot("A", 1, "100"), slot("B", 2, "100")]
        result = self.choose(slots, [Plan("split_bundle", 2)], "200")
        self.assertEqual([item.ground_id for item in result[1]], ["B", "A"])

    def test_large_split_range_with_insufficient_budget_returns_promptly(self):
        import time
        slots = [slot(str(court), hour, "100") for hour in range(8) for court in range(30)]
        started = time.monotonic()
        result = self.choose(slots, [Plan("split_bundle", 4)], "300")
        self.assertIsNone(result)
        self.assertLess(time.monotonic() - started, 2)
