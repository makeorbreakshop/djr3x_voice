# Holocron pgvector Optimization TODO

## 🔍 Phase 1: Initial Diagnostics
- [x] Access Supabase project console and verify permissions
- [x] Initial System Check:
  - Database: PostgreSQL 15.8.1.082
  - Vector extension: v0.8.0 installed
  - Table size: 942 MB
  - Row count: ~31,747 live rows

- [x] Check current database configuration:
  - ✅ Cleaned up duplicate indexes
  - Current active index: `holocron_knowledge_embedding_idx` (HNSW, m=16, ef_construction=64)
  - Current settings:
    - max_parallel_workers_per_gather: 1
  
⚠️ **Critical Issue Found**: Multiple competing vector indexes detected. This could be causing query planner confusion.

- [ ] Run baseline query performance:
  ```sql
  -- First, disable sequential scans to force index usage
  SET enable_seqscan = off;
  
  -- Set a longer statement timeout for testing
  SET statement_timeout = '30s';
  
  -- Run explain analyze
  EXPLAIN (ANALYZE, BUFFERS)
  SELECT id, content, metadata, 1 - (embedding <=> '[test_vector]') as similarity
  FROM holocron_knowledge
  WHERE 1 - (embedding <=> '[test_vector]') > 0.2
  ORDER BY embedding <=> '[test_vector]'
  LIMIT 5;
  ```
- [ ] Record current timeout frequency and error patterns
- [ ] Document current similarity threshold effectiveness

## 🛠 Phase 2: Implementation
### Index Optimization
- [x] **COMPLETED**: Cleaned up duplicate indexes
  - Removed: `idx_holocron_knowledge_embedding_hnsw`
  - Removed: `holocron_embedding_idx`
  - Kept: `holocron_knowledge_embedding_idx`
- [ ] Backup current index configuration
- [ ] Drop existing index:
  ```sql
  DROP INDEX IF EXISTS holocron_knowledge_embedding_idx;
  ```
- [ ] Create optimized index:
  ```sql
  CREATE INDEX holocron_knowledge_embedding_idx
  ON holocron_knowledge
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
  ```
- [ ] Verify index creation and parameters

### Database Configuration
- [ ] Set work memory:
  ```sql
  ALTER DATABASE current_database()
  SET work_mem = '128MB';
  ```
- [ ] Configure parallel workers:
  ```sql
  SET max_parallel_workers_per_gather = 2;
  ```
- [ ] Set search efficiency:
  ```sql
  SET hnsw.ef_search = 32;
  ```
- [ ] Verify all configuration changes

### RPC Function Update
- [ ] Backup current function definition
- [ ] Update match_documents function with timeout setting and optimized query
- [ ] Verify function update:
  ```sql
  SELECT routine_name, data_type 
  FROM information_schema.routines 
  WHERE routine_name = 'match_documents';
  ```

## 🧪 Phase 3: Testing
### Performance Testing
- [ ] Create test vectors for common query patterns
- [ ] Test with threshold = 0.45
- [ ] Test with threshold = 0.55
- [ ] Test with threshold = 0.65
- [ ] Document execution times for each threshold
- [ ] Verify timeout errors are resolved

### Quality Testing
- [ ] Prepare set of known-good test queries
- [ ] Execute test queries with new configuration
- [ ] Compare result relevance with previous implementation
- [ ] Document any relevance changes
- [ ] Verify result count accuracy

### Load Testing
- [ ] Implement concurrent query test script
- [ ] Run 5-minute load test with 5 concurrent users
- [ ] Run 15-minute load test with 10 concurrent users
- [ ] Document any performance degradation
- [ ] Verify connection pool behavior

## 📊 Phase 4: Monitoring Setup
### Performance Monitoring
- [ ] Set up query execution time logging
- [ ] Configure success/failure rate tracking
- [ ] Implement memory usage monitoring
- [ ] Set up cache hit rate tracking

### Resource Monitoring
- [ ] Configure CPU usage monitoring
- [ ] Set up memory consumption tracking
- [ ] Monitor connection pool status
- [ ] Track index usage statistics

### Error Monitoring
- [ ] Implement timeout occurrence logging
- [ ] Set up error pattern tracking
- [ ] Configure recovery effectiveness monitoring

## ✅ Phase 5: Validation
### Performance Validation
- [ ] Verify query execution times < 5 seconds
- [ ] Confirm zero timeout errors
- [ ] Check response time consistency
- [ ] Document performance improvements

### Quality Validation
- [ ] Verify search result relevance
- [ ] Confirm expected match counts
- [ ] Check similarity scoring accuracy
- [ ] Document quality metrics

### Resource Usage Validation
- [ ] Verify stable memory consumption
- [ ] Check CPU utilization patterns
- [ ] Confirm no resource exhaustion
- [ ] Document resource usage patterns

## 🔄 Rollback Preparation
- [ ] Document current index configuration
- [ ] Save current function definition
- [ ] Record current database settings
- [ ] Prepare rollback scripts:
  ```sql
  -- Index rollback
  DROP INDEX IF EXISTS holocron_knowledge_embedding_idx;
  CREATE INDEX holocron_knowledge_embedding_idx
  ON holocron_knowledge
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 32, ef_construction = 100);

  -- Configuration rollback
  ALTER DATABASE current_database()
  RESET work_mem;
  RESET max_parallel_workers_per_gather;
  ```

## 📝 Documentation
- [ ] Update implementation documentation
- [ ] Document performance improvements
- [ ] Record configuration changes
- [ ] Update maintenance procedures
- [ ] Document rollback procedures 