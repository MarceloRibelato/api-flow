from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text

engine = create_engine('postgresql://admin:admin@localhost:5432/flow_db')
Session = sessionmaker(bind=engine)
session = Session()

res = session.execute(text("SELECT db_id, api_calls FROM flow_card_data limit 10"))
for row in res:
    print(f"ID: {row[0]}")
    print(f"Type of api_calls: {type(row[1])}")
    print(f"Value: {str(row[1])[:100]}")
