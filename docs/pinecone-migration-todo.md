# Pinecone Migration TODO

## Environment Setup
- [x] Sign up for Pinecone account
- [x] Create Pinecone API key
- [x] Add API key to `.env` file as `PINECONE_API_KEY`
- [x] Install required dependencies (`pinecone`, `pandas`, `pyarrow`, `boto3`)

## Create Pinecone Index
- [x] Create Pinecone index with proper dimensions (1536 for OpenAI embeddings)
  - Implemented in `scripts/pinecone_migration.py`
  - Using "holocron-knowledge" as index name
  - Configured with cosine similarity metric
- [x] Set `PINECONE_INDEX_NAME` in `.env` file
  - Automatically set by migration script
- [x] Set `PINECONE_INDEX_HOST` in `.env` file
  - Automatically set by migration script

## Adapter Development
- [x] Create `PineconeVectorSearch` adapter class
  - Implemented in `holocron/knowledge/pinecone_adapter.py`
  - Includes search, add_vectors, and delete_vectors functionality
- [x] Implement vector search functionality compatible with existing interface
  - Maintains compatibility with BaseVectorSearch interface
  - Supports metadata filtering and top-k results
- [x] Add `USE_PINECONE` flag to environment variables

## Data Migration
- [x] Export existing vector data from Supabase to CSV
  - Completed on 2025-05-09 (see dev log)
- [x] Create script to convert CSV to Parquet format
  - Implemented in `scripts/csv_to_parquet.py`
  - Handles batched conversion with configurable size
  - Organizes files by namespace
  - Optimizes data structure for Pinecone import
- [x] Set up S3 bucket for Parquet storage
  - Implemented in `scripts/setup_s3_bucket.py`
  - Creates bucket with secure access policies
  - Sets up namespace directory structure
  - Configures environment variables automatically
- [x] Organize files according to Pinecone namespace structure
  - Implemented namespace directories in S3 bucket
  - Supports 'default', 'priority', and 'test' namespaces

## Import Process
- [x] Create script to initiate bulk import to Pinecone
  - Implemented in `scripts/pinecone_migration.py`
  - Includes batch processing with progress tracking
  - Handles vector preparation and metadata formatting
- [x] Create script to monitor import progress
  - Implemented in `scripts/monitor_pinecone_import.py`
  - Tracks import speed and estimates completion time
  - Provides real-time progress visualization
  - Supports expected vector count for accurate progress
- [x] Test import with a small subset before full migration
  - Implemented in `scripts/test_pinecone_import.py`
  - Tests complete pipeline from export to import
  - Includes verification and cleanup
  - Uses isolated test namespace

## URL Processing Pipeline
- [x] Ensure URL scraping pipeline outputs compatible vector format
  - Implemented in `scripts/pinecone_url_processor.py`
  - Generates Pinecone-compatible vectors with metadata
  - Handles content chunking and embedding generation
- [x] Create batching mechanism for new URL vectors
  - Implemented batch processing with configurable size
  - Supports concurrent URL processing with workers
  - Saves vectors to Parquet files by namespace
- [x] Implement efficient Parquet conversion for new vectors
  - Direct Parquet file generation from processed vectors
  - Uses snappy compression for efficiency
  - Organizes files by namespace and batch number
- [x] Develop incremental import process for new data
  - Processes unprocessed URLs from database
  - Prioritizes URLs by importance level
  - Marks URLs as processed after successful import

## Testing and Validation
- [x] Create test script to verify Pinecone search functionality
  - Implemented in `tests/test_pinecone_migration.py`
  - Tests vector addition, search, filtering, and deletion
  - Verifies proper metadata handling
- [x] Test with variety of query types and embedding formats
- [x] Verify proper handling of metadata filters

## Switchover
- [ ] Set `USE_PINECONE=true` in development environment
- [ ] Test application functionality with Pinecone
- [ ] Document any differences in behavior or performance
- [ ] Set `USE_PINECONE=true` in production environment

## Cleanup and Optimization
- [ ] Monitor Pinecone performance metrics
- [ ] Optimize query parameters based on performance
- [ ] Consider index scaling needs based on vector volume
- [ ] Document the migration process and new architecture

## Continuous Integration for New URLs
- [ ] Ensure web scraping pipeline integrates with Pinecone
- [ ] Set up batch processing for new URL content
- [ ] Implement monitoring for vector generation and storage
- [ ] Develop process for updating existing vectors when content changes

## Vector Similarity Search
- [x] Migrate the `simple_holocron_chat.py` script to use async/await patterns
- [x] Update the `PineconeVectorSearch` adapter with appropriate configuration
- [x] Lower similarity threshold from 0.5 to 0.01 to improve recall for character-specific queries

## RAG Enhancements 
- [ ] Implement reranking for character-specific queries using Pinecone's `rerank` parameter
- [ ] Add metadata filtering to supplement vector search for named entities
- [ ] Test hybrid search combining dense and sparse vectors for Star Wars terminology
- [ ] Evaluate chunking strategy and potentially adjust for more precise retrieval

## Monitoring and Optimization
- [ ] Monitor Pinecone performance metrics
- [ ] Set up benchmarking for query latency and relevance
- [ ] Compare retrieval quality between different threshold settings (0.01, 0.05, 0.1)
- [ ] Create test suite with known-good Star Wars character queries 