#!/bin/bash
set -e

echo "[$(date -Iseconds)] Entrypoint start (RUN_MIGRATIONS=$RUN_MIGRATIONS): $@" >> /app/entrypoint.log

if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Executando migrações do banco de dados (Alembic)..."
    export PYTHONPATH=/app
    if ! alembic -c alembic.ini upgrade head > /app/alembic_error.log 2>&1; then
        echo "Aviso: alembic upgrade head falhou. Detalhes do erro:"
        cat /app/alembic_error.log
        echo "Tentando alembic stamp head..."
        alembic -c alembic.ini stamp head
        echo "Alembic stamp head concluído."
    else
        echo "Migrações do Alembic concluídas com sucesso!"
    fi
fi

exec "$@"
