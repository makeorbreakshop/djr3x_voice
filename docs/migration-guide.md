# Migration Guide: Standardized Vector Search

This guide helps you migrate your code to use the new standardized vector search implementation. The new implementation provides better reliability, performance, and maintainability through connection pooling, automatic fallbacks, and standardized error handling.

## Key Changes

1. **Version Standardization**
   - Supabase client version is now pinned to 2.3.5
   - This version is required for proper vector search functionality
   - Update your `requirements.txt` and related files

2. **Centralized Client Factory**
   - All Supabase client instances should now be created through `holocron.database.client_factory`
   - Provides connection pooling and automatic retry logic
   - Handles version compatibility checks

3. **Standardized Vector Search**
   - New `holocron.database.vector_search` module replaces direct Supabase calls
   - Provides consistent interface regardless of underlying implementation
   - Includes automatic fallback mechanisms

## Migration Steps

### 1. Update Dependencies

Update your requirements file:

```txt
supabase==2.3.5  # Pin to exact version
```

### 2. Replace Direct Supabase Usage

Before:
```python
from supabase import create_client

# Direct client creation
supabase = create_client(url, key)

# Direct vector search
response = supabase.rpc('match_documents', {
    'query_embedding': embedding,
    'match_threshold': 0.5,
    'match_count': 10
}).execute()
```

After:
```python
from holocron.database.vector_search import VectorSearch

# Create vector search instance
vector_search = VectorSearch(
    table_name='your_table',
    pool_key='your_component'
)

# Use standardized search
results = await vector_search.search(
    embedding=embedding,
    threshold=0.5,
    limit=10
)
```

### 3. Handle Results

The new implementation returns `VectorSearchResult` objects:

```python
# Access result fields
for result in results:
    print(f"ID: {result.id}")
    print(f"Content: {result.content}")
    print(f"Metadata: {result.metadata}")
    print(f"Similarity: {result.similarity}")

# Convert to dict if needed
result_dicts = [result.to_dict() for result in results]
```

### 4. Add Cleanup

Always close vector search instances when done:

```python
try:
    vector_search = VectorSearch()
    results = await vector_search.search(...)
finally:
    vector_search.close()
```

### 5. Update Error Handling

The new implementation provides better error handling:

```python
try:
    results = await vector_search.search(
        embedding=embedding,
        threshold=0.5
    )
except ValueError as e:
    # Handle validation errors (e.g., wrong embedding dimension)
    logger.error(f"Validation error: {e}")
except RuntimeError as e:
    # Handle search failures
    logger.error(f"Search failed: {e}")
```

## Breaking Changes

1. **Async Interface**
   - All search operations are now async
   - Must be called with `await`
   - Must run in an async context

2. **Result Format**
   - Results are now `VectorSearchResult` objects
   - Use `.to_dict()` for backward compatibility

3. **Connection Management**
   - Must properly close vector search instances
   - Use connection pooling for better performance

4. **Error Handling**
   - More specific error types
   - Better error messages
   - Automatic retries for transient failures

## Best Practices

1. **Connection Pooling**
   - Use meaningful pool keys for different components
   - Close connections when done
   - Reuse vector search instances when possible

2. **Error Handling**
   - Always handle potential errors
   - Log errors appropriately
   - Use fallback behavior when needed

3. **Performance**
   - Set appropriate thresholds and limits
   - Use metadata filters to reduce result set
   - Monitor search performance

## Example: Complete Migration

Here's a complete example of migrating a search component:

```python
from holocron.database.vector_search import VectorSearch
import logging

logger = logging.getLogger(__name__)

class SearchComponent:
    def __init__(self):
        self.vector_search = VectorSearch(
            table_name='knowledge_base',
            pool_key='search_component'
        )
    
    async def search(self, query_embedding, min_relevance=0.5):
        try:
            results = await self.vector_search.search(
                embedding=query_embedding,
                threshold=min_relevance,
                metadata_filters={'type': 'article'}
            )
            return [result.to_dict() for result in results]
            
        except ValueError as e:
            logger.error(f"Invalid input: {e}")
            return []
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def close(self):
        self.vector_search.close()

# Usage
async def main():
    search = SearchComponent()
    try:
        results = await search.search(query_embedding)
        process_results(results)
    finally:
        search.close()
```

## Need Help?

If you encounter any issues during migration:

1. Check the error messages and logs
2. Review the example code
3. Ensure all dependencies are updated
4. Verify your async/await usage
5. Contact support if needed

## Timeline

1. **Immediate Actions**
   - Update dependencies
   - Review breaking changes
   - Plan migration schedule

2. **Migration Period**
   - Update code gradually
   - Test thoroughly
   - Monitor performance

3. **Completion**
   - Remove old implementations
   - Update documentation
   - Deploy changes 