#!/bin/bash

# Set environment variables (replace with your actual keys)
export OPENAI_API_KEY="your_openai_api_key_here"
export SUPABASE_URL="https://xkotscjkvejcgrweolsd.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="your_supabase_service_role_key_here"

# Run the scraper for Millennium Falcon
python test_scrape_single_article.py

# Run the processor if needed
# python test_process_single_article.py 