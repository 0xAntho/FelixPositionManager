import os
import json
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()
RPC_URL = os.getenv("RPC_URL")
USER_ADDRESS = os.getenv("USER_ADDRESS")

if not RPC_URL or not USER_ADDRESS:
    print("\n❌ Missing variables!")
    print(f"📁 Current directory: {os.getcwd()}")
    print(f"📄 .env file exists: {os.path.exists('.env')}")
    if os.path.exists('.env'):
        print("\n📋 .env content:")
        with open('.env', 'r') as f:
            for line in f:
                if line.strip():
                    key = line.split('=')[0]
                    print(f"  {key}=***")
    raise ValueError("⚠️ Missing RPC_URL or USER_ADDRESS in .env")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise ConnectionError("❌ Unable to connect to RPC")

# Convert address to checksum format
USER_ADDRESS = w3.to_checksum_address(USER_ADDRESS)


def load_abi(filename):
    """Load ABI from JSON file in abi/ directory, handling BOM"""
    filepath = f"abi/{filename}"
    try:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"ABI file not found: {filepath}")

        file_size = os.path.getsize(filepath)
        if file_size == 0:
            raise ValueError(f"ABI file is empty: {filepath}")

        with open(filepath, "r", encoding='utf-8-sig') as f:
            content = f.read().strip()
            if not content:
                raise ValueError(f"ABI file has no content: {filepath}")

            abi = json.loads(content)
            print(f"✅ Loaded {filename} ({file_size} bytes, {len(abi)} functions)")
            return abi

    except Exception as e:
        raise ValueError(f"Failed to load {filepath}: {str(e)}")


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


def get_contract_functions(contract):
    """Get available functions from a contract"""
    return [fn for fn in dir(contract.functions) if not fn.startswith('_')]


def get_lending_position(contract, user_addr, market_name):
    """
    Retrieve lending position including balance, collateral, debt and APY.
    Uses multiple methods to calculate asset value.
    """
    position = {}

    try:
        available_fns = get_contract_functions(contract)

        # Get user shares
        if "balanceOf" in available_fns:
            try:
                balance_shares_wei = contract.functions.balanceOf(user_addr).call()
                shares = balance_shares_wei / 1e18
                position["shares_balance"] = shares
                print(f"  User shares: {shares:.4f} ({balance_shares_wei} wei)")
            except Exception as e:
                position["balance_error"] = str(e)
                return position

        # Get vault totals
        if "totalAssets" in available_fns:
            try:
                total_assets_wei = contract.functions.totalAssets().call()
                position["vault_total_assets"] = total_assets_wei / 1e18
                print(f"  Vault total assets: {position['vault_total_assets']:.4f} ({total_assets_wei} wei)")
            except Exception as e:
                pass

        if "totalSupply" in available_fns:
            try:
                total_supply_wei = contract.functions.totalSupply().call()
                position["vault_total_shares"] = total_supply_wei / 1e18
                print(f"  Vault total shares: {position['vault_total_shares']:.4f} ({total_supply_wei} wei)")
            except Exception as e:
                pass

        # Calculate user's asset value
        if "shares_balance" in position and position["shares_balance"] > 0:
            balance_shares_wei = int(position["shares_balance"] * 1e18)

            # Method 1: Try convertToAssets (ERC4626 standard)
            if "convertToAssets" in available_fns:
                try:
                    assets_wei = contract.functions.convertToAssets(balance_shares_wei).call()
                    position["assets_value"] = assets_wei / 1e18
                    position["calculation_method"] = "convertToAssets"
                    print(f"  ✅ convertToAssets: {position['assets_value']:.4f} ({assets_wei} wei)")
                    return position
                except Exception as e:
                    print(f"  ❌ convertToAssets failed: {str(e)[:100]}")

            # Method 2: Manual calculation if needed
            if "vault_total_assets" in position and "vault_total_shares" in position:
                if position["vault_total_shares"] > 0:
                    total_supply_wei = int(position["vault_total_shares"] * 1e18)
                    total_assets_wei = int(position["vault_total_assets"] * 1e18)

                    # Calculate: user_assets = (user_shares * total_assets) / total_supply
                    assets_wei = (balance_shares_wei * total_assets_wei) // total_supply_wei
                    position["assets_value"] = assets_wei / 1e18
                    position["calculation_method"] = "manual_ratio"
                    print(f"  ✅ Manual calculation: {position['assets_value']:.4f} ({assets_wei} wei)")
                else:
                    position["assets_value"] = 0
                    print(f"  ⚠️ Total supply is 0, cannot calculate")

        # Try to get user position (collateral/debt if it exists)
        if "getUserPosition" in available_fns:
            try:
                data = contract.functions.getUserPosition(user_addr).call()
                position["collateral"] = data[0] / 1e18
                position["debt"] = data[1] / 1e18
                position["health_factor"] = data[2] / 1e18
            except Exception:
                pass

        # Try to get supply APY
        apy_methods = ["supplyRatePerBlock", "supplyRate", "getSupplyRate"]
        for method in apy_methods:
            if method in available_fns:
                try:
                    rate = getattr(contract.functions, method)().call()
                    position["supply_apy"] = rate / 1e18
                    break
                except Exception:
                    continue

        return position if position else {"info": "No position data available"}

    except Exception as e:
        return {"error": str(e)}


