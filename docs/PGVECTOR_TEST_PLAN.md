# Holocron pgvector Optimization Test Plan

## 🎯 Test Objectives
1. Verify and optimize pgvector search performance
2. Eliminate statement timeout errors
3. Validate search result quality
4. Ensure proper resource utilization

## 📋 Prerequisites
- Access to Supabase project console
- Database admin privileges
- Python environment with Supabase client
- Test dataset in `holocron_knowledge` table

## 🔍 Phase 1: Diagnostic Testing

### Step 1: Verify Current State
```sql
-- Check current index configuration
SELECT * FROM pg_indexes 
WHERE tablename = 'holocron_knowledge';

-- Check current database settings
SHOW work_mem;
SHOW maintenance_work_mem;
SHOW max_parallel_workers_per_gather;
```

### Step 2: Analyze Query Performance
```sql
-- Disable sequential scans
SET enable_seqscan = off;

-- Test query execution plan
EXPLAIN (ANALYZE, BUFFERS)
SELECT *
FROM holocron_knowledge
WHERE 1 - (embedding <=> '[test_vector]') > 0.2
ORDER BY embedding <=> '[test_vector]'
LIMIT 5;
```

### Step 3: Record Baseline Metrics
- Query execution time
- Buffer usage
- Scan type (Index vs. Sequential)
- Number of rows processed

## 🛠 Phase 2: Implementation Testing

### Step 1: Index Optimization
```sql
-- Drop existing index
DROP INDEX IF EXISTS holocron_knowledge_embedding_idx;

-- Create optimized index
CREATE INDEX holocron_knowledge_embedding_idx
ON holocron_knowledge
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Verify index creation
SELECT * FROM pg_indexes 
WHERE tablename = 'holocron_knowledge';
```

### Step 2: Database Configuration
```sql
-- Set work memory
ALTER DATABASE current_database()
SET work_mem = '128MB';

-- Set parallel workers
SET max_parallel_workers_per_gather = 2;

-- Set search efficiency
SET hnsw.ef_search = 32;

-- Verify settings
SHOW work_mem;
SHOW max_parallel_workers_per_gather;
SHOW hnsw.ef_search;
```

### Step 3: RPC Function Update
```sql
-- Update match_documents function
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

-- Verify function creation
SELECT routine_name, data_type 
FROM information_schema.routines 
WHERE routine_name = 'match_documents';
```

## 🧪 Phase 3: Validation Testing

### Step 1: Performance Testing
```python
# Test script outline
from time import time
from statistics import mean, stdev

def run_performance_test(query_vectors, thresholds):
    results = []
    for vector in query_vectors:
        for threshold in thresholds:
            start = time()
            # Execute vector search
            duration = time() - start
            results.append({
                'threshold': threshold,
                'duration': duration,
                'success': True  # Update based on result
            })
    return results

# Test with various thresholds
thresholds = [0.45, 0.55, 0.65]
# Run tests and collect metrics
```

### Step 2: Quality Testing
1. Prepare test queries with known expected results
2. Execute searches with different thresholds
3. Compare results for relevance and accuracy
4. Document any discrepancies

### Step 3: Load Testing
```python
# Concurrent query test
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def load_test(num_concurrent=5, duration_seconds=60):
    start = time()
    tasks = []
    while time() - start < duration_seconds:
        # Create concurrent search tasks
        # Collect results and monitor for timeouts
```

## 📊 Phase 4: Monitoring

### Step 1: Performance Metrics
- Query execution times
- Success/failure rates
- Memory usage patterns
- Cache hit rates

### Step 2: Resource Utilization
- CPU usage during searches
- Memory consumption
- Connection pool status
- Index usage statistics

### Step 3: Error Monitoring
- Timeout occurrences
- Error patterns
- Recovery effectiveness

## ✅ Success Criteria

1. **Performance**:
   - Query execution < 5 seconds
   - Zero timeout errors
   - Consistent response times

2. **Quality**:
   - Relevant search results
   - Expected number of matches
   - Proper similarity scoring

3. **Resource Usage**:
   - Stable memory consumption
   - Efficient CPU utilization
   - No resource exhaustion

## 📝 Test Results Template

```markdown
### Test Run Summary
- Date: [DATE]
- Test Phase: [PHASE]
- Test Cases: [NUMBER]
- Success Rate: [PERCENTAGE]

### Performance Metrics
- Average Query Time: [TIME]
- Max Query Time: [TIME]
- Min Query Time: [TIME]
- Timeout Count: [NUMBER]

### Quality Metrics
- Relevance Score: [SCORE]
- Result Count Accuracy: [PERCENTAGE]
- False Positives: [NUMBER]

### Resource Usage
- Peak Memory: [SIZE]
- Average CPU: [PERCENTAGE]
- Index Hit Rate: [PERCENTAGE]

### Issues Encountered
1. [ISSUE DESCRIPTION]
2. [ISSUE DESCRIPTION]

### Recommendations
1. [RECOMMENDATION]
2. [RECOMMENDATION]
```

## 🔄 Rollback Plan

If issues are encountered:

1. **Index Rollback**:
   ```sql
   DROP INDEX IF EXISTS holocron_knowledge_embedding_idx;
   CREATE INDEX holocron_knowledge_embedding_idx
   ON holocron_knowledge
   USING hnsw (embedding vector_cosine_ops)
   WITH (m = 32, ef_construction = 100);
   ```

2. **Function Rollback**:
   ```sql
   -- Restore original function if needed
   -- [Previous function definition]
   ```

3. **Configuration Rollback**:
   ```sql
   -- Reset to original values
   ALTER DATABASE current_database()
   RESET work_mem;
   RESET max_parallel_workers_per_gather;
   ``` 