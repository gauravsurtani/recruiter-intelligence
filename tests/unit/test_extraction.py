"""Unit tests for extraction module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.extraction.interfaces import Entity, Relationship, ExtractionResult
from src.extraction.llm_extractor import LLMExtractor


class TestLLMExtractor:
    """Tests for LLMExtractor with mocked LLM responses."""

    @pytest.fixture
    def mock_llm_response_acquisition(self):
        """Mock LLM response for acquisition."""
        return '''{
            "entities": [
                {"name": "Workday", "type": "company"},
                {"name": "HiredScore", "type": "company"},
                {"name": "Athena Karp", "type": "person", "role": "founder"}
            ],
            "relationships": [
                {
                    "subject": "Workday",
                    "predicate": "ACQUIRED",
                    "object": "HiredScore",
                    "context": "Workday acquired HiredScore for $530M",
                    "confidence": 0.95
                }
            ],
            "event_date": "2024-01-15",
            "amounts": {
                "acquisition": "$530M"
            }
        }'''

    @pytest.fixture
    def mock_llm_response_funding(self):
        """Mock LLM response for funding."""
        return '''{
            "entities": [
                {"name": "Anthropic", "type": "company"},
                {"name": "Google", "type": "investor"}
            ],
            "relationships": [
                {
                    "subject": "Anthropic",
                    "predicate": "FUNDED_BY",
                    "object": "Google",
                    "context": "Anthropic raised $2B led by Google",
                    "confidence": 0.9
                }
            ],
            "amounts": {
                "funding": "$2B"
            }
        }'''

    @pytest.mark.asyncio
    async def test_acquisition_extraction(self, mock_llm_response_acquisition):
        """Should extract acquisition entities and relationships."""
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_llm_response_acquisition)

        extractor = LLMExtractor(llm_client=mock_client)

        result = await extractor.extract(
            title="Workday acquires HiredScore for $530M",
            content="Workday announced the acquisition of HiredScore"
        )

        assert len(result.entities) == 3
        entity_names = [e.name for e in result.entities]
        assert "Workday" in entity_names
        assert "HiredScore" in entity_names

        assert len(result.relationships) == 1
        assert result.relationships[0].predicate == "ACQUIRED"
        assert result.relationships[0].subject == "Workday"
        assert result.relationships[0].object == "HiredScore"

    @pytest.mark.asyncio
    async def test_funding_extraction(self, mock_llm_response_funding):
        """Should extract funding entities and relationships."""
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_llm_response_funding)

        extractor = LLMExtractor(llm_client=mock_client)

        result = await extractor.extract(
            title="Anthropic raises $2B Series D led by Google",
            content="AI safety company Anthropic has raised $2 billion"
        )

        assert len(result.entities) == 2
        entity_names = [e.name for e in result.entities]
        assert "Anthropic" in entity_names
        assert "Google" in entity_names

        funded = [r for r in result.relationships if r.predicate == "FUNDED_BY"]
        assert len(funded) == 1

    @pytest.mark.asyncio
    async def test_parse_invalid_json(self):
        """Should handle invalid JSON gracefully."""
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value="not valid json")

        extractor = LLMExtractor(llm_client=mock_client)

        result = await extractor.extract(
            title="Some article",
            content="Some content"
        )

        assert result.entities == []
        assert result.relationships == []

    @pytest.mark.asyncio
    async def test_batch_extraction(self, mock_llm_response_acquisition):
        """Should extract from multiple articles."""
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_llm_response_acquisition)

        extractor = LLMExtractor(llm_client=mock_client)

        articles = [
            {"title": "Article 1", "content": "Content 1"},
            {"title": "Article 2", "content": "Content 2"},
        ]

        results = await extractor.extract_batch(articles)
        assert len(results) == 2


class TestExtractionDataclasses:
    """Tests for extraction dataclasses."""

    def test_entity_normalized_name(self):
        """Entity should have normalized name."""
        entity = Entity(name="Apple Inc.", entity_type="company")
        assert entity.normalized_name == "apple inc."

    def test_relationship_creation(self):
        """Should create relationship correctly."""
        rel = Relationship(
            subject="Apple",
            subject_type="company",
            predicate="ACQUIRED",
            object="Beats",
            object_type="company",
            confidence=0.9,
            context="Apple acquired Beats"
        )
        assert rel.predicate == "ACQUIRED"
        assert rel.confidence == 0.9

    def test_extraction_result_creation(self):
        """Should create extraction result correctly."""
        result = ExtractionResult(
            entities=[Entity(name="Test", entity_type="company")],
            relationships=[],
            amounts={"funding": "$50M"}
        )
        assert len(result.entities) == 1
        assert result.amounts["funding"] == "$50M"
