import sys
import json
from sqlalchemy import create_engine
sys.path.append("app")
from app.core.database import SQLALCHEMY_DATABASE_URL

engine = create_engine(SQLALCHEMY_DATABASE_URL)
res = engine.execute("SELECT requests FROM front_recordings ORDER BY id DESC LIMIT 1").fetchone()
if res and res[0]:
    reqs = json.loads(res[0]) if isinstance(res[0], str) else res[0]
    print(f"Requests Count: {len(reqs)}")
    for r in reqs:
        print(f" - {r.get('method')} {r.get('url')} | {r.get('customNodeName')}")
else:
    print("No requests found.")
