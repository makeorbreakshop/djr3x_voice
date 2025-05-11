# BERT Embeddings for Holocron Knowledge System

This directory contains scripts for implementing and testing BERT embeddings alongside OpenAI embeddings for the Holocron Knowledge System. These scripts allow you to create a secondary vector database using BERT, compare search results between the two embedding types, and analyze their semantic differences.

## Prerequisites

Before running these scripts, ensure you have the following:

1. Python 3.7+ installed
2. Required packages (install with `pip install -r requirements.txt`):
   - sentence-transformers
   - pinecone-client
   - openai
   - rich
   - numpy
   - tqdm
   - python-dotenv

3. Environment variables set in `.env`:
   - `OPENAI_API_KEY` - Your OpenAI API key
   - `PINECONE_API_KEY` - Your Pinecone API key

4. Existing Pinecone index with OpenAI embeddings

## Scripts Overview

### 1. Test BERT Embeddings (`test_bert_embeddings.py`)

This script allows you to compare OpenAI and BERT embeddings using a sample set of documents from an existing Pinecone index.

```bash
python test_bert_embeddings.py \
  --source-index-name your_openai_index \
  --num-documents 50 \
  --bert-model intfloat/e5-small-v2
```

Key features:
- Side-by-side comparison of embedding results
- Analysis of semantic differences between model types
- Export of test data for further analysis

### 2. Create BERT Index (`create_bert_index.py`)

This script creates a new Pinecone index populated with BERT embeddings generated from your existing documents.

```bash
python create_bert_index.py \
  --source-index-name your_openai_index \
  --target-index-name holocron-sbert \
  --max-documents 10000 \
  --batch-size 100 \
  --bert-model intfloat/e5-small-v2
```

Key features:
- Batch processing for efficient embedding generation
- Parallel metadata preservation from source index
- Progress tracking and error recovery

### 3. Compare Embeddings Search (`compare_embeddings_search.py`)

This script allows you to query both the OpenAI and BERT indexes side-by-side to compare search results for various queries.

```bash
python compare_embeddings_search.py \
  --openai-index-name your_openai_index \
  --bert-index-name holocron-sbert \
  --query "Who is Luke Skywalker?" \
  --interactive \
  --bert-model intfloat/e5-small-v2
```

Key features:
- Side-by-side result comparison
- Detailed content view of top results
- Semantic relationship analysis
- Interactive mode for sequential queries

## Default Configuration

- BERT Model: `intfloat/e5-small-v2` (384 dimensions)
- Target index name: `holocron-sbert`
- Similarity metric: Cosine similarity
- Default limit: 10 results per query

## Notes

- The intfloat/e5-small-v2 model has different semantic qualities than OpenAI embeddings, providing complementary search capabilities.
- The vector dimensions for intfloat/e5-small-v2 are 384, compared to OpenAI's 1536, resulting in smaller storage requirements.
- You can customize the BERT model using the `--bert-model` parameter to try other models from the sentence-transformers library.
- For production use, consider implementing hybrid search combining results from both embedding types.

## BERT Model Options

You can try different BERT models by specifying the `--bert-model` parameter:

- `intfloat/e5-small-v2` - Newer, improved model for retrieval tasks (384 dimensions, default)
- `all-MiniLM-L6-v2` - Fast, small model (384 dimensions)
- `all-mpnet-base-v2` - Better quality but slower (768 dimensions)
- `multi-qa-mpnet-base-dot-v1` - Optimized for QA tasks (768 dimensions)
- `all-distilroberta-v1` - Good balance of speed/quality (768 dimensions)

## Next Steps

After creating and testing the BERT embeddings index, consider:

1. Implementing a hybrid search system that combines results from both indexes
2. Creating a router that selects the appropriate index based on query type
3. Fine-tuning the system to leverage the strengths of each embedding type
4. Expanding to additional specialized models for specific domain knowledge

## Troubleshooting

- If you encounter memory issues during index creation, reduce the batch size
- For "index not found" errors, ensure your Pinecone API key has proper permissions
- If search results are poor, try adjusting the similarity threshold in the code
- For "dimension mismatch" errors, ensure you're using the correct model 