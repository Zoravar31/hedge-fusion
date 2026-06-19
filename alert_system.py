"""
HedgeFusion Alert System
==========================
Sends alerts when:
  - A watchlist stock enters BUY ZONE
  - A holding hits its stop loss
  - Portfolio Manager APPROVES an order
  - Daily portfolio summary

Channels supported:
  - Telegram (free, recommended)
  - Email via Gmail SMTP (free)
  - WhatsApp via Twilio (paid, ₹2-5 per message)

Setup guide for each channel is in .env.example comments below.

Usage:
    python alert_system.py --test              # send test message to all channels
    python alert_system.py --watchlist         # alert on watchlist BUY ZONEs
    python alert_system.py --portfolio-summary # send daily portfolio digest
    python alert_system.py --order RELIANCE BUY 5 1280  # manual order alert
"""

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

# ── Channel config from .env ──────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_SENDER       = os.getenv("ALERT_EMAIL_SENDER", "")
EMAIL_PASSWORD     = os.getenv("ALERT_EMAIL_PASSWORD", "")
EMAIL_RECIPIENT    = os.getenv("ALERT_EMAIL_RECIPIENT", "")
TWILIO_SID         = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN       = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM        = os.getenv("TWILIO_WHATSAPP_FROM", "")
TWILIO_TO          = os.getenv("TWILIO_WHATSAPP_TO", "")


# ── Telegram sender ───────────────────────────────────────────

def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send alert via Telegram bot.
    
    Setup:
      1. Open Telegram → search @BotFather → /newbot → follow steps
      2. Copy the bot token → TELEGRAM_BOT_TOKEN in .env
      3. Start a chat with your bot, send /start
      4. Visit https://api.telegram.org/bot<TOKEN>/getUpdates
         to find your chat_id → TELEGRAM_CHAT_ID in .env
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": parse_mode,
        }, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram alert sent ✓")
            return True
        else:
            logger.error("Telegram failed: {} {}", resp.status_code, resp.text[:100])
            return False
    except Exception as e:
        logger.error("Telegram error: {}", e)
        return False


# ── Email sender ──────────────────────────────────────────────

