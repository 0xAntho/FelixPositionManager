import os
import json
import asyncio
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

# User data storage: {chat_id: {"addresses": [addr1, addr2], "active_address": addr, "monitoring": True/False}}
user_data = {}


def load_abi(filename):
    """Load ABI from JSON file in abi/ directory"""
    filepath = f"abi/{filename}"
    with open(filepath, "r", encoding='utf-8-sig') as f:
        return json.loads(f.read().strip())


MARKETS = {
    "lending": [
        {
            "name": "USDhl Frontier Lending",
            "address": "0x9896a8605763106e57A51aa0a97Fe8099E806bb3",
            "abi_file": "USDhlFrontierLending.json",
            "asset_decimals": 6,  # USDhl uses 6 decimals
        },
        {
            "name": "USDT0 Frontier Lending",
            "address": "0x66c71204B70aE27BE6dC3eb41F9aF5868E68fDb6",
            "abi_file": "USDT0FrontierLending.json",
            "asset_decimals": 6,  # USDT0 uses 6 decimals
        },
    ],
    "borrow": []
}


def get_lending_position(contract, user_addr, asset_decimals=18):
    """Retrieve lending position with multiple calculation methods"""
    position = {}
    try:
        # Get user shares (vault shares are always 18 decimals in ERC4626)
        balance_shares_wei = contract.functions.balanceOf(user_addr).call()
        shares = balance_shares_wei / 1e18
        position["shares_balance"] = shares

        print(f"  User shares: {shares:.4f} ({balance_shares_wei} wei)")

        if shares == 0:
            position["assets_value"] = 0
            return position

        # Get vault totals
        total_assets_wei = contract.functions.totalAssets().call()
        total_supply_wei = contract.functions.totalSupply().call()

        # Assets use the underlying token decimals, shares use 18
        asset_divisor = 10 ** asset_decimals
        position["vault_total_assets"] = total_assets_wei / asset_divisor
        position["vault_total_shares"] = total_supply_wei / 1e18

        print(f"  Vault total assets: {position['vault_total_assets']:.4f} ({total_assets_wei} wei, {asset_decimals} decimals)")
        print(f"  Vault total shares: {position['vault_total_shares']:.4f} ({total_supply_wei} wei, 18 decimals)")

        # Method 1: Try convertToAssets (ERC4626 standard)
        try:
            assets_wei = contract.functions.convertToAssets(balance_shares_wei).call()
            position["assets_value"] = assets_wei / asset_divisor
            position["calculation_method"] = "convertToAssets"
            print(f"  ✅ convertToAssets: {position['assets_value']:.4f} assets ({assets_wei} wei ÷ 10^{asset_decimals})")
            return position
        except Exception as e:
            print(f"  ❌ convertToAssets failed: {str(e)[:100]}")

        # Method 2: Manual calculation using ratio
        if total_supply_wei > 0:
            # assets_value = (user_shares * total_assets) / total_supply
            assets_wei = (balance_shares_wei * total_assets_wei) // total_supply_wei
            position["assets_value"] = assets_wei / asset_divisor
            position["calculation_method"] = "manual_ratio"
            print(f"  ✅ Manual calculation: {position['assets_value']:.4f} ({assets_wei} wei)")
        else:
            position["assets_value"] = 0
            position["calculation_method"] = "zero_supply"
            print(f"  ⚠️ Total supply is 0, cannot calculate")

        return position

    except Exception as e:
        print(f"  ❌ Error: {str(e)}")
        return {"error": str(e)}


def fetch_positions(address):
    """Fetch all positions for an address"""
    try:
        checksum_addr = w3.to_checksum_address(address)
    except:
        return {"error": "Invalid address format"}

    results = {"lending": [], "timestamp": datetime.now()}

    print(f"\n{'='*60}")
    print(f"Fetching positions for {checksum_addr}")
    print(f"{'='*60}")

    for market in MARKETS["lending"]:
        print(f"\n📥 {market['name']}...")
        try:
            abi = load_abi(market["abi_file"])
            contract = w3.eth.contract(address=market["address"], abi=abi)
            asset_decimals = market.get("asset_decimals", 18)
            data = get_lending_position(contract, checksum_addr, asset_decimals)
            results["lending"].append({
                "name": market["name"],
                "address": market["address"],
                "data": data
            })
        except Exception as e:
            print(f"  ❌ Failed: {str(e)}")
            results["lending"].append({
                "name": market["name"],
                "address": market["address"],
                "data": {"error": str(e)}
            })

    return results


