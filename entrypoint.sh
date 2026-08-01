#!/bin/bash
set -e

if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Executando migrações do banco de dados (Alembic)..."
    export PYTHONPATH=/app
    if ! alembic -c alembic.ini upgrade head > /app/alembic_error.log 2>&1; then
        echo "Migração falhou (possivelmente devido a tabelas já criadas via create_all). Executando alembic stamp head..."
        alembic -c alembic.ini stamp head
    fi
fi

exec "$@"
