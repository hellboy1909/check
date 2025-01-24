import logging
import sqlite3
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime
import asyncio
from cachetools import cached, TTLCache

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

# ایجاد یک کش با زمان انقضا (مثلاً 5 دقیقه)
cache = TTLCache(maxsize=100, ttl=300)

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

# دریافت نرخ لحظه‌ای توکن‌ها به دلار
@cached(cache)
def get_token_to_usd_rate(symbol):
    try:
        response = requests.get("https://api.coingecko.com/api/v3/simple/price", params={
            "ids": symbol.lower(),
            "vs_currencies": "usd"
        })
        response.raise_for_status()
        data = response.json()
        usd_rate = data.get(symbol.lower(), {}).get("usd", None)
        if usd_rate is not None:
            return usd_rate
        else:
            logger.warning(f"نرخ ارز {symbol} یافت نشد.")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"خطا در دریافت نرخ از API: {e}")
        return None

# بررسی تراکنش‌های عادی و ERC-20
def check_wallet_transactions(address):
    params = {
        'module': 'account',
        'action': 'tokentx',  # بررسی تراکنش‌های توکن
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
            logger.warning(f"خطا در دریافت تراکنش‌ها برای آدرس {address}: {data.get('message', 'Unknown error')}")
            return []
    except requests.exceptions.RequestException as e:
        logger.error(f"خطای درخواست API: {e}")
        return []

# محاسبه سود و ضرر ارزهای خریداری شده
def calculate_profit_loss(address):
    transactions = check_wallet_transactions(address)  # دریافت تراکنش‌ها
    holdings = {}  # دیکشنری برای ذخیره مقدار هر ارز

    for tx in transactions:
        token_symbol = tx['tokenSymbol']
        token_value = int(tx['value']) / (10 ** int(tx['tokenDecimal']))  # مقدار ارز
        tx_type = "دریافت" if tx['to'].lower() == address.lower() else "ارسال"

        if tx_type == "دریافت":
            if token_symbol in holdings:
                holdings[token_symbol] += token_value
            else:
                holdings[token_symbol] = token_value
        elif tx_type == "ارسال":
            if token_symbol in holdings:
                holdings[token_symbol] -= token_value

    # محاسبه سود و ضرر
    profit_loss_report = []
    for symbol, amount in holdings.items():
        if amount <= 0:
            continue  # اگر مقدار صفر یا منفی است، از گزارش حذف شود

        current_price = get_token_to_usd_rate(symbol)  # قیمت فعلی ارز
        if current_price is None:
            profit_loss_report.append(f"• {symbol}: {amount:.4f} (قیمت فعلی نامشخص)")
            continue

        # قیمت خرید (میانگین قیمت خرید)
        total_cost = 0
        total_tokens = 0
        for tx in transactions:
            if tx['tokenSymbol'] == symbol and tx['to'].lower() == address.lower():
                tx_value = int(tx['value']) / (10 ** int(tx['tokenDecimal']))
                tx_price = get_token_to_usd_rate(symbol)  # قیمت خرید (فرضی)
                if tx_price is not None:
                    total_cost += tx_value * tx_price
                    total_tokens += tx_value

        if total_tokens == 0:
            average_cost = 0
        else:
            average_cost = total_cost / total_tokens

        # محاسبه سود یا ضرر
        profit_loss = (current_price - average_cost) * amount
        profit_loss_report.append(
            f"• {symbol}: {amount:.4f} (سود/ضرر: ${profit_loss:,.2f})"
        )

    return profit_loss_report

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

# فرمان /holdings
async def show_holdings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 1:
        await update.message.reply_text("❌ لطفاً آدرس کیف پول را وارد کنید. مثال:\n/holdings 0x123456789")
        return

    address = context.args[0]
    if not is_valid_address(address):
        await update.message.reply_text("❌ آدرس کیف پول معتبر نیست. لطفاً دوباره بررسی کنید.")
        return

    profit_loss_report = calculate_profit_loss(address)
    if not profit_loss_report:
        await update.message.reply_text("❌ هیچ ارزی در این کیف پول یافت نشد.")
        return

    response = "📊 گزارش ارزهای خریداری شده:\n"
    response += "\n".join(profit_loss_report)
    await update.message.reply_text(response)

# مانیتورینگ خودکار ولت‌ها
async def monitor_wallets(context: ContextTypes.DEFAULT_TYPE) -> None:
    global CHAT_ID
    if not CHAT_ID:
        logger.warning("شناسه چت تنظیم نشده است. پیام‌ها ارسال نمی‌شود.")
        return

    wallets = list_wallets_from_db()

    for address, name, last_tx in wallets:
        transactions = check_wallet_transactions(address)
        if not transactions:
            continue

        # بررسی تراکنش جدید
        latest_tx = transactions[0]  # جدیدترین تراکنش
        if latest_tx['hash'] != last_tx:
            update_last_tx(address, latest_tx['hash'])  # به‌روزرسانی دیتابیس

            tx_time = format_timestamp(latest_tx['timeStamp'])
            token_symbol = latest_tx['tokenSymbol']
            token_value = int(latest_tx['value']) / (10 ** int(latest_tx['tokenDecimal']))  # تبدیل مقدار
            rate = get_token_to_usd_rate(token_symbol)

            if rate is not None:
                token_value_usd = token_value * rate
                token_value_usd_formatted = f"${token_value_usd:,.2f}"
            else:
                token_value_usd_formatted = "نامشخص"

            tx_type = "دریافت" if latest_tx['to'].lower() == address.lower() else "ارسال"

            message = (
                f"🔔 تراکنش جدید برای {name}:\n"
                f"• نوع: {tx_type}\n"
                f"• مقدار: {token_value} {token_symbol}\n"
                f"• معادل: {token_value_usd_formatted}\n"
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
    application.add_handler(CommandHandler("holdings", show_holdings))  # دستور جدید

    # ثبت وظیفه مانیتورینگ خودکار
    application.job_queue.run_repeating(monitor_wallets, interval=30)

    # ثبت مدیریت خطا
    application.add_error_handler(error_handler)

    # اجرای ربات
    application.run_polling()

if __name__ == "__main__":
    main()