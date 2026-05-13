#!/usr/bin/env python3
"""Check all comments on EP27 and their reply status."""
import requests
import json

# Load cookies
with open('config.json', 'r') as f:
    config = json.load(f)
cookies = config.get('cookies', {})

# Load replied rpids
with open('data/replied_rpid.json', 'r') as f:
    replied_data = json.load(f)
replied_rpids = set(replied_data.get('replied_rpids', []))

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

print(f"EP27 (aid={aid}) 评论状态:")
print("=" * 60)

resp = session.get(url, params=params, timeout=15)
data = resp.json()
replies = data.get('data', {}).get('replies', [])

for c in replies:
    rpid = str(c.get('rpid', ''))
    user = c.get('member', {}).get('uname', '?')
    msg = c.get('content', {}).get('message', '?')[:60]
    rcount = c.get('rcount', 0)
    replied = rpid in replied_rpids
    
    status = "✅ 已回复" if replied else "❌ 未回复"
    print(f"\n评论: {msg}")
    print(f"用户: {user} | rpid: {rpid} | 子评论: {rcount}")
    print(f"状态: {status}")
    
    # Check sub-comments
    if rcount > 0:
        sub_url = "https://api.bilibili.com/x/v2/reply/reply"
        sub_params = {
            "type": 1,
            "oid": aid,
            "root": rpid,
            "pn": 1,
            "ps": 20,
        }
        sub_resp = session.get(sub_url, params=sub_params, timeout=15)
        sub_data = sub_resp.json()
        sub_replies = sub_data.get('data', {}).get('replies', [])
        
        for sc in sub_replies:
            sub_user = sc.get('member', {}).get('uname', '?')
            sub_msg = sc.get('content', {}).get('message', '?')[:60]
            print(f"  └─ {sub_user}: {sub_msg}")

print("\n" + "=" * 60)
print(f"已回复记录: {len(replied_rpids)} 条")
