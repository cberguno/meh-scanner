"""
Alert system for high-quality deal detection.
Logs to console + file always.
Also sends a Telegram message when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
To add email/SMS later, extend _fire() only — the caller never changes.
"""
import requests

from config import Config
from logger import logger

ALERT_MIN_SCORE  = 8
_TELEGRAM_API    = "https://api.telegram.org/bot{token}/sendMessage"


def check_and_fire_alerts(deals: list[dict]) -> int:
    """
    Inspect a list of deal dicts and fire an alert for each that passes
    the alert criteria.  Returns the number of alerts fired.

    Criteria
    ────────
    • quality_score  >= ALERT_MIN_SCORE (8)
    • deal_price      non-empty  (a price was extracted from the page)
    • original_price  non-empty  (a comparison price is available)
    """
    fired = 0
    for deal in deals:
        if _qualifies(deal):
            _fire(deal)
            fired += 1
    return fired


# ── private helpers ───────────────────────────────────────────────────────────

def _qualifies(deal: dict) -> bool:
    return (
        (deal.get("quality_score") or 0) >= ALERT_MIN_SCORE
        and bool((deal.get("deal_price")     or "").strip())
        and bool((deal.get("original_price") or "").strip())
    )


def _fire(deal: dict) -> None:
    """
    Emit a structured log entry and, when Telegram is configured, send a
    Telegram message.  Add further delivery channels here only.
    """
    # ── always log ────────────────────────────────────────────────────────────
    logger.info(
        "deal_alert",
        f"[ALERT] {deal.get('site_name', '(unknown)')} | "
        f"{deal.get('deal_price', '')} (was {deal.get('original_price', '')}) | "
        f"score={deal.get('quality_score')} | {deal.get('url', '')}",
        site_name=deal.get("site_name", ""),
        deal_price=deal.get("deal_price", ""),
        original_price=deal.get("original_price", ""),
        quality_score=deal.get("quality_score"),
        url=deal.get("url", ""),
    )

    # ── Telegram (optional) ───────────────────────────────────────────────────
    if Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID:
        _send_telegram(deal)


def _send_telegram(deal: dict) -> None:
    """POST one alert message to a Telegram chat via the Bot API."""
    roi_line = ""
    roi = deal.get("roi_pct")
    profit = deal.get("profit")
    if roi is not None and profit is not None:
        roi_line = f"\n📈 ROI: {roi}% (+${profit:.2f})"

    text = (
        f"🔔 *High-quality deal found*\n\n"
        f"*{deal.get('site_name', '(unknown)')}*\n"
        f"💰 {deal.get('deal_price', '')}  ~~{deal.get('original_price', '')}~~"
        f"{roi_line}\n"
        f"⭐ Score: {deal.get('quality_score')}/10\n\n"
        f"🔗 {deal.get('url', '')}"
    )

    url = _TELEGRAM_API.format(token=Config.TELEGRAM_BOT_TOKEN)
    try:
        resp = requests.post(
            url,
            json={
                "chat_id":    Config.TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        if resp.ok:
            logger.info(
                "telegram_alert_sent",
                f"Telegram alert sent for {deal.get('site_name', '')}",
                site_name=deal.get("site_name", ""),
                chat_id=Config.TELEGRAM_CHAT_ID,
            )
        else:
            logger.warning(
                "telegram_alert_failed",
                f"Telegram responded {resp.status_code}: {resp.text[:120]}",
                status_code=resp.status_code,
            )
    except Exception as exc:
        logger.error(
            "telegram_alert_error",
            f"Telegram request failed: {exc}",
            error=str(exc),
        )
