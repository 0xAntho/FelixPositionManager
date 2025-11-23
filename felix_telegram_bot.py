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

# User data storage: {chat_id: {"addresses": [addr1, addr2], "active_address": addr, "monitoring": True/False, "custom_markets": []}}
user_data = {}

# Global markets (available to all users)
GLOBAL_MARKETS = {
    "lending": [
        {
            "name": "USDhl Frontier Lending",
            "address": "0x9896a8605763106e57A51aa0a97Fe8099E806bb3",
            "abi_file": "USDhlFrontierLending.json",
            "asset_decimals": 6,
        },
        {
            "name": "USDT0 Frontier Lending",
            "address": "0x66c71204B70aE27BE6dC3eb41F9aF5868E68fDb6",
            "abi_file": "USDT0FrontierLending.json",
            "asset_decimals": 6,
        },
    ],
    "borrow": [
        {
            "name": "WHLP/USDT0 Borrow Market",
            "morpho_address": "0x68e37dE8d93d3496ae143F2E900490f6280C57cD",
            "market_id": "0xd4fd53f612eaf411a1acea053cfa28cbfeea683273c4133bf115b47a20130305",
            "abi_file": "MorphoBlue.json",
            "collateral_decimals": 6,
            "borrow_decimals": 6,
            "borrow_shares_decimals": 18,
        }
    ]
}


def get_markets_for_user(chat_id):
    """Get combined global and user-specific markets"""
    markets = {
        "lending": GLOBAL_MARKETS["lending"].copy(),
        "borrow": GLOBAL_MARKETS["borrow"].copy()
    }

    # Add custom markets if any
    if chat_id in user_data and "custom_markets" in user_data[chat_id]:
        custom = user_data[chat_id]["custom_markets"]
        if "lending" in custom:
            markets["lending"].extend(custom["lending"])
        if "borrow" in custom:
            markets["borrow"].extend(custom["borrow"])

    return markets
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
    "borrow": [
        {
            "name": "WHLP/USDT0 Borrow Market",
            "morpho_address": "0x68e37dE8d93d3496ae143F2E900490f6280C57cD",
            "market_id": "0xd4fd53f612eaf411a1acea053cfa28cbfeea683273c4133bf115b47a20130305",
            "abi_file": "MorphoBlue.json",
            "collateral_decimals": 6,  # WHLP uses 6 decimals in this context
            "borrow_decimals": 6,  # USDT0
            "borrow_shares_decimals": 18,  # Morpho uses 18 decimals for shares
        }
    ]
}

def load_abi(filename):
    """Load ABI from JSON file in abi/ directory"""
    filepath = f"abi/{filename}"
    with open(filepath, "r", encoding='utf-8-sig') as f:
        return json.loads(f.read().strip())

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


