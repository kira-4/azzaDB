"""
CLI: Scrape full message history from the target Telegram group,
then run the message grouper to create album records.

Usage:
    python scrape_history.py [--skip-scrape] [--skip-group] [--ai]
"""
import asyncio
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main(skip_scrape: bool, skip_group: bool, run_ai: bool):
    from src.database.db import init_db
    from src.config import TARGET_GROUP_ID

    logger.info("Initialising database…")
    init_db()

    if not skip_scrape:
        from src.scraper.history_scraper import scrape_full_history
        logger.info("Scraping history for group %d…", TARGET_GROUP_ID)
        inserted, skipped = await scrape_full_history(TARGET_GROUP_ID)
        logger.info("Scrape complete: %d inserted, %d skipped", inserted, skipped)

    if not skip_group:
        from src.scraper.message_grouper import group_messages
        logger.info("Running message grouper…")
        albums_created = group_messages(TARGET_GROUP_ID)
        logger.info("Grouper complete: %d albums created", albums_created)

    if run_ai:
        from src.pipeline.album_pipeline import run_ai_extraction_batch
        logger.info("Running AI extraction batch…")
        success = await run_ai_extraction_batch()
        logger.info("AI batch complete: %d albums extracted", success)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="azzaDB scraper + grouper")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip Telegram scraping")
    parser.add_argument("--skip-group", action="store_true", help="Skip message grouping")
    parser.add_argument("--ai", action="store_true", help="Also run AI extraction after grouping")
    args = parser.parse_args()

    asyncio.run(main(args.skip_scrape, args.skip_group, args.ai))
