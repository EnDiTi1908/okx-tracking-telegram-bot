import asyncio
import logging
import os
from datetime import datetime, timedelta
import json
import aiohttp
import hmac
import hashlib
import base64
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class OKXTracker:
    def __init__(self, api_key: str, secret_key: str, passphrase: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = "https://www.okx.com"
        
        # Lưu dữ liệu trong memory thay vì database (Railway friendly)
        self.daily_data = {}
        self.monthly_data = {}
        
        # Danh sách bot trading - CẬP NHẬT THEO BOT CỦA BẠN
        self.trading_bots = {
            "Bot-DCA-BTC": {"symbol": "BTC-USDT", "strategy": "DCA"},
            "Bot-Grid-ETH": {"symbol": "ETH-USDT", "strategy": "Grid"},
            "Bot-Martingale-BNB": {"symbol": "BNB-USDT", "strategy": "Martingale"}
        }
        
        logger.info("OKX Tracker initialized successfully")

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
        """Gửi request đến OKX API với error handling"""
        try:
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
                async with session.request(method, url, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"OKX API Error: {response.status}")
                        return None
                        
        except asyncio.TimeoutError:
            logger.error("OKX API timeout")
            return None
        except Exception as e:
            logger.error(f"OKX API Error: {e}")
            return None

    async def get_account_balance(self):
        """Lấy số dư tài khoản"""
        response = await self.make_okx_request('GET', '/api/v5/account/balance')
        if response and response.get('code') == '0':
            return response['data'][0]['details']
        return None

    async def get_trading_history(self, symbol: str, days: int = 1):
        """Lấy lịch sử giao dịch"""
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        params = {
            'instId': symbol,
            'after': str(int(start_time.timestamp() * 1000)),
            'before': str(int(end_time.timestamp() * 1000))
        }
        
        response = await self.make_okx_request('GET', '/api/v5/trade/fills-history', params)
        if response and response.get('code') == '0':
            return response['data']
        return []

    async def calculate_daily_profit(self):
        """Tính toán lợi nhuận hàng ngày cho tất cả bot"""
        today = datetime.now().strftime('%Y-%m-%d')
        total_profit = 0
        bot_profits = {}
        
        for bot_name, bot_info in self.trading_bots.items():
            symbol = bot_info['symbol']
            trades = await self.get_trading_history(symbol, 1)
            
            if trades:
                profit = sum(float(trade.get('pnl', 0)) for trade in trades)
                trades_count = len(trades)
                
                # Tính % lợi nhuận (giả định - cần dữ liệu thực tế)
                profit_percentage = (profit / 1000) * 100 if profit != 0 else 0
                
                bot_profits[bot_name] = {
                    'symbol': symbol,
                    'profit_usdt': profit,
                    'profit_percentage': profit_percentage,
                    'trades_count': trades_count
                }
                
                total_profit += profit
        
        self.daily_data[today] = bot_profits
        logger.info(f"Daily profit calculated: ${total_profit:.2f}")
        return bot_profits

    def get_daily_summary(self, date: str = None):
        """Lấy tổng kết hàng ngày"""
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        return self.daily_data.get(date, {})

    def get_monthly_summary(self):
        """Tính tổng kết tháng từ dữ liệu hàng ngày"""
        current_month = datetime.now().strftime('%Y-%m')
        monthly_summary = {}
        
        for date, daily_data in self.daily_data.items():
            if date.startswith(current_month):
                for bot_name, bot_data in daily_data.items():
                    if bot_name not in monthly_summary:
                        monthly_summary[bot_name] = {
                            'total_profit': 0,
                            'total_trades': 0,
                            'active_days': 0,
                            'avg_percentage': 0
                        }
                    
                    monthly_summary[bot_name]['total_profit'] += bot_data['profit_usdt']
                    monthly_summary[bot_name]['total_trades'] += bot_data['trades_count']
                    monthly_summary[bot_name]['active_days'] += 1
                    monthly_summary[bot_name]['avg_percentage'] = (
                        monthly_summary[bot_name]['total_profit'] / monthly_summary[bot_name]['active_days']
                    ) if monthly_summary[bot_name]['active_days'] > 0 else 0
        
        return monthly_summary

class TelegramBot:
    def __init__(self, okx_tracker: OKXTracker, allowed_users: List[int] = None):
        self.okx = okx_tracker
        self.allowed_users = allowed_users or []
        logger.info(f"Telegram bot initialized for {len(self.allowed_users)} users")

    def check_user_permission(self, user_id: int) -> bool:
        """Kiểm tra quyền truy cập"""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /start"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"User {username} ({user_id}) started bot")
        
        if not self.check_user_permission(user_id):
            await update.message.reply_text("❌ Bạn không có quyền sử dụng bot này!")
            logger.warning(f"Unauthorized access attempt from {user_id}")
            return

        keyboard = [
            [InlineKeyboardButton("📊 Lợi nhuận hôm nay", callback_data="today_profit")],
            [InlineKeyboardButton("📈 Báo cáo tháng", callback_data="monthly_report")],
            [InlineKeyboardButton("💰 Số dư tài khoản", callback_data="account_balance")],
            [InlineKeyboardButton("🔄 Refresh Data", callback_data="refresh_data")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"""
🤖 **OKX Trading Bot Monitor**

Xin chào {update.effective_user.first_name}!

📋 **Chức năng:**
• Theo dõi lợi nhuận hàng ngày
• Báo cáo chi tiết hàng tháng  
• Kiểm tra số dư tài khoản
• Cập nhật dữ liệu real-time

📱 **Lệnh nhanh:**
/today - Lợi nhuận hôm nay
/month - Báo cáo tháng
/balance - Số dư tài khoản
/refresh - Cập nhật dữ liệu

🌐 **Status:** 🟢 Online - Railway Hosted
        """
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def today_profit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /today - Lợi nhuận hôm nay"""
        if not self.check_user_permission(update.effective_user.id):
            return

        # Refresh data trước
        await self.okx.calculate_daily_profit()
        daily_data = self.okx.get_daily_summary()
        
        if not daily_data:
            await update.message.reply_text("📊 Đang thu thập dữ liệu giao dịch...")
            return

        text = "📊 **LỢI NHUẬN HÔM NAY**\n"
        text += f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n"
        
        total_profit = 0
        total_trades = 0
        
        for bot_name, bot_data in daily_data.items():
            profit_usdt = bot_data['profit_usdt']
            profit_percentage = bot_data['profit_percentage']
            trades_count = bot_data['trades_count']
            symbol = bot_data['symbol']
            
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
            await update.message.reply_text("📈 Chưa có dữ liệu tháng này! Hãy dùng /refresh để thu thập dữ liệu.")
            return

        current_month = datetime.now().strftime('%m/%Y')
        text = f"📈 **BÁO CÁO THÁNG {current_month}**\n\n"
        
        total_profit = 0
        total_trades = 0
        
        for bot_name, data in monthly_data.items():
            profit = data['total_profit']
            trades = data['total_trades']
            active_days = data['active_days']
            avg_percentage = data['avg_percentage']
            
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
            await update.message.reply_text("❌ Không thể lấy thông tin số dư! Kiểm tra API key.")
            return

        text = "💰 **SỐ DƯ TÀI KHOẢN OKX**\n\n"
        
        total_usd = 0
        active_balances = 0
        
        for asset in balance_data:
            balance = float(asset.get('cashBal', 0))
            if balance > 0.01:  # Chỉ hiển thị balance > 0.01
                currency = asset['ccy']
                text += f"• {currency}: {balance:.4f}\n"
                active_balances += 1
                
                if currency == 'USDT':
                    total_usd += balance
        
        if active_balances == 0:
            text += "Không có số dư đáng kể\n"
        
        text += f"\n💵 **Tổng USDT: ${total_usd:.2f}**\n"
        text += f"📊 Cập nhật: {datetime.now().strftime('%H:%M:%S')}"
        
        await update.message.reply_text(text, parse_mode='Markdown')

    async def refresh_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Lệnh /refresh - Cập nhật dữ liệu"""
        if not self.check_user_permission(update.effective_user.id):
            return

        await update.message.reply_text("🔄 Đang cập nhật dữ liệu...")
        
        await self.okx.calculate_daily_profit()
        
        await update.message.reply_text("✅ Dữ liệu đã được cập nhật! Dùng /today để xem kết quả.")

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
        elif query.data == "refresh_data":
            await self.refresh_command(update, context)

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Xử lý lỗi"""
        logger.error(f"Update {update} caused error {context.error}")
        
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Đã xảy ra lỗi! Vui lòng thử lại sau."
            )

def main():
    """Hàm chính chạy bot - SỬA ĐỂ TƯƠNG THÍCH RAILWAY"""
    
    # ========== CẤU HÌNH RAILWAY ==========
    OKX_API_KEY = os.getenv('OKX_API_KEY')
    OKX_SECRET_KEY = os.getenv('OKX_SECRET_KEY')
    OKX_PASSPHRASE = os.getenv('OKX_PASSPHRASE')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    # Parse allowed users từ environment variable
    allowed_users_str = os.getenv('ALLOWED_USERS', '')
    ALLOWED_USERS = []
    if allowed_users_str:
        try:
            ALLOWED_USERS = [int(x.strip()) for x in allowed_users_str.split(',') if x.strip()]
        except ValueError:
            logger.error("Invalid ALLOWED_USERS format")
    
    # Kiểm tra cấu hình
    if not all([OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, TELEGRAM_BOT_TOKEN]):
        logger.error("Missing required environment variables!")
        return
    
    logger.info("Starting OKX Telegram Bot...")
    logger.info(f"Allowed users: {ALLOWED_USERS}")
    
    try:
        # Khởi tạo components
        okx_tracker = OKXTracker(OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE)
        telegram_bot = TelegramBot(okx_tracker, ALLOWED_USERS)
        
        # Tạo Telegram Application
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Thêm handlers
        application.add_handler(CommandHandler("start", telegram_bot.start_command))
        application.add_handler(CommandHandler("today", telegram_bot.today_profit_command))
        application.add_handler(CommandHandler("month", telegram_bot.monthly_report_command))
        application.add_handler(CommandHandler("balance", telegram_bot.balance_command))
        application.add_handler(CommandHandler("refresh", telegram_bot.refresh_command))
        application.add_handler(CallbackQueryHandler(telegram_bot.button_callback))
        application.add_error_handler(telegram_bot.error_handler)
        
        # Khởi chạy bot - SỬA ĐỂ TRÁNH EVENT LOOP CONFLICT
        logger.info("🤖 Bot started successfully on Railway!")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
