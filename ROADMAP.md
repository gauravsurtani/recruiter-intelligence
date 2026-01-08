# Recruiter Intelligence - Production Roadmap

This document outlines the path from the current working prototype to a production-ready system.

## Current State (v0.1 - Prototype)

A working single-user system with:
- SQLite database (knowledge graph + article storage)
- FastAPI web UI
- RSS feed ingestion
- SEC Form D filings via edgartools
- spaCy NER + LLM extraction
- Basic entity enrichment via web search

---

## Phase 1: Foundation Improvements

### 1.1 Entity Resolution (Critical)
**Problem**: Entities like "SpaceX Dec 2025 a Series of Witz Ventures LLC" should resolve to "SpaceX"

**Implementation**:
- [ ] Create canonical entity table with aliases
- [ ] Implement ML-based entity linking (using sentence-transformers)
- [ ] Add parent-child company relationships
- [ ] Cross-reference with known company databases

**Files to modify**:
- `src/knowledge_graph/graph.py` - Add entity resolution layer
- `src/extraction/` - Add entity normalization post-processing

### 1.2 Scheduled Pipeline
**Problem**: Manual "Run Pipeline" button doesn't scale

**Implementation**:
- [ ] Add APScheduler or Celery for background jobs
- [ ] Cron-style scheduling (hourly for news, daily for SEC)
- [ ] Job status tracking and history
- [ ] Failure alerting

**Files to create**:
- `src/scheduler/` - New module for job scheduling
- `src/pipeline/jobs.py` - Individual job definitions

### 1.3 Configuration Management
**Problem**: Hardcoded values scattered throughout

**Implementation**:
- [ ] Centralize all config in `src/config/settings.py`
- [ ] Environment-based configuration (dev/staging/prod)
- [ ] Secrets management (API keys, credentials)

---

## Phase 2: Data Layer

### 2.1 PostgreSQL Migration
**Problem**: SQLite doesn't support concurrent writes

**Implementation**:
- [ ] Create SQLAlchemy models
- [ ] Migration scripts (Alembic)
- [ ] Connection pooling
- [ ] Read replicas for queries

**Files to modify**:
- `src/storage/` - Replace SQLite with PostgreSQL
- `src/knowledge_graph/kg_store.py` - Update queries

### 2.2 Caching Layer
**Problem**: Repeated expensive queries

**Implementation**:
- [ ] Redis for hot entity data
- [ ] Query result caching
- [ ] Cache invalidation on updates

### 2.3 Event Sourcing
**Problem**: No audit trail, can't replay history

**Implementation**:
- [ ] Event log for all data changes
- [ ] Temporal versioning of entities
- [ ] Point-in-time queries

---

## Phase 3: Extraction Quality

### 3.1 Multi-Model Ensemble
**Problem**: Single LLM pass can miss or hallucinate

**Implementation**:
- [ ] Fast pre-filter with spaCy (currently partial)
- [ ] LLM extraction only for high-signal articles
- [ ] Cross-validate with multiple models
- [ ] Confidence scoring based on agreement

**Files to modify**:
- `src/extraction/llm_extractor.py` - Add ensemble logic
- `src/extraction/spacy_extractor.py` - Improve as pre-filter

### 3.2 Human-in-the-Loop
**Problem**: No way to correct extraction errors

**Implementation**:
- [ ] Flag low-confidence extractions for review
- [ ] Admin UI for corrections
- [ ] Feedback loop to improve models

### 3.3 Active Learning
**Problem**: Model doesn't improve over time

**Implementation**:
- [ ] Track correction patterns
- [ ] Fine-tune extraction prompts based on feedback
- [ ] A/B test extraction strategies

---

## Phase 4: Data Sources

### 4.1 Additional Sources
| Source | Data Type | Priority |
|--------|-----------|----------|
| LinkedIn Jobs API | Hiring signals | High |
| Crunchbase API | Funding, company data | High |
| PitchBook API | Detailed funding rounds | Medium |
| Twitter/X API | Real-time signals | Medium |
| Company career pages | Direct hiring data | Medium |
| GitHub | Engineering team activity | Low |

