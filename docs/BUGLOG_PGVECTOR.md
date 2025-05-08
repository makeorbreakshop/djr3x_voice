# BUGLOG: Holocron pgvector Search Timeout Issue

## Issue Summary
The Holocron knowledge system's vector search functionality is experiencing critical timeout errors when attempting to query the Supabase pgvector database. Queries are being terminated by the database with a "statement timeout" error, preventing the retrieval of relevant Star Wars knowledge.

## Error Details
```
INFO:httpx:HTTP Request: POST https://xkotscjkvejcgrweolsd.supabase.co/rest/v1/rpc/match_documents "HTTP/2 500 Internal Server Error"
WARNING:holocron.database.vector_search:RPC search failed: {'code': '57014', 'details': None, 'hint': None, 'message': 'canceling statement due to statement timeout'}
```

- **Error Code**: 57014
- **Error Message**: "canceling statement due to statement timeout"
- **Occurrence**: When attempting vector similarity search via RPC function
- **Component**: `holocron.database.vector_search`
- **Database**: Supabase with pgvector extension v0.8.0
- **Vector Dimensions**: 1536 (text-embedding-ada-002)

## Technical Analysis

### Current Implementation
1. Vector search is implemented using a custom RPC function (`match_documents`)
2. The function performs similarity search against the `holocron_knowledge` table
3. HNSW indexing is configured with parameters (m=32, ef_construction=100)
4. Current similarity threshold is set to 0.2 (relatively low)
5. Database size: 8,631 rows of vector data, each with 1536 dimensions
6. Data volume: Approximately 200MB in `holocron_knowledge` table

### Root Causes

1. **Inefficient Vector Query Execution**:
   - The `match_documents` RPC function is likely performing computationally expensive operations
   - Full table scans may be occurring instead of using the HNSW index effectively
   - Low similarity threshold (0.2) potentially returns too many matches

2. **Database Resource Constraints**:
   - Supabase likely has strict query timeout limits on the current plan
   - The pgvector extension requires significant memory for efficient operation
   - Large vector dimensions (1536) require substantial computational resources

3. **Index Configuration Issues**:
   - HNSW index parameters might not be optimized for our specific query patterns
   - Index might not be properly utilized by the RPC function
   - Index maintenance operations may be affecting performance

4. **Query Optimization Problems**:
   - No LIMIT clause to constrain query execution time
   - Missing query plan optimization for vector operations
   - Potential query complexity issues with vector operations

## Impact
- Vector search fails consistently, triggering fallback to native LLM knowledge
- Reduced accuracy in Star Wars knowledge responses
- Poor user experience with longer response times
- Potential increased costs from relying more on LLM-generated responses
- System cannot leverage the full 8,631 articles of Star Wars knowledge

## Recommended Solutions

### Short-term Fixes

1. **Optimize RPC Function**:
   ```sql
   CREATE OR REPLACE FUNCTION match_documents(
     query_embedding vector(1536),
     match_threshold float,
     match_count int DEFAULT 5
   )
   RETURNS TABLE (
     id uuid,
     content text,
     metadata jsonb,
     similarity float
   )
   LANGUAGE plpgsql
   AS $$
   BEGIN
     RETURN QUERY
     SELECT
       id,
       content,
       metadata,
       1 - (embedding <=> query_embedding) AS similarity
     FROM
       holocron_knowledge
     WHERE
       1 - (embedding <=> query_embedding) > match_threshold
     ORDER BY
       embedding <=> query_embedding
     LIMIT match_count;
   END;
   $$;
   ```

2. **Increase Similarity Threshold**:
   - Raise threshold from 0.2 to 0.4 or 0.5 to reduce result set
   - Update application code to adjust threshold:
   ```python
   # Change in vector_search.py
   DEFAULT_SIMILARITY_THRESHOLD = 0.45  # Increased from 0.2
   ```

3. **Add Strict Query Limits**:
   - Enforce LIMIT clauses in all vector queries
   - Add explicit query timeout settings

4. **Add Query Monitoring**:
   - Implement detailed logging of query execution times
   - Track which queries are timing out consistently

### Medium-term Solutions

1. **Optimize HNSW Index**:
   ```sql
   -- Drop existing index
   DROP INDEX IF EXISTS holocron_knowledge_embedding_idx;
   
   -- Recreate with optimized parameters
   CREATE INDEX holocron_knowledge_embedding_idx
   ON holocron_knowledge
   USING hnsw (embedding vector_cosine_ops)
   WITH (m = 16, ef_construction = 64);
   ```

2. **Implement Database-side Caching**:
   - Cache frequent vector search results
   - Implement materialized views for common queries

3. **Optimize Vector Dimensions**:
   - Consider dimensionality reduction techniques
   - Evaluate if 1536 dimensions are necessary vs. potential 768 or 384

4. **Database Resource Allocation**:
   ```sql
   -- Increase work memory for vector operations
   ALTER DATABASE current_database() SET maintenance_work_mem = '256MB';
   ```

### Long-term Solutions

1. **Database Scaling**:
   - Upgrade Supabase plan for better resource allocation
   - Consider dedicated database for vector operations

2. **Vector Store Alternatives**:
   - Evaluate purpose-built vector databases (Pinecone, Weaviate, etc.)
   - Consider hybrid approaches with separate storage for vectors

3. **Query Optimization Research**:
   - Experiment with advanced pgvector techniques
   - Implement query plan analysis and optimization

