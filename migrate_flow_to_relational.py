import json
import logging
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models.flow_models import FlowDB, FlowNodeDB, FlowEdgeDB, FlowCardDataDB

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_flows():
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    db: Session = SessionLocal()
    try:
        flows = db.query(FlowDB).all()
        logger.info(f"Found {len(flows)} flows to migrate.")

        for flow in flows:
            logger.info(f"Migrating Flow ID: {flow.id} (Project {flow.project_id})")
            
            # --- NODES ---
            nodes_data = flow.nodes
            if isinstance(nodes_data, str):
                try:
                    nodes_data = json.loads(nodes_data)
                except json.JSONDecodeError:
                    nodes_data = []
            elif nodes_data is None:
                nodes_data = []
            
            # Clear existing relational data for this flow to support re-runs
            db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == flow.id).delete()
            db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == flow.id).delete()
            db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow.id).delete()
            
            for node in nodes_data:
                # Handle potential string-encoded items in list (legacy mess)
                if isinstance(node, str):
                    try:
                        node = json.loads(node)
                    except:
                        continue
                
                # Extract basic data
                node_id = node.get("id")
                if not node_id:
                    continue
                
                # Extract position and size
                pos = node.get("position", {})
                
                # Extract data payload
                data_payload = node.get("data", {})
                
                new_node = FlowNodeDB(
                    id=str(node_id),
                    flow_id=flow.id,
                    client_id=str(node_id),
                    type=node.get("type", "custom"),
                    position_x=float(pos.get("x", 0)),
                    position_y=float(pos.get("y", 0)),
                    width=float(node.get("width")) if node.get("width") else None,
                    height=float(node.get("height")) if node.get("height") else None,
                    parent_node_id=node.get("parentNode"),
                    data=data_payload
                )
                db.add(new_node)
            
            # --- EDGES ---
            edges_data = flow.edges
            if isinstance(edges_data, str):
                try:
                    edges_data = json.loads(edges_data)
                except:
                    edges_data = []
            elif edges_data is None:
                edges_data = []

            for edge in edges_data:
                if isinstance(edge, str):
                    try:
                        edge = json.loads(edge)
                    except:
                        continue
                        
                edge_id = edge.get("id")
                if not edge_id:
                    continue
                    
                new_edge = FlowEdgeDB(
                    flow_id=flow.id,
                    client_id=str(edge_id),
                    source=str(edge.get("source")),
                    target=str(edge.get("target")),
                    type=str(edge.get("type", "buttonedge")),
                    animated=bool(edge.get("animated", False))
                )
                db.add(new_edge)

            # --- CARD DATA ---
            card_data_raw = flow.card_data
            if isinstance(card_data_raw, str):
                try:
                    card_data_raw = json.loads(card_data_raw)
                except:
                    card_data_raw = {}
            elif card_data_raw is None:
                card_data_raw = {}
            
            for cid, cinfo in card_data_raw.items():
                if isinstance(cinfo, str):
                    try:
                        cinfo = json.loads(cinfo)
                    except:
                        cinfo = {}
                
                new_card = FlowCardDataDB(
                    flow_id=flow.id,
                    node_id=str(cid),
                    name=cinfo.get("name", ""),
                    description=cinfo.get("description", ""),
                    color=cinfo.get("color", "#10b981"),
                    bdd_scenarios=cinfo.get("bddScenarios", []),
                    api_calls=cinfo.get("apiCalls", []),
                    env_data=cinfo.get("envData", {})
                )
                db.add(new_card)

        db.commit()
        logger.info("Migration completed successfully.")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate_flows()
