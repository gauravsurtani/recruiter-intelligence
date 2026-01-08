#!/usr/bin/env python3
"""CLI tool to query the Knowledge Graph."""

import sys
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.knowledge_graph.graph import KnowledgeGraph
from src.storage.database import ArticleStorage


def print_header(title):
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)


def cmd_stats(args):
    """Show knowledge graph statistics."""
    kg = KnowledgeGraph()
    storage = ArticleStorage()

    stats = kg.get_stats()
    db_stats = storage.get_stats()

    print_header("KNOWLEDGE GRAPH STATISTICS")
    print(f"\nEntities:      {stats['total_entities']}")
    print(f"Relationships: {stats['total_relationships']}")
    print(f"\nArticles:      {db_stats['total_articles']}")
    print(f"Processed:     {db_stats['processed_articles']}")
    print(f"High Signal:   {db_stats['high_signal_articles']}")

    if stats.get('entities_by_type'):
        print("\nEntities by Type:")
        for etype, count in stats['entities_by_type'].items():
            print(f"  {etype:15} {count}")

    if stats.get('relationships_by_type'):
        print("\nRelationships by Type:")
        for rtype, count in stats['relationships_by_type'].items():
            print(f"  {rtype:15} {count}")


def cmd_acquisitions(args):
    """List recent acquisitions."""
    kg = KnowledgeGraph()
    acquisitions = kg.acquisitions()

    print_header(f"ACQUISITIONS ({len(acquisitions)} found)")

    for rel in acquisitions[:args.limit]:
        print(f"\n  {rel.subject.name}")
        print(f"    └─ ACQUIRED ─> {rel.object.name}")
        print(f"       Confidence: {rel.confidence:.0%}")
        if rel.event_date:
            print(f"       Date: {rel.event_date}")
        if rel.source_url:
            print(f"       Source: {rel.source_url[:50]}...")


def cmd_entities(args):
    """List all entities."""
    kg = KnowledgeGraph()
    entities = kg.search_entities(args.query or '')

    if args.type:
        entities = [e for e in entities if e.entity_type == args.type]

    print_header(f"ENTITIES ({len(entities)} found)")

    for entity in entities[:args.limit]:
        print(f"\n  [{entity.entity_type:8}] {entity.name}")
        print(f"             Mentions: {entity.mention_count}")
        if entity.first_seen:
            print(f"             First seen: {entity.first_seen}")


def cmd_search(args):
    """Search for entities and their relationships."""
    kg = KnowledgeGraph()

    # Search entities
    entities = kg.search_entities(args.query)
    print_header(f"SEARCH: '{args.query}'")

    if not entities:
        print("\n  No entities found.")
        return

    print(f"\nFound {len(entities)} matching entities:")
    for entity in entities[:10]:
        print(f"\n  [{entity.entity_type}] {entity.name}")

        # Get relationships
        rels = kg.query(subject=entity.name, limit=5)
        if rels:
            print("    Relationships:")
            for rel in rels:
                print(f"      -> {rel.predicate} -> {rel.object.name}")


def cmd_who_hired(args):
    """Find people hired by a company."""
    kg = KnowledgeGraph()
    hires = kg.who_hired(args.company)

    print_header(f"WHO DID {args.company.upper()} HIRE?")

    if not hires:
        print("\n  No hires found.")
        return

    for rel in hires[:args.limit]:
        print(f"\n  {rel.subject.name}")
        if rel.event_date:
            print(f"    Date: {rel.event_date}")


def main():
    parser = argparse.ArgumentParser(
        description="Query the Recruiter Intelligence Knowledge Graph"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # stats
    subparsers.add_parser("stats", help="Show knowledge graph statistics")

    # acquisitions
    p = subparsers.add_parser("acquisitions", help="List recent acquisitions")
    p.add_argument("--limit", type=int, default=20, help="Max results")

    # entities
    p = subparsers.add_parser("entities", help="List all entities")
    p.add_argument("--query", "-q", default="", help="Filter by name")
    p.add_argument("--type", "-t", help="Filter by type (company, person, investor)")
    p.add_argument("--limit", type=int, default=50, help="Max results")

    # search
    p = subparsers.add_parser("search", help="Search entities and relationships")
    p.add_argument("query", help="Search query")

    # who-hired
    p = subparsers.add_parser("who-hired", help="Find people hired by a company")
    p.add_argument("company", help="Company name")
    p.add_argument("--limit", type=int, default=20, help="Max results")

    args = parser.parse_args()

    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "acquisitions":
        cmd_acquisitions(args)
    elif args.command == "entities":
        cmd_entities(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "who-hired":
        cmd_who_hired(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