4. **Data Partitioning**:
   - Segment vector data into smaller, more manageable tables
   - Implement knowledge domain partitioning strategy

## Refined Solution Approach

### Diagnostic Phase
1. **Verify Index Usage**:
   ```sql
   -- Disable sequential scans to force index usage
   SET enable_seqscan = off;
   
   -- Analyze query execution plan
   EXPLAIN (ANALYZE, BUFFERS)
   SELECT *
   FROM holocron_knowledge
   WHERE 1 - (embedding <=> query_embedding) > 0.2
   ORDER BY embedding <=> query_embedding
   LIMIT 5;
   ```
   - Check for "Index Scan using holocron_knowledge_embedding_idx"
   - Verify rows aren't being filtered post-index scan
   - Monitor buffer usage and timing statistics

### Implementation Phase

1. **Optimize HNSW Index Parameters**:
   ```sql
   -- Drop existing index
   DROP INDEX IF EXISTS holocron_knowledge_embedding_idx;
   
   -- Recreate with optimized parameters for ~10k rows
   CREATE INDEX holocron_knowledge_embedding_idx
   ON holocron_knowledge
   USING hnsw (embedding vector_cosine_ops)
   WITH (m = 16, ef_construction = 64);
   
   -- Set search efficiency factor
   SET hnsw.ef_search = 32;
   ```

2. **Enhanced RPC Function**:
   ```sql
   CREATE OR REPLACE FUNCTION match_documents(
     query_embedding vector(1536),
     match_threshold float,
     match_count int DEFAULT 5
   )
   RETURNS TABLE (
     id uuid,
     content text,
     metadata jsonb,
     similarity float
   )
   LANGUAGE plpgsql
   SET statement_timeout TO '15s'
   AS $$
   BEGIN
     RETURN QUERY
     SELECT
       id,
       content,
       metadata,
       1 - (embedding <=> query_embedding) AS similarity
     FROM
       holocron_knowledge
     WHERE
       1 - (embedding <=> query_embedding) > match_threshold
     ORDER BY
       embedding <=> query_embedding
     LIMIT match_count;
   END;
   $$;
   ```

3. **Database Resource Optimization**:
   ```sql
   -- Increase work memory for vector operations
   ALTER DATABASE current_database()
   SET work_mem = '128MB';
   
   -- Control parallel query execution
   SET max_parallel_workers_per_gather = 2;
   ```

### Application Changes

1. **Vector Search Parameters**:
   ```python
   # Update in vector_search.py
   DEFAULT_SIMILARITY_THRESHOLD = 0.45  # Increased from 0.2
   DEFAULT_MATCH_COUNT = 5  # Explicit limit on results
   ```

2. **Error Handling Enhancement**:
   ```python
   # Progressive similarity thresholds on timeout
   FALLBACK_THRESHOLDS = [0.45, 0.55, 0.65]
   ```

## Expected Outcomes

1. **Performance Improvements**:
   - Query execution time should drop below timeout threshold
   - Consistent response times for vector searches
   - Reduced memory usage during searches
   - More predictable resource utilization

2. **Reliability Gains**:
   - Elimination of timeout errors for standard queries
   - Graceful handling of edge cases
   - Better resource management

3. **Search Quality**:
   - More precise results due to optimized similarity threshold
   - Consistent result set sizes
   - Better relevance ranking

## Monitoring and Validation

1. **Query Performance**:
   - Track execution times for vector searches
   - Monitor buffer usage and cache hit rates
   - Log any remaining timeout occurrences

2. **Resource Usage**:
   - Watch for memory consumption patterns
   - Monitor parallel query execution
   - Track index usage statistics

3. **Search Quality**:
   - Validate relevance of returned results
   - Compare results before and after optimization
   - Test with diverse query patterns

## Fallback Strategy

If issues persist after implementation:

1. **Short-term**:
   - Implement application-side retry logic with increasing thresholds
   - Add query result caching for frequent searches
   - Consider reducing vector dimensions

2. **Long-term**:
   - Evaluate Supabase plan upgrade options
   - Consider migration to dedicated vector database
   - Implement hybrid search approaches

## References

1. [pgvector Documentation](https://github.com/pgvector/pgvector)
2. [Supabase Vector Documentation](https://supabase.com/docs/guides/ai/vector-columns)
3. [HNSW Index Optimization](https://github.com/pgvector/pgvector#indexing)
4. [Supabase Query Performance](https://supabase.com/docs/guides/database/query-optimization)
5. [PostgreSQL Statement Timeout](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-STATEMENT-TIMEOUT)

## 🚀 Next Steps (Current Priority)

1. **Clean Index Configuration**
   - [x] Identified multiple competing indexes
   - [x] Removed duplicate HNSW and IVFFlat indexes
   - [ ] Verify index cleanup success with query plan

2. **Performance Baseline**
   - [ ] Run EXPLAIN ANALYZE with real vector from ID 351
   - [ ] Document current query execution path
   - [ ] Measure actual timeout frequency

3. **Index Optimization**
   - [ ] Apply optimized HNSW parameters (m=16, ef_construction=64)
   - [ ] Set hnsw.ef_search = 32
   - [ ] Test with increased similarity threshold (0.45)

4. **Resource Configuration**
   - [ ] Set work_mem = 128MB
   - [ ] Configure max_parallel_workers_per_gather = 2
   - [ ] Update statement_timeout in RPC function

Current Status: Completed index cleanup, preparing for performance testing with real vector data. 