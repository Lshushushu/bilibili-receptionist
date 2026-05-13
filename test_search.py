#!/usr/bin/env python3
"""Test search API."""
import requests
import json

with open('config.json', 'r') as f:
    config = json.load(f)
cookies = config.get('cookies', {})

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com',
})
session.cookies.set('SESSDATA', cookies['SESSDATA'], domain='.bilibili.com')
session.cookies.set('bili_jct', cookies['bili_jct'], domain='.bilibili.com')
session.cookies.set('DedeUserID', cookies['DedeUserID'], domain='.bilibili.com')

url = 'https://api.bilibili.com/x/web-interface/search/type'
params = {
    'keyword': '阿树__atree',
    'search_type': 'video',
    'page': 1,
    'pagesize': 30,
    'order': 'pubdate',
}

resp = session.get(url, params=params, timeout=15)
print(f'Status: {resp.status_code}')
data = resp.json()
print(f'Code: {data.get("code")}')
print(f'Message: {data.get("message")}')

if data.get('code') == 0:
    results = data.get('data', {}).get('result', [])
    print(f'Videos found: {len(results)}')
    for v in results[:5]:
        print(f'  {v.get("bvid")} | {v.get("title", "?")[:50]}')
