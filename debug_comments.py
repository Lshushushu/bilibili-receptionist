#!/usr/bin/env python3
"""Debug EP27 comments API response."""
import requests
import json

# Load cookies
with open('config.json', 'r') as f:
    config = json.load(f)
cookies = config.get('cookies', {})

# Setup session
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com',
})
session.cookies.set("SESSDATA", cookies["SESSDATA"], domain=".bilibili.com")
session.cookies.set("bili_jct", cookies["bili_jct"], domain=".bilibili.com")
session.cookies.set("DedeUserID", cookies["DedeUserID"], domain=".bilibili.com")

# Fetch comments
aid = 116556973476831
url = "https://api.bilibili.com/x/v2/reply/main"
params = {
    "type": 1,
    "oid": aid,
    "mode": 3,
    "next": 1,
    "ps": 20,
}

print(f"Fetching comments for aid={aid}...")
resp = session.get(url, params=params, timeout=15)
print(f"Status: {resp.status_code}")
data = resp.json()
print(f"Code: {data.get('code')}")
print(f"Message: {data.get('message')}")

replies = data.get('data', {}).get('replies', [])
print(f"Comments found: {len(replies)}")

for c in replies:
    rpid = c.get('rpid', '?')
    user = c.get('member', {}).get('uname', '?')
    msg = c.get('content', {}).get('message', '?')[:80]
    print(f"  rpid={rpid} | user={user} | {msg}")
