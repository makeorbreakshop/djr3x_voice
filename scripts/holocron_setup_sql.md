# Holocron Knowledge Base - Supabase SQL Setup

Run these SQL commands in the Supabase SQL Editor to set up the vector database for the Holocron RAG system.

## Step 1: Enable the pgvector extension

```sql
-- Enable the pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;
```

## Step 2: Create the holocron_knowledge table

```sql
-- Create the table to store Star Wars knowledge with vector embeddings
CREATE TABLE holocron_knowledge (
  id SERIAL PRIMARY KEY,
  content TEXT NOT NULL,
  content_tokens INTEGER,
  metadata JSONB,
  embedding VECTOR(1536)  -- For text-embedding-3-small
);

-- Add a comment to describe the table's purpose
COMMENT ON TABLE holocron_knowledge IS 'Star Wars canonical knowledge base for DJ R3X Holocron';
```

## Step 3: Create a vector index for efficient similarity search

```sql
-- Create a hyper-parameter index for faster similarity search
CREATE INDEX ON holocron_knowledge 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

## Step 4: Create a function for similarity search

```sql
-- Function to query embeddings with cosine similarity
CREATE OR REPLACE FUNCTION match_holocron_documents (
  query_embedding VECTOR(1536),
  match_threshold FLOAT,
  match_count INT
)
RETURNS TABLE (
  id BIGINT,
  content TEXT,
  metadata JSONB,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    holocron_knowledge.id,
    holocron_knowledge.content,
    holocron_knowledge.metadata,
    1 - (holocron_knowledge.embedding <=> query_embedding) AS similarity
  FROM holocron_knowledge
  WHERE 1 - (holocron_knowledge.embedding <=> query_embedding) > match_threshold
  ORDER BY holocron_knowledge.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
```

## Verification Query

After running the setup commands, you can verify the table structure with:

```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
SELECT * FROM information_schema.tables WHERE table_name = 'holocron_knowledge';
``` 