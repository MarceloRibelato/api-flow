import json
from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.schedule_models import ScheduleModel
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB

db = SessionLocal()

data = {"schedules": [], "history": [], "link_check": []}

schedules = db.query(ScheduleModel).order_by(ScheduleModel.id.desc()).limit(5).all()
for s in schedules:
    data["schedules"].append({
        "id": s.id,
        "name": s.name, 
        "type": s.type, 
        "target_id": s.target_id, 
        "status": s.last_run_status
    })

history = db.query(ApiExecutionHistory).order_by(ApiExecutionHistory.id.desc()).limit(10).all()
for h in history:
    data["history"].append({
        "id": h.id, 
        "schedule_id": h.schedule_id, 
        "api_name": h.api_name, 
        "status": h.status_code
    })

if schedules:
    last_sched_id = schedules[0].id
    linked_hist = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.schedule_id == last_sched_id).all()
    data["link_check"] = {
        "schedule_id": last_sched_id, 
        "count": len(linked_hist)
    }

# Check Features and Flows for Product 1
features = db.query(FeatureModel).filter(FeatureModel.product_id == 1).all()
data["features"] = []
for f in features:
    flow_count = db.query(FlowDB).filter(FlowDB.project_id == f.id).count()
    data["features"].append({
        "id": f.id,
        "name": f.name,
        "flow_count": flow_count
    })

with open("debug_result.json", "w") as f:
    json.dump(data, f, indent=2)

db.close()


