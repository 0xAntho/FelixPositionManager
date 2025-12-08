import os
import json
from datetime import datetime
from web3 import Web3
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# Configuration
RPC_URL = os.getenv("RPC_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHECK_INTERVAL = 1800  # 30 minutes in seconds

if not RPC_URL or not TELEGRAM_BOT_TOKEN:
    raise ValueError("⚠️ Missing RPC_URL or TELEGRAM_BOT_TOKEN in .env")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise ConnectionError("❌ Unable to connect to RPC")

# User data storage
user_data = {}

# All available markets
ALL_MARKETS = {
    "lending": [
        {"name": "USDe", "address": "0x835febf893c6dddee5cf762b0f8e31c5b06938ab", "abi_file": "USDeLending.json", "asset_decimals": 6},
        {"name": "USDT0", "address": "0xfc5126377f0efc0041c0969ef9ba903ce67d151e", "abi_file": "USDT0Lending.json", "asset_decimals": 6},
        {"name": "USDT0 (Frontier)", "address": "0x9896a8605763106e57A51aa0a97Fe8099E806bb3", "abi_file": "USDT0FrontierLending.json", "asset_decimals": 6},
        {"name": "USDhl", "address": "0x9c59a9389D8f72DE2CdAf1126F36EA4790E2275e", "abi_file": "USDhlLending.json", "asset_decimals": 6},
        {"name": "USDhl (Frontier)", "address": "0x66c71204B70aE27BE6dC3eb41F9aF5868E68fDb6", "abi_file": "USDhlFrontierLending.json", "asset_decimals": 6},
        {"name": "HYPE", "address": "0x2900ABd73631b2f60747e687095537B673c06A76", "abi_file": "HYPELending.json", "asset_decimals": 18},
    ],
    "borrow": [
        {"name": "WHLP/USDT0", "morpho_address": "0x68e37dE8d93d3496ae143F2E900490f6280C57cD", "market_id": "0xd4fd53f612eaf411a1acea053cfa28cbfeea683273c4133bf115b47a20130305", "abi_file": "MorphoBlue.json", "collateral_decimals": 6, "borrow_decimals": 6, "borrow_shares_decimals": 18}
    ]
}


def load_abi(filename):
    """Load ABI from JSON file in abi/ directory"""
    filepath = f"abi/{filename}"
    with open(filepath, "r", encoding='utf-8-sig') as f:
        return json.loads(f.read().strip())


def get_lending_position(contract, user_addr, asset_decimals=18):
    """Retrieve lending position"""
    position = {}
    try:
        balance_shares_wei = contract.functions.balanceOf(user_addr).call()
        shares = balance_shares_wei / 1e18
        position["shares_balance"] = shares

        if shares == 0:
            position["assets_value"] = 0
            return position

        total_assets_wei = contract.functions.totalAssets().call()
        total_supply_wei = contract.functions.totalSupply().call()
        asset_divisor = 10 ** asset_decimals

        position["vault_total_assets"] = total_assets_wei / asset_divisor
        position["vault_total_shares"] = total_supply_wei / 1e18

        try:
            assets_wei = contract.functions.convertToAssets(balance_shares_wei).call()
            position["assets_value"] = assets_wei / asset_divisor
            return position
        except:
            pass

        if total_supply_wei > 0:
            assets_wei = (balance_shares_wei * total_assets_wei) // total_supply_wei
            position["assets_value"] = assets_wei / asset_divisor
        else:
            position["assets_value"] = 0

        return position
    except Exception as e:
        return {"error": str(e)}


def get_borrow_position(contract, user_addr, market_id, collateral_decimals=18, borrow_decimals=6, borrow_shares_decimals=18):
    """Retrieve borrow position"""
    position = {}
    try:
        market_id_bytes = bytes.fromhex(market_id.replace('0x', '')) if isinstance(market_id, str) else market_id

        market_data = contract.functions.market(market_id_bytes).call()
        total_borrow_assets = market_data[2]
        total_borrow_shares = market_data[3]

        user_position = contract.functions.position(market_id_bytes, user_addr).call()
        borrow_shares_wei = user_position[1]
        collateral_wei = user_position[2]

        borrow_divisor = 10 ** borrow_decimals
        shares_divisor = 10 ** borrow_shares_decimals
        collateral_divisor = 10 ** collateral_decimals

        position["borrow_shares"] = borrow_shares_wei / shares_divisor
        position["collateral"] = collateral_wei / collateral_divisor

        if total_borrow_shares > 0 and borrow_shares_wei > 0:
            borrowed_amount_wei = (borrow_shares_wei * total_borrow_assets) // total_borrow_shares
            position["borrowed"] = borrowed_amount_wei / borrow_divisor
        else:
            position["borrowed"] = 0

        if position.get("borrowed", 0) > 0 and position["collateral"] > 0:
            position["health_factor"] = position["collateral"] / position["borrowed"]
        elif position.get("borrowed", 0) == 0:
            position["health_factor"] = float('inf')

        return position
    except Exception as e:
        return {"error": str(e)}


def fetch_positions(address, chat_id=None):
    """Fetch all positions"""
    try:
        checksum_addr = w3.to_checksum_address(address)
    except:
        return {"error": "Invalid address format"}

    results = {"lending": [], "borrow": [], "timestamp": datetime.now()}

    for market in ALL_MARKETS["lending"]:
        try:
            abi = load_abi(market["abi_file"])
            contract = w3.eth.contract(address=market["address"], abi=abi)
            data = get_lending_position(contract, checksum_addr, market.get("asset_decimals", 18))
            results["lending"].append({"name": market["name"], "address": market["address"], "data": data})
        except Exception as e:
            results["lending"].append({"name": market["name"], "address": market["address"], "data": {"error": str(e)}})

    for market in ALL_MARKETS["borrow"]:
        try:
            abi = load_abi(market["abi_file"])
            contract = w3.eth.contract(address=market["morpho_address"], abi=abi)
            data = get_borrow_position(contract, checksum_addr, market["market_id"], market.get("collateral_decimals", 18), market.get("borrow_decimals", 6), market.get("borrow_shares_decimals", 18))
            results["borrow"].append({"name": market["name"], "market_id": market["market_id"], "data": data})
        except Exception as e:
            results["borrow"].append({"name": market["name"], "market_id": market["market_id"], "data": {"error": str(e)}})

    return results


def format_position_message(address, positions):
    """Format positions into Telegram message"""
    if "error" in positions:
        return f"❌ Error: {positions['error']}"

    msg = f"📊 *Felix Protocol Positions*\n👤 `{address[:6]}...{address[-4:]}`\n🕐 {positions['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    total_lending_value = 0
    total_borrow_value = 0

    for market in positions["lending"]:
        msg += f"━━━━━━━━━━━━━━━━━━━━\n*📈 {market['name']}*\n"
        if "error" in market["data"]:
            msg += f"❌ Error: {market['data']['error'][:100].replace('_', '\\_')}\n"
        elif market["data"].get("shares_balance", 0) > 0:
            value = market["data"].get("assets_value", 0)
            total_lending_value += value
            msg += f"💰 *Assets: {value:,.4f}*\n📊 Shares: {market['data']['shares_balance']:,.4f}\n"
        else:
            msg += f"ℹ️ No lending position\n"

    for market in positions.get("borrow", []):
        msg += f"━━━━━━━━━━━━━━━━━━━━\n*📉 {market['name']}*\n"
        if "error" in market["data"]:
            msg += f"❌ Error: {market['data']['error'][:100].replace('_', '\\_')}\n"
        elif market["data"].get("borrowed", 0) > 0 or market["data"].get("collateral", 0) > 0:
            borrowed = market["data"].get("borrowed", 0)
            collateral = market["data"].get("collateral", 0)
            total_borrow_value += borrowed
            msg += f"🔴 *Borrowed: {borrowed:,.4f}*\n🔒 Collateral: {collateral:,.4f}\n"
            if "health_factor" in market["data"]:
                hf = market["data"]["health_factor"]
                if hf == float('inf'):
                    msg += f"✅ Health Factor: Infinity\n"
                else:
                    emoji = "✅" if hf > 1.5 else "⚠️" if hf > 1.1 else "🔴"
                    msg += f"{emoji} Health Factor: {hf:.4f}\n"
        else:
            msg += f"ℹ️ No borrow position\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n💚 *Total Lending: {total_lending_value:,.4f}*\n🔴 *Total Borrowed: {total_borrow_value:,.4f}*\n💎 *Net Value: {total_lending_value - total_borrow_value:,.4f}*\n"
    return msg


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    chat_id = update.effective_chat.id
    if chat_id not in user_data:
        user_data[chat_id] = {"addresses": [], "active_address": None, "monitoring": False}

    await update.message.reply_text(
        "👋 *Welcome to Felix Position Monitor Bot!*\n\n"
        "📌 *Commands:*\n"
        "/add <address> - Add wallet\n"
        "/list - Show addresses\n"
        "/check - Check positions\n"
        "/monitor - Start/Stop monitoring\n"
        "/markets - View markets\n"
        "/help - Show help",
        parse_mode='Markdown'
    )


async def add_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add address"""
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("❌ Usage: `/add 0x1234...5678`", parse_mode='Markdown')
        return

    address = ''.join(context.args).lower().strip()
    try:
        w3.to_checksum_address(address)
    except:
        await update.message.reply_text("❌ Invalid address")
        return

    if chat_id not in user_data:
        user_data[chat_id] = {"addresses": [], "active_address": None, "monitoring": False}

    if address in user_data[chat_id]["addresses"]:
        await update.message.reply_text("⚠️ Already added!")
        return

    user_data[chat_id]["addresses"].append(address)
    if not user_data[chat_id]["active_address"]:
        user_data[chat_id]["active_address"] = address

    await update.message.reply_text(f"✅ Added: `{address[:6]}...{address[-4:]}`", parse_mode='Markdown')


async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List addresses"""
    chat_id = update.effective_chat.id
    if chat_id not in user_data or not user_data[chat_id]["addresses"]:
        await update.message.reply_text("❌ No addresses")
        return

    active = user_data[chat_id]["active_address"]
    msg = "📋 *Your Addresses:*\n\n"
    for i, addr in enumerate(user_data[chat_id]["addresses"], 1):
        marker = "👉" if addr == active else "  "
        msg += f"{marker} {i}. `{addr[:6]}...{addr[-4:]}`\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def check_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check position"""
    chat_id = update.effective_chat.id
    if chat_id not in user_data or not user_data[chat_id]["active_address"]:
        await update.message.reply_text("❌ No active address")
        return

    msg = await update.message.reply_text("🔄 Fetching...")
    positions = fetch_positions(user_data[chat_id]["active_address"], chat_id)
    formatted = format_position_message(user_data[chat_id]["active_address"], positions)
    await msg.edit_text(formatted, parse_mode='Markdown')


async def manage_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show markets"""
    msg = (
        f"📊 *Market Selection*\n\n"
        f"Select which markets you want to track.\n"
        f"By default, all markets are shown.\n\n"
        f"📈 Lending markets: All\n"
        f"📉 Borrow markets: All\n\n"
        f"Choose a category:"
    )

    keyboard = [
        [InlineKeyboardButton("📈 Lending Markets", callback_data="show_lending")],
        [InlineKeyboardButton("📉 Borrow Markets", callback_data="show_borrow")],
    ]

    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


async def market_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle market callbacks"""
    query = update.callback_query
    await query.answer()

    if query.data == "show_lending":
        msg = "📈 *Lending Markets:*\n\n"
        for i, m in enumerate(ALL_MARKETS["lending"], 1):
            msg += f"{i}. *{m['name']}*\n   `{m['address'][:10]}...`\n\n"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="back_markets")]]
        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "show_borrow":
        msg = "📉 *Borrow Markets:*\n\n"
        for i, m in enumerate(ALL_MARKETS["borrow"], 1):
            msg += f"{i}. *{m['name']}*\n\n"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="back_markets")]]
        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "back_markets":
        msg = (
            f"📊 *Market Selection*\n\n"
            f"Select which markets you want to track.\n"
            f"By default, all markets are shown.\n\n"
            f"📈 Lending markets: All\n"
            f"📉 Borrow markets: All\n\n"
            f"Choose a category:"
        )
        keyboard = [
            [InlineKeyboardButton("📈 Lending Markets", callback_data="show_lending")],
            [InlineKeyboardButton("📉 Borrow Markets", callback_data="show_borrow")],
        ]
        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


