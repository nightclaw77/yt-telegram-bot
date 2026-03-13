"""Download service with queue management and progress tracking."""
import asyncio
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Callable, Any
from dataclasses import dataclass, field
from enum import Enum

from bot.config import config

logger = logging.getLogger(__name__)


class DownloadStatus(Enum):
    """Download status enum."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    """Download task data class."""
    task_id: str
    user_id: int
    url: str
    format_id: str
    mode: str  # video, audio, live
    status: DownloadStatus = DownloadStatus.PENDING
    progress_callback: Optional[Callable] = None
    file_path: Optional[str] = None
    error: Optional[str] = None
    progress: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


class DownloadManager:
    """Singleton download manager with queue system."""
    
    _instance = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        self._tasks: Dict[str, DownloadTask] = {}
        self._user_tasks: Dict[int, list] = {}
        self._results: Dict[str, asyncio.Future] = {}
    
    @classmethod
    def get_instance(cls) -> "DownloadManager":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def add_download(
        self,
        user_id: int,
        url: str,
        format_id: str,
        mode: str,
        progress_callback: Optional[Callable] = None
    ) -> str:
        """Add a download to the queue."""
        task_id = str(uuid.uuid4())
        
        task = DownloadTask(
            task_id=task_id,
            user_id=user_id,
            url=url,
            format_id=format_id,
            mode=mode,
            progress_callback=progress_callback
        )
        
        self._tasks[task_id] = task
        
        if user_id not in self._user_tasks:
            self._user_tasks[user_id] = []
        self._user_tasks[user_id].append(task_id)
        
        # Create future for result
        self._results[task_id] = asyncio.get_event_loop().create_future()
        
        # Start download task
        asyncio.create_task(self._run_download(task))
        
        return task_id
    
    async def _run_download(self, task: DownloadTask):
        """Run the download in background."""
        try:
            task.status = DownloadStatus.DOWNLOADING
            
            # Create download command
            config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%H%M%S")
            output_template = str(
                config.DOWNLOADS_DIR / f"dl_{timestamp}_%(title)s.%(ext)s"
            )
            
            requested_format = task.format_id
            audio_quality_k = None
            if task.mode == "audio" and "||" in requested_format:
                requested_format, audio_quality = requested_format.split("||", 1)
                if audio_quality.isdigit():
                    audio_quality_k = int(audio_quality)

            cmd = [
                "yt-dlp",
                "-f", requested_format,
                "--output", output_template,
                "--no-playlist",
                "-v",  # verbose for progress
                task.url
            ]
            
            if task.mode == "audio":
                # Prefer direct audio; if unavailable on YouTube/SABR, fall back to a small muxed MP4 instead of huge HLS variants.
                if requested_format == "bestaudio":
                    cmd[2] = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/18/22/best[height<=360]/best"
                    cmd.extend(["-S", "proto:https"])
                cmd.extend(["-x", "--audio-format", "mp3"])
                if audio_quality_k:
                    cmd.extend(["--audio-quality", f"{audio_quality_k}K"])
            elif task.mode == "live":
                cmd.extend(["--live-from-start"])
            
            logger.info(f"Starting download: {' '.join(cmd)}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            download_complete = False
            progress_percent = 0
            
            # Monitor progress - read stderr for progress info
            while process.returncode is None:
                await asyncio.sleep(2)
                
                # Update progress - simplified progress
                progress_percent = min(progress_percent + 5, 90)
                
                if task.progress_callback:
                    try:
                        if asyncio.iscoroutinefunction(task.progress_callback):
                            await task.progress_callback({
                                "percent": progress_percent,
                                "speed": "Checking...",
                                "eta": "Calculating..."
                            })
                        else:
                            task.progress_callback({
                                "percent": progress_percent,
                                "speed": "Checking...",
                                "eta": "Calculating..."
                            })
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")
                
                # Check if cancelled
                if task.task_id in self._tasks:
                    if self._tasks[task.task_id].status == DownloadStatus.CANCELLED:
                        process.terminate()
                        task.status = DownloadStatus.CANCELLED
                        self._results[task.task_id].set_result(None)
                        return
                else:
                    break
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                # Find the downloaded file
                files = list(config.DOWNLOADS_DIR.glob(f"dl_{timestamp}_*"))
                if files:
                    task.file_path = str(files[0])
                    task.status = DownloadStatus.COMPLETED
                    
                    # Final progress update
                    if task.progress_callback:
                        try:
                            if asyncio.iscoroutinefunction(task.progress_callback):
                                await task.progress_callback({
                                    "percent": 100,
                                    "speed": "Done!",
                                    "eta": "0s"
                                })
                            else:
                                task.progress_callback({
                                    "percent": 100,
                                    "speed": "Done!",
                                    "eta": "0s"
                                })
                        except:
                            pass
                    
                    self._results[task.task_id].set_result(task.file_path)
                else:
                    task.status = DownloadStatus.FAILED
                    task.error = "File not found after download"
                    self._results[task.task_id].set_result(None)
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Download failed: {error_msg}")
                task.status = DownloadStatus.FAILED
                task.error = error_msg
                self._results[task.task_id].set_result(None)
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            task.status = DownloadStatus.FAILED
            task.error = str(e)
            if task.task_id in self._results:
                self._results[task.task_id].set_result(None)
    
    async def wait_for_download(self, task_id: str) -> Optional[str]:
        """Wait for download to complete and return file path."""
        if task_id not in self._results:
            return None
        
        try:
            result = await asyncio.wait_for(self._results[task_id], timeout=600)
            return result
        except asyncio.TimeoutError:
            logger.error(f"Download timeout for task {task_id}")
            if task_id in self._tasks:
                self._tasks[task_id].status = DownloadStatus.FAILED
            return None
        finally:
            # Cleanup
            if task_id in self._tasks:
                del self._tasks[task_id]
            if task_id in self._results:
                del self._results[task_id]
    
    def cancel_user_downloads(self, user_id: int) -> bool:
        """Cancel all active downloads for a user."""
        if user_id not in self._user_tasks:
            return False
        
        cancelled = False
        for task_id in self._user_tasks[user_id]:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                if task.status in [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING]:
                    task.status = DownloadStatus.CANCELLED
                    cancelled = True
        
        self._user_tasks[user_id] = []
        return cancelled
    
    def get_task_status(self, task_id: str) -> Optional[DownloadTask]:
        """Get status of a download task."""
        return self._tasks.get(task_id)
    
    def get_user_active_downloads(self, user_id: int) -> list:
        """Get all active downloads for a user."""
        if user_id not in self._user_tasks:
            return []
        
        active = []
        for task_id in self._user_tasks[user_id]:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                if task.status in [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING]:
                    active.append(task)
        
        return active
