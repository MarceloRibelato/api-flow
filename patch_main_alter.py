import re

with open("app/main.py", "r") as f:
    content = f.read()

alter_code = """
    # AUTO-MIGRATE: Add force_real_api to service_mocks
    try:
        with engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("ALTER TABLE service_mocks ADD COLUMN force_real_api BOOLEAN DEFAULT FALSE NOT NULL;"))
            logger.info("✅ Coluna force_real_api adicionada na tabela service_mocks.")
    except Exception as e:
        if "already exists" not in str(e).lower() and "já existe" not in str(e).lower():
            logger.warning(f"Aviso ao tentar alterar service_mocks (force_real_api): {e}")

"""

# Insert right after: logger.info("Tabelas verificadas/criadas via metadata sqlalchemy")
target_string = 'logger.info("Tabelas verificadas/criadas via metadata sqlalchemy")'
if target_string in content and "force_real_api" not in content:
    content = content.replace(target_string, target_string + "\n" + alter_code)
    with open("app/main.py", "w") as f:
        f.write(content)
    print("app/main.py patched successfully!")
else:
    print("Could not find target string or already patched.")
