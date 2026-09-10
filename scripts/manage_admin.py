#!/usr/bin/env python3
import sys
import os
import argparse

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.user_models import UserDB
from app.auth import get_password_hash


def list_users():
    db = SessionLocal()
    try:
        users = db.query(UserDB).all()
        if not users:
            print("Nenhum usuário encontrado no banco de dados.")
            return
        print("\n--- Usuários Cadastrados ---")
        for u in users:
            print(f"ID: {u.id} | Usuário: {u.username} | Email: {u.email} | Role: {u.role} | Status: {u.status}")
        print("----------------------------\n")
    finally:
        db.close()


def reset_password(username, new_password):
    db = SessionLocal()
    try:
        user = db.query(UserDB).filter(
            (UserDB.username.ilike(username.strip())) | (UserDB.email.ilike(username.strip()))
        ).first()
        if not user:
            print(f"Erro: Usuário '{username}' não encontrado.")
            return False

        user.hashed_password = get_password_hash(new_password)
        user.status = "active"
        db.commit()
        print(f"Sucesso: Senha do usuário '{user.username}' (ID: {user.id}) atualizada e status definido para 'active'.")
        return True
    finally:
        db.close()


def create_admin(username, email, password):
    db = SessionLocal()
    try:
        user = db.query(UserDB).filter(
            (UserDB.username.ilike(username.strip())) | (UserDB.email.ilike(email.strip()))
        ).first()
        if user:
            print(f"Aviso: Usuário já existe ({user.username} / {user.email}). Atualizando senha...")
            user.hashed_password = get_password_hash(password)
            user.role = "admin"
            user.status = "active"
            db.commit()
            print(f"Sucesso: Usuário '{user.username}' atualizado para admin ativo com a nova senha.")
            return True

        new_user = UserDB(
            username=username.strip().lower(),
            email=email.strip().lower(),
            hashed_password=get_password_hash(password),
            full_name=username.strip(),
            role="admin",
            status="active",
            accepted_terms=True
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        print(f"Sucesso: Usuário admin '{new_user.username}' criado com sucesso (ID: {new_user.id})!")
        return True
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gerenciador de Usuários e Admin")
    parser.add_argument("--list", action="store_true", help="Listar todos os usuários")
    parser.add_argument("--reset-password", nargs=2, metavar=("USERNAME", "NEW_PASSWORD"), help="Redefinir senha de um usuário")
    parser.add_argument("--create", nargs=3, metavar=("USERNAME", "EMAIL", "PASSWORD"), help="Criar um usuário admin ativo")

    args = parser.parse_args()

    if args.list:
        list_users()
    elif args.reset_password:
        reset_password(args.reset_password[0], args.reset_password[1])
    elif args.create:
        create_admin(args.create[0], args.create[1], args.create[2])
    else:
        parser.print_help()
