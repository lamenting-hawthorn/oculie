"""
Reporter module for the Polymarket Weather Prediction Agent.

Formats and sends trade alerts and daily summaries via Telegram and/or WhatsApp.
"""

import asyncio
import logging
import os
from datetime import date

import httpx
from dotenv import load_dotenv

from bot.database import get_db, get_open_positions, get_pnl_summary, get_setting, get_trade_history, insert_alert, set_setting
from bot.trade_engine import run_scan_cycle

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transport: Telegram
# ---------------------------------------------------------------------------


async def send_telegram(message: str, chat_id: str, bot_token: str) -> bool:
    """Send a message via the Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram message sent to chat %s", chat_id)
                return True
            logger.warning("Telegram API returned ok=false: %s", data)
            return False
    except Exception:
        logger.exception("Failed to send Telegram message")
        return False


# ---------------------------------------------------------------------------
# Transport: WhatsApp (Meta Cloud API)
# ---------------------------------------------------------------------------


async def send_whatsapp(
    message: str, phone_id: str, access_token: str, recipient: str
) -> bool:
    """Send a message via the WhatsApp Cloud API. Returns True on success."""
    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": message},
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            logger.info("WhatsApp message sent to %s", recipient)
            return True
    except Exception:
        logger.exception("Failed to send WhatsApp message")
        return False


# ---------------------------------------------------------------------------
# Unified alert dispatcher
# ---------------------------------------------------------------------------

# Maps alert_type to the DB setting key that controls whether it's enabled
_ALERT_PREF_MAP = {
    "trade_entered": "alert_trade_entered",
    "trade_resolved": "alert_trade_resolved",
    "daily_summary": "alert_daily_summary",
    "error": "alert_errors",
    "floor_hit": "alert_errors",
    "paper_signal": "alert_trade_entered",
    "test": None,  # always send test alerts
}


async def send_alert(
    db,
    alert_type: str,
    message: str,
    city: str | None = None,
) -> bool:
    """
    Send an alert through the configured messaging channel.

    Checks user preferences, dispatches via Telegram or WhatsApp, and records
    the alert in the database. Returns True if the message was sent successfully.
    """
    try:
        # Check if this alert type is enabled
        pref_key = _ALERT_PREF_MAP.get(alert_type)
        if pref_key is not None:
            enabled = await get_setting(db, pref_key)
            if enabled and enabled.lower() != "true":
                logger.info("Alert type '%s' is disabled (setting %s=%s)", alert_type, pref_key, enabled)
                return False

        # Determine messaging channel
        channel = (await get_setting(db, "messaging_app")) or "telegram"
        sent = False

        if channel == "telegram":
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
            if not bot_token or not chat_id:
                logger.error("Telegram credentials not set in environment variables")
                return False
            sent = await send_telegram(message, chat_id, bot_token)

        elif channel == "whatsapp":
            phone_id = os.getenv("WHATSAPP_PHONE_ID", "")
            access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
            recipient = os.getenv("WHATSAPP_RECIPIENT", "")
            if not phone_id or not access_token or not recipient:
                logger.error("WhatsApp credentials not set in environment variables")
                return False
            sent = await send_whatsapp(message, phone_id, access_token, recipient)

        else:
            logger.error("Unknown messaging channel: %s", channel)
            return False

        # Record alert in database
        await insert_alert(
            db,
            alert_type=alert_type,
            city=city,
            message=message,
            channel=channel,
        )

        return sent

    except Exception:
        logger.exception("send_alert failed for type '%s'", alert_type)
        return False


# ---------------------------------------------------------------------------
# Message formatters
# ---------------------------------------------------------------------------


async def format_trade_entered(trade: dict, paper: bool = False) -> str:
    """Format a trade-entered or paper-signal message."""
    city = trade.get("city", "Unknown")
    range_ = trade.get("market_question", "")
    prob = trade.get("noaa_probability", 0)
    price = trade.get("market_price", 0)
    edge = trade.get("edge", 0)
    size = trade.get("bet_size", 0)

    if paper:
        return (
            f"\U0001f4dd [PAPER] Signal \u2014 {city} {range_} "
            f"| NOAA: {prob}% | Market: {price}% "
            f"| Edge: +{edge}% | Would bet: ${size}"
        )

    return (
        f"\U0001f7e2 Trade entered \u2014 {city} | {range_} "
        f"| NOAA: {prob}% | Market: {price}% "
        f"| Edge: +{edge}% | Bet: ${size}"
    )


async def format_trade_resolved(trade: dict) -> str:
    """Format a trade-resolved message (won or lost)."""
    city = trade.get("city", "Unknown")
    range_ = trade.get("market_question", "")
    entry = trade.get("entry_price", 0)
    pnl = abs(trade.get("pnl", 0))
    outcome = trade.get("outcome", "")

    if outcome == "won":
        return (
            f"\u2705 Won \u2014 {city} {range_} "
            f"| Entry: ${entry} | Resolved: $1.00 | Profit: +${pnl}"
        )

    return (
        f"\u274c Lost \u2014 {city} {range_} "
        f"| Entry: ${entry} | Resolved: $0.00 | Loss: -${pnl}"
    )


async def format_daily_summary(db) -> str:
    """Build a daily summary message from today's trade data."""
    summary = await get_pnl_summary(db)

    # Get today's trades for the daily count
    today_str = date.today().isoformat()
    all_trades = await get_trade_history(db, limit=1000)
    today_trades = [
        t for t in all_trades
        if t.get("resolved_at") and str(t["resolved_at"])[:10] == today_str
    ]

    n = len(today_trades)
    w = sum(1 for t in today_trades if t.get("outcome") == "won")
    l_ = sum(1 for t in today_trades if t.get("outcome") == "lost")
    pnl = summary["today_pnl"]

    # Approximate balance: we don't store balance directly, use total_pnl as proxy
    bal = summary["total_pnl"]

    return (
        f"\U0001f4ca Daily Summary | Trades: {n} | Won: {w} | Lost: {l_} "
        f"| P&L: {pnl} | Balance: ${bal}"
    )