def get_borrow_position(contract, user_addr, market_id, market_name):
    """
    Retrieve borrow position including collateral, borrowed amount, health factor and borrow APY.
    Adapts to available contract methods.
    """
    position = {}

    try:
        available_fns = get_contract_functions(contract)

        # Try to get user position
        if "getUserPosition" in available_fns:
            try:
                # Try with market_id first
                data = contract.functions.getUserPosition(market_id, user_addr).call()
                position["collateral"] = data[0] / 1e18
                position["borrowed"] = data[1] / 1e18
                position["health_factor"] = data[2] / 1e18
            except Exception:
                try:
                    # Try without market_id
                    data = contract.functions.getUserPosition(user_addr).call()
                    position["collateral"] = data[0] / 1e18
                    position["borrowed"] = data[1] / 1e18
                    position["health_factor"] = data[2] / 1e18
                except Exception:
                    pass

        # Try to get borrow APY
        borrow_methods = ["getBorrowRate", "borrowRate", "borrowRatePerBlock"]
        for method in borrow_methods:
            if method in available_fns:
                try:
                    if method == "getBorrowRate":
                        rate = contract.functions.getBorrowRate(market_id).call()
                    else:
                        rate = getattr(contract.functions, method)().call()
                    position["borrow_apy"] = rate / 1e18
                    break
                except Exception:
                    continue

        return position if position else {"info": "No position data available"}

    except Exception as e:
        return {"error": str(e)}


def fetch_all_positions():
    """Fetch all lending and borrow positions for the configured user address"""
    results = {"lending": [], "borrow": []}

    print("\n🔄 Loading ABIs and fetching positions...\n")

    for market in MARKETS["lending"]:
        print(f"📥 {market['name']}...")
        try:
            abi = load_abi(market["abi_file"])
            contract = w3.eth.contract(address=market["address"], abi=abi)
            data = get_lending_position(contract, USER_ADDRESS, market["name"])
            results["lending"].append({
                "name": market["name"],
                "address": market["address"],
                "data": data
            })
        except Exception as e:
            results["lending"].append({
                "name": market["name"],
                "address": market["address"],
                "data": {"error": str(e)}
            })
        print()

    for market in MARKETS["borrow"]:
        print(f"📥 {market['name']}...")
        try:
            abi = load_abi(market["abi_file"])
            contract = w3.eth.contract(address=market["address"], abi=abi)
            data = get_borrow_position(contract, USER_ADDRESS, market["market_id"], market["name"])
            results["borrow"].append({
                "name": market["name"],
                "address": market["address"],
                "data": data
            })
        except Exception as e:
            results["borrow"].append({
                "name": market["name"],
                "address": market["address"],
                "data": {"error": str(e)}
            })
        print()

    return results


def format_value(key, value):
    """Format values for display with appropriate units and precision"""
    if isinstance(value, (int, float)):
        if "apy" in key.lower() or "rate" in key.lower():
            return f"{value * 100:.4f}%"
        elif "factor" in key.lower():
            return f"{value:.4f}"
        elif value > 1000:
            return f"{value:,.2f}"
        else:
            return f"{value:.6f}"
    return str(value)


if __name__ == "__main__":
    print(f"\n{'='*70}")
    print(f"  📊 Felix Protocol - Position Monitor")
    print(f"{'='*70}")
    print(f"👤 Wallet: {USER_ADDRESS}")
    print(f"🌐 RPC: {RPC_URL[:50]}...")

    positions = fetch_all_positions()

    print(f"\n{'='*70}")
    print(f"  📊 SUMMARY OF POSITIONS")
    print(f"{'='*70}")

    for market_type, markets in positions.items():
        if not markets:
            continue

        print(f"\n{'▼'*70}")
        print(f"  {market_type.upper()} MARKETS")
        print(f"{'▼'*70}")

        for m in markets:
            print(f"\n→ {m['name']}")
            print(f"  Contract: {m['address']}")
            print(f"  {'-'*66}")

            if "error" in m["data"] and len(m["data"]) == 1:
                print(f"  ❌ Error: {m['data']['error']}")
            elif "info" in m["data"] and len(m["data"]) == 1:
                print(f"  ℹ️  {m['data']['info']}")
            else:
                has_position = False

                # Priority display: User's position
                priority_keys = ["assets_value", "shares_balance", "health_factor", "supply_apy", "borrow_apy", "calculation_method"]
                for key in priority_keys:
                    if key in m["data"] and not isinstance(m["data"][key], str) or key == "calculation_method":
                        val = m["data"][key]
                        if key == "calculation_method":
                            formatted_val = val
                        else:
                            formatted_val = format_value(key, val)
                        key_display = key.replace("_", " ").title()

                        # Highlight important values
                        if key == "assets_value":
                            print(f"  💰 {key_display:23s}: {formatted_val}")
                            has_position = True
                        elif key == "health_factor":
                            emoji = "✅" if val > 1.5 else "⚠️" if val > 1.1 else "🔴"
                            print(f"  {emoji} {key_display:23s}: {formatted_val}")
                        elif "apy" in key:
                            print(f"  📈 {key_display:23s}: {formatted_val}")
                        else:
                            print(f"  {key_display:25s}: {formatted_val}")

                # Other data
                for key, val in m["data"].items():
                    if key not in priority_keys and not key.endswith("_error") and key != "info":
                        formatted_val = format_value(key, val)
                        key_display = key.replace("_", " ").title()
                        print(f"  {key_display:25s}: {formatted_val}")

                # Show errors at the end if any
                for key, val in m["data"].items():
                    if key.endswith("_error"):
                        error_name = key.replace("_error", "").title()
                        print(f"  ⚠️  {error_name} Error: {val[:100]}")

                if not has_position and "shares_balance" not in m["data"]:
                    print(f"  ℹ️  No position found for this wallet")

    print(f"\n{'='*70}\n")