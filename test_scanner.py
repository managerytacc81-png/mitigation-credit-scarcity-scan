"""
Unit and Integration Tests for Mitigation-Credit Scarcity Scanner & Broker Deal-Flow Engine
"""

import os
import json
import csv
import unittest
from unittest.mock import patch, MagicMock
from scanner import (
    MitigationBank,
    BasinPermitDemand,
    BasinScarcityReport,
    ScarcityEngine,
    LeadExporter,
    RIBITSClient,
    CREDIT_PRICE_BENCHMARKS,
    CURATED_BANKS,
    CURATED_PERMIT_DEMANDS
)

class TestScarcityEngine(unittest.TestCase):
    def test_calculate_valuation_fl_south(self):
        val = ScarcityEngine.calculate_valuation("FL_SOUTH_UMAM", 5.0)
        self.assertAlmostEqual(val["unit_price_mid"], 550000.0)
        self.assertAlmostEqual(val["total_value_mid"], 2750000.0)
        self.assertAlmostEqual(val["commission_min"], 5.0 * 350000.0 * 0.05)
        self.assertAlmostEqual(val["commission_mid"], 2750000.0 * 0.075)
        self.assertAlmostEqual(val["commission_max"], 5.0 * 750000.0 * 0.10)

    def test_calculate_valuation_default_fallback(self):
        val = ScarcityEngine.calculate_valuation("UNKNOWN_CREDIT_TYPE", 2.0)
        bench = CREDIT_PRICE_BENCHMARKS["DEFAULT_WETLAND"]
        self.assertAlmostEqual(val["unit_price_mid"], bench["mid"])
        self.assertAlmostEqual(val["total_value_mid"], 2.0 * bench["mid"])
        self.assertGreater(val["commission_mid"], 0)

    def test_compute_basin_reports_critical_deficit(self):
        # Setup: zero available credits and high debit need -> CRITICAL_DEFICIT
        banks = [
            MitigationBank(
                bank_id=999,
                name="Depleted Bank",
                status="Sold-out",
                state="FL",
                county="Orange",
                huc8="03099999",
                district="Jacksonville",
                field_office="Orlando",
                chair="USACE",
                bank_type="Private Commercial",
                total_acres=100.0,
                credit_type="FL_CENTRAL_UMAM",
                released_credits_available=0.0,
                website="",
                ribits_url="",
                sponsor_name="Bank Sponsor X"
            )
        ]
        demands = [
            BasinPermitDemand(
                huc8="03099999",
                basin_name="Test High Demand Basin",
                state="FL",
                active_permit_applications=5,
                projected_debit_need_credits=10.0,
                major_drivers=["DOT Highway Widening"],
                regulatory_program="Clean Water Act §404"
            )
        ]

        reports = ScarcityEngine.compute_basin_reports(banks, demands)
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertEqual(r.huc8, "03099999")
        self.assertEqual(r.scarcity_tier, "CRITICAL_DEFICIT")
        self.assertGreater(r.scarcity_ratio, 10.0)
        self.assertEqual(r.approved_banks_count, 0)
        self.assertEqual(r.sold_out_banks_count, 1)

    def test_compute_basin_reports_surplus(self):
        # Setup: high supply, low demand -> SURPLUS
        banks = [
            MitigationBank(
                bank_id=888,
                name="Massive Surplus Bank",
                status="Approved",
                state="FL",
                county="Marion",
                huc8="03088888",
                district="Jacksonville",
                field_office="Jacksonville",
                chair="USACE",
                bank_type="Private Commercial",
                total_acres=2000.0,
                credit_type="DEFAULT_WETLAND",
                released_credits_available=100.0,
                website="",
                ribits_url="",
                sponsor_name="Surplus Sponsor"
            )
        ]
        demands = [
            BasinPermitDemand(
                huc8="03088888",
                basin_name="Quiet Rural Basin",
                state="FL",
                active_permit_applications=1,
                projected_debit_need_credits=2.0,
                major_drivers=["Single Subdivision"],
                regulatory_program="Clean Water Act §404"
            )
        ]

        reports = ScarcityEngine.compute_basin_reports(banks, demands)
        self.assertEqual(len(reports), 1)
        r = reports[0]
        self.assertEqual(r.scarcity_tier, "SURPLUS")
        self.assertLess(r.scarcity_ratio, 0.5)

class TestLeadExporter(unittest.TestCase):
    def setUp(self):
        self.test_dir = "/tmp/mitigation_test"
        os.makedirs(self.test_dir, exist_ok=True)
        self.csv_path = os.path.join(self.test_dir, "test_scarcity.csv")
        self.json_path = os.path.join(self.test_dir, "test_prospects.json")

    def tearDown(self):
        for f in [self.csv_path, self.json_path]:
            if os.path.exists(f):
                os.remove(f)

    def test_export_scarcity_csv(self):
        reports = ScarcityEngine.compute_basin_reports(CURATED_BANKS, CURATED_PERMIT_DEMANDS)
        LeadExporter.export_scarcity_csv(reports, self.csv_path)

        self.assertTrue(os.path.exists(self.csv_path))
        with open(self.csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), len(reports))
            self.assertIn("huc8", reader.fieldnames)
            self.assertIn("scarcity_tier", reader.fieldnames)
            self.assertIn("estimated_broker_fee_pool", reader.fieldnames)

    def test_export_sponsor_prospects_json(self):
        LeadExporter.export_sponsor_prospects_json(CURATED_BANKS, self.json_path)

        self.assertTrue(os.path.exists(self.json_path))
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertIn("prospects", data)
            prospects = data["prospects"]
            self.assertGreater(len(prospects), 0)
            for p in prospects:
                self.assertIn("total_inventory_value", p)
                self.assertIn("potential_broker_commission_7_5pct", p)
                self.assertGreater(p["available_credits"], 0)

    def test_generate_broker_packet_markdown(self):
        reports = ScarcityEngine.compute_basin_reports(CURATED_BANKS, CURATED_PERMIT_DEMANDS)
        packet = LeadExporter.generate_broker_packet_markdown(reports, CURATED_BANKS)

        self.assertIn("Commercial Wetland & Stream Mitigation-Credit Deal-Flow Packet", packet)
        self.assertIn("IRREVOCABLE FINDER FEE & COMMISSION AGREEMENT", packet)
        self.assertIn("Subject: Qualified Buyer Demand", packet)
        self.assertIn("Subject: Immediate Mitigation Credit Availability", packet)
        self.assertIn("Robert Ketler", packet)

class TestRIBITSClient(unittest.TestCase):
    def test_client_fallback_on_error(self):
        client = RIBITSClient(base_url="https://invalid-host-for-test-999.gov", timeout=1)
        results = client.fetch_banks_by_state("FL")
        self.assertEqual(results, [])

    @patch("urllib.request.urlopen")
    def test_client_mock_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "features": [
                {"attributes": {"BANK_ID": 777, "BANK_NAME": "Mock Wetlands", "STATE_LIST": "FL"}}
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        client = RIBITSClient()
        results = client.fetch_banks_by_state("FL")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["BANK_ID"], 777)

if __name__ == "__main__":
    unittest.main()
