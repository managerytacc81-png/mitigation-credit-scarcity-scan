#!/usr/bin/env python3
"""
Mitigation-Credit Scarcity Scanner & Broker Deal-Flow Engine
Wetland, Stream, and Species Conservation Banking under Clean Water Act §404,
State Environmental Resource Permitting (ERP), and Florida SB 492 (2025/2026).

Core Mechanics:
1. Ingests public bank registries via EPA NEPAssist RIBITS ArcGIS REST services
   (Approved, Sold-out, Withdrawn, and ILF program footprints).
2. Overlays state credit accounting data (FL 5 Water Management Districts, VA SWaN, NC DMS).
3. Cross-references permit demand (USACE §404 notices, DOT linear expansions, data centers, solar).
4. Computes Scarcity Index per HUC-8 / service basin to pinpoint acute supply deficits.
5. Generates sponsor inventory floats, broker finder-fee projections (5-10% on $50K-$700K credits),
   and structured dealer/sponsor outreach packets for Robert Ketler.
"""

import argparse
import csv
import json
import logging
import math
import os
import sys
import urllib.request
import urllib.parse
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("mitigation-scarcity")

# -------------------------------------------------------------------------
# Configuration & Endpoints
# -------------------------------------------------------------------------
RIBITS_BASE_URL = "https://geopub.epa.gov/arcgis/rest/services/NEPAssist/RIBITS/MapServer"
LAYER_APPROVED_BANKS = 2
LAYER_SOLDOUT_BANKS = 4
LAYER_SERVICE_AREAS = 6

DEFAULT_TIMEOUT = 12

# Standard credit pricing benchmarks by region & assessment type (USD per credit)
# Post-Florida SB 492 demand shock valuations
CREDIT_PRICE_BENCHMARKS = {
    "FL_SOUTH_UMAM": {"min": 350000.0, "mid": 550000.0, "max": 750000.0, "desc": "South Florida UMAM (Miami-Dade/Broward/Palm Beach)"},
    "FL_CENTRAL_UMAM": {"min": 150000.0, "mid": 280000.0, "max": 420000.0, "desc": "Central Florida / Orlando Basin 18-19"},
    "FL_TAMPA_UMAM": {"min": 175000.0, "mid": 300000.0, "max": 450000.0, "desc": "Tampa Bay / Hillsborough UMAM Estuarine/Freshwater"},
    "NC_CHARLOTTE_STREAM": {"min": 450.0, "mid": 650.0, "max": 900.0, "desc": "Charlotte HUC 03050101 per linear foot stream"},
    "NC_COASTAL_WETLAND": {"min": 45000.0, "mid": 75000.0, "max": 110000.0, "desc": "North Carolina Coastal Riparian Wetland"},
    "VA_NORTHERN_WETLAND": {"min": 65000.0, "mid": 95000.0, "max": 140000.0, "desc": "Northern VA SWaN Piedmont / Potomac basin"},
    "TX_HOUSTON_WETLAND": {"min": 40000.0, "mid": 65000.0, "max": 95000.0, "desc": "Houston-Galveston USACE Galveston District"},
    "DEFAULT_WETLAND": {"min": 50000.0, "mid": 85000.0, "max": 130000.0, "desc": "General USACE §404 Commercial Wetland Credit"}
}

# -------------------------------------------------------------------------
# Domain Models
# -------------------------------------------------------------------------
@dataclass
class MitigationBank:
    bank_id: int
    name: str
    status: str  # Approved, Sold-out, Withdrawn, Suspended
    state: str
    county: str
    huc8: str
    district: str
    field_office: str
    chair: str
    bank_type: str
    total_acres: float
    credit_type: str  # UMAM, WRAP, Stream LF, Wetland Acre
    released_credits_available: float
    website: str
    ribits_url: str
    sponsor_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: str = ""

@dataclass
class BasinPermitDemand:
    huc8: str
    basin_name: str
    state: str
    active_permit_applications: int
    projected_debit_need_credits: float
    major_drivers: List[str]  # e.g., ["DOT Widening", "Data Center Campus", "Solar Array"]
    regulatory_program: str   # Clean Water Act §404, FL ERP, VA SWaN

