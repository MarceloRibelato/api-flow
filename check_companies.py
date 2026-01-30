"""
Check companies in database
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models.company_models import CompanyDB

def check_companies():
    db = SessionLocal()
    try:
        companies = db.query(CompanyDB).all()
        print(f"Total companies: {len(companies)}")
        for company in companies:
            print(f"  - ID: {company.id}, Name: {company.name}, CNPJ: {company.cnpj}")
    finally:
        db.close()

if __name__ == "__main__":
    check_companies()