def send_email(subject: str, body_html: str) -> bool:
    """
    Send alert via Gmail SMTP.

    Setup:
      1. Enable 2FA on your Gmail account
      2. Go to Google Account → Security → App passwords
      3. Generate an app password for "Mail"
      4. Set ALERT_EMAIL_SENDER=your@gmail.com in .env
      5. Set ALERT_EMAIL_PASSWORD=your_app_password in .env
      6. Set ALERT_EMAIL_RECIPIENT=where_to_send@gmail.com in .env
    """
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
        logger.warning("Email not configured (ALERT_EMAIL_* vars missing)")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_RECIPIENT
        msg.attach(MIMEText(body_html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

        logger.info("Email alert sent ✓")
        return True
    except Exception as e:
        logger.error("Email error: {}", e)
        return False


# ── WhatsApp sender via Twilio ────────────────────────────────

def send_whatsapp(message: str) -> bool:
    """
    Send WhatsApp alert via Twilio.

    Setup:
      1. Sign up at https://twilio.com (free trial = $15 credit)
      2. Enable WhatsApp sandbox: console.twilio.com → Messaging → Try WhatsApp
      3. Send the join code from your WhatsApp to +14155238886
      4. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN in .env
      5. Set TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
      6. Set TWILIO_WHATSAPP_TO=whatsapp:+91XXXXXXXXXX (your number)
    """
    if not TWILIO_SID or not TWILIO_TOKEN:
        logger.warning("Twilio not configured (TWILIO_* vars missing)")
        return False
    try:
        url  = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
        resp = requests.post(url,
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={"From": TWILIO_FROM, "To": TWILIO_TO, "Body": message},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info("WhatsApp alert sent ✓")
            return True
        else:
            logger.error("WhatsApp failed: {} {}", resp.status_code, resp.text[:100])
            return False
    except Exception as e:
        logger.error("WhatsApp error: {}", e)
        return False


# ── Alert composers ───────────────────────────────────────────

def alert_buy_zone(ticker: str, current_price: float, target_price: float,
                   stop_loss: float, note: str) -> bool:
    """Send BUY ZONE alert across all configured channels."""
    timestamp  = datetime.now().strftime("%H:%M IST, %d %b %Y")
    pct_from   = (current_price - target_price) / target_price * 100

    tg_msg = (
        f"🔥 <b>BUY ZONE ALERT — {ticker}</b>\n\n"
        f"💰 Current price: ₹{current_price:,.2f}\n"
        f"🎯 Your entry target: ₹{target_price:,.2f}\n"
        f"📍 Distance: {pct_from:+.1f}% from target\n"
        f"🛑 Suggested SL: ₹{stop_loss:,.2f}\n\n"
        f"📝 {note}\n\n"
        f"<i>HedgeFusion · {timestamp}</i>"
    )

    wa_msg = (
        f"🔥 BUY ZONE: {ticker}\n"
        f"Price: ₹{current_price:,.2f} (target: ₹{target_price:,.2f})\n"
        f"SL: ₹{stop_loss:,.2f}\n"
        f"{note}\n"
        f"HedgeFusion · {timestamp}"
    )

    email_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;
                background:#050d1a;color:#e2e8f0;padding:24px;border-radius:10px">
      <h2 style="color:#f59e0b;margin-bottom:4px">🔥 BUY ZONE ALERT</h2>
      <h1 style="color:#f8fafc;font-size:28px;margin:0">{ticker}</h1>
      <hr style="border-color:#1e293b;margin:16px 0">
      <table style="width:100%;font-size:14px;color:#94a3b8">
        <tr><td>Current Price</td><td style="color:#f8fafc;font-weight:700;
            text-align:right">₹{current_price:,.2f}</td></tr>
        <tr><td>Entry Target</td><td style="color:#22c55e;font-weight:700;
            text-align:right">₹{target_price:,.2f}</td></tr>
        <tr><td>Distance</td><td style="text-align:right">{pct_from:+.1f}%</td></tr>
        <tr><td>Stop Loss</td><td style="color:#ef4444;font-weight:700;
            text-align:right">₹{stop_loss:,.2f}</td></tr>
      </table>
      <p style="font-size:13px;color:#94a3b8;margin-top:16px">{note}</p>
      <p style="font-size:11px;color:#475569;margin-top:20px">
        HedgeFusion · {timestamp}<br>
        Not SEBI-registered advice. Apply your own judgment.</p>
    </div>"""

    ok1 = send_telegram(tg_msg)
    ok2 = send_email(f"🔥 BUY ZONE: {ticker} @ ₹{current_price:,.0f}", email_body)
    ok3 = send_whatsapp(wa_msg)
    return ok1 or ok2 or ok3


def alert_order_executed(ticker: str, action: str, qty: int,
                          price: float, order_id: str, mode: str = "PAPER") -> bool:
    """Send order execution alert."""
    timestamp = datetime.now().strftime("%H:%M IST, %d %b %Y")
    emoji     = "📄" if mode == "PAPER" else "💹"
    value     = qty * price

    tg_msg = (
        f"{emoji} <b>{'PAPER ' if mode=='PAPER' else ''}ORDER EXECUTED</b>\n\n"
        f"Stock: <b>{ticker}</b>\n"
        f"Action: <b>{action}</b>\n"
        f"Qty: {qty} shares\n"
        f"Price: ₹{price:,.2f}\n"
        f"Value: ₹{value:,.0f}\n"
        f"Order ID: {order_id}\n\n"
        f"<i>HedgeFusion · {timestamp}</i>"
    )

    wa_msg = (
        f"{emoji} {'PAPER ' if mode=='PAPER' else ''}ORDER: "
        f"{action} {qty}×{ticker} @ ₹{price:,.0f} = ₹{value:,.0f}\n"
        f"ID: {order_id}\nHedgeFusion · {timestamp}"
    )

    ok1 = send_telegram(tg_msg)
    ok2 = send_whatsapp(wa_msg)
    return ok1 or ok2


def alert_stop_loss_hit(ticker: str, entry_price: float, current_price: float,
                         loss_pct: float) -> bool:
    """Send stop loss alert."""
    timestamp = datetime.now().strftime("%H:%M IST, %d %b %Y")

    tg_msg = (
        f"🛑 <b>STOP LOSS WARNING — {ticker}</b>\n\n"
        f"Entry price: ₹{entry_price:,.2f}\n"
        f"Current price: ₹{current_price:,.2f}\n"
        f"Loss: <b>{loss_pct:.1f}%</b>\n\n"
        f"Consider exiting to limit further losses.\n\n"
        f"<i>HedgeFusion · {timestamp}</i>"
    )

    ok1 = send_telegram(tg_msg)
    ok2 = send_whatsapp(f"🛑 SL HIT: {ticker} down {loss_pct:.1f}% from entry. Review position. {timestamp}")
    return ok1 or ok2


def send_daily_summary(portfolio_results: list) -> bool:
    """Send a daily portfolio digest."""
    timestamp = datetime.now().strftime("%d %b %Y")
    buys  = [r for r in portfolio_results if r.get("research_verdict",{}).get("recommendation") == "BUY"]
    holds = [r for r in portfolio_results if r.get("research_verdict",{}).get("recommendation") == "HOLD"]
    sells = [r for r in portfolio_results if r.get("research_verdict",{}).get("recommendation") == "SELL"]
    approved = [r for r in portfolio_results if r.get("pm_decision",{}).get("decision") == "APPROVE"]

    lines = [f"📊 <b>HedgeFusion Daily Digest — {timestamp}</b>\n"]
    if buys:
        lines.append(f"🟢 <b>BUY signals ({len(buys)}):</b> {', '.join(r['ticker'] for r in buys)}")
    if holds:
        lines.append(f"🟡 <b>HOLD signals ({len(holds)}):</b> {', '.join(r['ticker'] for r in holds)}")
    if sells:
        lines.append(f"🔴 <b>SELL signals ({len(sells)}):</b> {', '.join(r['ticker'] for r in sells)}")
    if approved:
        lines.append(f"\n✅ <b>PM approved:</b> {', '.join(r['ticker'] for r in approved)}")

    tg_msg = "\n".join(lines) + "\n\n<i>Not investment advice. Paper mode active.</i>"
    return send_telegram(tg_msg)


def send_test_alert() -> None:
    """Send test message to all configured channels."""
    timestamp = datetime.now().strftime("%H:%M IST, %d %b %Y")
    print("\nSending test alerts to configured channels...\n")

    tg = send_telegram(
        f"✅ <b>HedgeFusion Test Alert</b>\n\n"
        f"Your Telegram alerts are working correctly.\n"
        f"<i>{timestamp}</i>"
    )
    em = send_email(
        "✅ HedgeFusion Test Alert",
        f"<p>Your email alerts are working correctly.</p><p>{timestamp}</p>"
    )
    wa = send_whatsapp(f"✅ HedgeFusion: Your WhatsApp alerts are working. {timestamp}")

    print(f"\nResults:")
    print(f"  Telegram: {'✅ Sent' if tg else '❌ Not configured or failed'}")
    print(f"  Email:    {'✅ Sent' if em else '❌ Not configured or failed'}")
    print(f"  WhatsApp: {'✅ Sent' if wa else '❌ Not configured or failed'}")

    if not any([tg, em, wa]):
        print("\n⚠️  No channels configured. Add to .env:")
        print("    TELEGRAM_BOT_TOKEN=  (from @BotFather)")
        print("    TELEGRAM_CHAT_ID=    (from getUpdates API)")
        print("    ALERT_EMAIL_SENDER=  your@gmail.com")
        print("    ALERT_EMAIL_PASSWORD= app_password")
        print("    ALERT_EMAIL_RECIPIENT= recipient@gmail.com")


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Alert System")
    parser.add_argument("--test",     action="store_true", help="Send test alert")
    parser.add_argument("--watchlist",action="store_true", help="Alert on BUY ZONEs")
    parser.add_argument("--order",    nargs=4,
                        metavar=("TICKER","ACTION","QTY","PRICE"),
                        help="Manual order alert")
    args = parser.parse_args()

    if args.test:
        send_test_alert()
    elif args.order:
        ticker, action, qty, price = args.order
        alert_order_executed(ticker, action, int(qty), float(price),
                             "MANUAL-001", mode="PAPER")
    elif args.watchlist:
        from watchlist import run_watchlist_scan
        results = run_watchlist_scan()
        for r in results:
            if r.get("alert_level") == "BUY_ZONE":
                alert_buy_zone(
                    ticker=r["ticker"],
                    current_price=float(r.get("current_price", 0)),
                    target_price=float(r.get("entry_target", 0)),
                    stop_loss=float(r.get("stop_loss", 0)),
                    note=r.get("note", ""),
                )
    else:
        send_test_alert()
