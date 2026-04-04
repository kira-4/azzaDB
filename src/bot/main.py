import logging

from telegram.error import RetryAfter
from telegram.ext import AIORateLimiter, Application, ContextTypes

from src.config import TELEGRAM_BOT_TOKEN
from src.bot.handlers.verification_handler import build_verification_conversation
from src.bot.handlers.admin_handler import build_admin_handlers
from src.bot.handlers.prescreen_handler import build_prescreen_handler

logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, RetryAfter):
        logger.warning("Flood control: retry in %ds", context.error.retry_after)
        return
    logger.error("Unhandled exception", exc_info=context.error)


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).rate_limiter(AIORateLimiter()).build()

    for handler in build_admin_handlers():
        app.add_handler(handler)

    app.add_handler(build_prescreen_handler())
    app.add_handler(build_verification_conversation())
    app.add_error_handler(_error_handler)

    return app


def run():
    app = build_app()
    logger.info("Verification bot starting…")
    app.run_polling()
