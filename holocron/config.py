class Config:
    def __init__(self):
        # Directory paths
        self.processed_articles_dir = "data/processed_articles"
        self.vectors_dir = "data/vectors"
        
        # Processing settings
        self.batch_size = 50  # Increased for better throughput
        self.max_concurrent = 5  # Increased concurrent requests
        
        # Embedding settings
        self.embedding_model = "text-embedding-3-small"  # OpenAI's latest small model
        self.max_retries = 3
        self.retry_delay = 1  # seconds
        self.timeout = 30  # seconds
        
        # Rate limiting settings
        self.max_tokens_per_minute = 290000  # Target 290K/min (just under OpenAI's 300K limit)
        self.rate_limit_window = 60  # 60-second sliding window
        self.embedding_batch_size = 50  # Increased chunks per API call
        
        # Adaptive delay settings
        self.base_delay = 0.1  # Reduced base delay
        self.rate_limit_delays = {
            0.9: 1.0,   # 90%+ of limit: 1.0s delay
            0.7: 0.5,   # 70-90% of limit: 0.5s delay
            0.0: 0.1    # Below 70%: 0.1s delay
        }
        
        # Error handling
        self.backoff_factor = 2
        self.max_backoff_time = 30  # Reduced max backoff time
        self.jitter = True  # Add randomness to backoff times 