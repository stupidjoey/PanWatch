import requests, json, os, sys

BASE = "https://stupidjoey-panwatch.fly.dev"
pw = sys.argv[1]

r = requests.post(f"{BASE}/api/auth/login", json={"username":"stupidjoey","password":pw})
if not r.json().get("success"):
    print("LOGIN FAILED:", r.text[:200])
    sys.exit(1)

token = r.json()["data"]["token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Check quote for 003156
r = requests.get(f"{BASE}/api/stocks/quotes?symbols=003156", headers=headers, timeout=15)
print(json.dumps(r.json(), ensure_ascii=False, indent=2))
