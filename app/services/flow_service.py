import json
from datetime import datetime
from sqlalchemy.orm import Session, joinedload

from app.models.flow_models import FlowDB, FlowNodeDB, FlowEdgeDB, FlowCardDataDB, FlowE2EStepDB
from app.schemas.flow_schemas import FlowSaveSchema
from app.models.feature_models import FeatureModel

class FlowService:
    @staticmethod
    def _verify_project_ownership(db: Session, project_id: int, company_id: int) -> bool:
        from app.models.product_models import ProductModel
        feature = db.query(FeatureModel).join(ProductModel).filter(
            FeatureModel.id == project_id,
            ProductModel.company_id == company_id
        ).first()
        return feature is not None

    @staticmethod
    def list_by_project(db: Session, project_id: int, company_id: int):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            return []
        
        flows = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).all()
        return [
            {
                "id": f.id,
                "project_id": f.project_id,
                "name": f.name,
                "flow_type": f.flow_type,
                "created_at": f.created_at,
                "updated_at": f.updated_at,
                "nodes_count": len(f.flow_nodes),
                "edges_count": len(f.flow_edges)
            } for f in flows
        ]

    @staticmethod
    def create(db: Session, project_id: int, name: str, company_id: int, flow_type: str = "api"):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            return None
        
        new_flow = FlowDB(
            project_id=project_id,
            name=name,
            flow_type=flow_type,
            company_id=company_id,
            user_id=1 
        )
        db.add(new_flow)
        db.commit()
        db.refresh(new_flow)
        return {
            "id": new_flow.id,
            "project_id": new_flow.project_id,
            "name": new_flow.name,
            "status": "created"
        }

    @staticmethod
    def save(db: Session, data: FlowSaveSchema, company_id: int, user_id: int):
        if not FlowService._verify_project_ownership(db, data.projectId, company_id):
             raise ValueError("Projeto não pertence à sua empresa")

        # 1. Get or Create Flow
        flow = db.query(FlowDB).filter(
            FlowDB.project_id == data.projectId, 
            FlowDB.flow_type == data.flow_type
        ).first()
        
        if not flow:
            flow = FlowDB(
                project_id=data.projectId,
                name=f"Flow {str(data.flow_type).upper()}",
                flow_type=data.flow_type,
                company_id=company_id,
                user_id=user_id
            )
            db.add(flow)
            db.flush()

        # 2. Sync Logic (Proper Deletion Order to avoid FK Violations)
        # We delete all children (steps) first, then parents (cards, nodes, edges)
        # Using a subquery for steps ensures we catch all references even if the session is out of sync
        db.query(FlowE2EStepDB).filter(
            FlowE2EStepDB.card_db_id.in_(
                db.query(FlowCardDataDB.db_id).filter(FlowCardDataDB.flow_id == flow.id)
            )
        ).delete(synchronize_session=False)
        
        db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == flow.id).delete(synchronize_session=False)
        db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == flow.id).delete(synchronize_session=False)
        db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow.id).delete(synchronize_session=False)
        
        # Flush to ensure all deletions are processed by the DB before we start adding new ones
        db.flush()

        # 3. Add Nodes
        for n in (data.nodes or []):
            node_data = n.data
            if hasattr(node_data, 'model_dump'):
                node_data = node_data.model_dump()
            elif hasattr(node_data, 'dict'):
                node_data = node_data.dict()

            node_db = FlowNodeDB(
                flow_id=flow.id,
                client_id=str(n.id),
                type=n.type,
                position_x=n.position.get('x', 0) if isinstance(n.position, dict) else 0,
                position_y=n.position.get('y', 0) if isinstance(n.position, dict) else 0,
                data=node_data
            )
            db.add(node_db)

        # 4. Add Edges
        for e in (data.edges or []):
            edge_db = FlowEdgeDB(
                flow_id=flow.id,
                client_id=str(e.id),
                source=str(e.source),
                target=str(e.target),
                type=e.type,
                animated=e.animated
            )
            db.add(edge_db)

        # 5. Add CardData
        for nid, c in (data.cardData or {}).items():
            # Ensure complex fields are converted to dict/list for JSON storage
            api_calls = []
            if c.apiCalls:
                for step in c.apiCalls:
                    if hasattr(step, 'model_dump'): api_calls.append(step.model_dump())
                    else: api_calls.append(step)

            env_data_dict = {}
            if hasattr(c, 'envData') and c.envData:
                for env_name, env_val in c.envData.items():
                    if hasattr(env_val, 'model_dump'): env_data_dict[env_name] = env_val.model_dump()
                    else: env_data_dict[env_name] = env_val

            card_db = FlowCardDataDB(
                flow_id=flow.id,
                node_id=str(nid),
                name=c.name,
                color=c.color,
                description=c.description,
                bdd_scenarios=c.bddScenarios or [],
                api_calls=api_calls,
                env_data=env_data_dict
            )
            db.add(card_db)
            db.flush()

            # Add E2E Steps
            e2e_steps_list = getattr(c, 'e2eSteps', [])
            if e2e_steps_list:
                for idx, step in enumerate(e2e_steps_list):
                    step_db = FlowE2EStepDB(
                        card_db_id=card_db.db_id,
                        client_id=step.get('id', str(idx)),
                        type=step.get('type', 'action'),
                        name=step.get('name', f'Step {idx}'),
                        properties=step.get('properties', {}),
                        order=idx
                    )
                    db.add(step_db)

        flow.updated_at = datetime.utcnow()
        db.commit()
        return {"id": flow.id, "project_id": flow.project_id, "status": "saved"}

    @staticmethod
    def load(db: Session, project_id: int, company_id: int, flow_id: int = None, flow_type: str = "api"):
        query = db.query(FlowDB).filter(FlowDB.project_id == project_id)
        if flow_id:
            query = query.filter(FlowDB.id == flow_id)
        else:
            query = query.filter(FlowDB.flow_type == flow_type).order_by(FlowDB.updated_at.desc())
            
        flow = query.first()
        if not flow:
            return {"project_id": project_id, "nodes": [], "edges": [], "cardData": {}, "flow_type": flow_type}

        # Reconstruct JSON
        nodes = [{
            "id": n.client_id,
            "type": n.type,
            "position": {"x": n.position_x, "y": n.position_y},
            "data": n.data
        } for n in flow.flow_nodes]

        edges = [{
            "id": e.client_id,
            "source": e.source,
            "target": e.target,
            "type": e.type,
            "animated": e.animated
        } for e in flow.flow_edges]

        cardData = {}
        for c in flow.flow_card_data:
            steps = []
            if c.e2e_steps_rel:
                steps = [{
                    "id": s.client_id,
                    "type": s.type,
                    "name": s.name,
                    "properties": s.properties
                } for s in sorted(c.e2e_steps_rel, key=lambda x: x.order)]
            
            cardData[c.node_id] = {
                "name": c.name,
                "color": c.color,
                "description": c.description,
                "bddScenarios": c.bdd_scenarios or [],
                "apiCalls": c.api_calls or [],
                "e2eSteps": steps,
                "envData": c.env_data or {}
            }

        return {
            "id": flow.id,
            "project_id": flow.project_id,
            "name": flow.name,
            "flow_type": flow.flow_type,
            "nodes": nodes,
            "edges": edges,
            "cardData": cardData,
            "updated_at": flow.updated_at
        }

    @staticmethod
    def get_stats(db: Session, project_id: int):
        flow = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).first()
        if not flow:
            return {"exists": False, "message": "Nenhum fluxo encontrado"}
        
        return {
            "exists": True,
            "projectId": project_id,
            "nodes": len(flow.flow_nodes),
            "edges": len(flow.flow_edges),
            "cards": len(flow.flow_card_data),
            "bddScenarios": sum(len(c.bdd_scenarios or []) for c in flow.flow_card_data),
            "apiCalls": sum(len(c.api_calls or []) for c in flow.flow_card_data),
            "lastUpdated": flow.updated_at
        }

    @staticmethod
    def delete(db: Session, project_id: int, company_id: int):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            return False
        
        flows = db.query(FlowDB).filter(FlowDB.project_id == project_id).all()
        if not flows:
            return False
        
        for f in flows:
            # 1. Delete E2E steps first (child of flow_card_data) using a subquery
            db.query(FlowE2EStepDB).filter(
                FlowE2EStepDB.card_db_id.in_(
                    db.query(FlowCardDataDB.db_id).filter(FlowCardDataDB.flow_id == f.id)
                )
            ).delete(synchronize_session=False)
            
            # 2. Delete cards, nodes, edges
            db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == f.id).delete(synchronize_session=False)
            db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == f.id).delete(synchronize_session=False)
            db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == f.id).delete(synchronize_session=False)
            
            # 4. Finally delete the flow itself
            db.delete(f)
        
        db.commit()
        return True

    @staticmethod
    def get_cards_inventory(db: Session, company_id: int):
        """Retorna uma lista de todos os cards configurados na empresa para reaproveitamento"""
        cards = db.query(FlowCardDataDB).join(FlowDB).filter(FlowDB.company_id == company_id).all()
        
        inventory = []
        seen = set()
        
        for c in cards:
            raw_type = (c.flow.flow_type if c.flow else None) or "api"

            # Normalize: keep 'mobile' as-is; map web/frontend/e2e → 'web'; rest → 'api'
            if raw_type == "mobile":
                f_type = "mobile"
            elif raw_type in ("web", "frontend", "e2e"):
                f_type = "web"
            else:
                f_type = "api"

            # Simple deduplication by Name, Type and Description
            card_key = (c.name or "", f_type, c.description or "")
            if card_key in seen:
                continue
            seen.add(card_key)

            inventory.append({
                "id": c.db_id,
                "name": c.name,
                "description": c.description,
                "color": c.color,
                "bddScenarios": c.bdd_scenarios,
                "apiCalls": c.api_calls,
                "e2eSteps": c.e2e_steps,  # mobile flows also use e2e_steps field
                "envData": c.env_data,
                "flowType": f_type,
                "sourceFlow": c.flow.name if c.flow else "Desconhecido"
            })
        return inventory

