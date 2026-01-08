"""Unit tests for knowledge graph module."""

import pytest
import tempfile
import os
from datetime import date

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.knowledge_graph.graph import KnowledgeGraph
from src.knowledge_graph.resolver import EntityResolver


@pytest.fixture
def temp_kg():
    """Provide a temporary knowledge graph database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    kg = KnowledgeGraph(db_path)
    yield kg
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def temp_resolver():
    """Provide a temporary entity resolver."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    resolver = EntityResolver(db_path)
    yield resolver
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


class TestKnowledgeGraph:
    """Tests for KnowledgeGraph."""

    def test_add_entity(self, temp_kg):
        """Should add an entity."""
        entity_id = temp_kg.add_entity("Google", "company")
        assert entity_id > 0

    def test_entity_deduplication(self, temp_kg):
        """Should deduplicate entities by normalized name."""
        id1 = temp_kg.add_entity("Google", "company")
        id2 = temp_kg.add_entity("google", "company")
        id3 = temp_kg.add_entity("GOOGLE", "company")

        assert id1 == id2 == id3

    def test_get_entity(self, temp_kg):
        """Should retrieve entity by name."""
        temp_kg.add_entity("Apple Inc.", "company", {"sector": "tech"})
        entity = temp_kg.get_entity("apple inc.")

        assert entity is not None
        assert entity.name == "Apple Inc."
        assert entity.entity_type == "company"

    def test_search_entities(self, temp_kg):
        """Should search entities by pattern."""
        temp_kg.add_entity("Google", "company")
        temp_kg.add_entity("Google Cloud", "company")
        temp_kg.add_entity("Meta", "company")

        results = temp_kg.search_entities("google")
        assert len(results) >= 2
        assert all("google" in e.normalized_name for e in results)

    def test_add_relationship(self, temp_kg):
        """Should add a relationship between entities."""
        rel_id = temp_kg.add_relationship(
            subject_name="Workday", subject_type="company",
            predicate="ACQUIRED",
            object_name="HiredScore", object_type="company",
            confidence=0.9,
            context="Workday acquired HiredScore"
        )

        assert rel_id is not None

    def test_duplicate_relationship(self, temp_kg):
        """Should prevent duplicate relationships."""
        today = date.today()
        id1 = temp_kg.add_relationship(
            "Workday", "company", "ACQUIRED", "HiredScore", "company",
            event_date=today
        )
        id2 = temp_kg.add_relationship(
            "Workday", "company", "ACQUIRED", "HiredScore", "company",
            event_date=today
        )

        assert id1 is not None
        assert id2 is None  # Duplicate

    def test_query_by_predicate(self, temp_kg):
        """Should query relationships by predicate."""
        temp_kg.add_relationship("Apple", "company", "ACQUIRED", "Beats", "company")
        temp_kg.add_relationship("Google", "company", "ACQUIRED", "Fitbit", "company")
        temp_kg.add_relationship("John Smith", "person", "HIRED_BY", "Google", "company")

        acquisitions = temp_kg.query(predicate="ACQUIRED")
        assert len(acquisitions) == 2

    def test_query_by_subject(self, temp_kg):
        """Should query relationships by subject."""
        temp_kg.add_relationship("Apple", "company", "ACQUIRED", "Beats", "company")
        temp_kg.add_relationship("Apple", "company", "ACQUIRED", "Intel Modem", "company")

        apple_acq = temp_kg.query(subject="apple", predicate="ACQUIRED")
        assert len(apple_acq) == 2

    def test_who_hired(self, temp_kg):
        """Should find people hired by a company."""
        temp_kg.add_relationship("John Doe", "person", "HIRED_BY", "Google", "company")
        temp_kg.add_relationship("Jane Smith", "person", "HIRED_BY", "Google", "company")
        temp_kg.add_relationship("Bob Jones", "person", "HIRED_BY", "Meta", "company")

        google_hires = temp_kg.who_hired("Google")
        assert len(google_hires) == 2

    def test_acquisitions(self, temp_kg):
        """Should get acquisitions."""
        temp_kg.add_relationship("Apple", "company", "ACQUIRED", "Beats", "company")
        temp_kg.add_relationship("Google", "company", "ACQUIRED", "Fitbit", "company")

        acqs = temp_kg.acquisitions()
        assert len(acqs) == 2

    def test_get_stats(self, temp_kg):
        """Should return graph statistics."""
        temp_kg.add_entity("Google", "company")
        temp_kg.add_entity("Meta", "company")
        temp_kg.add_entity("John Doe", "person")
        temp_kg.add_relationship("Google", "company", "ACQUIRED", "Fitbit", "company")

        stats = temp_kg.get_stats()
        assert stats["total_entities"] >= 3
        assert stats["total_relationships"] >= 1
        assert "company" in stats["entities_by_type"]


class TestEntityResolver:
    """Tests for EntityResolver."""

    def test_resolve_known_alias(self, temp_resolver):
        """Should resolve known company aliases."""
        # Built-in aliases
        assert temp_resolver.resolve("facebook", "company") == "meta"
        assert temp_resolver.resolve("alphabet", "company") == "google"

    def test_resolve_unknown_name(self, temp_resolver):
        """Should return original name for unknown entities."""
        result = temp_resolver.resolve("Unknown Corp", "company")
        assert result == "Unknown Corp"

    def test_add_and_resolve_alias(self, temp_resolver):
        """Should add and resolve custom aliases."""
        temp_resolver.add_alias("Microsoft", "MSFT", "company")
        result = temp_resolver.resolve("msft", "company")
        assert result.lower() == "microsoft"

    def test_merge_entities(self, temp_resolver):
        """Should merge entities via alias."""
        temp_resolver.merge("Apple Inc.", "Apple Computer", "company")
        result = temp_resolver.resolve("apple computer", "company")
        assert "apple" in result.lower()
