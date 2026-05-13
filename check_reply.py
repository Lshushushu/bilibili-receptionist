#!/usr/bin/env python3
"""Check if reply was sent for EP27 comment."""
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

# Check sub-comments for the comment
aid = 116556973476831
rpid = 302102224448
url = "https://api.bilibili.com/x/v2/reply/reply"
params = {
    "type": 1,
    "oid": aid,
    "root": rpid,
    "pn": 1,
    "ps": 20,
}

print(f"Checking sub-comments for rpid={rpid}...")
resp = session.get(url, params=params, timeout=15)
print(f"Status: {resp.status_code}")
data = resp.json()
print(f"Code: {data.get('code')}")
print(f"Message: {data.get('message')}")

replies = data.get('data', {}).get('replies', [])
print(f"Sub-comments found: {len(replies)}")

for c in replies:
    sub_rpid = c.get('rpid', '?')
    user = c.get('member', {}).get('uname', '?')
    msg = c.get('content', {}).get('message', '?')[:80]
    print(f"  rpid={sub_rpid} | user={user} | {msg}")
