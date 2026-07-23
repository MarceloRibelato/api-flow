import sys
import json
sys.path.append("/home/server/Desktop/Projetos/Flow/api-flow")
from sqlalchemy import create_engine
engine = create_engine("postgresql://root:root@localhost:5432/flowdb")
with engine.connect() as conn:
    res = conn.execute("SELECT id, node_name, method, url, status_code, error_message FROM execution_history ORDER BY created_at DESC LIMIT 10;")
    print("Execution History:")
    for row in res:
        print(row)
