import os
import json
from web3 import Web3
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()
RPC_URL = os.getenv("RPC_URL")
USER_ADDRESS = os.getenv("USER_ADDRESS")

if not RPC_URL or not USER_ADDRESS:
    raise ValueError("⚠️ RPC_URL ou USER_ADDRESS manquant dans le .env")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise ConnectionError("❌ Impossible de se connecter au RPC.")

# Fonction utilitaire pour charger les ABIs
def load_abi(filename):
    with open(f"abi/{filename}", "r") as f:
        return json.load(f)

# Configuration des marchés
MARKETS = {
    "lending": [
        {
            "name": "USDhl Frontier Lending",
            "address": "0x9896a8605763106e57A51aa0a97Fe8099E806bb3",
            "abi": load_abi("USDhlFrontierLending.json"),
        },
        {
            "name": "USDT0 Frontier Lending",
            "address": "0x66c71204B70aE27BE6dC3eb41F9aF5868E68fDb6",
            "abi": load_abi("USDT0FrontierLending.json"),
        },
    ],
    "borrow": [
        {
            "name": "WHLP/USDT0 Borrow Market",
            "address": "0xd4fd53f612eaf411a1acea053cfa28cbfeea683273c4133bf115b47a20130305",
            "abi": load_abi("WHLPUSDT0Borrow.json"),
            "market_id": 0,  # à confirmer plus tard
        }
    ],
}

# Lecture des positions
def get_lending_position(contract, user_addr):
    """Essaie de récupérer balance, health factor, APY."""
    position = {}
    try:
        if hasattr(contract.functions, "getUserPosition"):
            data = contract.functions.getUserPosition(user_addr).call()
            position["collateral"] = data[0] / 1e18
            position["debt"] = data[1] / 1e18
            position["health_factor"] = data[2] / 1e18
        if hasattr(contract.functions, "supplyRatePerBlock"):
            position["lend_apy"] = contract.functions.supplyRatePerBlock().call() / 1e18
        elif hasattr(contract.functions, "supplyRate"):
            position["lend_apy"] = contract.functions.supplyRate().call() / 1e18
        return position
    except Exception as e:
        return {"error": str(e)}

def get_borrow_position(contract, user_addr, market_id):
    """Lecture spécifique des positions borrow par marketID."""
    position = {}
    try:
        if hasattr(contract.functions, "getUserPosition"):
            # Certaines implémentations demandent (marketId, user)
            data = contract.functions.getUserPosition(market_id, user_addr).call()
            position["collateral"] = data[0] / 1e18
            position["borrowed"] = data[1] / 1e18
            position["health_factor"] = data[2] / 1e18
        if hasattr(contract.functions, "getBorrowRate"):
            position["borrow_apy"] = contract.functions.getBorrowRate(market_id).call() / 1e18
        return position
    except Exception as e:
        return {"error": str(e)}

def fetch_all_positions():
    results = {"lending": [], "borrow": []}

    for market in MARKETS["lending"]:
        contract = w3.eth.contract(address=market["address"], abi=market["abi"])
        data = get_lending_position(contract, USER_ADDRESS)
        results["lending"].append({"name": market["name"], "address": market["address"], "data": data})

    for market in MARKETS["borrow"]:
        contract = w3.eth.contract(address=market["address"], abi=market["abi"])
        data = get_borrow_position(contract, USER_ADDRESS, market["market_id"])
        results["borrow"].append({"name": market["name"], "address": market["address"], "data": data})

    return results

if __name__ == "__main__":
    positions = fetch_all_positions()
    print("\n📊 Résumé des positions sur Felix\n")
    for market_type, markets in positions.items():
        print(f"\n=== {market_type.upper()} MARKETS ===")
        for m in markets:
            print(f"\n→ {m['name']} ({m['address']})")
            for key, val in m["data"].items():
                print(f"  {key}: {val}")
