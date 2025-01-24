import logging
import sqlite3
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime
import asyncio

# تنظیمات لاگینگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# متغیر برای ذخیره شناسه چت
CHAT_ID = None

# تنظیمات API
ETHERSCAN_API_KEY = 'NDVC7KJVNVUK1SIDKQVC6AXH8UN6QYTPIV'
ETHERSCAN_URL = 'https://api.etherscan.io/api'

# دیتابیس SQLite برای ذخیره ولت‌ها
def create_db():
    conn = sqlite3.connect('wallets.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS wallets (
                    address TEXT PRIMARY KEY, 
                    name TEXT,
                    last_tx TEXT)''')  # اضافه شدن فیلد آخرین تراکنش
    conn.commit()
    conn.close()

def add_wallet_to_db(address, name):
    conn = sqlite3.connect('wallets.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO wallets (address, name, last_tx) VALUES (?, ?, ?)", (address, name, None))
    conn.commit()
    conn.close()

def list_wallets_from_db():
    conn = sqlite3.connect('wallets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM wallets")
    wallets = c.fetchall()
    conn.close()
    return wallets

def update_last_tx(address, tx_hash):
    conn = sqlite3.connect('wallets.db')
    c = conn.cursor()
    c.execute("UPDATE wallets SET last_tx = ? WHERE address = ?", (tx_hash, address))
    conn.commit()
    conn.close()

# بررسی صحت آدرس کیف پول
def is_valid_address(address):
    return address.startswith("0x") and len(address) == 42

# فرمت زمان یونیکس به تاریخ خوانا
def format_timestamp(timestamp):
    return datetime.utcfromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S')

# تابع دریافت نرخ اتر به تومان از Nobitex
def get_eth_to_toman_rate():
    try:
        # درخواست به API صرافی Nobitex برای نرخ ETH/IRR
        response = requests.get("https://api.nobitex.ir/market/stats")
        response.raise_for_status()
        data = response.json()
        eth_price_toman = float(data['stats']['IRT']['ETH']['latest'])  # نرخ آخرین معامله اتر به تومان
        return eth_price_toman
    except requests.exceptions.RequestException as e:
        logger.error(f"خطا در دریافت نرخ اتر به تومان: {e}")
        return None

# تابع بررسی تراکنش‌ها
def check_wallet_transactions(address):
    params = {
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }

    try:
        response = requests.get(ETHERSCAN_URL, params=params)
        response.raise_for_status()
        data = response.json()
        if data["status"] == "1" and data["message"] == "OK":
            return data["result"]
        else:
            return []
    except requests.exceptions.RequestException as e:
        logger.error(f"خطای درخواست API: {e}")
        return []

# فرمان /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    await update.message.reply_text("سلام! من آماده‌ام کیف پول‌ها را مانیتور کنم.")

# فرمان /addwallet
async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("❌ لطفاً آدرس و اسم ولت را وارد کنید. مثال:\n/add 0x123456789 Wallet1")
        return

    address = context.args[0]
    name = " ".join(context.args[1:])

    if not is_valid_address(address):
        await update.message.reply_text("❌ آدرس ولت معتبر نیست. لطفاً دوباره بررسی کنید.")
        return

    add_wallet_to_db(address, name)
    await update.message.reply_text(f"✅ ولت {name} با آدرس {address} ثبت شد.")

# فرمان /listwallets
async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wallets = list_wallets_from_db()
    if not wallets:
        await update.message.reply_text("❌ هیچ ولتی ثبت نشده است.")
        return

    response = "🔍 لیست ولت‌های ثبت شده:\n"
    for address, name, _ in wallets:
        response += f"• {name}: {address}\n"
    await update.message.reply_text(response)

# مانیتورینگ خودکار ولت‌ها
async def monitor_wallets(context: ContextTypes.DEFAULT_TYPE) -> None:
    global CHAT_ID
    if not CHAT_ID:
        logger.warning("شناسه چت تنظیم نشده است. پیام‌ها ارسال نمی‌شود.")
        return

    wallets = list_wallets_from_db()
    eth_to_toman_rate = get_eth_to_toman_rate()
    if eth_to_toman_rate is None:
        logger.warning("❌ نرخ لحظه‌ای اتر به تومان قابل دریافت نیست.")
        return

    for address, name, last_tx in wallets:
        transactions = check_wallet_transactions(address)
        if not transactions:
            continue

        # بررسی تراکنش جدید
        latest_tx = transactions[0]  # جدیدترین تراکنش
        if latest_tx['hash'] != last_tx:
            update_last_tx(address, latest_tx['hash'])  # به‌روزرسانی دیتابیس

            # محاسبه مقدار به تومان
            tx_value = int(latest_tx['value']) / (10 ** 18)  # تبدیل Wei به اتر
            tx_value_toman = tx_value * eth_to_toman_rate  # تبدیل به تومان
            tx_value_toman_formatted = f"{tx_value_toman:,.0f}"  # فرمت‌گذاری تومان

            tx_time = format_timestamp(latest_tx['timeStamp'])
            tx_type = "خرید" if latest_tx['to'].lower() == address.lower() else "فروش"

            message = (
                f"🔔 تراکنش جدید برای {name}:\n"
                f"• نوع: {tx_type}\n"
                f"• مقدار: {tx_value:.18f} ETH\n"
                f"• معادل: {tx_value_toman_formatted} تومان\n"
                f"• زمان: {tx_time}\n"
                f"• آدرس تراکنش: {latest_tx['hash']}"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=message)

# مدیریت خطاها
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"⚠️ خطایی رخ داد: {context.error}")
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ مشکلی پیش آمد. لطفاً دوباره تلاش کنید.")

# راه‌اندازی ربات
def main() -> None:
    create_db()
    application = ApplicationBuilder().token("7066120089:AAGrSvgrVwNwSMyy8tpnWUDmaUaQpde-OlA").build()

    # ثبت فرمان‌ها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_wallet))
    application.add_handler(CommandHandler("list", list_wallets))

    # ثبت وظیفه مانیتورینگ خودکار
    application.job_queue.run_repeating(monitor_wallets, interval=30)

    # ثبت مدیریت خطا
    application.add_error_handler(error_handler)

    # اجرای ربات
    application.run_polling()

if __name__ == "__main__":
    main()