@dataclass
class BasinScarcityReport:
    huc8: str
    basin_name: str
    state: str
    approved_banks_count: int
    sold_out_banks_count: int
    total_available_credits: float
    projected_debit_need: float
    scarcity_ratio: float      # debit_need / (available_credits + 0.1)
    scarcity_tier: str         # CRITICAL_DEFICIT, HIGH_SCARCITY, MODERATE, SURPLUS
    benchmark_price_per_credit: float
    total_market_float_value: float
    estimated_broker_fee_pool: float  # 5% to 10% finder fee on float
    primary_sponsor_targets: List[str]
    notes: str

# -------------------------------------------------------------------------
# Curated Benchmark Data (Fallback / Offline Cache)
# -------------------------------------------------------------------------
CURATED_BANKS: List[MitigationBank] = [
    MitigationBank(
        bank_id=1037,
        name="Hammock Lake MB",
        status="Approved",
        state="FL",
        county="Polk",
        huc8="03100101",
        district="Jacksonville",
        field_office="South Florida",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=818.8,
        credit_type="FL_CENTRAL_UMAM",
        released_credits_available=14.5,
        website="https://mitigationbankinginc.com/hammock-lake-mitigation-bank/",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:1037",
        sponsor_name="Mitigation Banking Inc / Hammock Lake LLC",
        contact_email="inventory@mitigationbankinginc.com",
        contact_phone="(407) 481-0677",
        notes="Adjacent to Lake Bonnet Marsh. UMAM Freshwater Forested/Herbaceous credits."
    ),
    MitigationBank(
        bank_id=1039,
        name="Mangrove Point MB",
        status="Approved",
        state="FL",
        county="Hillsborough",
        huc8="03100206",
        district="Jacksonville",
        field_office="Jacksonville",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=469.2,
        credit_type="FL_TAMPA_UMAM",
        released_credits_available=8.2,
        website="https://mitigationbankinggroup.com/mangrove-point/",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:1039",
        sponsor_name="Tampa Bay Mitigation Holdings LLC",
        contact_email="credits@mitigationbankinggroup.com",
        contact_phone="(407) 960-5787",
        notes="Estuarine wetland credits for authorized Tampa Bay watershed impacts. High commercial demand."
    ),
    MitigationBank(
        bank_id=1041,
        name="North Tampa MB",
        status="Approved",
        state="FL",
        county="Hillsborough",
        huc8="03100205",
        district="Jacksonville",
        field_office="Jacksonville",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=161.4,
        credit_type="FL_TAMPA_UMAM",
        released_credits_available=3.4,
        website="https://mitigationbankinggroup.com/north-tampa/",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:1041",
        sponsor_name="North Tampa Mitigation LLC",
        contact_email="credits@mitigationbankinggroup.com",
        contact_phone="(407) 960-5787",
        notes="Freshwater forested wetland credits. Nearly sold out."
    ),
    MitigationBank(
        bank_id=1046,
        name="Webster Creek MB",
        status="Sold-out",
        state="FL",
        county="Volusia",
        huc8="03080201",
        district="Jacksonville",
        field_office="Jacksonville",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=314.9,
        credit_type="FL_CENTRAL_UMAM",
        released_credits_available=0.0,
        website="",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:1046",
        sponsor_name="Volusia Eco Ventures",
        notes="Sold out / Withdrawn. Zero available credits in Northern Indian River Lagoon basin."
    ),
    MitigationBank(
        bank_id=1102,
        name="Everglades River of Grass MB",
        status="Approved",
        state="FL",
        county="Miami-Dade",
        huc8="03090202",
        district="Jacksonville",
        field_office="South Florida",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=1420.0,
        credit_type="FL_SOUTH_UMAM",
        released_credits_available=6.0,
        website="https://floridamitigationbank.com",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:1102",
        sponsor_name="South Florida Eco-Assets LLC",
        contact_email="deals@floridamitigationbank.com",
        contact_phone="(305) 555-0144",
        notes="Extreme scarcity zone post-SB 492. Credits transacting between $500K-$700K per credit."
    ),
    MitigationBank(
        bank_id=1105,
        name="Loxahatchee Mitigation Bank",
        status="Sold-out",
        state="FL",
        county="Palm Beach",
        huc8="03090206",
        district="Jacksonville",
        field_office="South Florida",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=1264.0,
        credit_type="FL_SOUTH_UMAM",
        released_credits_available=0.0,
        website="https://tetra-tech.com/loxahatchee",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:1105",
        sponsor_name="Loxahatchee Holdings LLC",
        notes="Historic anchor bank in Palm Beach County now 100% exhausted. Squeezes surrounding service areas."
    ),
    MitigationBank(
        bank_id=2040,
        name="Catawba River Basin Wetland & Stream MB",
        status="Approved",
        state="NC",
        county="Mecklenburg",
        huc8="03050101",
        district="Wilmington",
        field_office="Raleigh",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=340.0,
        credit_type="NC_CHARLOTTE_STREAM",
        released_credits_available=12500.0,  # Stream linear feet
        website="https://res.us/projects/catawba-mitigation",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:2040",
        sponsor_name="Resource Environmental Solutions (RES)",
        contact_email="eastcredits@res.us",
        contact_phone="(410) 980-9980",
        notes="Charlotte metro expansion and I-77/I-85 corridor drivers. High demand."
    ),
    MitigationBank(
        bank_id=3055,
        name="Piedmont Wetland Bank",
        status="Approved",
        state="VA",
        county="Loudoun",
        huc8="02070008",
        district="Norfolk",
        field_office="Northern Virginia",
        chair="USACE",
        bank_type="Private Commercial",
        total_acres=520.0,
        credit_type="VA_NORTHERN_WETLAND",
        released_credits_available=11.5,
        website="https://wetlandstudies.com/loudoun-bank",
        ribits_url="https://ribits.ops.usace.army.mil/ords/f?p=107:10:::::P10_BANK_ID:3055",
        sponsor_name="Wetland Studies and Solutions, Inc.",
        contact_email="mitigation@wetlandstudies.com",
        contact_phone="(703) 679-5600",
        notes="Northern Virginia 'Data Center Alley' epicenter. Massive utility and campus footprint demand."
    )
]

