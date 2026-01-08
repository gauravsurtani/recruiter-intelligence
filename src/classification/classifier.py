"""Keyword-based classifier for fast article classification."""

import re
from typing import List, Dict
from .interfaces import (
    ClassifierInterface, ClassificationResult,
    EventType, QualityScore, QualityEvaluatorInterface
)


class KeywordClassifier(ClassifierInterface):
    """Fast keyword-based classifier for initial filtering."""

    PATTERNS: Dict[EventType, Dict[str, List[str]]] = {
        EventType.ACQUISITION: {
            "strong": [
                r"\bacquires?\b", r"\bacquired\b", r"\bacquisition\b",
                r"\bto acquire\b", r"\bbought\b", r"\bbuys\b",
                r"\bmerger\b", r"\bmerges?\b", r"\btakeover\b"
            ],
            "weak": ["deal", "transaction", "strategic"]
        },
        EventType.FUNDING: {
            "strong": [
                r"\braises?\b.*\$", r"\braised\b.*\$",
                r"\bseries [a-e]\b", r"\bseed round\b",
                r"\bfunding round\b", r"\bsecures? funding\b"
            ],
            "weak": ["million", "billion", "valuation", "investors"]
        },
        EventType.EXECUTIVE_MOVE: {
            "strong": [
                r"\bjoins?\b.*\bas\s+(ceo|cto|cfo|coo|vp|president|chief)\b",
                r"\bjoins?\b.*\bas\b",
                r"\bnamed\s+(ceo|cto|cfo|coo|vp|president)\b",
                r"\bappoints?\b", r"\bsteps? down\b",
                r"\bdeparts?\b", r"\bresigns?\b",
                r"\bhired\s+as\b"
            ],
            "weak": ["executive", "leadership", "chief", "president", "vp", "vice president"]
        },
        EventType.LAYOFF: {
            "strong": [
                r"\blayoffs?\b", r"\blays? off\b", r"\blaid off\b",
                r"\bcuts? jobs\b", r"\bjob cuts\b",
                r"\bworkforce reduction\b"
            ],
            "weak": ["restructuring", "downsizing"]
        },
        EventType.IPO: {
            "strong": [
                r"\bipo\b", r"\binitial public offering\b",
                r"\bgoes? public\b", r"\bpublic listing\b"
            ],
            "weak": ["stock", "shares", "trading"]
        }
    }

    def __init__(self):
        self._compile_patterns()

    def _compile_patterns(self):
        """Compile regex patterns for efficiency."""
        self.compiled = {}
        for event_type, patterns in self.PATTERNS.items():
            self.compiled[event_type] = {
                "strong": [re.compile(p, re.IGNORECASE) for p in patterns["strong"]],
                "weak": [re.compile(p, re.IGNORECASE) for p in patterns["weak"]]
            }

    def classify(self, title: str, content: str) -> ClassificationResult:
        """Classify article by event type."""
        # Weight title more heavily
        text = f"{title} {title} {content}"

        scores = {}
        all_matches = []

        for event_type, patterns in self.compiled.items():
            score = 0
            matches = []

            for pattern in patterns["strong"]:
                found = pattern.findall(text)
                score += len(found) * 2
                matches.extend(found)

            for pattern in patterns["weak"]:
                found = pattern.findall(text)
                score += len(found) * 0.5
                matches.extend(found)

            if score > 0:
                scores[event_type] = score
                all_matches.extend(matches)

        if not scores:
            return ClassificationResult(
                primary_type=EventType.OTHER,
                all_types=[EventType.OTHER],
                confidence=0.5,
                matched_keywords=[],
                is_high_signal=False
            )

        # Sort by score
        sorted_types = sorted(scores.keys(), key=lambda t: scores[t], reverse=True)
        primary = sorted_types[0]
        confidence = min(1.0, scores[primary] / 5.0)

        return ClassificationResult(
            primary_type=primary,
            all_types=sorted_types,
            confidence=confidence,
            matched_keywords=list(set(all_matches)),
            is_high_signal=primary != EventType.OTHER and confidence >= 0.5
        )

    def classify_batch(self, articles: List[dict]) -> List[ClassificationResult]:
        """Classify multiple articles."""
        return [
            self.classify(
                article.get("title", ""),
                article.get("content", "") or article.get("summary", "")
            )
            for article in articles
        ]


class QualityEvaluator(QualityEvaluatorInterface):
    """Evaluates extraction quality potential."""

    AMOUNT_PATTERN = re.compile(r'\$[\d,.]+\s*(million|billion|M|B)?', re.IGNORECASE)
    PERSON_INDICATORS = ["ceo", "cto", "cfo", "founder", "president", "partner", "executive", "chief"]

    def evaluate(self, title: str, content: str) -> QualityScore:
        """Evaluate extraction potential."""
        text = f"{title} {content}".lower()

        has_amounts = bool(self.AMOUNT_PATTERN.search(text))
        has_persons = any(ind in text for ind in self.PERSON_INDICATORS)
        has_companies = bool(re.search(r'(inc\.|corp\.|llc|ltd\.)', text, re.IGNORECASE))
        has_dates = bool(re.search(
            r'(january|february|march|april|may|june|july|august|september|october|november|december|\d{4})',
            text
        ))

        factors = [has_amounts, has_persons, has_companies, has_dates]
        score = sum(factors) / len(factors)

        if score >= 0.6:
            potential = "high"
        elif score >= 0.3:
            potential = "medium"
        else:
            potential = "low"

        return QualityScore(
            overall_score=score,
            has_company_names=has_companies,
            has_person_names=has_persons,
            has_amounts=has_amounts,
            has_dates=has_dates,
            extraction_potential=potential
        )
