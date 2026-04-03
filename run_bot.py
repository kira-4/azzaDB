"""
CLI: Run the human verification bot.

Usage:
    python run_bot.py
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

from src.database.db import init_db
from src.bot.main import run

if __name__ == "__main__":
    init_db()
    run()