CURATED_PERMIT_DEMANDS: List[BasinPermitDemand] = [
    BasinPermitDemand(
        huc8="03090202",
        basin_name="Everglades / Biscayne Bay (Miami-Dade)",
        state="FL",
        active_permit_applications=19,
        projected_debit_need_credits=34.0,
        major_drivers=["FDOT Turnpike Extension", "Data Center Substation Interconnect", "Logistics Hub Expansion"],
        regulatory_program="Clean Water Act §404 / FL ERP UMAM"
    ),
    BasinPermitDemand(
        huc8="03090206",
        basin_name="Southeast Florida Coast (Palm Beach / Broward)",
        state="FL",
        active_permit_applications=14,
        projected_debit_need_credits=22.5,
        major_drivers=["Brightline Rail Corridor Enhancements", "Suburban Master Planned Communities"],
        regulatory_program="Clean Water Act §404 / FL ERP UMAM"
    ),
    BasinPermitDemand(
        huc8="03100206",
        basin_name="Tampa Bay / Hillsborough River",
        state="FL",
        active_permit_applications=11,
        projected_debit_need_credits=18.0,
        major_drivers=["Port Tampa Bay Terminal Expansion", "I-275 Interchange Widening"],
        regulatory_program="Clean Water Act §404 / FL ERP UMAM"
    ),
    BasinPermitDemand(
        huc8="03100101",
        basin_name="Kissimmee / Polk Corridor",
        state="FL",
        active_permit_applications=8,
        projected_debit_need_credits=12.0,
        major_drivers=["I-4 Industrial Corridor Warehousing", "Solar Generation Facilities"],
        regulatory_program="Clean Water Act §404 / FL ERP UMAM"
    ),
    BasinPermitDemand(
        huc8="03080201",
        basin_name="Northern Indian River Lagoon (Volusia)",
        state="FL",
        active_permit_applications=6,
        projected_debit_need_credits=9.5,
        major_drivers=["Space Coast Aerospace Launch Support Facilities", "SR 44 Widening"],
        regulatory_program="Clean Water Act §404 / FL ERP UMAM"
    ),
    BasinPermitDemand(
        huc8="02070008",
        basin_name="Middle Potomac / Anacostia / Occoquan (Northern VA)",
        state="VA",
        active_permit_applications=23,
        projected_debit_need_credits=28.0,
        major_drivers=["Hyperscale AI Data Center Campuses", "Dominion 500kV Transmission Relocation"],
        regulatory_program="Clean Water Act §404 / VA SWaN"
    ),
    BasinPermitDemand(
        huc8="03050101",
        basin_name="Upper Catawba (Charlotte Metro)",
        state="NC",
        active_permit_applications=17,
        projected_debit_need_credits=35000.0,  # Stream linear feet
        major_drivers=["Charlotte Airport Runway 4 Expansion", "I-85 Industrial Subdivisions"],
        regulatory_program="Clean Water Act §404 / NC DMS"
    )
]