def format_position_message(address, positions):
    """Format positions into a Telegram message"""
    if "error" in positions:
        return f"❌ Error: {positions['error']}"

    msg = f"📊 *Felix Protocol Positions*\n"
    msg += f"👤 `{address[:6]}...{address[-4:]}`\n"
    msg += f"🕐 {positions['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    total_value = 0

    for market in positions["lending"]:
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"*{market['name']}*\n"

        if "error" in market["data"]:
            msg += f"❌ Error: {market['data']['error'][:100]}\n"
        elif market["data"].get("shares_balance", 0) > 0:
            shares = market["data"]["shares_balance"]
            value = market["data"].get("assets_value", 0)
            total_value += value

            # Show asset value prominently
            msg += f"💰 *Assets: {value:,.4f}$*\n"
            msg += f"📊 Shares: {shares:,.4f}\n"

            # Debug info if values seem wrong
            if value == 0 and shares > 0:
                vault_assets = market["data"].get("vault_total_assets", 0)
                vault_shares = market["data"].get("vault_total_shares", 0)
                msg += f"⚠️ *Debug Info:*\n"
                msg += f"  Vault Assets: {vault_assets:,.6f}\n"
                msg += f"  Vault Shares: {vault_shares:,.6f}\n"
                if vault_shares > 0:
                    ratio = vault_assets / vault_shares
                    expected = shares * ratio
                    msg += f"  Share Price: {ratio:.8f}\n"
                    msg += f"  Expected Value: {expected:.6f}\n"

            # Health factor if available
            if "health_factor" in market["data"]:
                hf = market["data"]["health_factor"]
                emoji = "✅" if hf > 1.5 else "⚠️" if hf > 1.1 else "🔴"
                msg += f"{emoji} Health Factor: {hf:.4f}\n"
        else:
            msg += f"ℹ️ No position\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💎 *Total Value: {total_value:,.4f}$*\n"

    return msg


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data:
        user_data[chat_id] = {
            "addresses": [],
            "active_address": None,
            "monitoring": False
        }

    welcome_msg = (
        "👋 *Welcome to Felix Position Monitor Bot!*\n\n"
        "📌 *Commands:*\n"
        "/add <address> - Add wallet address\n"
        "/list - Show all your addresses\n"
        "/select - Choose active address\n"
        "/check - Check current positions\n"
        "/monitor - Start/Stop monitoring (30min)\n"
        "/remove <address> - Remove address\n"
        "/help - Show this help\n\n"
        "💡 Start by adding your wallet address!"
    )

    await update.message.reply_text(welcome_msg, parse_mode='Markdown')


