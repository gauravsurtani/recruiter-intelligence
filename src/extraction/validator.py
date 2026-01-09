"""Validate extracted relationships before storing."""
import re
from typing import List, Tuple, Any

# Known false positives to reject
INVALID_ENTITIES = {
    "target", "blank", "href", "http", "https", "www",
    "Reuters", "TechCrunch", "Bloomberg", "Fortune",
    "The Wall Street Journal", "CNBC", "Yahoo Finance",
    "employees", "investor", "investors", "Series A",
    "Series B", "Series C", "Series D", "Series E",
    "Seed", "seed round", "new tech", "AI startup",
    "cap table management", "nuclear fusion company",
    "fusion power company", "chief medical officer",
}

INVALID_PATTERNS = [
    r'^[A-Z]{20,}$',  # All caps gibberish
    r'^CBM[iI]',  # Google News URL artifacts
    r'^\d+$',  # Just numbers
    r'^[a-f0-9]{32,}$',  # Hashes
    r'^target=',  # HTML artifacts
    r'^href=',  # HTML artifacts
    r'<[^>]+>',  # HTML tags
    r'^[^a-zA-Z]*$',  # No letters at all
]


def is_valid_entity_name(name: str) -> bool:
    """Check if entity name is valid."""
    if not name or len(name) < 2:
        return False

    # Strip and normalize
    name = name.strip()
    name_lower = name.lower()

    # Check against invalid names
    if name_lower in {n.lower() for n in INVALID_ENTITIES}:
        return False

    # Check against invalid patterns
    for pattern in INVALID_PATTERNS:
        if re.match(pattern, name):
            return False

    # Must have at least one letter
    if not re.search(r'[a-zA-Z]', name):
        return False

    # Reject if too short (single character or very short non-acronyms)
    if len(name) < 2:
        return False

    # Reject if it looks like a URL
    if name.startswith(('http://', 'https://', 'www.')):
        return False

    return True


def validate_relationship(subject: str, predicate: str, obj: str) -> Tuple[bool, str]:
    """Validate a relationship before storing.

    Returns (is_valid, reason)
    """
    if not is_valid_entity_name(subject):
        return False, f"Invalid subject: {subject}"

    if not is_valid_entity_name(obj):
        return False, f"Invalid object: {obj}"

    # Subject and object shouldn't be the same
    if subject.lower().strip() == obj.lower().strip():
        return False, f"Self-reference: {subject}"

    # Predicate-specific validation
    if predicate == "ACQUIRED":
        # Neither should be generic terms
        generic_terms = {"company", "startup", "firm", "business", "corporation"}
        if subject.lower() in generic_terms or obj.lower() in generic_terms:
            return False, f"Generic acquisition target: {subject} -> {obj}"

    if predicate in ("HIRED_BY", "DEPARTED_FROM"):
        # Subject should look like a person name (typically has space)
        # But allow single-word names if capitalized
        if len(subject.split()) == 1 and not subject[0].isupper():
            return False, f"Subject doesn't look like person name: {subject}"

    if predicate == "FUNDED_BY":
        # Object shouldn't be the same as subject (company can't fund itself)
        # Already checked above
        pass

    return True, "OK"


def filter_extraction_results(relationships: List[Any]) -> List[Any]:
    """Filter out invalid relationships.

    Args:
        relationships: List of relationship objects with subject, predicate, object attributes

    Returns:
        List of valid relationships
    """
    valid = []
    for rel in relationships:
        # Handle both object and dict formats
        if hasattr(rel, 'subject'):
            subject = rel.subject
            predicate = rel.predicate
            obj = rel.object
        else:
            subject = rel.get('subject', '')
            predicate = rel.get('predicate', '')
            obj = rel.get('object', '')

        is_valid, reason = validate_relationship(subject, predicate, obj)
        if is_valid:
            valid.append(rel)
        # Silently filter invalid ones (already logged during extraction)

    return valid
