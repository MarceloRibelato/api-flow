"""
Atualizar variáveis Faker antigas que não têm faker_options
Define formatted=false para variáveis que têm valor sem formatação
"""
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
import json
import re

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def is_formatted_cpf(value):
    """Verifica se o CPF está formatado (xxx.xxx.xxx-xx)"""
    return bool(re.match(r'^\d{3}\.\d{3}\.\d{3}-\d{2}$', value))

def is_formatted_cnpj(value):
    """Verifica se o CNPJ está formatado (xx.xxx.xxx/xxxx-xx)"""
    return bool(re.match(r'^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$', value))

def is_formatted_phone(value):
    """Verifica se o telefone está formatado"""
    # Pode ser +55 (11) 99999-9999 ou (11) 99999-9999
    return bool(re.match(r'^(\+\d{2}\s)?\(\d{2}\)\s\d{4,5}-\d{4}$', value))

with engine.connect() as conn:
    # Buscar todas as variáveis faker sem faker_options
    result = conn.execute(text("""
        SELECT id, name, value, faker_type 
        FROM variables 
        WHERE type = 'faker' AND faker_options IS NULL
    """))
    
    rows = result.fetchall()
    print(f"📋 Encontradas {len(rows)} variáveis Faker sem faker_options\n")
    
    for row in rows:
        var_id, name, value, faker_type = row
        
        # Determinar se está formatado baseado no valor atual
        formatted = True  # Default
        country_code = True  # Default para phone
        
        if faker_type == 'cpf':
            formatted = is_formatted_cpf(value or '')
        elif faker_type == 'cnpj':
            formatted = is_formatted_cnpj(value or '')
        elif faker_type == 'phone':
            formatted = is_formatted_phone(value or '')
            country_code = '+' in (value or '')
        
        # Criar faker_options baseado na análise
        if faker_type in ['cpf', 'cnpj']:
            faker_options = {"formatted": formatted}
        elif faker_type == 'phone':
            faker_options = {"formatted": formatted, "countryCode": country_code}
        else:
            # Outros tipos de faker não precisam de options
            faker_options = None
        
        if faker_options:
            faker_options_json = json.dumps(faker_options)
            conn.execute(text(f"""
                UPDATE variables 
                SET faker_options = :options 
                WHERE id = :id
            """), {"options": faker_options_json, "id": var_id})
            conn.commit()
            
            print(f"✅ ID {var_id}: {name} ({faker_type})")
            print(f"   Valor: {value}")
            print(f"   Options: {faker_options}")
            print()

print("✨ Atualização concluída!")
