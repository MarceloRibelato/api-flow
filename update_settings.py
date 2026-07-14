from app.database import SessionLocal
from app.models.agent_models import AgentSettingsDB

db = SessionLocal()
user_id = 1 # Assuming default user is 1

settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
if not settings:
    settings = AgentSettingsDB(user_id=user_id)
    db.add(settings)

settings.ai_provider = "openai"
settings.ai_base_url = "http://host.docker.internal:3000/api"
settings.ai_api_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6ImIyNjg4MWExLTYyYWEtNDM2ZS05ODBlLTU1ODMyY2ZkYzAzMyIsImV4cCI6MTc4NjI5MDk0MywianRpIjoiOTczNjczY2QtM2IyOC00NWM5LWExMDQtYTQzYzlmY2Q4NGNiIiwiaWF0IjoxNzgzODcxNzQzfQ.GsJxFXT4hXoQFTGH1qMNS-OU4gDCe4Lj866CADQscuk"
settings.ai_model = "Qwen-Especialista-Flow" # Or default to qwen2.5:14b if they didn't create it exactly with that name

db.commit()
print("Settings updated successfully!")
db.close()
