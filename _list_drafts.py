import sys

from app.config import database_target, load_config
from app.db import Database
from app.layout import select_secondary_articles
from app.wechat.auth import WeChatAuth
from app.wechat.client import WeChatClient
from app.wechat.draft import list_draft_summaries

sys.stdout.reconfigure(encoding="utf-8")

cfg = load_config()
db = Database(database_target(cfg))
w = cfg["wechat"]
auth = WeChatAuth(w["app_id"], w["app_secret"], db)
c = WeChatClient(auth.get_access_token, lambda: auth.get_access_token(True))
rows = list_draft_summaries(c, max_items=40)
print("total", len(rows))
for i, r in enumerate(rows):
    print(f"{i+1}. count={r['article_count']} | {r['title']}")

print("--- selected secondaries ---")
secs = select_secondary_articles(c, cfg.get("layout") or {})
for i, s in enumerate(secs, start=2):
    print(f"{i}. {s.get('title')}")
