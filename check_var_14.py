import sqlite3
import sys

def check_db():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

    conn = sqlite3.connect('flow.db')
    cursor = conn.cursor()
    
    print("--- Checking Variables ---")
    cursor.execute("SELECT id, name, value, faker_type, faker_options FROM variables ORDER BY id DESC LIMIT 20")
    
    rows = cursor.fetchall()
    for row in rows:
        print(f"ID: {row[0]}")
        print(f"Name: {repr(row[1])}")
        print(f"B_Type: {repr(row[3])}")
        print(f"B_Options: {repr(row[4])}")
        print("-" * 20)

    conn.close()

if __name__ == "__main__":
    check_db()
