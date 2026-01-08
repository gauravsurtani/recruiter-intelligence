"""Unit tests for classification module."""

import pytest

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.classification.classifier import KeywordClassifier, QualityEvaluator
from src.classification.interfaces import EventType


class TestKeywordClassifier:
    """Tests for KeywordClassifier."""

    def test_acquisition_classification(self):
        """Should classify acquisition articles correctly."""
        classifier = KeywordClassifier()

        result = classifier.classify(
            title="Workday acquires HiredScore for $530M",
            content="Workday announced the acquisition of HiredScore..."
        )

        assert result.primary_type == EventType.ACQUISITION
        assert result.confidence > 0.5
        assert result.is_high_signal

    def test_funding_classification(self):
        """Should classify funding articles correctly."""
        classifier = KeywordClassifier()

        result = classifier.classify(
            title="Anthropic raises $2B Series D led by Google",
            content="AI safety company Anthropic has raised..."
        )

        assert result.primary_type == EventType.FUNDING
        assert result.is_high_signal

    def test_executive_move_classification(self):
        """Should classify executive move articles correctly."""
        classifier = KeywordClassifier()

        result = classifier.classify(
            title="Former Google VP joins Anthropic as CTO",
            content="Anthropic has appointed John Doe as CTO..."
        )

        assert result.primary_type == EventType.EXECUTIVE_MOVE
        assert result.is_high_signal

    def test_layoff_classification(self):
        """Should classify layoff articles correctly."""
        classifier = KeywordClassifier()

        result = classifier.classify(
            title="Meta lays off 10,000 employees in restructuring",
            content="Meta has announced layoffs affecting..."
        )

        assert result.primary_type == EventType.LAYOFF
        assert result.is_high_signal

    def test_ipo_classification(self):
        """Should classify IPO articles correctly."""
        classifier = KeywordClassifier()

        result = classifier.classify(
            title="Reddit files for IPO, plans public listing",
            content="Reddit has filed for initial public offering..."
        )

        assert result.primary_type == EventType.IPO
        assert result.is_high_signal

    def test_noise_classification(self):
        """Should classify non-event articles as OTHER."""
        classifier = KeywordClassifier()

        result = classifier.classify(
            title="New iPhone features revealed",
            content="Apple's latest phone has amazing camera..."
        )

        assert result.primary_type == EventType.OTHER
        assert not result.is_high_signal

    def test_batch_classification(self):
        """Should classify multiple articles."""
        classifier = KeywordClassifier()

        articles = [
            {"title": "Company acquires startup", "content": "Acquisition deal..."},
            {"title": "Startup raises $50M", "content": "Funding round..."},
            {"title": "Weather is nice", "content": "Sunny day..."},
        ]

        results = classifier.classify_batch(articles)
        assert len(results) == 3
        assert results[0].primary_type == EventType.ACQUISITION
        assert results[1].primary_type == EventType.FUNDING
        assert results[2].primary_type == EventType.OTHER


class TestQualityEvaluator:
    """Tests for QualityEvaluator."""

    def test_high_quality_content(self):
        """Should detect high quality content."""
        evaluator = QualityEvaluator()

        result = evaluator.evaluate(
            title="Acme Corp. acquires startup for $100M",
            content="In January 2024, Acme Corp. announced the acquisition led by CEO John Smith..."
        )

        assert result.overall_score >= 0.5
        assert result.has_amounts is True
        assert result.extraction_potential in ["high", "medium"]

    def test_low_quality_content(self):
        """Should detect low quality content."""
        evaluator = QualityEvaluator()

        result = evaluator.evaluate(
            title="Things happened today",
            content="Some stuff occurred in the industry..."
        )

        assert result.overall_score <= 0.5
        assert result.extraction_potential == "low"

    def test_amount_detection(self):
        """Should detect monetary amounts."""
        evaluator = QualityEvaluator()

        result = evaluator.evaluate(
            title="",
            content="The deal was worth $50 million"
        )
        assert result.has_amounts is True

    def test_person_detection(self):
        """Should detect person indicators."""
        evaluator = QualityEvaluator()

        result = evaluator.evaluate(
            title="CEO announces new strategy",
            content=""
        )
        assert result.has_person_names is True
