"""Daily pipeline orchestration."""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

import structlog

from ..config.settings import settings
from ..config.feeds import load_feeds
from ..ingestion.fetcher import RSSFetcher
from ..storage.database import ArticleStorage
from ..classification.classifier import KeywordClassifier
from ..extraction.llm_extractor import LLMExtractor
from ..extraction.validator import filter_extraction_results
from ..knowledge_graph.graph import KnowledgeGraph
from ..knowledge_graph.entity_resolver import EntityResolver
from ..validation.source_validator import SourceValidator
from ..enrichment.enrichment_service import EnrichmentService

# Optional enhanced components
try:
    from ..ingestion.edgar_form_d import FormDFetcher
    FORM_D_AVAILABLE = True
except ImportError:
    FORM_D_AVAILABLE = False

try:
    from ..extraction.spacy_extractor import HybridExtractor
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

try:
    from ..validation.cross_reference import CrossReferencer, FundingEvent, create_funding_event_from_form_d
    CROSS_REF_AVAILABLE = True
except ImportError:
    CROSS_REF_AVAILABLE = False

try:
    from ..ingestion.gdelt_fetcher import GDELTFetcher
    GDELT_AVAILABLE = True
except ImportError:
    GDELT_AVAILABLE = False

try:
    from ..ingestion.layoffs_scraper import LayoffsScraper
    LAYOFFS_AVAILABLE = True
except ImportError:
    LAYOFFS_AVAILABLE = False

try:
    from ..ingestion.yc_scraper import YCScraper
    YC_AVAILABLE = True
except ImportError:
    YC_AVAILABLE = False

logger = structlog.get_logger()


