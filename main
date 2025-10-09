import asyncio
import logging
from datetime import datetime, timedelta
import json
import sqlite3
from typing import Dict, List
import aiohttp
import hmac
import hashlib
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class OKXTracker:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, telegram_token: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.telegram_token = telegram_token
        self.base_url = "https://www.okx.com"
        
        # Khởi tạo database
        self.init_database()
        
        # Thông tin bot trading (cấu hình theo bot của bạn)
        self.trading_bots = {
            "Bot-DCA-BTC": {"symbol": "BTC-USDT", "strategy": "DCA"},
            "Bot-Grid-ETH": {"symbol": "ETH-USDT", "strategy": "Grid"},
            "Bot-Martingale-BNB": {"symbol": "BNB-USDT", "strategy": "Martingale"}
        }

    def init_database(self):
        """Khởi tạo database SQLite để lưu lịch sử"""
        conn = sqlite3.connect('okx_trading.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_profits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                bot_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                profit_usdt REAL NOT NULL,
                profit_percentage REAL NOT NULL,
                trades_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                bot_name TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_profit REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0
            )
        ''')
        
        conn.commit()
        conn.close()

    def generate_signature(self, timestamp: str, method: str, request_path: str, body: str = ''):
        """Tạo chữ ký cho OKX API"""
        message = timestamp + method + request_path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    async def make_okx_request(self, method: str, endpoint: str, params: Dict = None):
        """Gửi request đến OKX API"""
        timestamp = datetime.utcnow().isoformat()[:-3] + 'Z'
        request_path = endpoint
        
        if params and method == 'GET':
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            request_path += f"?{query_string}"
        
        signature = self.generate_signature(timestamp, method, request_path)
        
        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
        
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}{request_path}"
            async with session.request(method, url, headers=headers) as response:
                return await response.json()

    async def get_account_balance(self):
        """Lấy số dư tài khoản"""
        try:
            response = await self.make_okx_request('GET', '/api/v5/account/balance')
            if response.get('code') == '0':
                return response['data'][0]['details']
            return None
        except Exception as e:
            logger.error(f"Lỗi khi lấy số dư: {e}")
            return None

    async def get_trading_history(self, symbol: str, days: int = 1):
        """Lấy lịch sử giao dịch"""
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)
            
            params = {
                'instId': symbol,
                'after': str(int(start_time.timestamp() * 1000)),
                'before': str(int(end_time.timestamp() * 1000))
            }
            
            response = await self.make_okx_request('GET', '/api/v5/trade/fills-history', params)
            if response.get('code') == '0':
                return response['data']
            return []
        except Exception as e:
            logger.error(f"Lỗi khi lấy lịch sử giao dịch: {e}")
            return []

    def save_daily_profit(self, bot_name: str, symbol: str, profit_usdt: float, profit_percentage: float, trades_count: int):
        """Lưu lợi nhuận hàng ngày vào database"""
        conn = sqlite3.connect('okx_trading.db')
        cursor = conn.cursor()
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        cursor.execute('''
            INSERT OR REPLACE INTO daily_profits 
            (date, bot_name, symbol, profit_usdt, profit_percentage, trades_count)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (today, bot_name, symbol, profit_usdt, profit_percentage, trades_count))
        
        conn.commit()
        conn.close()

    def get_daily_summary(self, date: str = None):
        """Lấy tổng kết lợi nhuận theo ngày"""
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
            
        conn = sqlite3.connect('okx_trading.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT bot_name, symbol, profit_usdt, profit_percentage, trades_count
            FROM daily_profits 
            WHERE date = ?
        ''', (date,))
        
        results = cursor.fetchall()
        conn.close()
        
        return results

    def get_monthly_summary(self, month: str = None):
        """Lấy tổng kết lợi nhuận theo tháng"""
        if not month:
            month = datetime.now().strftime('%Y-%m')
            
        conn = sqlite3.connect('okx_trading.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT bot_name, 
                   SUM(profit_usdt) as total_profit,
                   AVG(profit_percentage) as avg_percentage,
                   SUM(trades_count) as total_trades,
                   COUNT(*) as active_days
            FROM daily_profits 
            WHERE date LIKE ? || '%'
            GROUP BY bot_name
        ''', (month,))
        
        results = cursor.fetchall()
        conn.close()
        
        return results

# Bot Telegram Commands
class TelegramBot:
    def __init__(self, okx_tracker: OKXTracker, allowed_users: List[int] = None):
        self.okx = okx_tracker
        self.allowed_users = allowed_users or []

    def check_user_permission(self, user_id: int) -> bool:
        """Kiểm tra quyền truy cập"""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /start"""
        if not self.check_user_permission(update.effective_user.id):
            await update.message.reply_text("❌ Bạn không có quyền sử dụng bot này!")
            return

        keyboard = [
            [InlineKeyboardButton("📊 Lợi nhuận hôm nay", callback_data="today_profit")],
            [InlineKeyboardButton("📈 Báo cáo tháng", callback_data="monthly_report")],
            [InlineKeyboardButton("💰 Số dư tài khoản", callback_data="account_balance")],
            [InlineKeyboardButton("🔄 Trạng thái Bot", callback_data="bot_status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = """
🤖 **OKX Trading Bot Monitor**

Chào mừng bạn đến với hệ thống theo dõi Trading Bot!

📋 **Chức năng:**
• Theo dõi lợi nhuận hàng ngày
• Báo cáo chi tiết hàng tháng  
• Kiểm tra số dư tài khoản
• Trạng thái hoạt động các bot