def get_borrow_position(contract, user_addr, market_id, collateral_decimals=18, borrow_decimals=6, borrow_shares_decimals=18):
    """Retrieve borrow position from Morpho Blue"""
    position = {}

    try:
        # Convert market_id to bytes32
        if isinstance(market_id, str):
            market_id_bytes = bytes.fromhex(market_id.replace('0x', ''))
        else:
            market_id_bytes = market_id

        print(f"  Market ID: {market_id}")

        # Get market data
        market_data = contract.functions.market(market_id_bytes).call()
        total_supply_assets = market_data[0]
        total_supply_shares = market_data[1]
        total_borrow_assets = market_data[2]
        total_borrow_shares = market_data[3]

        borrow_divisor = 10 ** borrow_decimals
        shares_divisor = 10 ** borrow_shares_decimals

        print(f"  Market total borrow assets: {total_borrow_assets / borrow_divisor:.4f} ({total_borrow_assets} wei, {borrow_decimals} decimals)")
        print(f"  Market total borrow shares: {total_borrow_shares / shares_divisor:.4f} ({total_borrow_shares} wei, {borrow_shares_decimals} decimals)")

        # Get user position
        user_position = contract.functions.position(market_id_bytes, user_addr).call()
        supply_shares = user_position[0]
        borrow_shares_wei = user_position[1]
        collateral_wei = user_position[2]

        collateral_divisor = 10 ** collateral_decimals

        position["supply_shares"] = supply_shares / 1e18
        position["borrow_shares"] = borrow_shares_wei / shares_divisor
        position["collateral"] = collateral_wei / collateral_divisor

        print(f"  User borrow shares: {position['borrow_shares']:.6f} ({borrow_shares_wei} wei, {borrow_shares_decimals} decimals)")
        print(f"  User collateral: {position['collateral']:.4f} ({collateral_wei} wei, {collateral_decimals} decimals)")

        # Calculate borrowed amount: (user_shares * total_assets) / total_shares
        if total_borrow_shares > 0 and borrow_shares_wei > 0:
            borrowed_amount_wei = (borrow_shares_wei * total_borrow_assets) // total_borrow_shares
            position["borrowed"] = borrowed_amount_wei / borrow_divisor
            print(f"  ✅ Calculated borrowed: {position['borrowed']:.4f} ({borrowed_amount_wei} wei)")
        else:
            position["borrowed"] = 0
            print(f"  No borrow shares")

        # Calculate health factor
        # Health factor = collateral_value / borrowed_value
        # This is simplified - real calculation needs oracle prices and LLTV
        if position.get("borrowed", 0) > 0 and position["collateral"] > 0:
            # Simplified: assuming 1:1 pricing
            position["health_factor"] = position["collateral"] / position["borrowed"]
            print(f"  ⚠️  Simplified health factor: {position['health_factor']:.4f} (needs oracle for accuracy)")
        elif position.get("borrowed", 0) == 0:
            position["health_factor"] = float('inf')
            print(f"  Health factor: ∞ (no debt)")

        return position

    except Exception as e:
        print(f"  ❌ Error: {str(e)}")
        return {"error": str(e)}
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


