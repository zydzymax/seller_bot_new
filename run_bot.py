#!/usr/bin/env python3
"""
Main Bot Runner - Universal entry point for AI Seller Bot

Supports both webhook and polling modes with full integration:
- Multi-domain support (textile_manufacturing, ai_seller_self)
- Admin commands
- Flow routing
- Conversation logging
- Simple learning

Usage:
    # Webhook mode (production)
    python run_bot.py --mode webhook

    # Polling mode (development)
    python run_bot.py --mode polling

    # Specific domain
    python run_bot.py --mode polling --domain ai_seller_self
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv()

from utils.logging import get_logger
from utils.domain_loader import get_current_domain_name, switch_domain

logger = get_logger(__name__)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="AI Seller Bot Runner")
    parser.add_argument(
        "--mode",
        choices=["webhook", "polling"],
        default=os.getenv("BOT_MODE", "webhook"),
        help="Bot mode: webhook or polling",
    )
    parser.add_argument(
        "--domain",
        choices=["textile_manufacturing", "ai_seller_self"],
        default=None,
        help="Override domain (default: from AI_SELLER_DOMAIN env var)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("WEBHOOK_PORT", "8000")),
        help="Webhook port (default: 8000)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
        help="Webhook host (default: 0.0.0.0)",
    )
    return parser.parse_args()


async def run_polling_mode():
    """Run bot in polling mode (development)"""
    from telegram.ext import Application, MessageHandler, filters
    from bot.admin_commands import setup_admin_handlers
    from bot.message_router import route_user_message

    logger.info("🔧 Starting bot in POLLING mode")

    # Get Telegram token
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        logger.error("TELEGRAM_TOKEN not set in environment")
        sys.exit(1)

    # Create application
    application = Application.builder().token(token).build()

    # Setup admin command handlers
    setup_admin_handlers(application)
    logger.info("✅ Admin commands registered")

    # Setup message handler (uses flow_router internally)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, route_user_message)
    )
    logger.info("✅ Message handler registered")

    # Start polling
    logger.info("🚀 Bot started in polling mode")
    logger.info(f"📱 Domain: {get_current_domain_name()}")
    logger.info("💡 Send /admin_help to see available commands")

    await application.run_polling(allowed_updates=["message"])


def run_webhook_mode(host: str, port: int):
    """Run bot in webhook mode (production)"""
    import uvicorn

    logger.info("🌐 Starting bot in WEBHOOK mode")
    logger.info(f"📱 Domain: {get_current_domain_name()}")
    logger.info(f"🔗 Listening on {host}:{port}")

    # Run webhook server (bot/webhook.py already uses flow_router)
    uvicorn.run(
        "bot.webhook:app",
        host=host,
        port=port,
        log_config=None,  # Use our logger
        access_log=False,
        workers=int(os.getenv("UVICORN_WORKERS", "1")),
    )


def main():
    """Main entry point"""
    args = parse_args()

    # Banner
    print("=" * 60)
    print("   AI SELLER BOT - Universal Sales Assistant")
    print("   © SoVAni 2025")
    print("=" * 60)
    print()

    # Override domain if specified
    if args.domain:
        logger.info(f"🔄 Switching to domain: {args.domain}")
        try:
            switch_domain(args.domain)
        except Exception as e:
            logger.error(f"Failed to switch domain: {e}")
            sys.exit(1)

    # Show current configuration
    current_domain = get_current_domain_name()
    logger.info(f"🎯 Current domain: {current_domain}")
    logger.info(f"🚀 Bot mode: {args.mode}")

    # Run bot
    if args.mode == "polling":
        logger.info("⚠️  Polling mode - for development only!")
        asyncio.run(run_polling_mode())
    else:
        logger.info("✅ Webhook mode - production ready")
        run_webhook_mode(args.host, args.port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