async def add_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new address"""
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "❌ *Usage:* `/add <wallet_address>`\n\n"
            "📝 *Example:*\n"
            "`/add 0x6af0b3433e185614f2ee8a6cdb789fe1de4ccd05`\n\n"
            "💡 Make sure to include the full address after /add",
            parse_mode='Markdown'
        )
        return

    # Join all args in case address was split
    address = ''.join(context.args).lower().strip()

    # Validate address
    try:
        w3.to_checksum_address(address)
    except:
        await update.message.reply_text("❌ Invalid Ethereum address format")
        return

    if chat_id not in user_data:
        user_data[chat_id] = {"addresses": [], "active_address": None, "monitoring": False}

    if address in user_data[chat_id]["addresses"]:
        await update.message.reply_text("⚠️ Address already added!")
        return

    user_data[chat_id]["addresses"].append(address)

    # Set as active if it's the first address
    if not user_data[chat_id]["active_address"]:
        user_data[chat_id]["active_address"] = address

    await update.message.reply_text(
        f"✅ Address added: `{address[:6]}...{address[-4:]}`\n"
        f"📊 Total addresses: {len(user_data[chat_id]['addresses'])}\n\n"
        f"💡 Use /check to see your positions\n"
        f"💡 Use /monitor to start tracking",
        parse_mode='Markdown'
    )


async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all saved addresses"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data or not user_data[chat_id]["addresses"]:
        await update.message.reply_text("❌ No addresses added yet. Use /add <address>")
        return

    active = user_data[chat_id]["active_address"]
    msg = "📋 *Your Addresses:*\n\n"

    for i, addr in enumerate(user_data[chat_id]["addresses"], 1):
        marker = "👉 " if addr == active else "   "
        msg += f"{marker}{i}. `{addr[:6]}...{addr[-4:]}`\n"

    msg += f"\n✅ Active: `{active[:6]}...{active[-4:]}`" if active else ""

    await update.message.reply_text(msg, parse_mode='Markdown')


async def select_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select active address with inline keyboard"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data or not user_data[chat_id]["addresses"]:
        await update.message.reply_text("❌ No addresses added yet. Use /add <address>")
        return

    keyboard = []
    for addr in user_data[chat_id]["addresses"]:
        short_addr = f"{addr[:6]}...{addr[-4:]}"
        keyboard.append([InlineKeyboardButton(short_addr, callback_data=f"select_{addr}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👇 Select an address:", reply_markup=reply_markup)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("select_"):
        address = data.replace("select_", "")
        user_data[chat_id]["active_address"] = address
        await query.edit_message_text(
            f"✅ Active address set to:\n`{address[:6]}...{address[-4:]}`",
            parse_mode='Markdown'
        )


async def check_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check current position"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data or not user_data[chat_id]["active_address"]:
        await update.message.reply_text("❌ No active address. Use /add and /select first")
        return

    address = user_data[chat_id]["active_address"]

    msg = await update.message.reply_text("🔄 Fetching positions...")

    positions = fetch_positions(address)
    formatted = format_position_message(address, positions)

    await msg.edit_text(formatted, parse_mode='Markdown')


async def toggle_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start/Stop monitoring"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data or not user_data[chat_id]["active_address"]:
        await update.message.reply_text("❌ No active address. Use /add and /select first")
        return

    is_monitoring = user_data[chat_id].get("monitoring", False)

    if is_monitoring:
        user_data[chat_id]["monitoring"] = False
        # Cancel scheduled job if exists
        jobs = context.job_queue.get_jobs_by_name(f"monitor_{chat_id}")
        for job in jobs:
            job.schedule_removal()

        await update.message.reply_text("⏸️ Monitoring stopped")
    else:
        user_data[chat_id]["monitoring"] = True
        # Schedule monitoring job
        context.job_queue.run_repeating(
            monitor_position,
            interval=CHECK_INTERVAL,
            first=10,
            chat_id=chat_id,
            name=f"monitor_{chat_id}",
            data=user_data[chat_id]["active_address"]
        )

        await update.message.reply_text(
            f"✅ Monitoring started!\n"
            f"📊 Check interval: {CHECK_INTERVAL // 60} minutes\n"
            f"👤 Address: `{user_data[chat_id]['active_address'][:6]}...{user_data[chat_id]['active_address'][-4:]}`",
            parse_mode='Markdown'
        )


async def monitor_position(context: ContextTypes.DEFAULT_TYPE):
    """Periodic monitoring job"""
    chat_id = context.job.chat_id
    address = context.job.data

    if not user_data.get(chat_id, {}).get("monitoring", False):
        return

    positions = fetch_positions(address)
    formatted = format_position_message(address, positions)

    await context.bot.send_message(chat_id=chat_id, text=formatted, parse_mode='Markdown')


async def remove_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an address"""
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("❌ Usage: /remove <address>")
        return

    address = ''.join(context.args).lower().strip()

    if chat_id not in user_data or address not in user_data[chat_id]["addresses"]:
        await update.message.reply_text("❌ Address not found")
        return

    user_data[chat_id]["addresses"].remove(address)

    # Update active address if removed
    if user_data[chat_id]["active_address"] == address:
        user_data[chat_id]["active_address"] = (
            user_data[chat_id]["addresses"][0] if user_data[chat_id]["addresses"] else None
        )

    await update.message.reply_text(f"✅ Address removed: `{address[:6]}...{address[-4:]}`", parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    help_text = (
        "📖 *Felix Position Monitor - Help*\n\n"
        "📌 *Commands:*\n"
        "/start - Start the bot\n"
        "/add `<address>` - Add wallet address\n"
        "   Example: `/add 0x6af0...cd05`\n"
        "/list - List all your addresses\n"
        "/select - Choose active address\n"
        "/check - Check current positions\n"
        "/monitor - Start/Stop auto-monitoring\n"
        "/remove `<address>` - Remove address\n"
        "/help - Show this help\n\n"
        "⏱️ *Monitoring interval:* 30 minutes\n"
        "💡 *Tip:* You can add multiple addresses\n"
        "   but only one is monitored at a time\n\n"
        "📝 *How to add an address:*\n"
        "Just type: `/add` followed by your wallet\n"
        "The address must start with 0x"
    )

    await update.message.reply_text(help_text, parse_mode='Markdown')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages (potential addresses)"""
    text = update.message.text.strip()

    # Check if it looks like an Ethereum address
    if text.startswith('0x') and len(text) == 42:
        chat_id = update.effective_chat.id
        address = text.lower()

        # Validate address
        try:
            w3.to_checksum_address(address)
        except:
            await update.message.reply_text("❌ Invalid Ethereum address format")
            return

        if chat_id not in user_data:
            user_data[chat_id] = {"addresses": [], "active_address": None, "monitoring": False}

        if address in user_data[chat_id]["addresses"]:
            await update.message.reply_text("⚠️ Address already added!")
            return

        user_data[chat_id]["addresses"].append(address)

        # Set as active if it's the first address
        if not user_data[chat_id]["active_address"]:
            user_data[chat_id]["active_address"] = address

        await update.message.reply_text(
            f"✅ Address added: `{address[:6]}...{address[-4:]}`\n"
            f"📊 Total addresses: {len(user_data[chat_id]['addresses'])}\n\n"
            f"💡 Use /check to see your positions\n"
            f"💡 Use /monitor to start tracking",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "👋 Send me an Ethereum address or use:\n"
            "/add <address> - to add a wallet\n"
            "/help - for all commands"
        )


def main():
    """Start the bot"""
    print("🤖 Starting Felix Telegram Bot...")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_address))
    application.add_handler(CommandHandler("list", list_addresses))
    application.add_handler(CommandHandler("select", select_address))
    application.add_handler(CommandHandler("check", check_position))
    application.add_handler(CommandHandler("monitor", toggle_monitoring))
    application.add_handler(CommandHandler("remove", remove_address))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Handle plain text messages (for direct address input)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot started! Send /start to your bot to begin")
    application.run_polling()


if __name__ == "__main__":
    main()