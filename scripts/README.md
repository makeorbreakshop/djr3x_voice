# DJ R3X Voice App Scripts

This directory contains utility scripts for the DJ R3X Voice app.

## Holocron Knowledge System Scripts

### run_holocron_pipeline.py
Runs the complete Holocron knowledge pipeline:
1. Scrapes content from Wookieepedia
2. Processes content into chunks
3. Generates embeddings
4. Uploads to Supabase vector database

```bash
# Run complete pipeline with default settings (20 articles per category)
python scripts/run_holocron_pipeline.py

# Run with custom limit
python scripts/run_holocron_pipeline.py --limit 50

# Skip scraping and use previously scraped data
python scripts/run_holocron_pipeline.py --skip-scrape
```

### collect_phase1_urls.py
Collects URLs for Phase 1 of the Holocron RAG system:
- R3X/RX-24 specific information
- Oga's Cantina and direct workplace
- DJ/entertainment roles in Star Wars

This script uses both category-based crawling and search term-based collection to find the most relevant articles.

```bash
# Run the complete URL collection for Phase 1
python scripts/collect_phase1_urls.py

# Only generate a report of currently collected URLs
python scripts/collect_phase1_urls.py --report-only
```

### test_supabase_connection.py
Tests connection to the Supabase vector database and verifies proper setup.

```bash
python scripts/test_supabase_connection.py
```

## Database Setup

For reference on setting up the Supabase database for the Holocron system, see `holocron_setup_sql.md` in this directory.

## Holocron RAG System Scripts

### `holocron_setup_sql.md`
Contains the SQL commands needed to set up the Supabase database for the Holocron RAG system:
1. Enabling the pgvector extension
2. Creating the holocron_knowledge table
3. Setting up vector indexing for similarity search
4. Creating helper functions for vector queries

These commands should be run in the Supabase SQL Editor.

## Future Scripts
Additional scripts will be added for:
- Web scraping Wookieepedia content
- Processing text into chunks and generating embeddings
- Uploading data to the Supabase vector database
- Testing vector search functionality 