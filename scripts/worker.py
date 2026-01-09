"""Production worker for scheduled pipeline tasks.

This worker runs as a separate service and handles:
- Periodic article fetching (every 6 hours)
- Classification and extraction (every 6 hours)
- Entity resolution (daily)
- Enrichment (daily)
- Newsletter generation (daily)
- Health monitoring (hourly)

Usage:
    python scripts/worker.py

Environment Variables:
    DATABASE_URL: PostgreSQL connection string
    LLM_PROVIDER: gemini, anthropic, or openai
    GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
    SLACK_WEBHOOK_URL: Optional, for alerts
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta
from typing import Optional
import signal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import structlog

logger = structlog.get_logger()

# Check for required dependencies
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    logger.warning("apscheduler not installed, using simple loop instead")


class PipelineWorker:
    """Manages scheduled pipeline tasks."""

    def __init__(self):
        from src.storage.factory import get_article_storage, get_knowledge_graph

        self.storage = get_article_storage()
        self.kg = get_knowledge_graph()
        self.scheduler = AsyncIOScheduler() if SCHEDULER_AVAILABLE else None
        self.running = True

    def setup_jobs(self):
        """Configure scheduled jobs."""
        if not self.scheduler:
            return

        # Fetch new articles every 6 hours
        self.scheduler.add_job(
            self.fetch_articles,
            CronTrigger(hour='0,6,12,18'),
            id='fetch_articles',
            name='Fetch RSS articles',
            replace_existing=True,
            misfire_grace_time=3600
        )

        # Classify and extract every 6 hours (30 min after fetch)
        self.scheduler.add_job(
            self.process_articles,
            CronTrigger(hour='0,6,12,18', minute=30),
            id='process_articles',
            name='Classify and extract articles',
            replace_existing=True,
            misfire_grace_time=3600
        )

        # Entity resolution daily at 2am UTC
        self.scheduler.add_job(
            self.resolve_entities,
            CronTrigger(hour=2),
            id='resolve_entities',
            name='Resolve duplicate entities',
            replace_existing=True,
            misfire_grace_time=3600
        )

        # Enrichment daily at 3am UTC
        self.scheduler.add_job(
            self.enrich_entities,
            CronTrigger(hour=3),
            id='enrich_entities',
            name='Enrich entities with web data',
            replace_existing=True,
            misfire_grace_time=3600
        )

        # Generate newsletter daily at 6am UTC
        self.scheduler.add_job(
            self.generate_newsletter,
            CronTrigger(hour=6),
            id='generate_newsletter',
            name='Generate daily newsletter',
            replace_existing=True,
            misfire_grace_time=3600
        )

        # Health check every hour
        self.scheduler.add_job(
            self.health_check,
            IntervalTrigger(hours=1),
            id='health_check',
            name='Pipeline health check',
            replace_existing=True
        )

        logger.info("jobs_configured", count=len(self.scheduler.get_jobs()))

    async def fetch_articles(self):
        """Fetch new articles from all feeds."""
        logger.info("job_started", job="fetch_articles")
        start_time = datetime.now()

        try:
            from src.pipeline.daily import DailyPipeline

            pipeline = DailyPipeline(
                storage=self.storage,
                kg=self.kg,
                use_form_d=os.environ.get('ENABLE_FORM_D', 'true').lower() == 'true',
                use_gdelt=False,
                use_layoffs=os.environ.get('ENABLE_LAYOFFS', 'true').lower() == 'true',
                use_yc=os.environ.get('ENABLE_YC', 'true').lower() == 'true',
            )

            # Just fetch, don't process
            articles = await pipeline._fetch(days_back=1)
            saved = self.storage.save_articles(articles)

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("job_completed", job="fetch_articles",
                       fetched=len(articles), saved=saved, elapsed_seconds=elapsed)

            return {"fetched": len(articles), "saved": saved}

        except Exception as e:
            logger.error("job_failed", job="fetch_articles", error=str(e))
            await self.send_alert(f"Fetch articles failed: {e}", level="error")
            return {"error": str(e)}

    async def process_articles(self):
        """Classify and extract unprocessed articles."""
        logger.info("job_started", job="process_articles")
        start_time = datetime.now()

        try:
            from src.pipeline.daily import DailyPipeline

            pipeline = DailyPipeline(storage=self.storage, kg=self.kg)

            # Get unprocessed articles
            max_articles = int(os.environ.get('MAX_ARTICLES_PER_RUN', 200))
            unprocessed = self.storage.get_unprocessed(limit=max_articles)

            # Classify
            high_signal = pipeline._classify(unprocessed)

            # Extract high signal articles
            to_extract = self.storage.get_unextracted_high_signal(limit=max_articles // 2)
            extracted = await pipeline._extract(to_extract)

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("job_completed", job="process_articles",
                       classified=len(unprocessed),
                       high_signal=len(high_signal),
                       extracted=extracted,
                       elapsed_seconds=elapsed)

            return {
                "classified": len(unprocessed),
                "high_signal": len(high_signal),
                "extracted": extracted
            }

        except Exception as e:
            logger.error("job_failed", job="process_articles", error=str(e))
            await self.send_alert(f"Process articles failed: {e}", level="error")
            return {"error": str(e)}

    async def resolve_entities(self):
        """Run entity resolution to merge duplicates."""
        logger.info("job_started", job="resolve_entities")
        start_time = datetime.now()

        try:
            from src.knowledge_graph.entity_resolver import EntityResolver

            resolver = EntityResolver(self.kg)
            result = resolver.run_all()

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("job_completed", job="resolve_entities",
                       result=result, elapsed_seconds=elapsed)

            return result

        except Exception as e:
            logger.error("job_failed", job="resolve_entities", error=str(e))
            await self.send_alert(f"Entity resolution failed: {e}", level="error")
            return {"error": str(e)}

    async def enrich_entities(self):
        """Enrich unenriched entities with web data."""
        logger.info("job_started", job="enrich_entities")
        start_time = datetime.now()

        try:
            from src.enrichment.enrichment_service import EnrichmentService

            service = EnrichmentService(self.kg)
            limit = int(os.environ.get('ENRICHMENT_REQUESTS_PER_DAY', 50))

            # Get unenriched entities
            entities = self.kg.search_entities('', entity_type='company')
            unenriched = [e for e in entities if not self.kg.get_enrichment(e.id)]

            enriched_count = 0
            for entity in unenriched[:limit]:
                try:
                    await service.enrich_company(entity.id)
                    enriched_count += 1
                    await asyncio.sleep(1)  # Rate limiting
                except Exception as e:
                    logger.warning("enrichment_failed", entity=entity.name, error=str(e))

            await service.close()

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("job_completed", job="enrich_entities",
                       enriched=enriched_count, elapsed_seconds=elapsed)

            return {"enriched": enriched_count}

        except Exception as e:
            logger.error("job_failed", job="enrich_entities", error=str(e))
            await self.send_alert(f"Enrichment failed: {e}", level="error")
            return {"error": str(e)}

    async def generate_newsletter(self):
        """Generate daily newsletter."""
        logger.info("job_started", job="generate_newsletter")
        start_time = datetime.now()

        try:
            from src.newsletter.generator import NewsletterGenerator
            from pathlib import Path

            gen = NewsletterGenerator()
            newsletter = gen.generate_daily()
            html = gen.to_html(newsletter)

            # Save to file
            output_dir = Path("data/newsletters")
            output_dir.mkdir(exist_ok=True)

            date_str = datetime.now().strftime('%Y%m%d')
            output_path = output_dir / f"newsletter_{date_str}.html"
            output_path.write_text(html)

            # Also save as latest
            (Path("data") / "newsletter.html").write_text(html)

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("job_completed", job="generate_newsletter",
                       path=str(output_path), elapsed_seconds=elapsed)

            return {"path": str(output_path)}

        except Exception as e:
            logger.error("job_failed", job="generate_newsletter", error=str(e))
            await self.send_alert(f"Newsletter generation failed: {e}", level="error")
            return {"error": str(e)}

    async def health_check(self):
        """Check system health and alert if issues."""
        try:
            stats = self.storage.get_stats()
            kg_stats = self.kg.get_stats()

            # Check for large backlogs
            unprocessed = stats.get('unprocessed_articles', 0)
            if unprocessed > 500:
                await self.send_alert(
                    f"Large backlog: {unprocessed} unprocessed articles",
                    level="warning"
                )

            # Check extraction backlog
            # This would require a method to check unextracted count

            logger.debug("health_check",
                        articles=stats,
                        knowledge_graph=kg_stats)

            return {"status": "healthy", "stats": stats, "kg_stats": kg_stats}

        except Exception as e:
            logger.error("health_check_failed", error=str(e))
            await self.send_alert(f"Health check failed: {e}", level="error")
            return {"status": "unhealthy", "error": str(e)}

    async def send_alert(self, message: str, level: str = "warning"):
        """Send alert via Slack webhook (if configured)."""
        webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
        if not webhook_url:
            return

        try:
            import httpx

            emoji = {
                "info": "ℹ️",
                "warning": "⚠️",
                "error": "🚨"
            }.get(level, "📢")

            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json={
                    "text": f"{emoji} *Recruiter Intelligence*\n{message}"
                })
        except Exception as e:
            logger.error("alert_failed", error=str(e))

    def start(self):
        """Start the worker."""
        if self.scheduler:
            self.setup_jobs()
            self.scheduler.start()
            logger.info("worker_started",
                       mode="scheduler",
                       jobs=len(self.scheduler.get_jobs()))
        else:
            logger.info("worker_started", mode="simple_loop")

    def stop(self):
        """Stop the worker gracefully."""
        self.running = False
        if self.scheduler:
            self.scheduler.shutdown(wait=True)
        logger.info("worker_stopped")


async def run_simple_loop(worker: PipelineWorker):
    """Simple loop fallback when APScheduler is not available."""
    last_fetch = None
    last_process = None
    last_resolve = None
    last_newsletter = None

    while worker.running:
        now = datetime.now()

        # Fetch every 6 hours
        if last_fetch is None or (now - last_fetch) >= timedelta(hours=6):
            await worker.fetch_articles()
            last_fetch = now

        # Process 30 min after fetch
        if last_process is None or (now - last_process) >= timedelta(hours=6):
            await asyncio.sleep(60 * 30)  # Wait 30 min
            await worker.process_articles()
            last_process = now

        # Resolve daily
        if last_resolve is None or (now - last_resolve) >= timedelta(days=1):
            await worker.resolve_entities()
            last_resolve = now

        # Newsletter daily
        if last_newsletter is None or (now - last_newsletter) >= timedelta(days=1):
            await worker.generate_newsletter()
            last_newsletter = now

        # Health check
        await worker.health_check()

        # Sleep for an hour
        await asyncio.sleep(3600)


async def main():
    """Main entry point."""
    worker = PipelineWorker()

    # Handle graceful shutdown
    def signal_handler(signum, frame):
        logger.info("shutdown_signal_received", signal=signum)
        worker.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    worker.start()

    # Run immediately on startup
    logger.info("running_initial_tasks")
    await worker.fetch_articles()
    await worker.process_articles()
    await worker.health_check()

    # Keep running
    if SCHEDULER_AVAILABLE:
        try:
            while worker.running:
                await asyncio.sleep(60)
        except (KeyboardInterrupt, SystemExit):
            pass
    else:
        await run_simple_loop(worker)

    worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
