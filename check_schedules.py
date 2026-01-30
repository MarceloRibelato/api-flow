"""
Script para verificar agendamentos ativos no banco de dados
"""
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Database URL
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"

def check_schedules():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    try:
        # Query schedules directly with SQL
        result = db.execute(text("""
            SELECT id, name, type, status, cron_expression, next_run, last_run, 
                   last_run_status, environment_id, target_id
            FROM schedules
            WHERE status = 'active'
            ORDER BY next_run
        """))
        
        schedules = result.fetchall()
        
        print(f"\n{'='*80}")
        print(f"📅 AGENDAMENTOS ATIVOS: {len(schedules)}")
        print(f"{'='*80}\n")
        
        if not schedules:
            print("⚠️  Nenhum agendamento ativo encontrado.")
            print("\nPara testar a correção, você precisa:")
            print("1. Criar um agendamento no frontend")
            print("2. Ou executar um fluxo manualmente")
            return
        
        for schedule in schedules:
            print(f"ID: {schedule.id}")
            print(f"Nome: {schedule.name}")
            print(f"Tipo: {schedule.type}")
            print(f"Status: {schedule.status}")
            print(f"Cron: {schedule.cron_expression}")
            print(f"Próxima execução: {schedule.next_run}")
            print(f"Última execução: {schedule.last_run}")
            print(f"Status última execução: {schedule.last_run_status}")
            print(f"Environment ID: {schedule.environment_id}")
            print(f"Target ID: {schedule.target_id}")
            
            if schedule.next_run:
                now = datetime.utcnow()
                time_until = schedule.next_run - now
                total_seconds = time_until.total_seconds()
                
                if total_seconds > 0:
                    minutes = int(total_seconds // 60)
                    seconds = int(total_seconds % 60)
                    print(f"⏰ Executará em: {minutes}m {seconds}s")
                else:
                    print(f"⏰ Deveria ter executado há {abs(int(total_seconds))}s")
            
            print(f"{'-'*80}\n")
    
    except Exception as e:
        print(f"❌ Erro ao consultar banco de dados: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_schedules()
