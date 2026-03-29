import psycopg2

def migrate():
    print("Conectando ao banco de dados...")
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    cur = conn.cursor()
    
    try:
        print("Adicionando coluna...")
        cur.execute("ALTER TABLE products ADD COLUMN platform VARCHAR(20) DEFAULT 'web'")
        conn.commit()
        print("Coluna adicionada com sucesso!")
    except Exception as e:
        print("Erro (provavelmente já existe):", e)
        conn.rollback()
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    migrate()