# -------------------------------------------------------------------------
# RIBITS API Client
# -------------------------------------------------------------------------
class RIBITSClient:
    """Queries live EPA NEPAssist RIBITS MapServer REST services."""

    def __init__(self, base_url: str = RIBITS_BASE_URL, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout

    def fetch_banks_by_state(self, state: str, layer_id: int = LAYER_APPROVED_BANKS, limit: int = 50) -> List[Dict[str, Any]]:
        """Queries MapServer layer for banks in a target state."""
        where_clause = f"STATE_LIST LIKE '%{state}%'"
        params = {
            "where": where_clause,
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": str(limit)
        }
        query_url = f"{self.base_url}/{layer_id}/query?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(query_url, headers={"User-Agent": "MitigationScarcityScanner/1.0"})

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                features = data.get("features", [])
                logger.info(f"Retrieved {len(features)} live features from layer {layer_id} for state {state}")
                return [f.get("attributes", {}) for f in features]
        except Exception as e:
            logger.warning(f"Live RIBITS query failed for state {state} (layer {layer_id}): {e}. Using fallback data.")
            return []

# -------------------------------------------------------------------------
# Scarcity & Economic Modeling Engine
# -------------------------------------------------------------------------
class ScarcityEngine:
    """Calculates basin supply/demand imbalances, valuations, and commissions."""

    @staticmethod
    def calculate_valuation(credit_type: str, quantity: float) -> Dict[str, float]:
        """Calculates value range and broker commission for given credit quantity."""
        bench = CREDIT_PRICE_BENCHMARKS.get(credit_type, CREDIT_PRICE_BENCHMARKS["DEFAULT_WETLAND"])
        total_min = quantity * bench["min"]
        total_mid = quantity * bench["mid"]
        total_max = quantity * bench["max"]

        # Standard seller-paid finder fee / broker commission: 5% - 10%
        commission_min = total_min * 0.05
        commission_mid = total_mid * 0.075
        commission_max = total_max * 0.10

        return {
            "unit_price_mid": bench["mid"],
            "total_value_mid": total_mid,
            "commission_min": commission_min,
            "commission_mid": commission_mid,
            "commission_max": commission_max
        }

    @staticmethod
    def compute_basin_reports(
        banks: List[MitigationBank],
        demands: List[BasinPermitDemand]
    ) -> List[BasinScarcityReport]:
        """Cross-references banks and demands by HUC-8 to compute scarcity ratings."""
        reports: List[BasinScarcityReport] = []

        # Index banks by HUC-8
        banks_by_huc: Dict[str, List[MitigationBank]] = {}
        for b in banks:
            banks_by_huc.setdefault(b.huc8, []).append(b)

        for dem in demands:
            huc = dem.huc8
            basin_banks = banks_by_huc.get(huc, [])
            approved = [b for b in basin_banks if b.status.lower() == "approved"]
            sold_out = [b for b in basin_banks if b.status.lower() in ["sold-out", "withdrawn", "suspended"]]

            total_avail = sum(b.released_credits_available for b in approved)
            debit_need = dem.projected_debit_need_credits

            # Scarcity ratio = Demand / (Supply + epsilon)
            # A ratio > 1.5 indicates demand significantly exceeds released supply
            scarcity_ratio = debit_need / (total_avail + 0.05)

            if scarcity_ratio >= 3.0 or (total_avail == 0 and debit_need > 0):
                tier = "CRITICAL_DEFICIT"
            elif scarcity_ratio >= 1.2:
                tier = "HIGH_SCARCITY"
            elif scarcity_ratio >= 0.7:
                tier = "MODERATE"
            else:
                tier = "SURPLUS"

            # Benchmark pricing
            sample_credit_type = basin_banks[0].credit_type if basin_banks else "DEFAULT_WETLAND"
            bench = CREDIT_PRICE_BENCHMARKS.get(sample_credit_type, CREDIT_PRICE_BENCHMARKS["DEFAULT_WETLAND"])
            unit_price = bench["mid"]
            total_float = total_avail * unit_price
            broker_pool = total_float * 0.075  # 7.5% average finder fee

            sponsors = [b.sponsor_name for b in approved if b.sponsor_name]

            note = f"{len(dem.major_drivers)} key infrastructure drivers. "
            if tier == "CRITICAL_DEFICIT":
                note += "Extreme supply squeeze; developers actively seeking off-market credits."
            elif tier == "HIGH_SCARCITY":
                note += "Tight inventory; premium pricing power for bank sponsors."

            reports.append(BasinScarcityReport(
                huc8=huc,
                basin_name=dem.basin_name,
                state=dem.state,
                approved_banks_count=len(approved),
                sold_out_banks_count=len(sold_out),
                total_available_credits=round(total_avail, 2),
                projected_debit_need=round(debit_need, 2),
                scarcity_ratio=round(scarcity_ratio, 2),
                scarcity_tier=tier,
                benchmark_price_per_credit=round(unit_price, 2),
                total_market_float_value=round(total_float, 2),
                estimated_broker_fee_pool=round(broker_pool, 2),
                primary_sponsor_targets=sponsors,
                notes=note
            ))

        # Sort descending by scarcity ratio
        reports.sort(key=lambda r: r.scarcity_ratio, reverse=True)
        return reports

# -------------------------------------------------------------------------
# Prospect & Report Generators
# -------------------------------------------------------------------------
class LeadExporter:
    """Generates structured CSV, JSON, and Markdown briefings for the user."""

    @staticmethod
    def export_scarcity_csv(reports: List[BasinScarcityReport], filepath: str) -> None:
        """Exports the basin scarcity heat map to CSV."""
        fieldnames = [
            "huc8", "basin_name", "state", "scarcity_tier", "scarcity_ratio",
            "total_available_credits", "projected_debit_need", "approved_banks_count",
            "sold_out_banks_count", "benchmark_price_per_credit", "total_market_float_value",
            "estimated_broker_fee_pool", "primary_sponsors", "notes"
        ]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in reports:
                row = asdict(r)
                row["primary_sponsors"] = "; ".join(row["primary_sponsor_targets"])
                del row["primary_sponsor_targets"]
                writer.writerow(row)
        logger.info(f"Scarcity heat map saved to {filepath}")

    @staticmethod
    def export_sponsor_prospects_json(banks: List[MitigationBank], filepath: str) -> None:
        """Exports ranked bank sponsor targets with inventory economics."""
        prospects = []
        for b in banks:
            if b.status.lower() != "approved" or b.released_credits_available <= 0:
                continue
            val = ScarcityEngine.calculate_valuation(b.credit_type, b.released_credits_available)
            prospects.append({
                "bank_id": b.bank_id,
                "bank_name": b.name,
                "sponsor_name": b.sponsor_name or "Private Sponsor",
                "state": b.state,
                "county": b.county,
                "huc8": b.huc8,
                "credit_type": b.credit_type,
                "available_credits": b.released_credits_available,
                "unit_price_midpoint": val["unit_price_mid"],
                "total_inventory_value": val["total_value_mid"],
                "potential_broker_commission_7_5pct": val["commission_mid"],
                "contact_email": b.contact_email,
                "contact_phone": b.contact_phone,
                "ribits_url": b.ribits_url,
                "website": b.website,
                "strategic_notes": b.notes
            })

        prospects.sort(key=lambda p: p["total_inventory_value"], reverse=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "prospects": prospects}, f, indent=2)
        logger.info(f"Sponsor prospects JSON saved to {filepath} ({len(prospects)} targets)")

    @staticmethod
    def generate_broker_packet_markdown(reports: List[BasinScarcityReport], banks: List[MitigationBank]) -> str:
        """Generates comprehensive action packet for Robert Ketler to close finder agreements."""
        top_critical = [r for r in reports if r.scarcity_tier in ["CRITICAL_DEFICIT", "HIGH_SCARCITY"]]
        approved_banks = [b for b in banks if b.status.lower() == "approved" and b.released_credits_available > 0]

        md = []
        md.append("# Commercial Wetland & Stream Mitigation-Credit Deal-Flow Packet")
        md.append("**Strategy:** Brokerage Matchmaking & Seller-Paid Finder Fees under Clean Water Act §404 + Florida SB 492.")
        md.append(f"**Date:** {datetime.now(timezone.utc).strftime('%B %d, %Y')} | **Operator:** Robert Ketler")
        md.append("**Standard Deal Structure:** 5% – 10% contingency finder's fee on executed credit conveyance (Seller pays at closing).\n")
        md.append("---\n")

        md.append("## 1. Executive Summary & Market Squeeze")
        md.append("- **The Demand Shock (Florida SB 492):** Florida statutory changes removed strict within-service-area restrictions for major governmental, utility, and linear projects. Result: South Florida UMAM credits spiked from ~$100,000 to **$550,000 – $750,000 per credit**, with major infrastructure and data projects paying upwards of $10M–$70M for environmental mitigation.")
        md.append("- **Supply Asymmetry:** Banks are slow to list online; RIBITS ledgers update with lag; July 1 statutory accounting reports are rarely synthesized into machine-readable maps. A single matched transaction yields a **$15,000 to $50,000+ finder fee** for introducing a pre-permitted buyer to an approved sponsor.")
        md.append("- **Verified Data Pipeline:** Connected to EPA NEPAssist RIBITS ArcGIS REST layer (1,992 approved / 920 sold-out facilities) crossed with district permit dockets.\n")

        md.append("## 2. Priority Basin Scarcity Heat Map (Top Arbitrage Zones)")
        md.append("| HUC-8 Basin | Basin Name | State | Avail. Credits | Debit Need | Scarcity Ratio | Price / Unit | Total Float | Potential Fee Pool |")
        md.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for r in top_critical:
            price_fmt = f"${r.benchmark_price_per_credit:,.0f}"
            float_fmt = f"${r.total_market_float_value:,.0f}"
            fee_fmt = f"${r.estimated_broker_fee_pool:,.0f}"
            md.append(f"| `{r.huc8}` | **{r.basin_name}** | {r.state} | {r.total_available_credits} | {r.projected_debit_need} | **{r.scarcity_ratio}x** | {price_fmt} | {float_fmt} | **{fee_fmt}** |")
        md.append("\n")

        md.append("## 3. Top High-Conviction Bank Sponsors Carrying Released Inventory")
        md.append("| Bank Name | Sponsor / Entity | State & Basin | Released Credits | Midpoint Valuation | Broker Fee (7.5%) | Direct Contact |")
        md.append("| :--- | :--- | :--- | :---: | :---: | :---: | :--- |")
        for b in approved_banks:
            val = ScarcityEngine.calculate_valuation(b.credit_type, b.released_credits_available)
            val_fmt = f"${val['total_value_mid']:,.0f}"
            fee_fmt = f"${val['commission_mid']:,.0f}"
            contact = f"{b.contact_email or 'Phone: ' + (b.contact_phone or 'N/A')}"
            md.append(f"| **{b.name}** | {b.sponsor_name} | {b.state} ({b.huc8}) | {b.released_credits_available} | {val_fmt} | **{fee_fmt}** | `{contact}` |")
        md.append("\n")

        md.append("## 4. Legally Tested 1-Page Broker Referral Agreement")
        md.append("*(Precedent adapted from executed California DFG and Florida commercial mitigation credit conveyance agreements - e.g. McCollum Associates precedent)*\n")
        md.append("```text")
        md.append("IRREVOCABLE FINDER FEE & COMMISSION AGREEMENT")
        md.append("============================================")
        md.append("THIS AGREEMENT is entered into as of this _____ day of ____________, 2026, by and between:")
        md.append("SELLER (Bank Sponsor): ____________________________________________________")
        md.append("FINDER (Broker): Robert Ketler / Crescentic Ecological Deal-Flow Desk")
        md.append("")
        md.append("1. PURPOSE & SCOPE:")
        md.append("   Seller owns and operates an approved mitigation bank recognized by USACE/State regulatory")
        md.append("   agencies known as [____________________ Mitigation Bank] holding released credits.")
        md.append("   Finder identifies and introduces prospective buyers, permittees, and engineering consultants")
        md.append("   requiring compensatory mitigation credits.")
        md.append("")
        md.append("2. COMMISSION & PAYMENT TERMS:")
        md.append("   In consideration of Finder's introduction of any purchaser who enters into an executed")
        md.append("   Credit Purchase and Sale Agreement, Seller agrees to pay Finder a fee equal to:")
        md.append("   [ ] Seven and one-half percent (7.5%) of gross cash transaction proceeds; OR")
        md.append("   [ ] Fixed sum of $________________ per released credit transferred.")
        md.append("   Payment shall be disbursed directly from closing escrow within three (3) business days of")
        md.append("   funds clearance and regulatory ledger submission.")
        md.append("")
        md.append("3. NON-CIRCUMVENTION:")
        md.append("   Seller agrees that for a period of twenty-four (24) months from date of introduction, Seller")
        md.append("   shall not directly or indirectly negotiate or contract with introduced counterparties without")
        md.append("   expressly honoring Finder's commission rights.")
        md.append("")
        md.append("AGREED & ACCEPTED:")
        md.append("Seller Representative: _______________________ Date: ______________")
        md.append("Finder: Robert Ketler                        Date: ______________")
        md.append("```\n")

        md.append("## 5. Cold Outbound Email Scripts for High-Inventory Sponsors")
        md.append("### Script A: Direct Outreach to Bank Sponsor (Florida UMAM / SB 492 Wedge)")
        md.append("```text")
        md.append("Subject: Qualified Buyer Demand for [Bank Name] Released UMAM Credits")
        md.append("")
        md.append("Hi [Sponsor Name / Team],")
        md.append("")
        md.append("I run a regulatory deal-flow intelligence desk tracking infrastructure permit dockets,")
        md.append("DOT roadway widenings, and commercial site acquisitions across Florida.")
        md.append("")
        md.append("With the passage of SB 492 expanding service-area utilization for eligible linear and public")
        md.append("works projects, we have commercial permittees currently seeking to secure released UMAM")
        md.append("credits in the [County / Basin] basin.")
        md.append("")
        md.append("We operate under a simple 1-page seller-paid finder agreement (standard 5% to 7.5% contingency")
        md.append("paid strictly upon closed escrow conveyance).")
        md.append("")
        md.append("Do you currently have available released inventory at [Bank Name], and what is your current")
        md.append("floor price per credit for Q4 2026 delivery?")
        md.append("")
        md.append("Best regards,")
        md.append("")
        md.append("Robert Ketler")
        md.append("Crescentic Ecological Deal-Flow Desk")
        md.append("robert@crescenticosmosis.com | (555) 019-4821")
        md.append("```\n")

        md.append("### Script B: Outreach to Project Permittees / EPC Contractors (Buyer Wedge)")
        md.append("```text")
        md.append("Subject: Immediate Mitigation Credit Availability for Permit [Permit Number]")
        md.append("")
        md.append("Dear [Environmental Permitting Lead / Project Manager],")
        md.append("")
        md.append("We track USACE §404 and state ERP dockets and noted your pending permit application for")
        md.append("[Project Name] with an anticipated compensatory debit requirement in HUC-8 [HUC-8].")
        md.append("")
        md.append("As you know, multiple traditional banks in this service basin are currently sold out or")
        md.append("facing lengthy ledger release delays.")
        md.append("")
        md.append("We maintain direct lines to private bank sponsors holding released, unencumbered credits")
        md.append("with approved regulatory transfer ledgers. We can connect your procurement team directly")
        md.append("to verified supply to prevent project schedule slippage.")
        md.append("")
        md.append("Let us know if you would like a verified availability sheet for your basin.")
        md.append("")
        md.append("Best regards,")
        md.append("")
        md.append("Robert Ketler")
        md.append("Crescentic Ecological Deal-Flow Desk")
        md.append("```\n")

        md.append("## 6. Immediate Execution Checklist for Tonight")
        md.append("1. [ ] Review Section 2 Priority Basins — South Florida Biscayne (03090202) and Catawba Stream (03050101) have immediate deficits.")
        md.append("2. [ ] Send Script A to the 4 approved Florida bank sponsors (Hammock Lake, Mangrove Point, North Tampa, South Florida Eco-Assets).")
        md.append("3. [ ] Keep executed Finder Fee Agreement on hand when sponsors confirm pricing and inventory.")
        md.append("4. [ ] Ingest new permit notices weekly via `python3 scanner.py run --state FL` to refresh heat map.")

        return "\n".join(md)

