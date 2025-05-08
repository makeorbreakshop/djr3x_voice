# OpenAI Client Initialization Fix

## Issue

During Phase 1 data extraction for the Holocron Knowledge System, the following error was encountered when running the extraction pipeline:

```
ERROR:src.holocron.data_processor:Error generating embeddings for batch: Client.__init__() got an unexpected keyword argument 'proxies'
```

## Cause

The issue was related to the OpenAI client initialization in `data_processor.py`. The code was using the older style of OpenAI client initialization which is incompatible with the newer version of the OpenAI Python SDK.

Specifically:
1. The code was using a global `openai.api_key` assignment
2. Then directly calling `openai.embeddings.create()` later in the code
3. In the newer SDK, this has changed to an instance-based approach with a client object

## Solution

The fix involved two main changes:

1. Updated imports to explicitly import `OpenAI` class:
   ```python
   from openai import OpenAI, AsyncOpenAI
   ```

2. Created an explicit OpenAI client instance with proper configuration:
   ```python
   self.openai_client = OpenAI(
       api_key=api_key,
       http_client=httpx.Client(
           base_url="https://api.openai.com/v1",
           follow_redirects=True,
           timeout=60.0
       )
   )
   ```

3. Updated all API calls to use the client instance:
   ```python
   response = self.openai_client.embeddings.create(
       model=EMBEDDING_MODEL,
       input=texts,
       encoding_format="float"
   )
   ```

This approach explicitly configures the HTTP client and avoids any environment-based proxy configuration that might be causing issues.

## Testing

The fix was verified with:

1. First running `test_extraction.py` which successfully extracted content from a sample article, generated embeddings, and stored them in the Supabase database.

2. Then running the full `run_holocron_pipeline.py` which successfully:
   - Scraped 21 articles from Wookieepedia
   - Created 146 chunks from these articles
   - Generated embeddings for all chunks
   - Uploaded all chunks with their embeddings to the Supabase database

## Notes for Future Development

1. The OpenAI client initialization should always use this explicit instance-based approach rather than the global configuration.

2. The HTTP client configuration may need adjustments for different environments, particularly if there are proxy settings involved.

3. Consider adding more robust error handling and retry logic for API calls to handle rate limiting and transient failures.

4. The httpx configuration might need adjustments based on network requirements. 