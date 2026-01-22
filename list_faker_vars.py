"""Verificar todas as variáveis CPF"""
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
        WHERE faker_type IN ('cpf', 'cnpj', 'phone')
        ORDER BY id DESC
        LIMIT 10
    """))
    
    rows = result.fetchall()
    print(f"📋 Últimas 10 variáveis Faker (CPF/CNPJ/Phone):\n")
    
    for row in rows:
        print(f"ID {row[0]}: {row[1]} = {row[2]}")
        print(f"  Tipo: {row[3]}")
        print(f"  Options: {row[4]}")
        print()
