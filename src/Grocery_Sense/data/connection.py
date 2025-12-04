"""
pricebrain.data.connection

SQLite connection utilities for the Price app backend.
Stores the database inside src/pricebrain/data/db/
"""

from pathlib import Path
from typing import Optional
import sqlite3
import os

# The name of your DB file
DB_FILENAME = "pricebrain.db"


def get_db_directory() -> Path:
    """
    Returns the path to the database folder:
        src/pricebrain/data/db/
    Creates it if it doesn't exist.
    """
    base_dir = Path(__file__).resolve().parent  # .../pricebrain/data
    db_dir = base_dir / "db"

    if not db_dir.exists():
        os.makedirs(db_dir, exist_ok=True)

    return db_dir


def get_db_path() -> Path:
    """
    Path to the SQLite database file.
    Example:
        src/pricebrain/data/db/pricebrain.db
    """
    return get_db_directory() / DB_FILENAME


def get_connection() -> sqlite3.Connection:
    """
    Opens a SQLite connection with foreign keys enabled.
    Always use this instead of sqlite3.connect().
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
