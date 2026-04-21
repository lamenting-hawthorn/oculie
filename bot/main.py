"""
Main orchestrator for the Polymarket Weather Prediction Agent.

Ties together the scan cycle, scheduler, and lifecycle management.
Runs the trading bot as a long-lived process with configurable scan intervals.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.database import get_bot_status, get_db, get_setting, init_db, set_setting
from bot.reporter import format_error, poll_telegram_commands, send_alert, send_daily_summary
from bot.resolver import run_resolver
from bot.trade_engine import _parse_positive_usdc, run_scan_cycle

os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler("data/bot.log", mode="a", maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)
log = logging.getLogger("openclaw")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
scheduler: AsyncIOScheduler | None = None
_shutdown_event: asyncio.Event | None = None


async def _prompt_live_trading_budget(
    wallet_balance: float, default_budget: float | None
) -> float:
    """Prompt for the live trading budget when running in an interactive shell."""
    default_text = f" [{default_budget:.2f}]" if default_budget is not None else ""
    prompt = (
        f"Live wallet balance: ${wallet_balance:.2f}\n"
        f"USDC amount to make available for this bot{default_text}: "
    )

    while True:
        raw = await asyncio.to_thread(input, prompt)
        if not raw.strip() and default_budget is not None:
            amount = default_budget
        else:
            amount = _parse_positive_usdc(raw)

        if amount is None:
            print("Enter a positive USDC amount.")
            continue
        if amount > wallet_balance:
            print(f"Budget ${amount:.2f} exceeds wallet balance ${wallet_balance:.2f}.")
            continue
        return amount


async def _configure_live_trading_budget(db) -> None:
    """Fetch live wallet balance and require a live-trading budget before startup."""
    try:
        from bot.clob_client import build_clob_client, get_usdc_balance
    except Exception as exc:
        raise RuntimeError("Live mode requires py_clob_client to check wallet balance") from exc

    wallet_balance = get_usdc_balance(build_clob_client())
    if wallet_balance <= 0:
        raise RuntimeError(
            "Live mode wallet balance is zero or unavailable; refusing to start live trading"
        )

    env_budget = os.environ.get("LIVE_TRADING_BUDGET_USDC")
    saved_budget = _parse_positive_usdc(await get_setting(db, "live_trading_budget_usdc"))
    source = "saved setting"

    if env_budget is not None:
        budget = _parse_positive_usdc(env_budget)
        source = "LIVE_TRADING_BUDGET_USDC"
        if budget is None:
            raise RuntimeError("LIVE_TRADING_BUDGET_USDC must be a positive USDC amount")
    elif sys.stdin.isatty():
        default_budget = saved_budget if saved_budget and saved_budget <= wallet_balance else None
        budget = await _prompt_live_trading_budget(wallet_balance, default_budget)
        source = "startup prompt"
    elif saved_budget is not None:
        budget = saved_budget
    else:
        raise RuntimeError(
            "Live trading budget is not set. Start the bot in an interactive terminal "
            "or set LIVE_TRADING_BUDGET_USDC."
        )

    if budget > wallet_balance:
        raise RuntimeError(
            f"Live trading budget ${budget:.2f} exceeds wallet balance ${wallet_balance:.2f}"
        )

    await set_setting(db, "live_trading_budget_usdc", f"{budget:.2f}")
    log.info(
        "Live wallet balance checked: wallet=$%.2f trading_budget=$%.2f (%s)",
        wallet_balance,
        budget,
        source,
    )


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------
async def scheduled_scan():
    """Run one scan cycle — called by the scheduler every N minutes."""
    db = await get_db()
    status = await get_bot_status(db)
    if status == "stopped":
        log.info("Bot is paused (bot_status=stopped) — skipping scan cycle")
        return

    log.info("⏱  Heartbeat triggered — starting scan cycle")
    try:
        result = await run_scan_cycle()
        log.info(
            "Scan complete: cities=%d markets=%d opportunities=%d trades=%d errors=%d",
            result.cities_scanned,
            result.markets_found,
            len(result.opportunities),
            len(result.trades_executed),
            len(result.errors),
        )
        if result.errors:
            for err in result.errors:
                log.warning("Scan error: %s", err)
    except Exception as exc:
        log.exception("Scan cycle crashed: %s", exc)
        try:
            db = await get_db()
            msg = await format_error(str(exc), retry_minutes=5)
            await send_alert(db, "error", msg)
        except Exception:
            log.exception("Failed to send error alert")


async def scheduled_resolver():
    """Check open positions for resolution — called by the scheduler every 15 minutes."""
    log.info("Resolver triggered — checking open positions")
    try:
        count = await run_resolver()
        log.info("Resolver complete: %d position(s) resolved", count)
    except Exception as exc:
        log.exception("Resolver crashed: %s", exc)
        try:
            db = await get_db()
            msg = await format_error(str(exc), retry_minutes=15)
            await send_alert(db, "error", msg)
        except Exception:
            log.exception("Failed to send resolver error alert")


async def scheduled_daily_summary():
    """Send the daily P&L summary — called once per day at midnight UTC."""
    log.info("📊 Sending daily summary")
    try:
        db = await get_db()
        await send_daily_summary(db)
    except Exception as exc:
        log.exception("Daily summary failed: %s", exc)


async def _poll_telegram():
    """Poll Telegram for inbound commands — called every 10 seconds."""
    db = await get_db()
    await poll_telegram_commands(db)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def start():
    """Initialize DB, configure scheduler, and start the bot."""
    global scheduler, _shutdown_event
    _shutdown_event = asyncio.Event()

    # Init database
    log.info("Initializing database…")
    db = await init_db()

    # Read scan interval from settings
    interval_str = await get_setting(db, "scan_interval_minutes")
    interval_minutes = int(interval_str) if interval_str else 30
    log.info("Scan interval: %d minutes", interval_minutes)

    # Paper mode check
    paper = await get_setting(db, "paper_mode")
    if paper and paper.lower() == "true":
        log.info("🟡 Running in PAPER TRADING mode — no real orders will be placed")
    else:
        log.info("🟢 Running in LIVE TRADING mode")
        await _configure_live_trading_budget(db)

    # Configure scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Scan cycle job
    scheduler.add_job(
        scheduled_scan,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scan_cycle",
        name="Weather Market Scan",
        max_instances=1,
        next_run_time=datetime.now(timezone.utc),  # run immediately on start
    )

    # Position resolver job (every 15 minutes)
    scheduler.add_job(
        scheduled_resolver,
        trigger=IntervalTrigger(minutes=15),
        id="resolver",
        name="Position Resolver",
        max_instances=1,
    )

    # Daily summary at 00:05 UTC
    scheduler.add_job(
        scheduled_daily_summary,
        trigger="cron",
        hour=0,
        minute=5,
        id="daily_summary",
        name="Daily P&L Summary",
        max_instances=1,
    )

    # Telegram command poller (every 10 seconds) — only if token is configured
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if telegram_token:
        scheduler.add_job(
            _poll_telegram,
            trigger=IntervalTrigger(seconds=10),
            id="telegram_poll",
            name="Telegram Command Poller",
            max_instances=1,
        )
        log.info("Telegram polling enabled")
    else:
        log.info("Telegram polling disabled — TELEGRAM_BOT_TOKEN not set")

    scheduler.start()
    log.info("🚀 Polymarket Weather Agent started — scheduler running")

    # Update bot status in settings
    await set_setting(db, "bot_status", "running")
    await set_setting(db, "bot_started_at", datetime.now(timezone.utc).isoformat())

    # Wait for shutdown signal
    await _shutdown_event.wait()


async def stop():
    """Graceful shutdown."""
    global scheduler, _shutdown_event
    log.info("Shutting down…")

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")

    try:
        db = await get_db()
        await set_setting(db, "bot_status", "stopped")
    except Exception:
        log.exception("Failed to persist stopped status during shutdown")

    if _shutdown_event:
        _shutdown_event.set()

    log.info("✅ Polymarket Weather Agent stopped")


def _handle_signal(sig, frame):
    """Handle OS signals for graceful shutdown."""
    log.info("Received signal %s", sig)
    if _shutdown_event:
        _shutdown_event.set()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    """Synchronous entry point."""
    # Register signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("=" * 60)
    log.info("Polymarket Weather Prediction Agent v0.1.0")
    log.info("=" * 60)

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        try:
            asyncio.run(stop())
        except Exception:
            log.exception("stop() failed during shutdown")


if __name__ == "__main__":
    main()
