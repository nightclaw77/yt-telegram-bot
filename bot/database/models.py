"""Database models and management using SQLite."""
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

DB_PATH = Path("bot.db")


class Database:
    """SQLite database wrapper for bot data."""
    
    def __init__(self):
        self.conn: Optional[sqlite3.Connection] = None
    
    async def init(self):
        """Initialize database and create tables."""
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        # Create tables
        cursor = self.conn.cursor()
        
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                preferred_format TEXT DEFAULT 'best',
                language TEXT DEFAULT 'en',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Downloads table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (telegram_id)
            )
        """)
        
        # User settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                telegram_id INTEGER PRIMARY KEY,
                bale_mode TEXT DEFAULT 'auto',
                bale_encrypt INTEGER DEFAULT 1,
                compression_level TEXT DEFAULT 'medium',
                bale_password TEXT DEFAULT '924780166Vf',
                sos_mode INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Lightweight migration for older DBs
        cursor.execute("PRAGMA table_info(user_settings)")
        cols = {row[1] for row in cursor.fetchall()}
        if "bale_password" not in cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN bale_password TEXT DEFAULT '924780166Vf'")
        if "sos_mode" not in cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN sos_mode INTEGER DEFAULT 0")

        # Create index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_downloads_user_id 
            ON downloads (user_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_downloads_created_at 
            ON downloads (created_at)
        """)
        
        self.conn.commit()
    
    async def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    async def add_user(self, telegram_id: int) -> bool:
        """Add a new user to the database."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
                (telegram_id,)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding user: {e}")
            return False
    
    async def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Get user by Telegram ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    async def update_user_preferences(
        self, 
        telegram_id: int, 
        preferred_format: Optional[str] = None,
        language: Optional[str] = None
    ):
        """Update user preferences."""
        updates = []
        params = []
        
        if preferred_format:
            updates.append("preferred_format = ?")
            params.append(preferred_format)
        
        if language:
            updates.append("language = ?")
            params.append(language)
        
        if updates:
            updates.append("last_active = CURRENT_TIMESTAMP")
            params.append(telegram_id)
            
            cursor = self.conn.cursor()
            cursor.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE telegram_id = ?",
                params
            )
            self.conn.commit()
    
    async def add_download(
        self,
        user_id: int,
        url: str,
        title: str,
        status: str,
        file_path: Optional[str] = None
    ) -> int:
        """Add a download record and return its ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO downloads (user_id, url, title, status, file_path) 
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, url, title, status, file_path)
        )
        self.conn.commit()
        return cursor.lastrowid
    
    async def update_download_status(
        self,
        download_id: int,
        status: str,
        file_path: Optional[str] = None
    ):
        """Update download status."""
        cursor = self.conn.cursor()
        if file_path:
            cursor.execute(
                "UPDATE downloads SET status = ?, file_path = ? WHERE id = ?",
                (status, file_path, download_id)
            )
        else:
            cursor.execute(
                "UPDATE downloads SET status = ? WHERE id = ?",
                (status, download_id)
            )
        self.conn.commit()
    
    async def get_user_history(
        self, 
        telegram_id: int, 
        limit: int = 10
    ) -> List[Dict]:
        """Get download history for a user."""
        cursor = self.conn.cursor()
        cursor.execute(
            """SELECT url, title, status, file_path, created_at 
               FROM downloads 
               WHERE user_id = ? 
               ORDER BY created_at DESC 
               LIMIT ?""",
            (telegram_id, limit)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    async def get_user_stats(self, telegram_id: int) -> Dict:
        """Get download statistics for a user."""
        cursor = self.conn.cursor()
        
        # Total downloads
        cursor.execute(
            "SELECT COUNT(*) as total FROM downloads WHERE user_id = ?",
            (telegram_id,)
        )
        total = cursor.fetchone()["total"]
        
        # Completed downloads
        cursor.execute(
            "SELECT COUNT(*) as completed FROM downloads WHERE user_id = ? AND status = 'completed'",
            (telegram_id,)
        )
        completed = cursor.fetchone()["completed"]
        
        # Failed downloads
        cursor.execute(
            "SELECT COUNT(*) as failed FROM downloads WHERE user_id = ? AND status = 'failed'",
            (telegram_id,)
        )
        failed = cursor.fetchone()["failed"]
        
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "success_rate": (completed / total * 100) if total > 0 else 0
        }
    
    async def get_user_settings(self, telegram_id: int) -> Dict:
        """Get or initialize user settings."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM user_settings WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)

        cursor.execute(
            "INSERT OR IGNORE INTO user_settings (telegram_id) VALUES (?)",
            (telegram_id,),
        )
        self.conn.commit()
        cursor.execute("SELECT * FROM user_settings WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        return dict(row) if row else {
            "telegram_id": telegram_id,
            "bale_mode": "auto",
            "bale_encrypt": 1,
            "compression_level": "medium",
            "bale_password": "924780166Vf",
            "sos_mode": 0,
        }

    async def update_user_settings(self, telegram_id: int, **kwargs):
        """Update user settings columns."""
        allowed = {"bale_mode", "bale_encrypt", "compression_level", "bale_password", "sos_mode"}
        items = [(k, v) for k, v in kwargs.items() if k in allowed]
        if not items:
            return

        await self.get_user_settings(telegram_id)
        updates = ", ".join([f"{k} = ?" for k, _ in items] + ["updated_at = CURRENT_TIMESTAMP"])
        params = [v for _, v in items] + [telegram_id]
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE user_settings SET {updates} WHERE telegram_id = ?", params)
        self.conn.commit()

    async def delete_old_downloads(self, days: int = 30):
        """Delete download records older than specified days."""
        cursor = self.conn.cursor()
        cursor.execute(
            """DELETE FROM downloads 
               WHERE created_at < datetime('now', '-' || ? || ' days')""",
            (days,)
        )
        deleted = cursor.rowcount
        self.conn.commit()
        return deleted
