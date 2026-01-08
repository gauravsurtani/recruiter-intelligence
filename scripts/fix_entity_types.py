#!/usr/bin/env python3
"""Data migration script to fix entity classification issues.

Fixes:
1. LLCs/organizations misclassified as "person"
2. Placeholder prefixes (N/A, ---, [none], etc.)
3. "LLC" prefix -> suffix
"""

import sqlite3
import re
from pathlib import Path


def clean_entity_name(name: str) -> str:
    """Clean up entity name by removing placeholder prefixes."""
    if not name:
        return name
    # Remove common placeholder prefixes
    prefixes_to_remove = ['N/A ', 'n/a ', '--- ', '[none] ', '. ', '- ', '-- ']
    for prefix in prefixes_to_remove:
        if name.startswith(prefix):
            name = name[len(prefix):]

    # Fix "LLC CompanyName" -> "CompanyName LLC"
    if name.upper().startswith('LLC '):
        name = name[4:] + ' LLC'

    return name.strip()


def is_organization_name(name: str) -> bool:
    """Detect if a name is an organization rather than a person."""
    if not name:
        return False
    name_upper = name.upper()
    # Organization suffixes
    org_indicators = [
        'LLC', 'L.L.C.', 'INC', 'INC.', 'CORP', 'CORP.', 'LTD', 'LTD.',
        'L.P.', 'LP', 'LIMITED', 'PARTNERS', 'PARTNERSHIP', 'FUND',
        'CAPITAL', 'VENTURES', 'MANAGEMENT', 'ADVISORS', 'HOLDINGS',
        'TRUST', 'REIT', 'GROUP', 'COMPANY', 'CO.', 'SARL', 'S.A.'
    ]
    for indicator in org_indicators:
        if indicator in name_upper:
            return True
    if name_upper.startswith(('LLC ', 'THE ')):
        return True
    return False


def fix_entities(db_path: str, dry_run: bool = True):
    """Fix entity classification and names in the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find entities that need fixing
    cursor.execute("""
        SELECT id, name, entity_type FROM kg_entities
        WHERE entity_type = 'person'
        AND (
            name LIKE 'N/A %' OR name LIKE 'n/a %' OR
            name LIKE '--- %' OR name LIKE '[none] %' OR
            name LIKE '. %' OR name LIKE '- %' OR
            name LIKE 'LLC %' OR
            name LIKE '%LLC%' OR name LIKE '%Ltd%' OR
            name LIKE '%Inc%' OR name LIKE '%L.P.%' OR
            name LIKE '%Corp%' OR name LIKE '%Fund%' OR
            name LIKE '%Partners%' OR name LIKE '%Capital%' OR
            name LIKE '%Management%' OR name LIKE '%Advisors%'
        )
    """)

    entities_to_fix = cursor.fetchall()
    print(f"Found {len(entities_to_fix)} entities that may need fixing")

    fixed_names = 0
    fixed_types = 0

    for entity_id, old_name, old_type in entities_to_fix:
        new_name = clean_entity_name(old_name)
        new_type = 'company' if is_organization_name(new_name) else old_type

        name_changed = new_name != old_name
        type_changed = new_type != old_type

        if name_changed or type_changed:
            if dry_run:
                print(f"  Would fix: '{old_name}' ({old_type})")
                print(f"        ->  '{new_name}' ({new_type})")
            else:
                cursor.execute(
                    "UPDATE kg_entities SET name = ?, entity_type = ? WHERE id = ?",
                    (new_name, new_type, entity_id)
                )

            if name_changed:
                fixed_names += 1
            if type_changed:
                fixed_types += 1

    if not dry_run:
        conn.commit()
        print(f"\nFixed {fixed_names} names and {fixed_types} types")
    else:
        print(f"\n[DRY RUN] Would fix {fixed_names} names and {fixed_types} types")
        print("Run with --apply to apply changes")

    conn.close()


if __name__ == "__main__":
    import sys

    db_path = Path(__file__).parent.parent / "data" / "knowledge_graph.db"

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    dry_run = "--apply" not in sys.argv

    print(f"Database: {db_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLYING CHANGES'}")
    print()

    fix_entities(str(db_path), dry_run=dry_run)