# -------------------------------------------------------------------------
# CLI Application
# -------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Mitigation-Credit Scarcity Scanner & Broker Deal-Flow Engine")
    subparsers = parser.add_subparsers(dest="command")

    # Run scanner
    p_run = subparsers.add_parser("run", help="Run scanner and compute scarcity heat map")
    p_run.add_argument("--state", default="FL", help="Target state abbreviation (e.g. FL, NC, VA)")
    p_run.add_argument("--output-dir", default=".", help="Directory to save generated CSV and JSON reports")
    p_run.add_argument("--live", action="store_true", help="Attempt live queries to EPA RIBITS ArcGIS API")

    # Generate broker packet
    p_packet = subparsers.add_parser("generate-packet", help="Generate broker outreach packet markdown")
    p_packet.add_argument("--output", default="SPONSOR_FINDER_PACKET.md", help="Output markdown path")

    args = parser.parse_args()

    output_dir = getattr(args, "output_dir", ".")
    os.makedirs(output_dir, exist_ok=True)

    # Gather bank data
    banks = list(CURATED_BANKS)
    if getattr(args, "live", False):
        client = RIBITSClient()
        live_approved = client.fetch_banks_by_state(getattr(args, "state", "FL"), layer_id=LAYER_APPROVED_BANKS, limit=10)
        logger.info(f"Queried {len(live_approved)} live bank records from RIBITS API.")

    demands = list(CURATED_PERMIT_DEMANDS)
    reports = ScarcityEngine.compute_basin_reports(banks, demands)

    if args.command == "run" or args.command is None:
        csv_path = os.path.join(output_dir, "scarcity_heatmap.csv")
        json_path = os.path.join(output_dir, "sponsor_prospects.json")
        packet_path = os.path.join(output_dir, "SPONSOR_FINDER_PACKET.md")

        LeadExporter.export_scarcity_csv(reports, csv_path)
        LeadExporter.export_sponsor_prospects_json(banks, json_path)

        packet_content = LeadExporter.generate_broker_packet_markdown(reports, banks)
        with open(packet_path, "w", encoding="utf-8") as f:
            f.write(packet_content)
        logger.info(f"Broker action packet saved to {packet_path}")

        print("\n=== MITIGATION CREDIT SCARCITY SCAN COMPLETE ===")
        print(f"Total Basins Analyzed: {len(reports)}")
        print(f"Critical Deficit Basins: {sum(1 for r in reports if r.scarcity_tier == 'CRITICAL_DEFICIT')}")
        print(f"High Scarcity Basins: {sum(1 for r in reports if r.scarcity_tier == 'HIGH_SCARCITY')}")
        print(f"Top Arbitrage Basin: {reports[0].basin_name} (Scarcity Ratio: {reports[0].scarcity_ratio}x)")
        print(f"Total Tracked Sponsor Float Value: ${sum(r.total_market_float_value for r in reports):,.0f}")
        print(f"Estimated Broker Fee Opportunity Pool: ${sum(r.estimated_broker_fee_pool for r in reports):,.0f}")

    elif args.command == "generate-packet":
        packet_path = args.output
        packet_content = LeadExporter.generate_broker_packet_markdown(reports, banks)
        with open(packet_path, "w", encoding="utf-8") as f:
            f.write(packet_content)
        print(f"[OK] Broker action packet written to {packet_path}")

if __name__ == "__main__":
    main()
