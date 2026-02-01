
from app.database import SessionLocal
from app.models.product_models import ProductModel

def check_urls():
    db = SessionLocal()
    products = db.query(ProductModel).all()
    print(f"Found {len(products)} products.")
    print(f"Found {len(products)} products.")
    for p in products:
        try:
            print(f"Product: {p.id}, {p.name}, URL: {p.notification_urls}")
        except Exception as e:
            print(f"Error reading product {getattr(p, 'id', '?')}: {e}")
    print("Done.")
    db.close()

if __name__ == "__main__":
    check_urls()
