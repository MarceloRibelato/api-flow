"""
Direct test of user creation to see the actual error
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.schemas.auth_schemas import UserCreate
from app.services.auth_service import AuthService

def test_create_user():
    db = SessionLocal()
    try:
        user_data = UserCreate(
            username="bruna.ribelato@gmail.com",
            email="bruna.ribelato@gmail.com",
            full_name="Bruna Ribelato",
            cpf="321.432.432-42",
            company="Veloe GO",
            cnpj="24.234.324/3243-24",
            phone="(19) 98962-4096",
            password="test123"
        )
        
        print("Creating user...")
        user = AuthService.create_user(db, user_data)
        print(f"SUCCESS User created! ID: {user.id}")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        print(f"ERROR TYPE: {type(e).__name__}")
        import traceback
        for line in traceback.format_exc().split('\n'):
            print(line)
    finally:
        db.close()

if __name__ == "__main__":
    test_create_user()
