"""
Retry Decorator Utility for Testing

This utility provides a decorator that retries flaky tests a specified number of times
before considering them failed. This is particularly useful for tests that interact with
external systems or have timing-related issues.
"""

import asyncio
import functools
import logging
import time
import traceback
from typing import Any, Callable, List, Optional, Set, Type, Union, cast

logger = logging.getLogger(__name__)

def retry(
    max_attempts: int = 3,
    allowed_exceptions: Optional[List[Type[Exception]]] = None,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    log_level: int = logging.INFO
) -> Callable:
    """
    Decorator to retry a test function on failure.
    
    Args:
        max_attempts: Maximum number of retry attempts
        allowed_exceptions: List of exception types that trigger a retry
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for the delay between retries
        log_level: Logging level for retry messages
        
    Returns:
        Decorated function
    """
    if allowed_exceptions is None:
        allowed_exceptions = [Exception]
        
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper_sync(*args: Any, **kwargs: Any) -> Any:
            """Wrapper for synchronous functions."""
            last_exception = None
            attempt = 1
            
            while attempt <= max_attempts:
                try:
                    if attempt > 1:
                        logger.log(
                            log_level, 
                            f"Retry attempt {attempt}/{max_attempts} for {func.__name__}"
                        )
                    
                    return func(*args, **kwargs)
                    
                except tuple(allowed_exceptions) as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait_time = delay_seconds * (backoff_factor ** (attempt - 1))
                        logger.log(
                            log_level, 
                            f"Test {func.__name__} failed, retrying in {wait_time:.2f}s: {e}"
                        )
                        time.sleep(wait_time)
                    attempt += 1
                except Exception as e:
                    # Re-raise exceptions that are not in allowed_exceptions
                    raise e
            
            # Reached max attempts, re-raise the last exception
            if last_exception:
                logger.error(
                    f"Test {func.__name__} failed after {max_attempts} attempts"
                )
                raise last_exception
                
        @functools.wraps(func)
        async def wrapper_async(*args: Any, **kwargs: Any) -> Any:
            """Wrapper for asynchronous functions."""
            last_exception = None
            attempt = 1
            
            while attempt <= max_attempts:
                try:
                    if attempt > 1:
                        logger.log(
                            log_level, 
                            f"Retry attempt {attempt}/{max_attempts} for {func.__name__}"
                        )
                    
                    return await func(*args, **kwargs)
                    
                except tuple(allowed_exceptions) as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait_time = delay_seconds * (backoff_factor ** (attempt - 1))
                        logger.log(
                            log_level, 
                            f"Test {func.__name__} failed, retrying in {wait_time:.2f}s: {e}"
                        )
                        await asyncio.sleep(wait_time)
                    attempt += 1
                except Exception as e:
                    # Re-raise exceptions that are not in allowed_exceptions
                    raise e
            
            # Reached max attempts, re-raise the last exception
            if last_exception:
                logger.error(
                    f"Test {func.__name__} failed after {max_attempts} attempts"
                )
                raise last_exception
        
        # Return the appropriate wrapper based on whether the function is async or not
        if asyncio.iscoroutinefunction(func):
            return wrapper_async
        else:
            return wrapper_sync
            
    return decorator


class RetrySession:
    """
    A session for tracking and reporting on retried tests.
    
    This is useful for collecting statistics on which tests are flaky
    and how often they need to be retried.
    """
    
    def __init__(self):
        """Initialize the retry session."""
        self.retried_tests: Dict[str, List[Dict[str, Any]]] = {}
        self.start_time = time.time()
        
    def record_retry(
        self, 
        test_name: str, 
        attempt: int, 
        exception: Exception, 
        stack_trace: Optional[str] = None
    ) -> None:
        """
        Record a test retry.
        
        Args:
            test_name: Name of the test
            attempt: Current attempt number
            exception: Exception that caused the retry
            stack_trace: Optional stack trace
        """
        if test_name not in self.retried_tests:
            self.retried_tests[test_name] = []
            
        self.retried_tests[test_name].append({
            'timestamp': time.time(),
            'attempt': attempt,
            'exception': str(exception),
            'exception_type': type(exception).__name__,
            'stack_trace': stack_trace or ''.join(traceback.format_exception(
                type(exception), exception, exception.__traceback__
            ))
        })
        
    def record_success(self, test_name: str, attempts: int) -> None:
        """
        Record a successful test completion after retries.
        
        Args:
            test_name: Name of the test
            attempts: Number of attempts before success
        """
        if test_name not in self.retried_tests:
            self.retried_tests[test_name] = []
            
        self.retried_tests[test_name].append({
            'timestamp': time.time(),
            'attempt': attempts,
            'success': True
        })
        
    def get_report(self) -> str:
        """
        Generate a report of retried tests.
        
        Returns:
            Report string
        """
        if not self.retried_tests:
            return "No tests were retried."
            
        report_lines = [
            "Retry Session Report",
            "-" * 40,
            f"Session duration: {time.time() - self.start_time:.2f}s",
            f"Total retried tests: {len(self.retried_tests)}",
            ""
        ]
        
        for test_name, retries in sorted(
            self.retried_tests.items(), 
            key=lambda x: len(x[1]), 
            reverse=True
        ):
            successful = any(r.get('success', False) for r in retries)
            max_attempt = max(r.get('attempt', 0) for r in retries)
            
            report_lines.append(
                f"{test_name}: {len(retries)} retries, "
                f"max attempt {max_attempt}, "
                f"{'succeeded' if successful else 'failed'}"
            )
            
            # Group exceptions by type
            exception_types = {}
            for retry in retries:
                if 'exception_type' in retry:
                    exc_type = retry['exception_type']
                    if exc_type not in exception_types:
                        exception_types[exc_type] = 0
                    exception_types[exc_type] += 1
                    
            if exception_types:
                report_lines.append("  Exception types:")
                for exc_type, count in sorted(
                    exception_types.items(), 
                    key=lambda x: x[1], 
                    reverse=True
                ):
                    report_lines.append(f"    - {exc_type}: {count}")
                    
            report_lines.append("")
            
        return "\n".join(report_lines)
        
    def reset(self) -> None:
        """Reset the session, clearing all recorded retries."""
        self.retried_tests.clear()
        self.start_time = time.time()