async def format_error(description: str, retry_minutes: int = 5) -> str:
    """Format a system error message."""
    return (
        f"\u26a0\ufe0f Error: {description}. "
        f"Retrying in {retry_minutes} min. No trades affected."
    )


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------


async def send_daily_summary(db) -> bool:
    """Generate and send the daily summary. Called by the scheduler once per day."""
    try:
        message = await format_daily_summary(db)
        return await send_alert(db, "daily_summary", message)
    except Exception:
        logger.exception("Failed to send daily summary")
        return False


async def send_test_alert(db) -> bool:
    """Send a test alert to verify messaging setup."""
    message = "\U0001f514 Test alert \u2014 Polymarket Weather Agent is connected and working."
    return await send_alert(db, "test", message)


# ---------------------------------------------------------------------------
# Telegram inbound command handling (polling)
# ---------------------------------------------------------------------------


async def _handle_telegram_command(db, text: str, bot_token: str, chat_id: str) -> None:
    """Dispatch an inbound Telegram command and reply to the user."""
    cmd = text.split()[0].lower() if text else ""

    if cmd == "/status":
        bot_status = await get_setting(db, "bot_status") or "running"
        paper_mode = await get_setting(db, "paper_mode") or "false"
        scan_interval = await get_setting(db, "scan_interval_minutes") or "30"
        positions = await get_open_positions(db)
        reply = (
            f"<b>Polymarket Weather Agent Status</b>\n"
            f"Status: {bot_status}\n"
            f"Paper mode: {paper_mode}\n"
            f"Scan interval: every {scan_interval} min\n"
            f"Open positions: {len(positions)}"
        )

    elif cmd == "/pause":
        await set_setting(db, "bot_status", "stopped")
        reply = "Bot paused. Use /resume to restart."

    elif cmd == "/resume":
        await set_setting(db, "bot_status", "running")
        reply = "Bot resumed."

    elif cmd == "/positions":
        positions = await get_open_positions(db)
        if not positions:
            reply = "No open positions."
        else:
            lines = ["<b>Open Positions</b>"]
            for p in positions:
                lines.append(
                    f"• {p.get('city')} | {p.get('direction')} | "
                    f"Entry: ${p.get('entry_price')} | Size: ${p.get('size')}"
                )
            reply = "\n".join(lines)

    elif cmd == "/forcescan":
        try:
            result = await run_scan_cycle()
            reply = (
                f"Scan complete: {len(result.opportunities)} opportunities, "
                f"{len(result.trades_executed)} trades."
            )
        except Exception:
            logger.exception("Force scan failed")
            reply = "Force scan failed — check logs for details."

    else:
        # Unknown or empty command — silently ignore non-command messages
        if not cmd.startswith("/"):
            return
        reply = f"Unknown command: {cmd}\nSupported: /status /pause /resume /positions /forcescan"

    await send_telegram(reply, chat_id, bot_token)


async def poll_telegram_commands(db) -> None:
    """Long-poll Telegram for incoming commands. Runs once per scheduler tick."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    # Use offset stored in DB to avoid re-processing old messages
    offset_val = await get_setting(db, "telegram_update_offset") or "0"
    offset = int(offset_val)

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": offset, "timeout": 5, "limit": 10}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("Failed to poll Telegram updates")
        return

    updates = data.get("result", [])
    for update in updates:
        offset = max(offset, update["update_id"] + 1)
        msg = update.get("message", {})
        from_chat = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        # Only respond to the configured chat
        if from_chat != chat_id:
            continue

        await _handle_telegram_command(db, text, bot_token, chat_id)

    if updates:
        await set_setting(db, "telegram_update_offset", str(offset))


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    async def _main():
        db = await get_db()
        try:
            success = await send_test_alert(db)
            if success:
                logger.info("Test alert sent successfully.")
            else:
                logger.warning("Test alert failed. Check credentials and settings.")
        finally:
            await db.close()

    asyncio.run(_main())