📱 **Lệnh nhanh:**
/today - Lợi nhuận hôm nay
/month - Báo cáo tháng
/balance - Số dư tài khoản
/status - Trạng thái bot
        """
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def today_profit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /today - Lợi nhuận hôm nay"""
        if not self.check_user_permission(update.effective_user.id):
            return

        daily_data = self.okx.get_daily_summary()
        
        if not daily_data:
            await update.message.reply_text("📊 Chưa có dữ liệu giao dịch hôm nay!")
            return

        text = "📊 **LỢI NHUẬN HÔM NAY**\n"
        text += f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n"
        
        total_profit = 0
        total_trades = 0
        
        for bot_name, symbol, profit_usdt, profit_percentage, trades_count in daily_data:
            total_profit += profit_usdt
            total_trades += trades_count
            
            profit_emoji = "🟢" if profit_usdt > 0 else "🔴" if profit_usdt < 0 else "⚪"
            
            text += f"{profit_emoji} **{bot_name}**\n"
            text += f"   • Cặp: {symbol}\n"
            text += f"   • Lợi nhuận: ${profit_usdt:.2f} ({profit_percentage:.2f}%)\n"
            text += f"   • Giao dịch: {trades_count}\n\n"
        
        text += "─" * 30 + "\n"
        text += f"💰 **Tổng lợi nhuận: ${total_profit:.2f}**\n"
        text += f"📊 Tổng giao dịch: {total_trades}"
        
        await update.message.reply_text(text, parse_mode='Markdown')

    async def monthly_report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /month - Báo cáo tháng"""
        if not self.check_user_permission(update.effective_user.id):
            return

        monthly_data = self.okx.get_monthly_summary()
        
        if not monthly_data:
            await update.message.reply_text("📈 Chưa có dữ liệu giao dịch tháng này!")
            return

        current_month = datetime.now().strftime('%m/%Y')
        text = f"📈 **BÁO CÁO THÁNG {current_month}**\n\n"
        
        total_profit = 0
        total_trades = 0
        
        for bot_name, profit, avg_percentage, trades, active_days in monthly_data:
            total_profit += profit
            total_trades += trades
            
            profit_emoji = "🟢" if profit > 0 else "🔴" if profit < 0 else "⚪"
            
            text += f"{profit_emoji} **{bot_name}**\n"
            text += f"   • Lợi nhuận: ${profit:.2f}\n"
            text += f"   • % Trung bình: {avg_percentage:.2f}%\n"
            text += f"   • Tổng giao dịch: {trades}\n"
            text += f"   • Ngày hoạt động: {active_days}\n\n"
        
        text += "─" * 30 + "\n"
        text += f"💰 **Tổng lợi nhuận tháng: ${total_profit:.2f}**\n"
        text += f"📊 Tổng giao dịch: {total_trades}"
        
        await update.message.reply_text(text, parse_mode='Markdown')

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /balance - Số dư tài khoản"""
        if not self.check_user_permission(update.effective_user.id):
            return

        await update.message.reply_text("⏳ Đang lấy thông tin số dư...")
        
        balance_data = await self.okx.get_account_balance()
        
        if not balance_data:
            await update.message.reply_text("❌ Không thể lấy thông tin số dư!")
            return

        text = "💰 **SỐ DƯ TÀI KHOẢN**\n\n"
        
        total_usd = 0
        for asset in balance_data:
            if float(asset['cashBal']) > 0:
                balance = float(asset['cashBal'])
                currency = asset['ccy']
                
                text += f"• {currency}: {balance:.4f}\n"
                
                if currency == 'USDT':
                    total_usd += balance
        
        text += f"\n💵 **Tổng giá trị: ~${total_usd:.2f}**"
        
        await update.message.reply_text(text, parse_mode='Markdown')

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Xử lý callback từ inline buttons"""
        query = update.callback_query
        await query.answer()

        if query.data == "today_profit":
            await self.today_profit_command(update, context)
        elif query.data == "monthly_report":
            await self.monthly_report_command(update, context)
        elif query.data == "account_balance":
            await self.balance_command(update, context)
        elif query.data == "bot_status":
            await query.edit_message_text("🔄 Chức năng đang phát triển...")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Xử lý lỗi"""
        logger.error(f"Update {update} caused error {context.error}")

# Hàm chính để chạy bot
async def main():
    # ========== CẤU HÌNH - THAY ĐỔI THEO THÔNG TIN CỦA BẠN ==========
    OKX_API_KEY = "your_okx_api_key"
    OKX_SECRET_KEY = "your_okx_secret_key" 
    OKX_PASSPHRASE = "your_okx_passphrase"
    TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"
    
    # Danh sách User ID được phép sử dụng (để trống nếu cho phép tất cả)
    ALLOWED_USERS = [123456789]  # Thay bằng Telegram User ID của bạn
    # ==============================================================
    
    # Khởi tạo OKX Tracker
    okx_tracker = OKXTracker(OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, TELEGRAM_BOT_TOKEN)
    
    # Khởi tạo Telegram Bot
    telegram_bot = TelegramBot(okx_tracker, ALLOWED_USERS)
    
    # Tạo Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Thêm handlers
    application.add_handler(CommandHandler("start", telegram_bot.start_command))
    application.add_handler(CommandHandler("today", telegram_bot.today_profit_command))
    application.add_handler(CommandHandler("month", telegram_bot.monthly_report_command))
    application.add_handler(CommandHandler("balance", telegram_bot.balance_command))
    application.add_handler(CallbackQueryHandler(telegram_bot.button_callback))
    application.add_error_handler(telegram_bot.error_handler)
    
    # Chạy bot
    print("🤖 Bot đang khởi động...")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
