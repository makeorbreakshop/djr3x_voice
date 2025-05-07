# DJ R3X Holocron — RAG Knowledge System

## Overview
This document outlines the requirements and implementation plan for adding a Retrieval-Augmented Generation (RAG) knowledge system to the DJ R3X voice assistant. The system will allow DJ R3X to access and utilize Star Wars canonical knowledge from Wookieepedia, enhancing the character's ability to engage in informed conversations about the Star Wars universe.

## Goals & Requirements

### Primary Goals
- Enable DJ R3X to retrieve and utilize canonical Star Wars knowledge
- Maintain character voice and personality when delivering factual information
- Create a seamless, efficient knowledge retrieval system that enhances user interaction

### Functional Requirements
1. ✅ Scrape canonical content (excluding Legends/Extended Universe) from Wookieepedia
2. ✅ Process and store information in a vector database on Supabase
3. ✅ Retrieve relevant information based on user queries
4. ✅ Integrate retrieved knowledge into DJ R3X's responses while maintaining character voice
5. ✅ Signal knowledge retrieval through in-character "consulting the holocron" references

### Non-Functional Requirements
1. ⏳ **Performance**: Response latency should remain under 2 seconds when utilizing the RAG system
2. ✅ **Reliability**: System should gracefully handle cases where relevant information is not found
3. ✅ **Maintainability**: Code should be well-documented and follow existing architectural patterns
4. ✅ **Efficiency**: Vector search should be optimized to reduce unnecessary database queries

## System Architecture

### 1. Web Scraping Component (✅ IMPLEMENTED)
- **Function**: Extract canonical content from Wookieepedia
- **Key Features**:
  - ✅ Focus on canonical Star Wars content only
  - ✅ Extract text, headings, and relevant metadata
  - ✅ Respect website's rate limits and robots.txt
  - ✅ Store articles with source references

### 2. Data Processing Pipeline (✅ IMPLEMENTED)
- **Function**: Transform raw content into vector embeddings
- **Key Features**:
  - ✅ Clean and normalize text
  - ✅ Chunk text into semantically meaningful segments
  - ✅ Generate embeddings using OpenAI's text-embedding-3-small model
  - ✅ Add metadata for improved retrieval context

### 3. Supabase Vector Database (✅ IMPLEMENTED)
- **Function**: Store and retrieve vector embeddings
- **Key Features**:
  - ✅ pgvector extension with HNSW indexing
  - ✅ Tables for text chunks, embeddings, and metadata
  - ✅ Optimized similarity search queries
  - ✅ Secure API access from application

### 4. Voice Manager Integration (✅ IMPLEMENTED, ⚠️ TO BE REFACTORED)
- **Function**: Incorporate knowledge retrieval into conversation flow
- **Key Features**:
  - ✅ Hybrid activation approach (direct questions + passive high-relevance checks)
  - ✅ Context insertion into GPT-4o prompts
  - ✅ In-character knowledge presentation
  - ✅ Fallback handling for no-results cases
  - ⚠️ Will need to be refactored to use ROS instead of current event bus

## Implementation Status

The Holocron RAG system has been implemented with all core components functioning as designed:

### Completed Components
1. **Wookieepedia Scraper**: 
   - Successfully extracts canonical Star Wars content
   - Filters out non-canonical (Legends) material
   - Preserves structure and metadata

2. **Data Processing Pipeline**:
   - Chunks articles into appropriate segments
   - Generates embeddings using OpenAI's text-embedding-3-small
   - Uploads content to Supabase vector database

3. **RAG Provider**:
   - Performs semantic search using vector similarity
   - Formats retrieved knowledge for prompt injection
   - Handles edge cases gracefully

4. **Holocron Manager**:
   - Detects knowledge-seeking queries
   - Injects contextual knowledge into system prompts
   - Ensures in-character knowledge delivery
   - Integrates with the current event bus system

## Knowledge Base Expansion Plan

To scale the system from our initial proof-of-concept to a comprehensive Star Wars knowledge base, we've developed a phased approach focused on both technical scalability and content relevance.

### 1. URL Collection System (🔄 IN PROGRESS)
- **Purpose**: Systematically gather and prioritize Wookieepedia articles
- **Tasks**:
  - [✅] Create sitemap crawler for discovering all available articles
  - [✅] Implement category-based URL collection focusing on relevant topics
  - [✅] Store URLs with metadata (priority, category, etc.) in structured format
  - [✅] Add filtering to exclude non-canonical content
  - [✅] Create reporting to understand content coverage
  - **Implementation Details**:
    - Created `sitemap_crawler.py` for comprehensive article discovery
    - Implemented `category_crawler.py` for focused content collection
    - Developed `url_store.py` for Supabase storage with metadata
    - Added `content_filter.py` for canonical vs non-canonical filtering
    - Built `reporting.py` for coverage analysis
    - Resolved dependency conflicts in requirements.txt for stable deployment

