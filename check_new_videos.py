#!/usr/bin/env python3
"""Check for new videos using space API."""
import json
import requests
from datetime import datetime

CHANNEL_MID = 9307823

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

# Fetch videos via space API
print("Fetching videos via space API...")
url = "https://api.bilibili.com/x/space/arc/search"
params = {
    "mid": CHANNEL_MID,
    "ps": 30,
    "pn": 1,
    "order": "pubdate",
}

resp = session.get(url, params=params, timeout=15)
print(f"Status: {resp.status_code}")
data = resp.json()
print(f"Code: {data.get('code')}")
print(f"Message: {data.get('message')}")

if data.get('code') == 0:
    vlist = data.get('data', {}).get('list', {}).get('vlist', [])
    print(f"Videos found: {len(vlist)}")
    
    # Load existing monitored videos
    with open('monitored_videos.json', 'r', encoding='utf-8') as f:
        existing_data = json.load(f)
    existing = existing_data.get('videos', existing_data) if isinstance(existing_data, dict) else existing_data
    existing_bvids = {v['bvid'] for v in existing}
    print(f"Already monitored: {len(existing_bvids)}")
    
    # Find new videos
    new_videos = [v for v in vlist if v['bvid'] not in existing_bvids]
    print(f"New videos to add: {len(new_videos)}")
    
    for v in new_videos:
        print(f"  NEW: {v['bvid']} | {v.get('title', '?')[:60]}")
        entry = {
            'bvid': v['bvid'],
            'aid': v.get('aid', ''),
            'title': v.get('title', ''),
            'priority': 1,
            'added_at': datetime.now().isoformat(),
            'pubdate': v.get('created', 0),
            'source': 'space_api'
        }
        existing.append(entry)
    
    if new_videos:
        output = {'videos': existing} if isinstance(existing_data, dict) else existing
        with open('monitored_videos.json', 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\nUpdated monitored_videos.json ({len(existing)} total)")
    else:
        print("\nNo new videos found.")
else:
    print(f"\nSpace API failed: {data.get('message')}")
    print("Falling back to search API...")