### 4.2 Source Reliability Scoring
- [ ] Track accuracy by source
- [ ] Weight confidence by source reliability
- [ ] Detect and handle source-specific biases

---

## Phase 5: API & Integrations

### 5.1 REST/GraphQL API
**Implementation**:
- [ ] FastAPI endpoints for programmatic access
- [ ] GraphQL for flexible entity queries
- [ ] API versioning
- [ ] Rate limiting

**Files to create**:
- `src/api/` - New API module
- `src/api/v1/` - Versioned endpoints

### 5.2 Authentication & Authorization
- [ ] OAuth 2.0 / API keys
- [ ] Role-based access control
- [ ] Audit logging

### 5.3 Webhooks & Alerts
- [ ] Real-time notifications on signals
- [ ] Configurable alert rules
- [ ] Slack/Email integrations

### 5.4 CRM Integrations
- [ ] Salesforce connector
- [ ] HubSpot connector
- [ ] Export to CSV/Excel

---

## Phase 6: Infrastructure

### 6.1 Containerization
- [ ] Dockerfile for web app
- [ ] docker-compose for local dev
- [ ] Kubernetes manifests for production

### 6.2 Monitoring & Observability
- [ ] Structured logging (already using structlog)
- [ ] Metrics (Prometheus)
- [ ] Distributed tracing
- [ ] Error tracking (Sentry)

### 6.3 CI/CD
- [ ] GitHub Actions for tests
- [ ] Automated deployments
- [ ] Database migrations in pipeline

---

## Architecture Diagram (Target State)

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA SOURCES                              │
├──────────┬──────────┬──────────┬──────────┬────────────────┤
│ SEC EDGAR│ RSS/News │ LinkedIn │ Crunchbase│ Company Sites  │
│ Form D   │ Feeds    │ API      │ API       │ (Careers)      │
└────┬─────┴────┬─────┴────┬─────┴────┬──────┴───────┬────────┘
     │          │          │          │              │
     ▼          ▼          ▼          ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│              INGESTION LAYER (Kafka/SQS)                    │
│  • Rate limiting  • Deduplication  • Schema validation      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              EXTRACTION LAYER                                │
│  • spaCy NER (fast, cheap)                                  │
│  • LLM extraction (high-value articles only)                │
│  • Confidence scoring                                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              ENTITY RESOLUTION                               │
│  • ML-based entity linking                                  │
│  • Canonical entity database                                │
│  • Cross-source validation                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              KNOWLEDGE GRAPH (Neo4j/Neptune)                │
│  • Entities + Relationships + Provenance                    │
│  • Temporal versioning                                       │
│  • Confidence decay over time                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              API LAYER                                       │
│  • GraphQL for flexible queries                             │
│  • Webhooks for real-time signals                           │
│  • CRM integrations                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Wins (Can implement in < 1 day each)

1. **Scheduled pipeline** - Add APScheduler to run hourly
2. **Better entity names** - Strip "a Series of..." patterns from company names
3. **Deduplication** - Content hash to avoid re-processing
4. **Export to CSV** - Download companies/candidates lists
5. **Email alerts** - Daily digest of high-signal events

---

## Tech Stack Recommendations

| Component | Current | Recommended |
|-----------|---------|-------------|
| Database | SQLite | PostgreSQL + TimescaleDB |
| Cache | None | Redis |
| Queue | None | Celery + Redis / Temporal |
| Search | SQL LIKE | Elasticsearch |
| Graph DB | Custom SQLite | Neo4j (optional) |
| Monitoring | Logs only | Prometheus + Grafana |
| Deployment | Manual | Docker + Kubernetes |

---

## Contributing

When working on production features:

1. Create a feature branch from `main`
2. Reference this roadmap in PR descriptions
3. Update checkboxes as features are completed
4. Add tests for new functionality
