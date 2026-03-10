from app.main import app
import json

for route in app.routes:
    if hasattr(route, "path"):
        methods = getattr(route, "methods", [])
        print(f"Path: {route.path} | Methods: {list(methods)}")
