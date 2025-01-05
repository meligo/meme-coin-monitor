import json
from base64 import b64decode

import requests


def get_pump_tokens(rpc_url):
    headers = {"Content-Type": "application/json"}
    
    # We want to get all accounts owned by the pump program
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getProgramAccounts",
        "params": [
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PUMP_PROGRAM
            {
                "encoding": "base64",
                "commitment": "confirmed",
                "filters": [
                    {
                        "dataSize": 165  # Size of token account data
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(rpc_url, headers=headers, data=json.dumps(payload))
        result = response.json()
        
        if "result" in result:
            accounts = result["result"]
            print(f"Found {len(accounts)} pump.fun token accounts")
            
            # Process each account
            for account in accounts:
                try:
                    data = b64decode(account["account"]["data"][0])
                    pubkey = account["pubkey"]
                    print(f"Token Account: {pubkey}")
                    print(f"Data length: {len(data)}")
                    # Here you can add logic to parse the account data structure
                    
                except Exception as e:
                    print(f"Error processing account {account['pubkey']}: {str(e)}")
                    
            return accounts
        else:
            print("No 'result' in response:", result)
            return []
            
    except Exception as e:
        print(f"Error making RPC request: {str(e)}")
        return []

if __name__ == "__main__":
    # Use your RPC endpoint
    RPC_URL = "https://solana-mainnet.core.chainstack.com/3b0ed795772b1898efd6ff013f7b764e"
    
    tokens = get_pump_tokens(RPC_URL)
    print(f"Total tokens found: {len(tokens)}")