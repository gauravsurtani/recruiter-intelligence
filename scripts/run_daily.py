#!/usr/bin/env python3
"""Run the daily pipeline."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline.daily import run_daily_pipeline


def main():
    print("\n" + "=" * 50)
    print("RECRUITER INTELLIGENCE PIPELINE")
    print("=" * 50 + "\n")

    stats = asyncio.run(run_daily_pipeline())

    print("\nRESULTS:")
    print(f"  Articles: {stats['fetched_articles']} fetched, {stats['saved_articles']} new")
    print(f"  High signal: {stats['high_signal_articles']}")
    print(f"  Relationships extracted: {stats['extracted_relationships']}")

    res = stats.get('entity_resolution', {})
    print(f"  Entities cleaned: {res.get('duplicates_merged', 0)} merged, {res.get('invalid_removed', 0)} removed")

    enr = stats.get('enrichment', {})
    print(f"  Enriched: {enr.get('companies_enriched', 0)} companies, {enr.get('people_enriched', 0)} people")

    kg = stats['knowledge_graph']
    print(f"\nKNOWLEDGE GRAPH: {kg['total_entities']} entities, {kg['total_relationships']} relationships")

    quality = stats.get('data_quality', {})
    print(f"DATA QUALITY: {quality.get('data_quality_score', 0)}%")
    print(f"TIME: {stats['elapsed_seconds']:.1f}s\n")


if __name__ == "__main__":
    main()
