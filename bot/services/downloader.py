"""Download service with queue management and progress tracking."""
import asyncio
import uuid
import logging
import re
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
        self._active_keys: Dict[str, str] = {}
    
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
        # Hard-stop duplicate/concurrent downloads per user while one is active.
        existing_active = self.get_user_active_downloads(user_id)
        if existing_active:
            existing = existing_active[0]
            if progress_callback:
                existing.progress_callback = progress_callback
            return existing.task_id

        dedupe_key = f"{user_id}|{mode}|{format_id}|{url.strip()}"
        existing_task_id = self._active_keys.get(dedupe_key)
        if existing_task_id and existing_task_id in self._tasks:
            existing = self._tasks[existing_task_id]
            if existing.status in [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING]:
                if progress_callback:
                    existing.progress_callback = progress_callback
                return existing_task_id

        task_id = str(uuid.uuid4())
        
        task = DownloadTask(
            task_id=task_id,
            user_id=user_id,
            url=url,
            format_id=format_id,
            mode=mode,
            progress_callback=progress_callback
        )
        task.progress["dedupe_key"] = dedupe_key
        
        self._tasks[task_id] = task
        self._active_keys[dedupe_key] = task_id
        
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
            
            cmd = [
                "yt-dlp",
                "-f", task.format_id,
                "--output", output_template,
                "--no-playlist",
                "-v",  # verbose for progress
                task.url
            ]
            
            if task.mode == "audio":
                # bestaudio may be unavailable on some YouTube responses; fallback to best muxed stream
                if task.format_id == "bestaudio":
                    cmd[2] = "bestaudio/best"
                cmd.extend(["-x", "--audio-format", "mp3"])
            elif task.mode == "live":
                cmd.extend(["--live-from-start"])

            # Probe metadata first so we can handle live/fragmented sources honestly.
            probe_cmd = ["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", task.url]
            try:
                import json as _json
                probe_raw = await asyncio.to_thread(lambda: __import__('subprocess').check_output(probe_cmd, text=True, timeout=45, stderr=__import__('subprocess').STDOUT))
                meta = _json.loads(probe_raw)
                live_status = meta.get("live_status")
                if task.mode == "audio" and live_status in {"is_live", "post_live"}:
                    task.progress["live_source"] = True
            except Exception:
                pass
            
            logger.info(f"Starting download: {' '.join(cmd)}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            progress_percent = 0.0
            last_stage = "starting"

            async def emit_progress(percent: float, speed: str = "N/A", eta: str = "N/A", stage: Optional[str] = None):
                if not task.progress_callback:
                    return
                payload = {
                    "percent": percent,
                    "speed": speed,
                    "eta": eta,
                    "stage": stage or last_stage,
                }
                try:
                    if asyncio.iscoroutinefunction(task.progress_callback):
                        await task.progress_callback(payload)
                    else:
                        task.progress_callback(payload)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="ignore").strip()

                m = re.search(r"\[download\]\s+([0-9.]+)%.*?at\s+([^\s]+).*?ETA\s+([^\s]+)", text)
                if m:
                    progress_percent = float(m.group(1))
                    last_stage = "downloading"
                    await emit_progress(progress_percent, m.group(2), m.group(3), last_stage)
                    continue

                m = re.search(r"\[download\]\s+([0-9.]+)%", text)
                if m:
                    progress_percent = float(m.group(1))
                    last_stage = "downloading"
                    await emit_progress(progress_percent, "N/A", "N/A", last_stage)
                    continue

                if task.progress.get("live_source") and progress_percent >= 90:
                    last_stage = "live-fragments"
                    await emit_progress(progress_percent, "Live source", "Collecting fragments", last_stage)
                    continue

                if "Destination:" in text or "Merger" in text:
                    last_stage = "finalizing"
                    await emit_progress(max(progress_percent, 92.0), "Processing...", "Finalizing", last_stage)
                    continue

                if "Extracting audio" in text or "Post-process file" in text:
                    last_stage = "converting"
                    await emit_progress(max(progress_percent, 94.0), "FFmpeg", "Converting", last_stage)
                    continue

                if "Deleting original file" in text:
                    last_stage = "cleaning"
                    await emit_progress(max(progress_percent, 98.0), "Cleanup", "Almost done", last_stage)
                    continue

                # Check if cancelled while running
                if task.task_id in self._tasks and self._tasks[task.task_id].status == DownloadStatus.CANCELLED:
                    process.terminate()
                    task.status = DownloadStatus.CANCELLED
                    self._results[task.task_id].set_result(None)
                    return

            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                # Find the downloaded file
                files = list(config.DOWNLOADS_DIR.glob(f"dl_{timestamp}_*"))
                if files:
                    task.file_path = str(files[0])
                    task.status = DownloadStatus.COMPLETED
                    
                    # Final progress update
                    await emit_progress(100, "Done!", "0s", "completed")
                    
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
                dedupe_key = self._tasks[task_id].progress.get("dedupe_key")
                del self._tasks[task_id]
                if dedupe_key and self._active_keys.get(dedupe_key) == task_id:
                    del self._active_keys[dedupe_key]
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