async def toggle_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle monitoring"""
    chat_id = update.effective_chat.id
    if chat_id not in user_data or not user_data[chat_id]["active_address"]:
        await update.message.reply_text("❌ No active address")
        return

    is_monitoring = user_data[chat_id].get("monitoring", False)
    if is_monitoring:
        user_data[chat_id]["monitoring"] = False
        jobs = context.job_queue.get_jobs_by_name(f"monitor_{chat_id}")
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("⏸️ Stopped")
    else:
        user_data[chat_id]["monitoring"] = True
        context.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=10, chat_id=chat_id, name=f"monitor_{chat_id}", data=user_data[chat_id]["active_address"])
        await update.message.reply_text(f"✅ Monitoring started (30min interval)", parse_mode='Markdown')


async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    """Monitor job"""
    chat_id = context.job.chat_id
    address = context.job.data
    if not user_data.get(chat_id, {}).get("monitoring", False):
        return
    positions = fetch_positions(address, chat_id)
    formatted = format_position_message(address, positions)
    await context.bot.send_message(chat_id=chat_id, text=formatted, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help"""
    await update.message.reply_text(
        "📖 *Help*\n\n"
        "/add <address> - Add wallet\n"
        "/list - List addresses\n"
        "/check - Check positions\n"
        "/monitor - Toggle monitoring\n"
        "/markets - View markets",
        parse_mode='Markdown'
    )


def main():
    """Start bot"""
    print("🤖 Starting Felix Telegram Bot...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_address))
    app.add_handler(CommandHandler("list", list_addresses))
    app.add_handler(CommandHandler("check", check_position))
    app.add_handler(CommandHandler("monitor", toggle_monitoring))
    app.add_handler(CommandHandler("markets", manage_markets))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(market_callback, pattern="^(show_lending|show_borrow|back_markets)"))

    print("✅ Bot started!")
    app.run_polling()


if __name__ == "__main__":
    main()