# Holocron Export Process

This document describes the process of exporting Wookieepedia content to vector embeddings for use in the Holocron knowledge base.

## Overview

The export process consists of several steps:
1. Processing the Wookieepedia XML dump
2. Generating vector embeddings
3. Uploading to Pinecone
4. Tracking and monitoring progress

## Components

### XML Vector Processor

The main processing pipeline is handled by the `XMLVectorProcessor` class, which:
- Processes the XML dump in batches
- Generates embeddings using multiple models
- Uploads vectors to Pinecone
- Tracks processing status

### Processing Dashboard

The processing dashboard provides real-time monitoring of the export process:

#### CLI Dashboard
The CLI dashboard shows:
- Overall progress (articles processed/total)
- Current batch status
- Processing rate
- Error tracking
- Vector generation status

To view the dashboard while processing:
```bash
python scripts/xml_vector_processor.py path/to/dump.xml
```

The dashboard will automatically start and show:
- Processing Status Table
  - Total Articles
  - Processed Articles
  - Vectorized Articles
  - Uploaded Articles
  - Failed Articles
  
- Processing Metrics
  - Processing Rate (articles/minute)
  - Elapsed Time
  - Current Batch Size
  - Average Batch Time

#### Metrics Export
Processing metrics are automatically saved to JSON files in the `logs` directory:
```json
{
  "timestamp": "2025-05-09T13:45:23",
  "total_articles": 1000,
  "processed_articles": 750,
  "failed_articles": 10,
  "vectorized_articles": 700,
  "uploaded_articles": 650,
  "processing_rate": 12.5
}
```

## Usage

1. Prepare the XML dump:
```bash
# Download and extract the dump
wget https://dumps.fandom.com/starwars/latest.xml.gz
gunzip latest.xml.gz
```

2. Run the processor:
```bash
python scripts/xml_vector_processor.py \
  --batch-size 100 \
  --workers 3 \
  --vectors-dir data/vectors \
  path/to/dump.xml
```

Options:
- `--batch-size`: Number of articles to process in each batch (default: 100)
- `--workers`: Number of worker processes for parallel processing (default: 3)
- `--vectors-dir`: Directory to store vector files (default: data/vectors)

## Monitoring

The processing dashboard provides real-time monitoring through:

1. CLI Interface:
   - Rich interactive display
   - Auto-updating metrics
   - Error tracking
   - Progress visualization

2. Metrics Export:
   - JSON files in logs directory
   - Timestamp-based naming
   - Complete processing statistics

## Error Handling

The system includes comprehensive error handling:
- Failed articles are tracked and logged
- Processing can be resumed from last successful point
- Detailed error messages in logs
- Error statistics in dashboard

## Status Files

Processing status is maintained in:
- `data/processing_status.csv`: Article processing status
- `data/pinecone_upload_status.json`: Vector upload status
- `logs/processing_metrics_*.json`: Processing metrics snapshots

## Development

To run tests:
```bash
pytest tests/
```

Key test files:
- `test_xml_vector_processor.py`
- `test_processing_dashboard.py`
- `test_process_status_manager.py` 