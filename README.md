# Recruiter Intelligence

A knowledge graph-based system for tracking tech industry signals relevant to recruiting: funding rounds, acquisitions, executive moves, and hiring activity.

## Features

- **RSS Feed Aggregation** - Pulls from Crunchbase, Techmeme, VentureBeat, GeekWire, and more
- **SEC Form D Filings** - Real-time funding disclosures from SEC EDGAR
- **Entity Extraction** - spaCy NER + LLM-based relationship extraction
- **Knowledge Graph** - SQLite-based graph of companies, people, and investors
- **Web UI** - Dashboard, company rankings, candidate tracking, timeline view

## Quick Start

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_lg

# Set up environment
cp .env.example .env
# Edit .env with your API keys (ANTHROPIC_API_KEY required)

# Run the web UI
python scripts/kg_viewer.py
# Open http://localhost:8000
```

## Project Structure

```
recruiter-intelligence/
├── src/
│   ├── config/           # Settings and feed configuration
│   ├── ingestion/        # RSS, SEC Form D, GDELT fetchers
│   ├── extraction/       # spaCy and LLM extractors
│   ├── knowledge_graph/  # Graph storage and queries
│   ├── storage/          # Article database
│   ├── pipeline/         # Daily pipeline orchestration
│   ├── validation/       # Cross-referencing and quality
│   └── enrichment/       # Entity enrichment via web search
├── scripts/
│   └── kg_viewer.py      # FastAPI web UI
├── data/                 # SQLite databases (gitignored)
├── tests/                # Test suite
├── ROADMAP.md           # Production roadmap
└── requirements.txt
```

## Data Sources

| Source | Type | Data |
|--------|------|------|
| RSS Feeds | News | Funding announcements, acquisitions, hires |
| SEC EDGAR | Regulatory | Form D filings (funding rounds) |
| spaCy NER | Extraction | Company, person, organization entities |
| LLM (Claude) | Extraction | Relationships, context, confidence |

## Web UI Pages

- **Dashboard** - Overview stats, recent acquisitions/funding/hires
- **Companies** - Ranked by recruiting value signals
- **Candidates** - People ranked by availability signals
- **Pipeline** - Run data ingestion, view source stats
- **Timeline** - Chronological event view
- **Search** - Query entities and relationships
- **Entities/Relationships** - Browse the knowledge graph

## Running the Pipeline

From the web UI:
1. Go to Pipeline page
2. Select days back (1-30)
3. Click "Run Pipeline"

Or programmatically:
```python
from src.pipeline.daily import run_daily_pipeline
import asyncio

stats = asyncio.run(run_daily_pipeline(days_back=7))
print(stats)
```

## Configuration

Key settings in `src/config/settings.py`:
- `ANTHROPIC_API_KEY` - Required for LLM extraction
- `SEC_EDGAR_EMAIL` - Required for SEC API access
- Feed URLs in `src/config/feeds.yaml`

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the production scaling plan, including:
- Entity resolution improvements
- PostgreSQL migration
- Scheduled pipelines
- API layer
- Additional data sources

## License

MIT
