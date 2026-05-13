#!/usr/bin/env python3
"""Check comments on EP27."""
from bilibili_api import fetch_comments

# EP27 aid
aid = 116556973476831
result = fetch_comments(aid, page=1)
comments = result.get('data', {}).get('replies', []) if isinstance(result, dict) else []
print(f'Total comments: {len(comments)}')
for c in comments:
    rpid = c.get('rpid', '?')
    user = c.get('member', {}).get('uname', '?')
    msg = c.get('content', {}).get('message', '?')[:80]
    replies = c.get('rcount', 0)
    print(f'  rpid={rpid} | user={user} | replies={replies} | {msg}')
