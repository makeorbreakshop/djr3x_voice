import os
import time
from functools import wraps
from typing import Callable, Any
import logging
from config.app_settings import DEBUG_MODE, DEBUG_TIMING_THRESHOLD, DEBUG_MEMORY_TRACKING

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Change to INFO level
    format='\033[36m[TIMING] %(message)s\033[0m',  # Simplified format
    datefmt='%H:%M:%S'
)

# Disable other loggers
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('pydub.converter').setLevel(logging.WARNING)

logger = logging.getLogger('DJ-R3X-Timer')

def debug_timer(func: Callable) -> Callable:
    """
    A decorator that measures and logs the execution time of functions when DEBUG_MODE is enabled.
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        if not DEBUG_MODE:
            return func(*args, **kwargs)
        
        start_time = time.perf_counter()
        start_memory = process_memory() if DEBUG_MEMORY_TRACKING else 0
        
        try:
            result = func(*args, **kwargs)
            end_time = time.perf_counter()
            
            duration = end_time - start_time
            if duration >= DEBUG_TIMING_THRESHOLD:
                if DEBUG_MEMORY_TRACKING:
                    end_memory = process_memory()
                    memory_diff = end_memory - start_memory
                    logger.info(f"⏱️  {func.__name__}: {duration:.2f}s | Memory Δ: {memory_diff:.1f}MB")
                else:
                    logger.info(f"⏱️  {func.__name__}: {duration:.2f}s")
            
            return result
        
        except Exception as e:
            end_time = time.perf_counter()
            logger.error(f"❌ Error in {func.__name__}: {str(e)} | Time: {end_time - start_time:.2f}s")
            raise
    
    return wrapper

def process_memory() -> float:
    """Get current process memory usage in MB"""
    import psutil
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # Convert to MB

class DebugTimer:
    """
    Context manager for timing code blocks
    Usage:
        with DebugTimer("Operation name"):
            # code to time
    """
    def __init__(self, name: str):
        self.name = name
        
    def __enter__(self):
        if DEBUG_MODE:
            self.start_time = time.perf_counter()
            self.start_memory = process_memory() if DEBUG_MEMORY_TRACKING else 0
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if DEBUG_MODE:
            end_time = time.perf_counter()
            duration = end_time - self.start_time
            
            if duration >= DEBUG_TIMING_THRESHOLD:
                if DEBUG_MEMORY_TRACKING:
                    end_memory = process_memory()
                    memory_diff = end_memory - self.start_memory
                    logger.info(f"⏱️  {self.name}: {duration:.2f}s | Memory Δ: {memory_diff:.1f}MB")
                else:
                    logger.info(f"⏱️  {self.name}: {duration:.2f}s") 