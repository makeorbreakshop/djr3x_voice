# Wookiepedia XML Dump Processing Plan

## Overview
This document outlines the steps to process the full Wookieepedia XML dump (`Star Wars Pages Current_3-22-2025.7z`) while preserving our existing ~100K vectors and prior work.

## Current Status
- Total Pages: 682,279
- Content Pages: 209,827
- Target: Canon content subset from content pages
- Current Progress: XML processor, markup converter, and vector processing pipeline implemented and tested

## Step-by-Step Plan

### 1. Preparation ✅
- [x] Extract the XML dump to a dedicated processing directory
- [x] Create a backup of our existing processing status files:
  - `data/processing_status.csv`
  - `data/pinecone_upload_status.json`
- [x] Analyze dump statistics and content distribution

### 2. Content Type Analysis & Filtering ✅
- [x] Identify total content pages vs. other pages
- [x] Analyze Canon vs. Legends distribution
- [x] Develop Canon content detection logic
- [x] Create test cases for content filtering

### 3. XML Processing Pipeline Development ✅
- [x] Create memory-efficient XML parser
- [x] Implement Canon content filtering
- [x] Add batch processing capability
- [x] Implement progress tracking and logging
- [x] Create WikiMarkupConverter for clean text extraction
- [x] Handle templates, tables, and special markup
- [x] Preserve section structure in plain text output
- [x] Add comprehensive test suite
- [x] Verify pipeline functionality

### 4. Processing Status Management ✅
- [x] Create ProcessStatusManager class for tracking article state
- [x] Implement status file I/O with pandas DataFrame support
- [x] Add error tracking and retry capabilities
- [x] Create comprehensive test suite for status management
- [x] Compare with existing processed URLs to identify:
  - New articles to process
  - Updated articles to reprocess
  - Deleted articles to remove
- [x] Implement resumable processing support
- [x] Add batch processing capabilities
- [ ] Create processing dashboard

### 5. Vector Processing Implementation ✅
- [x] Create XMLVectorProcessor class
- [x] Integrate with existing WikiDumpProcessor
- [x] Implement dual-model embedding generation:
  - OpenAI embeddings (text-embedding-ada-002)
  - E5-small-v2 BERT embeddings
- [x] Add batch vector generation
- [x] Implement Pinecone upload functionality
- [x] Add comprehensive test suite
- [x] Verify end-to-end pipeline

### 6. Processing Dashboard ✅
- [x] Create real-time progress monitoring (CLI dashboard implemented)
- [x] Add error tracking and reporting (CLI dashboard)
- [x] Implement vector upload status visualization (CLI dashboard)
- [x] Add processing statistics display (CLI dashboard)
- [x] Create retry mechanism for failed items (status manager + dashboard)

### 7. Quality Assurance ✅
- [x] Implement content validation checks
- [x] Add vector quality metrics
- [x] Create duplicate detection
- [x] Add automated testing for edge cases
- [x] Implement validation reporting

### 8. Production Deployment 🚧
- [ ] Set up monitoring and alerting
- [ ] Create backup and recovery procedures
- [ ] Document operational procedures
- [ ] Create deployment checklist
- [ ] Perform test run with sample data
- [ ] Execute full production run

## Notes
- CLI dashboard complete; web dashboard and advanced QA are next priorities.
- See `README_HOLOCRON_EXPORT.md` for updated documentation.
- Focus on Canon content filtering accuracy
- Maintain processing state for resumability
- Preserve existing vector quality
- Monitor memory usage during processing

## Potential Bottlenecks

### 1. OpenAI Embedding Generation
- **Challenge**: API rate limits and costs for potentially hundreds of thousands of new articles
- **Solution**: 
  - Implement aggressive batching (100 chunks per API call)
  - Use parallel API calls with careful rate limiting
  - Consider time-based throttling to spread costs
  - Explore local embedding alternatives (e.g., continue using E5-small-v2 model)

### 2. Pinecone Upload Capacity
- **Challenge**: Uploading large volume of vectors efficiently
- **Solution**:
  - Optimize batch sizes (100 vectors per upload)
  - Implement exponential backoff for rate limit handling
  - Monitor upload success rates and adjust batch sizes dynamically

### 3. Memory Management ✅
- **Challenge**: XML parsing of a 1.9GB file requires careful memory management
- **Solution**:
  - Implemented streaming XML parsing in `process_wiki_dump.py`
  - Process in document-by-document mode with batch output
  - Memory-efficient data structures with element clearing
  - Progress tracking with minimal memory overhead

### 4. Duplication Control ✅
- **Challenge**: Avoiding duplicate vectors for already processed articles
- **Solution**:
  - Implemented robust URL tracking in ProcessStatusManager
  - Added URL comparison to identify new/updated/deleted articles
  - Created verification system with batch processing support
  - Added comprehensive test coverage for deduplication logic

## Success Metrics
- Total unique Canon articles processed
- Processing rate (articles/hour)
- API efficiency (tokens/dollar)
- Vector quality metrics
- Coverage compared to prior scraping method 