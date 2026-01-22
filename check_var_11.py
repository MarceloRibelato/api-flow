"""Verificar faker_options da variável 11"""
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT id, name, value, faker_type, faker_options 
        FROM variables 
        WHERE id = 11
    """))
    
    row = result.fetchone()
    
    if row:
        print(f"✅ Variável ID 11 no banco:")
        print(f"   Nome: {row[1]}")
        print(f"   Valor: {row[2]}")
        print(f"   faker_type: {row[3]}")
        print(f"   faker_options: {row[4]}")
        print(f"   Tipo: {type(row[4])}")
    else:
        print("❌ Não encontrada")
