import json; import os; from sqlalchemy import create_engine, text
db_url = os.getenv("DATABASE_URL", "postgresql://admin:admin@db:5432/flow_db")
e = create_engine(db_url)
with e.connect() as c:
    r = c.execute(text("SELECT node_id, api_calls FROM flow_card_data WHERE flow_id=4"))
    for row in r:
        print(f"--- NODE: {row[0]} ---")
        print(json.dumps(row[1], indent=2))