def fetch_positions(address, chat_id=None):
    """Fetch all positions for an address"""
    try:
        checksum_addr = w3.to_checksum_address(address)
    except:
        return {"error": "Invalid address format"}

    results = {"lending": [], "borrow": [], "timestamp": datetime.now()}

    print(f"\n{'='*60}")
    print(f"Fetching positions for {checksum_addr}")
    print(f"{'='*60}")

    # Get markets (global + custom for this user)
    markets = get_markets_for_user(chat_id) if chat_id else GLOBAL_MARKETS

    # Fetch lending positions
    for market in markets["lending"]:
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

    # Fetch borrow positions
    for market in markets["borrow"]:
        print(f"\n📥 {market['name']}...")
        try:
            abi = load_abi(market["abi_file"])
            contract = w3.eth.contract(address=market["morpho_address"], abi=abi)
            collateral_decimals = market.get("collateral_decimals", 18)
            borrow_decimals = market.get("borrow_decimals", 6)
            borrow_shares_decimals = market.get("borrow_shares_decimals", 18)
            data = get_borrow_position(
                contract,
                checksum_addr,
                market["market_id"],
                collateral_decimals,
                borrow_decimals,
                borrow_shares_decimals
            )
            results["borrow"].append({
                "name": market["name"],
                "market_id": market["market_id"],
                "data": data
            })
        except Exception as e:
            print(f"  ❌ Failed: {str(e)}")
            results["borrow"].append({
                "name": market["name"],
                "market_id": market["market_id"],
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

    total_lending_value = 0
    total_borrow_value = 0

    # Lending positions
    for market in positions["lending"]:
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"*📈 {market['name']}*\n"

        if "error" in market["data"]:
            error_msg = market['data']['error'][:100].replace('_', '\\_')
            msg += f"❌ Error: {error_msg}\n"
        elif market["data"].get("shares_balance", 0) > 0:
            shares = market["data"]["shares_balance"]
            value = market["data"].get("assets_value", 0)
            total_lending_value += value

            msg += f"💰 *Assets: {value:,.4f}*\n"
            msg += f"📊 Shares: {shares:,.4f}\n"

            if value == 0 and shares > 0:
                vault_assets = market["data"].get("vault_total_assets", 0)
                vault_shares = market["data"].get("vault_total_shares", 0)
                msg += f"⚠️ *Debug Info:*\n"
                msg += f"  Vault Assets: {vault_assets:,.6f}\n"
                msg += f"  Vault Shares: {vault_shares:,.6f}\n"
        else:
            msg += f"ℹ️ No lending position\n"

    # Borrow positions
    for market in positions.get("borrow", []):
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"*📉 {market['name']}*\n"

        if "error" in market["data"]:
            error_msg = market['data']['error'][:100].replace('_', '\\_')
            msg += f"❌ Error: {error_msg}\n"
        elif market["data"].get("borrowed", 0) > 0 or market["data"].get("collateral", 0) > 0:
            borrowed = market["data"].get("borrowed", 0)
            collateral = market["data"].get("collateral", 0)
            total_borrow_value += borrowed

            msg += f"🔴 *Borrowed: {borrowed:,.4f}*\n"
            msg += f"🔒 Collateral: {collateral:,.4f}\n"

            if "health_factor" in market["data"]:
                hf = market["data"]["health_factor"]
                if hf == float('inf'):
                    msg += f"✅ Health Factor: Infinity\n"
                else:
                    emoji = "✅" if hf > 1.5 else "⚠️" if hf > 1.1 else "🔴"
                    msg += f"{emoji} Health Factor: {hf:.4f}\n"
        else:
            msg += f"ℹ️ No borrow position\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💚 *Total Lending: {total_lending_value:,.4f}*\n"
    msg += f"🔴 *Total Borrowed: {total_borrow_value:,.4f}*\n"
    msg += f"💎 *Net Value: {total_lending_value - total_borrow_value:,.4f}*\n"

    return msg


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data:
        user_data[chat_id] = {
            "addresses": [],
            "active_address": None,
            "monitoring": False,
            "selected_markets": {"lending": [], "borrow": []}
        }

    welcome_msg = (
        "👋 *Welcome to Felix Position Monitor Bot!*\n\n"
        "📌 *Commands:*\n"
        "/add <address> - Add wallet address\n"
        "/list - Show all your addresses\n"
        "/select - Choose active address\n"
        "/check - Check current positions\n"
        "/monitor - Start/Stop monitoring (30min)\n"
        "/markets - Select markets to track\n"
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
            "`/add 0x1234...5678`\n\n"
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


async def manage_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show market selection menu"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data:
        user_data[chat_id] = {
            "addresses": [],
            "active_address": None,
            "monitoring": False,
            "selected_markets": {"lending": [], "borrow": []}
        }

    selected = user_data[chat_id]["selected_markets"]

    msg = (
        f"📊 *Market Selection*\n\n"
        f"Select which markets you want to track.\n"
        f"By default, all markets are shown.\n\n"
        f"📈 Lending markets: {len(selected.get('lending', [])) if selected.get('lending') else 'All'}\n"
        f"📉 Borrow markets: {len(selected.get('borrow', [])) if selected.get('borrow') else 'All'}\n\n"
        f"Choose a category:"
    )

    keyboard = [
        [InlineKeyboardButton("📈 Lending Markets", callback_data="select_lending")],
        [InlineKeyboardButton("📉 Borrow Markets", callback_data="select_borrow")],
        [InlineKeyboardButton("🔄 Reset to All", callback_data="reset_markets")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)


async def add_market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a custom lending market via command"""
    chat_id = update.effective_chat.id

    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ *Usage:* `/addmarket <name> <address> <decimals>`\n\n"
            "📝 *Example:*\n"
            "`/addmarket MyToken 0x1234...5678 6`\n\n"
            "💡 Decimals: usually 6 for stablecoins, 18 for ETH-like tokens",
            parse_mode='Markdown'
        )
        return

    name = ' '.join(context.args[:-2])
    address = context.args[-2].lower().strip()
    try:
        decimals = int(context.args[-1])
    except:
        await update.message.reply_text("❌ Decimals must be a number (e.g., 6 or 18)")
        return

    # Validate address
    try:
        w3.to_checksum_address(address)
    except:
        await update.message.reply_text("❌ Invalid contract address format")
        return

    if chat_id not in user_data:
        user_data[chat_id] = {
            "addresses": [],
            "active_address": None,
            "monitoring": False,
            "custom_markets": {"lending": [], "borrow": []}
        }

    # Add market
    custom_market = {
        "name": name,
        "address": address,
        "abi_file": "USDhlFrontierLending.json",  # Use default ABI
        "asset_decimals": decimals,
        "custom": True
    }

    user_data[chat_id]["custom_markets"]["lending"].append(custom_market)

    await update.message.reply_text(
        f"✅ *Market added successfully!*\n\n"
        f"📊 Name: {name}\n"
        f"📍 Address: `{address[:6]}...{address[-4:]}`\n"
        f"🔢 Decimals: {decimals}\n\n"
        f"💡 Use /check to see your positions",
        parse_mode='Markdown'
    )


async def list_custom_markets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List user's custom markets"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    if chat_id not in user_data or not user_data[chat_id]["custom_markets"]["lending"]:
        await query.edit_message_text(
            "ℹ️ You don't have any custom markets yet.\n\n"
            "Use /addmarket to add one!"
        )
        return

    msg = "📋 *Your Custom Markets:*\n\n"

    for i, market in enumerate(user_data[chat_id]["custom_markets"]["lending"], 1):
        msg += f"{i}. *{market['name']}*\n"
        msg += f"   `{market['address'][:10]}...{market['address'][-6:]}`\n"
        msg += f"   Decimals: {market['asset_decimals']}\n\n"

    await query.edit_message_text(msg, parse_mode='Markdown')


async def remove_market_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show remove market menu"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    if chat_id not in user_data or not user_data[chat_id]["custom_markets"]["lending"]:
        await query.edit_message_text("ℹ️ You don't have any custom markets to remove.")
        return

    keyboard = []
    for i, market in enumerate(user_data[chat_id]["custom_markets"]["lending"]):
        short_name = market['name'][:30]
        keyboard.append([InlineKeyboardButton(f"🗑️ {short_name}", callback_data=f"remove_market_{i}")])

    keyboard.append([InlineKeyboardButton("« Back", callback_data="back_to_markets")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Select a market to remove:", reply_markup=reply_markup)


async def market_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle market management button callbacks"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    if data == "add_lending_market":
        await query.edit_message_text(
            "➕ *Add Lending Market*\n\n"
            "Send the command:\n"
            "`/addmarket <name> <address> <decimals>`\n\n"
            "📝 *Example:*\n"
            "`/addmarket MyToken 0x1234...5678 6`",
            parse_mode='Markdown'
        )

    elif data == "list_custom_markets":
        await list_custom_markets_callback(update, context)

    elif data == "remove_market_menu":
        await remove_market_menu_callback(update, context)

    elif data.startswith("remove_market_"):
        index = int(data.replace("remove_market_", ""))
        if chat_id in user_data and index < len(user_data[chat_id]["custom_markets"]["lending"]):
            removed = user_data[chat_id]["custom_markets"]["lending"].pop(index)
            await query.edit_message_text(
                f"✅ Market removed: *{removed['name']}*\n\n"
                f"Use /markets to manage your markets",
                parse_mode='Markdown'
            )

    elif data == "back_to_markets":
        await manage_markets(query, context)


async def check_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check current position"""
    chat_id = update.effective_chat.id

    if chat_id not in user_data or not user_data[chat_id]["active_address"]:
        await update.message.reply_text("❌ No active address. Use /add and /select first")
        return

    address = user_data[chat_id]["active_address"]

    msg = await update.message.reply_text("🔄 Fetching positions...")

    positions = fetch_positions(address, chat_id)
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

    positions = fetch_positions(address, chat_id)
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
        "   Example: `/add 0x1234...5678`\n"
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
    application.add_handler(CommandHandler("markets", manage_markets))
    application.add_handler(CommandHandler("remove", remove_address))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(market_button_callback, pattern="^(select_lending|select_borrow|back_to_market_menu|reset_markets|toggle_)"))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Handle plain text messages (for direct address input)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot started! Send /start to your bot to begin")
    application.run_polling()


if __name__ == "__main__":
    main()