# Global retry session for tracking retries across tests
global_retry_session = RetrySession()

def retry_with_session(
    max_attempts: int = 3,
    allowed_exceptions: Optional[List[Type[Exception]]] = None,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    log_level: int = logging.INFO,
    session: Optional[RetrySession] = None
) -> Callable:
    """
    Decorator to retry a test function on failure, with session tracking.
    
    Args:
        max_attempts: Maximum number of retry attempts
        allowed_exceptions: List of exception types that trigger a retry
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for the delay between retries
        log_level: Logging level for retry messages
        session: Optional retry session for tracking
        
    Returns:
        Decorated function
    """
    if allowed_exceptions is None:
        allowed_exceptions = [Exception]
        
    retry_session = session or global_retry_session
        
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper_sync(*args: Any, **kwargs: Any) -> Any:
            """Wrapper for synchronous functions."""
            last_exception = None
            attempt = 1
            
            while attempt <= max_attempts:
                try:
                    if attempt > 1:
                        logger.log(
                            log_level, 
                            f"Retry attempt {attempt}/{max_attempts} for {func.__name__}"
                        )
                    
                    result = func(*args, **kwargs)
                    
                    if attempt > 1:
                        retry_session.record_success(func.__name__, attempt)
                        
                    return result
                    
                except tuple(allowed_exceptions) as e:
                    last_exception = e
                    retry_session.record_retry(func.__name__, attempt, e)
                    
                    if attempt < max_attempts:
                        wait_time = delay_seconds * (backoff_factor ** (attempt - 1))
                        logger.log(
                            log_level, 
                            f"Test {func.__name__} failed, retrying in {wait_time:.2f}s: {e}"
                        )
                        time.sleep(wait_time)
                    attempt += 1
                except Exception as e:
                    # Re-raise exceptions that are not in allowed_exceptions
                    raise e
            
            # Reached max attempts, re-raise the last exception
            if last_exception:
                logger.error(
                    f"Test {func.__name__} failed after {max_attempts} attempts"
                )
                raise last_exception
                
        @functools.wraps(func)
        async def wrapper_async(*args: Any, **kwargs: Any) -> Any:
            """Wrapper for asynchronous functions."""
            last_exception = None
            attempt = 1
            
            while attempt <= max_attempts:
                try:
                    if attempt > 1:
                        logger.log(
                            log_level, 
                            f"Retry attempt {attempt}/{max_attempts} for {func.__name__}"
                        )
                    
                    result = await func(*args, **kwargs)
                    
                    if attempt > 1:
                        retry_session.record_success(func.__name__, attempt)
                        
                    return result
                    
                except tuple(allowed_exceptions) as e:
                    last_exception = e
                    retry_session.record_retry(func.__name__, attempt, e)
                    
                    if attempt < max_attempts:
                        wait_time = delay_seconds * (backoff_factor ** (attempt - 1))
                        logger.log(
                            log_level, 
                            f"Test {func.__name__} failed, retrying in {wait_time:.2f}s: {e}"
                        )
                        await asyncio.sleep(wait_time)
                    attempt += 1
                except Exception as e:
                    # Re-raise exceptions that are not in allowed_exceptions
                    raise e
            
            # Reached max attempts, re-raise the last exception
            if last_exception:
                logger.error(
                    f"Test {func.__name__} failed after {max_attempts} attempts"
                )
                raise last_exception
        
        # Return the appropriate wrapper based on whether the function is async or not
        if asyncio.iscoroutinefunction(func):
            return wrapper_async
        else:
            return wrapper_sync
            
    return decorator 