### 2. Phased Content Approach (📋 PLANNED)
- **Purpose**: Prioritize the most relevant content for DJ R3X
- **Tasks**:
  - [ ] **Phase 1 - Core Identity**: Collect ~50-100 articles about:
    - [ ] R3X/RX-24 specific information
    - [ ] Oga's Cantina and direct workplace
    - [ ] DJ/entertainment roles in Star Wars
  - [ ] **Phase 2 - Immediate Context**: Expand to ~200-300 articles about:
    - [ ] Batuu and Black Spire Outpost
    - [ ] Galaxy's Edge locations and characters
    - [ ] Star Tours history and locations
  - [ ] **Phase 3 - Professional Context**: Add ~500-1000 articles covering:
    - [ ] Droids (especially RX-series and entertainment models)
    - [ ] Music and musicians in Star Wars
    - [ ] Spaceports and cantinas
    - [ ] Piloting and navigation
  - [ ] **Phase 4 - General Knowledge**: Expand to broader Star Wars knowledge
    - [ ] Major characters, events, and locations
    - [ ] Prioritized by relevance to typical DJ R3X conversations

### 3. Batch Processing System (📋 PLANNED)
- **Purpose**: Efficiently process large numbers of articles
- **Tasks**:
  - [ ] Enhance scraper to work with batched URL lists
  - [ ] Implement queue-based worker system for parallel processing
  - [ ] Add progress tracking and resumability features
  - [ ] Implement polite crawling with rate limiting
  - [ ] Add detailed logging and error recovery
  - [ ] Optimize database operations for bulk insertions

### 4. Database Optimizations (📋 PLANNED)
- **Purpose**: Ensure performance at scale
- **Tasks**:
  - [ ] Implement bulk vector insertion for better performance
  - [ ] Add connection pooling enhancements for parallel operations
  - [ ] Implement retry logic with exponential backoff
  - [ ] Create monitoring for query performance as the database grows
  - [ ] Adjust index parameters if needed for larger collections

### 5. Testing and Validation (📋 PLANNED)
- **Purpose**: Ensure quality and performance at scale
- **Tasks**:
  - [ ] Create benchmark suite for testing retrieval performance
  - [ ] Implement validation for knowledge quality and relevance
  - [ ] Test with increasingly large knowledge bases
  - [ ] Measure and optimize query latency
  - [ ] Create automated reporting on knowledge base health

## Future Work

1. **ROS Migration** (PLANNED):
   - The current implementation uses a custom event bus system
   - Will be refactored to use Robot Operating System (ROS) instead
   - Core scraping and vectorization functionality will be preserved
   - Voice manager integration will need to be reimplemented for ROS

2. **Performance Optimization** (PENDING):
   - Measure and optimize response latency
   - Implement caching for common queries

3. **Visual Integration** (PENDING):
   - Add LED patterns or animations for "consulting the holocron"
   - Provide visual feedback during knowledge retrieval

## Transition Plan

### Phase 1: Standalone RAG Pipeline (CURRENT)
- Continue using the implemented scraping and vectorization components
- Maintain and expand the knowledge base in Supabase
- Run these components independently of the voice system

### Phase 2: ROS Migration (PLANNED)
- Refactor the Holocron Manager to use ROS instead of the current event bus
- Create ROS nodes for knowledge retrieval and processing
- Implement proper message passing between components
- Ensure backward compatibility with existing data format

### Phase 3: Full Integration (FUTURE)
- Integrate the ROS-based Holocron system with the main DJ R3X platform
- Implement visual effects for knowledge retrieval
- Add comprehensive monitoring and logging
- Optimize for performance in real-time interactions

## Technical Insights

Based on our initial implementation and testing, we've identified several key technical considerations for scaling:

1. **Vector Database Performance**:
   - HNSW indexing provides excellent O(log N) search performance, suitable for large collections
   - Connection pooling is essential for handling parallel operations
   - Index creation becomes time-intensive at scale; incremental approaches are recommended

2. **Resource Requirements**:
   - Storage needs: ~60MB per 1000 articles (vector data only)
   - For full Wookieepedia coverage (~100K articles), we'll need ~6GB for vectors
   - Current Supabase configuration adequate for thousands of articles; may need adjustment for tens of thousands

3. **Batch Processing Considerations**:
   - Rate limiting is essential for respectful crawling
   - Worker-based parallel processing with proper coordination
   - Robust error handling and resumability crucial for large operations

## Test Queries

The following test queries can be used to validate embedding quality and retrieval performance:

1. "Tell me about the Millennium Falcon"
2. "Who is Darth Vader?"
3. "What is a lightsaber made of?"
4. "Explain the Force"
5. "What happened during the Clone Wars?"
6. "What planet is Tatooine?"
7. "Who built C-3PO?"
8. "What is the Jedi Code?"
9. "Who was Luke Skywalker's father?"
10. "What happened to the Republic?"

## Appendix: Sample RAG Flow
1. User asks: "Tell me about the history of the Millennium Falcon"
2. System detects knowledge-seeking query
3. RAG system retrieves relevant context from vector DB
4. Context is injected into GPT-4o prompt
5. DJ R3X responds: "Let me consult the holocron on that one... *brief pause* Ah, the Millennium Falcon! That legendary YT-1300 light freighter has quite the story. Originally owned by Lando Calrissian who lost it to Han Solo in a game of sabacc, it became famous for making the Kessel Run in less than 12 parsecs! It's been through some serious adventures, from outrunning Imperial cruisers to helping destroy not one, but two Death Stars. The ship might look like a piece of junk, but she's got it where it counts!" 