"""Rate limiting utilities."""
import asyncio
import time
from typing import Dict
from collections import defaultdict


class RateLimiter:
    """Simple rate limiter for Telegram bot requests."""
    
    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self.requests: Dict[int, list] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def check_limit(self, user_id: int) -> bool:
        """Check if user is within rate limit."""
        async with self._lock:
            now = time.time()
            minute_ago = now - 60
            
            # Clean old requests
            self.requests[user_id] = [
                req_time for req_time in self.requests[user_id]
                if req_time > minute_ago
            ]
            
            # Check if within limit
            if len(self.requests[user_id]) >= self.requests_per_minute:
                return False
            
            # Add current request
            self.requests[user_id].append(now)
            return True
    
    async def get_remaining(self, user_id: int) -> int:
        """Get remaining requests for user."""
        async with self._lock:
            now = time.time()
            minute_ago = now - 60
            
            # Clean old requests
            self.requests[user_id] = [
                req_time for req_time in self.requests[user_id]
                if req_time > minute_ago
            ]
            
            return max(0, self.requests_per_minute - len(self.requests[user_id]))
    
    async def reset(self, user_id: int):
        """Reset rate limit for user."""
        async with self._lock:
            if user_id in self.requests:
                del self.requests[user_id]
    
    async def time_until_next(self, user_id: int) -> float:
        """Get seconds until next request is allowed."""
        async with self._lock:
            if user_id not in self.requests or not self.requests[user_id]:
                return 0
            
            now = time.time()
            oldest = min(self.requests[user_id])
            return max(0, 60 - (now - oldest))
