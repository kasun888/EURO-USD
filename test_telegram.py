"""
Telegram connection test — Fiber Scalp v1.5
Run: python test_telegram.py
"""
from telegram_alert import TelegramAlert
from config_loader import load_settings

if __name__ == "__main__":
    _name = load_settings().get("bot_name", "Fiber Scalp v1.5")
    alert = TelegramAlert()
    ok = alert.send(
        f"✅ Test message — Telegram is connected and working!\n"
        f"Fiber Scalp v1.5 — Telegram connected. Ready to deploy."
    )
    if ok:
        print("✅ Message sent successfully.")
    else:
        print("❌ Failed to send. Check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in secrets.json.")
