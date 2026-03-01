"""AI Summary service using ytsummarize CLI."""
import asyncio
import logging
from typing import Optional

from bot.config import config

logger = logging.getLogger(__name__)


class SummarizerService:
    """Service for generating AI summaries."""
    
    def __init__(self):
        self.cli_path = config.YTSUMMARIZE_CLI
    
    async def summarize(self, url: str, quality: str = "xl") -> Optional[str]:
        """Generate AI summary for a YouTube video."""
        try:
            cmd = [self.cli_path, url, quality]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return stdout.decode().strip()
            else:
                error_msg = stderr.decode().strip() if stderr else "Unknown error"
                logger.error(f"Summary error: {error_msg}")
                return None
                
        except FileNotFoundError:
            logger.error(f"ytsummarize CLI not found at {self.cli_path}")
            return None
        except Exception as e:
            logger.error(f"Summary error: {e}")
            return None
    
    async def is_available(self) -> bool:
        """Check if summarizer CLI is available."""
        try:
            cmd = ["which", self.cli_path]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            return process.returncode == 0
        except Exception:
            return False
