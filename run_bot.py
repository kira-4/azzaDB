"""
CLI: Run the human verification bot.

Usage:
    python run_bot.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.log import setup_logging
setup_logging()

from src.database.db import init_db, run_migrations
from src.bot.main import run

if __name__ == "__main__":
    init_db()
    run_migrations()
    run()