class DailyPipeline:
    """Daily ingestion and processing pipeline."""

    def __init__(
        self,
        storage: ArticleStorage = None,
        kg: KnowledgeGraph = None,
        use_form_d: bool = True,
        use_spacy: bool = True,
        use_gdelt: bool = True,  # Enabled for historical news
        use_cross_ref: bool = True,
        use_layoffs: bool = True,  # Layoffs.fyi scraper
        use_yc: bool = True,  # YC Company directory
    ):
        self.storage = storage or ArticleStorage()
        self.kg = kg or KnowledgeGraph()
        self.classifier = KeywordClassifier()

        # Use hybrid extractor if spaCy available
        self.use_spacy = use_spacy and SPACY_AVAILABLE
        if self.use_spacy:
            self.extractor = HybridExtractor(llm_extractor=LLMExtractor())
            logger.info("using_hybrid_extractor", spacy=True)
        else:
            self.extractor = LLMExtractor()

        # Feature flags
        self.use_form_d = use_form_d and FORM_D_AVAILABLE
        self.use_gdelt = use_gdelt and GDELT_AVAILABLE
        self.use_cross_ref = use_cross_ref and CROSS_REF_AVAILABLE
        self.use_layoffs = use_layoffs and LAYOFFS_AVAILABLE
        self.use_yc = use_yc and YC_AVAILABLE

        logger.info(
            "pipeline_initialized",
            form_d=self.use_form_d,
            spacy=self.use_spacy,
            gdelt=self.use_gdelt,
            cross_ref=self.use_cross_ref,
            layoffs=self.use_layoffs,
            yc=self.use_yc,
        )

    async def run(self, days_back: int = 1, max_articles: int = None) -> dict:
        """Run complete pipeline: fetch → classify → extract → resolve → enrich."""
        start = datetime.now()
        max_articles = max_articles or settings.max_articles_per_run

        # Fetch RSS and save
        articles = await self._fetch(days_back)
        saved = self.storage.save_articles(articles)

        # Fetch SEC Form D filings (parallel)
        form_d_stats = {}
        if self.use_form_d:
            form_d_stats = await self._fetch_form_d(days_back)

        # Optionally fetch GDELT (supplementary)
        gdelt_stats = {}
        if self.use_gdelt:
            gdelt_stats = await self._fetch_gdelt(days_back)

        # Fetch layoffs data
        layoffs_stats = {}
        if self.use_layoffs:
            layoffs_stats = await self._fetch_layoffs(days_back)

        # Fetch YC companies
        yc_stats = {}
        if self.use_yc:
            yc_stats = await self._fetch_yc()

        # Classify new articles
        unprocessed = self.storage.get_unprocessed(limit=max_articles)
        high_signal = self._classify(unprocessed)

        # Get all unextracted high-signal articles (including from previous runs)
        to_extract = self.storage.get_unextracted_high_signal(limit=max_articles)
        logger.info("articles_to_extract", new=len(high_signal), total_unextracted=len(to_extract))

        # Extract to knowledge graph (uses hybrid if available)
        extracted = await self._extract(to_extract)

        # Cross-reference news with Form D
        cross_ref_stats = {}
        if self.use_cross_ref and self.use_form_d:
            cross_ref_stats = self._cross_reference()

        # Clean up entities
        resolution = EntityResolver(self.kg).run_all()

        # Validate sources
        quality = SourceValidator(self.kg).get_validation_report()

        # Enrich entities
        enrichment = await self._enrich()

        return {
            "fetched_articles": len(articles),
            "saved_articles": saved,
            "high_signal_articles": len(high_signal),
            "extracted_relationships": extracted,
            "elapsed_seconds": (datetime.now() - start).total_seconds(),
            "knowledge_graph": self.kg.get_stats(),
            "entity_resolution": resolution,
            "data_quality": quality,
            "enrichment": enrichment,
            "form_d": form_d_stats,
            "gdelt": gdelt_stats,
            "cross_reference": cross_ref_stats,
            "layoffs": layoffs_stats,
            "yc": yc_stats,
        }

    async def _fetch(self, days_back: int) -> List:
        """Fetch articles from RSS feeds."""
        feeds = load_feeds()
        since = datetime.utcnow() - timedelta(days=days_back)

        # Stats callback
        def on_fetch(feed_name: str, articles: int = 0, error: str = None, fetch_time_ms: int = 0):
            self.storage.update_feed_stats(
                feed_name=feed_name,
                articles=articles,
                error=error,
                fetch_time_ms=fetch_time_ms
            )

        async with RSSFetcher(on_fetch_complete=on_fetch) as fetcher:
            return await fetcher.fetch_all(feeds, since=since)

    def _classify(self, articles: List) -> List:
        """Classify articles and mark as processed. Return high-signal ones."""
        high_signal = []
        for article in articles:
            result = self.classifier.classify(article.title, article.content or article.summary)
            self.storage.mark_processed(
                article.id,
                event_type=result.primary_type.value,
                confidence=result.confidence,
                is_high_signal=result.is_high_signal
            )
            if result.is_high_signal:
                high_signal.append(article)
        return high_signal

    async def _extract(self, articles: List) -> int:
        """Extract entities/relationships from articles."""
        count = 0
        for article in articles:
            try:
                result = await self.extractor.extract(article.title, article.content or article.summary)
                if result.relationships:
                    # Filter out invalid relationships before storing
                    valid_relationships = filter_extraction_results(result.relationships)
                    if valid_relationships:
                        result.relationships = valid_relationships
                        self.kg.add_extraction_result(result, source_url=article.url)
                        count += len(valid_relationships)
                        logger.debug("extraction_validated",
                                   article_id=article.id,
                                   original=len(result.relationships) if hasattr(result, '_original_count') else len(valid_relationships),
                                   valid=len(valid_relationships))
                # Mark as extracted AFTER successful extraction (CRITICAL!)
                self.storage.mark_extracted(article.id)
            except Exception as e:
                logger.warning("extraction_error", article_id=article.id, error=str(e))
                # Don't mark as extracted on failure - will retry next run
        return count

    async def _enrich(self, limit: int = 20) -> dict:
        """Enrich unenriched entities with web search."""
        service = EnrichmentService(self.kg)
        stats = {'companies_enriched': 0, 'people_enriched': 0}

        try:
            # Enrich companies
            stats['companies_enriched'] = await self._enrich_type(service, 'company', limit // 2)
            # Enrich people
            stats['people_enriched'] = await self._enrich_type(service, 'person', limit // 2)
        finally:
            await service.close()

        return stats

    async def _enrich_type(self, service: EnrichmentService, entity_type: str, limit: int) -> int:
        """Enrich entities of a specific type."""
        entities = self.kg.search_entities('', entity_type=entity_type)
        unenriched = [e for e in entities if not self.kg.get_enrichment(e.id)]
        count = 0

        for entity in unenriched[:limit]:
            try:
                if entity_type == 'company':
                    await service.enrich_company(entity.id)
                else:
                    await service.enrich_person(entity.id)
                count += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning("enrichment_failed", entity=entity.name, error=str(e))

        return count

    async def _fetch_form_d(self, days_back: int) -> dict:
        """Fetch SEC Form D filings and add to knowledge graph."""
        if not FORM_D_AVAILABLE:
            return {"enabled": False}

        try:
            fetcher = FormDFetcher()
            filings = fetcher.fetch_recent(days_back=days_back)

            added = 0
            for filing in filings:
                try:
                    result = fetcher.to_extraction_result(filing)
                    if result.relationships:
                        self.kg.add_extraction_result(result, source_url=filing.source_url)
                        added += len(result.relationships)
                except Exception as e:
                    logger.warning("form_d_add_error", company=filing.company_name, error=str(e))

            # Store filings for cross-referencing
            self._form_d_filings = filings

            logger.info("form_d_complete", filings=len(filings), relationships=added)
            return {
                "enabled": True,
                "filings_fetched": len(filings),
                "relationships_added": added,
            }

        except Exception as e:
            logger.error("form_d_error", error=str(e))
            return {"enabled": True, "error": str(e)}

    async def _fetch_gdelt(self, days_back: int) -> dict:
        """Fetch GDELT news (supplementary source)."""
        if not GDELT_AVAILABLE:
            return {"enabled": False}

        try:
            fetcher = GDELTFetcher()
            articles = fetcher.fetch_startup_news(days_back=days_back, max_results=100)

            # Convert to raw articles and save
            raw_articles = fetcher.to_raw_articles(articles)
            saved = self.storage.save_articles(raw_articles)

            logger.info("gdelt_complete", fetched=len(articles), saved=saved)
            return {
                "enabled": True,
                "articles_fetched": len(articles),
                "articles_saved": saved,
            }

        except Exception as e:
            logger.error("gdelt_error", error=str(e))
            return {"enabled": True, "error": str(e)}

    async def _fetch_layoffs(self, days_back: int) -> dict:
        """Fetch layoff data from Layoffs.fyi."""
        if not LAYOFFS_AVAILABLE:
            return {"enabled": False}

        try:
            scraper = LayoffsScraper()
            events = await scraper.fetch_layoffs(days_back=days_back)

            added = 0
            for event in events:
                try:
                    result = scraper.to_extraction_result(event)
                    if result.relationships:
                        self.kg.add_extraction_result(result, source_url=event.source_url)
                        added += len(result.relationships)
                except Exception as e:
                    logger.warning("layoffs_add_error", company=event.company, error=str(e))

            logger.info("layoffs_complete", events=len(events), relationships=added)
            return {
                "enabled": True,
                "events_fetched": len(events),
                "relationships_added": added,
            }

        except Exception as e:
            logger.error("layoffs_error", error=str(e))
            return {"enabled": True, "error": str(e)}

    async def _fetch_yc(self) -> dict:
        """Fetch YC company directory."""
        if not YC_AVAILABLE:
            return {"enabled": False}

        try:
            scraper = YCScraper()
            companies = await scraper.fetch_recent_batches(num_batches=4)

            added = 0
            for company in companies:
                try:
                    result = scraper.to_extraction_result(company)
                    if result.relationships:
                        self.kg.add_extraction_result(result, source_url=company.website)
                        added += len(result.relationships)
                except Exception as e:
                    logger.warning("yc_add_error", company=company.name, error=str(e))

            logger.info("yc_complete", companies=len(companies), relationships=added)
            return {
                "enabled": True,
                "companies_fetched": len(companies),
                "relationships_added": added,
            }

        except Exception as e:
            logger.error("yc_error", error=str(e))
            return {"enabled": True, "error": str(e)}

    def _cross_reference(self) -> dict:
        """Cross-reference news funding with Form D filings."""
        if not CROSS_REF_AVAILABLE:
            return {"enabled": False}

        try:
            # Get Form D filings (stored from earlier fetch)
            form_d_filings = getattr(self, '_form_d_filings', [])
            if not form_d_filings:
                return {"enabled": True, "matches": 0, "note": "no_form_d_filings"}

            # Get funding relationships from KG
            funding_rels = self.kg.query(predicate="FUNDED_BY", limit=500)

            # Convert to FundingEvent objects
            news_events = []
            for rel in funding_rels:
                try:
                    event = FundingEvent(
                        company_name=rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject),
                        amount=rel.metadata.get('amount') if hasattr(rel, 'metadata') and rel.metadata else None,
                        date=rel.event_date or datetime.now(),
                        source_type="news",
                        source_url=rel.source_url if hasattr(rel, 'source_url') else None,
                        confidence=rel.confidence if hasattr(rel, 'confidence') else 0.8,
                    )
                    news_events.append(event)
                except Exception:
                    pass

            form_d_events = [create_funding_event_from_form_d(f) for f in form_d_filings]

            # Run cross-reference
            referrer = CrossReferencer()
            matches = referrer.match_news_to_form_d(news_events, form_d_events)

            # Boost confidence for matched events
            boosts = referrer.boost_confidence(matches)

            # Find unmatched Form D filings (potential new data)
            unmatched_form_d = referrer.find_unmatched_form_d(form_d_events, matches)

            logger.info(
                "cross_reference_complete",
                news_events=len(news_events),
                form_d_events=len(form_d_events),
                matches=len(matches),
                unmatched_form_d=len(unmatched_form_d),
            )

            return {
                "enabled": True,
                "news_events": len(news_events),
                "form_d_events": len(form_d_events),
                "matches": len(matches),
                "confidence_boosts": len(boosts),
                "unmatched_form_d": len(unmatched_form_d),
            }

        except Exception as e:
            logger.error("cross_reference_error", error=str(e))
            return {"enabled": True, "error": str(e)}


async def run_daily_pipeline(
    days_back: int = 1,
    max_articles: int = None,
    use_form_d: bool = True,
    use_spacy: bool = True,
    use_gdelt: bool = True,
    use_cross_ref: bool = True,
    use_layoffs: bool = True,
    use_yc: bool = True,
) -> dict:
    """Run the daily pipeline with enhanced features.

    Args:
        days_back: Number of days to look back for data
        max_articles: Maximum articles to process
        use_form_d: Enable SEC Form D filing fetching
        use_spacy: Enable spaCy NER extraction
        use_gdelt: Enable GDELT historical news fetching
        use_cross_ref: Enable cross-referencing news with Form D
        use_layoffs: Enable Layoffs.fyi scraping for displaced talent
        use_yc: Enable Y Combinator directory scraping

    Returns:
        Dict with pipeline statistics
    """
    pipeline = DailyPipeline(
        use_form_d=use_form_d,
        use_spacy=use_spacy,
        use_gdelt=use_gdelt,
        use_cross_ref=use_cross_ref,
        use_layoffs=use_layoffs,
        use_yc=use_yc,
    )
    return await pipeline.run(days_back=days_back, max_articles=max_articles)
