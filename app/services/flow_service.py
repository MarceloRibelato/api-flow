import json
from datetime import datetime
from sqlalchemy.orm import Session, joinedload

from app.models.flow_models import FlowDB, FlowNodeDB, FlowEdgeDB, FlowCardDataDB, FlowE2EStepDB
from app.schemas.flow_schemas import FlowSaveSchema
from app.models.feature_models import FeatureModel
from app.models.product_models import ProductModel


class FlowService:
    @staticmethod
    def _verify_project_ownership(db: Session, project_id: int, company_id: int):
        # Implementation remains the same
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
                # Note: This technically still triggers lazy load if we don't join, 
                # but for list view usually we don't need full details. 
                # Improving this would require a group_by query, but let's stick to the main load/save optim.
                "nodes_count": len(f.flow_nodes), 
                "edges_count": len(f.flow_edges)
            } for f in flows
        ]

    @staticmethod
    def create(db: Session, project_id: int, name: str, company_id: int):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            return None

        new_flow = FlowDB(project_id=project_id, name=name)
        db.add(new_flow)
        db.commit()
        db.refresh(new_flow)
        return new_flow

    @staticmethod
    def load(db: Session, project_id: int, company_id: int, flow_id: int = None, flow_type: str = "api"):
        if not FlowService._verify_project_ownership(db, project_id, company_id):  
             return {"nodes": [], "edges": [], "cardData": {}, "id": None, "name": "", "project_id": project_id}

        query = db.query(FlowDB).options(
            joinedload(FlowDB.flow_nodes),
            joinedload(FlowDB.flow_edges),
            joinedload(FlowDB.flow_card_data).joinedload(FlowCardDataDB.e2e_steps_rel)
        )

        if flow_id:
            flow = query.filter(FlowDB.id == flow_id).first()
        else:
            # Migration/Fallback: Load the most recently updated flow of the SPECIFIC TYPE for the project
            flow = query.filter(FlowDB.project_id == project_id, FlowDB.flow_type == flow_type).order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            print(f"❌ FlowService.load: Flow not found for project_id={project_id}, flow_id={flow_id}")
            return {"nodes": [], "edges": [], "cardData": {}, "id": None, "name": "", "project_id": project_id}

        print(f"✅ FlowService.load: Loaded Flow ID={flow.id} Name='{flow.name}'")

        # Fetch Relational Data (Now eager loaded)
        db_nodes = flow.flow_nodes
        db_edges = flow.flow_edges
        db_cards = flow.flow_card_data

        # --- Reconstruct Nodes ---
        formatted_nodes = []
        if db_nodes:
            for node in db_nodes:
                formatted_node = {
                    "id": node.client_id,
                    "type": node.type,
                    "position": {"x": node.position_x, "y": node.position_y},
                    "width": node.width,
                    "height": node.height,
                    "parentNode": node.parent_node_id,
                    "data": node.data or {},
                    "hidden": node.data.get("hidden", False) if node.data else False
                }
                formatted_nodes.append(formatted_node)
        elif flow.nodes:
            # Fallback to legacy JSON
            formatted_nodes = flow.nodes if isinstance(flow.nodes, list) else []

        # --- Reconstruct Edges ---
        formatted_edges = []
        if db_edges:
            for edge in db_edges:
                formatted_edge = {
                    "id": edge.client_id,
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                    "animated": edge.animated
                }
                formatted_edges.append(formatted_edge)
        elif flow.edges:
            # Fallback to legacy JSON
            formatted_edges = flow.edges if isinstance(flow.edges, list) else []

        # --- Reconstruct Card Data ---
        card_data = {}
        if db_cards:
            for card in db_cards:
                node_id = card.node_id
                card_data[node_id] = {
                    "name": card.name,
                    "description": card.description,
                    "color": card.color,
                    "bddScenarios": card.bdd_scenarios or [],
                    "apiCalls": card.api_calls or [],
                    "e2eSteps": [
                        {
                            "id": step.client_id,
                            "type": step.type,
                            "name": step.name,
                            "description": step.description,
                            "properties": step.properties
                        } for step in card.e2e_steps_rel
                    ] if card.e2e_steps_rel else (card.e2e_steps or []),
                    "envData": card.env_data or {}
                }
        elif flow.card_data:
            # Fallback to legacy JSON
            # Legacy card_data is usually a dict {node_id: {data}}
            card_data = flow.card_data if isinstance(flow.card_data, dict) else {}

        # Fetch Product ID from Feature/Project
        product_id = None
        feature = db.query(FeatureModel).filter(FeatureModel.id == flow.project_id).first()
        if feature:
             product_id = feature.product_id

        return {
            "id": flow.id,
            "project_id": flow.project_id,
            "product_id": product_id, 
            "name": flow.name,
            "flow_type": flow.flow_type,
            "nodes": formatted_nodes,
            "edges": formatted_edges,
            "cardData": card_data,
        }

    @staticmethod
    def save(db: Session, data: FlowSaveSchema, company_id: int):
        if not FlowService._verify_project_ownership(db, data.projectId, company_id):
             raise ValueError("Projeto não pertence à sua empresa")

        project_id = data.projectId
        flow_id = data.flowId
        nodes = data.nodes
        edges = data.edges
        card_data = data.cardData

        flow = None
        if flow_id:
             flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        
        if not flow:
             # Try find SPECIFIC flow type for project to update
             flow = db.query(FlowDB).filter(FlowDB.project_id == project_id, FlowDB.flow_type == data.flow_type).order_by(FlowDB.updated_at.desc()).first()

        if not flow:
             flow = FlowDB(project_id=project_id, name=data.name or "Fluxo Principal", flow_type=data.flow_type)
             db.add(flow)
             db.commit()
             db.refresh(flow)
        
        # Update metadata if provided
        if data.name:
            flow.name = data.name
        
        flow.updated_at = datetime.utcnow()
        flow_id = flow.id

        # 2. Clear existing data (Full Replace Strategy)
        # ⚠️ We MUST delete E2E steps FIRST because of FK card_db_id -> flow_card_data.db_id
        # query.delete() bypasses ORM cascade, so we do it explicitly.
        print(f"🧹 FlowService.save: Cleaning up old data for Flow ID {flow_id}")
        
        # Delete steps related to any card in this flow
        existent_card_ids = [c.db_id for c in db.query(FlowCardDataDB.db_id).filter(FlowCardDataDB.flow_id == flow_id).all()]
        if existent_card_ids:
            db.query(FlowE2EStepDB).filter(FlowE2EStepDB.card_db_id.in_(existent_card_ids)).delete(synchronize_session=False)

        db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).delete(synchronize_session=False)
        db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == flow_id).delete(synchronize_session=False)
        db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == flow_id).delete(synchronize_session=False)

        # 3. Batch Insert Preparation
        print(f"🛠️ FlowService.save: Inserting {len(nodes)} nodes, {len(edges)} edges and {len(card_data)} cards for Flow ID {flow_id}")
        
        nodes_to_insert = []
        edges_to_insert = []
        cards_to_insert = []

        # Nodes
        for node in nodes:
            node_data = node.data.model_dump()
            db_node = FlowNodeDB(
                flow_id=flow_id,
                client_id=node.id,
                type=node.type,
                position_x=node.position['x'],
                position_y=node.position['y'],
                width=node.width,
                height=node.height,
                parent_node_id=node.parentNode,
                data={
                    "name": node_data.get("name"),
                    "color": node_data.get("color"),
                    "childCount": node_data.get("childCount"),
                    "isCollapsed": node_data.get("isCollapsed"),
                    "hidden": node.hidden
                }
            )
            nodes_to_insert.append(db_node)

        # Edges
        for edge in edges:
            db_edge = FlowEdgeDB(
                client_id=edge.id, 
                flow_id=flow_id,
                source=edge.source,
                target=edge.target,
                type=edge.type,
                animated=edge.animated
            )
            edges_to_insert.append(db_edge)

        # Cards
        for cid, cinfo in card_data.items():
            api_calls_payload = [api.model_dump() for api in cinfo.apiCalls]
            
            db_card = FlowCardDataDB(
                node_id=cid,
                flow_id=flow_id,
                name=cinfo.name,
                description=cinfo.description,
                color=cinfo.color,
                bdd_scenarios=cinfo.bddScenarios,
                api_calls=api_calls_payload,
                e2e_steps=cinfo.e2eSteps or [],
                env_data={k: v.model_dump() for k, v in cinfo.envData.items()}
            )
            print(f"   - Card {cid}: {len(cinfo.apiCalls)} APIs, {len(cinfo.e2eSteps or [])} E2E Steps")
            cards_to_insert.append(db_card)

        # 4. Bulk Save
        if nodes_to_insert:
            db.bulk_save_objects(nodes_to_insert)
        if edges_to_insert:
            db.bulk_save_objects(edges_to_insert)
        if cards_to_insert:
            # Use add_all + flush instead of bulk_save_objects 
            # so that db_id is populated back into the objects for the steps below
            db.add_all(cards_to_insert)
            db.flush() 
            
            # 3.1 Insert E2E Steps for each card
            steps_to_insert = []
            for db_card in cards_to_insert:
                fe_card = card_data.get(db_card.node_id)
                if fe_card and fe_card.e2eSteps:
                    print(f"   - Relational steps for {db_card.node_id}: {len(fe_card.e2eSteps)}")
                    for i, step in enumerate(fe_card.e2eSteps):
                        # 'step' is usually a dict from JSON from frontend
                        # depending on schema validation.
                        db_step = FlowE2EStepDB(
                            card_db_id=db_card.db_id,
                            client_id=step.get('id'),
                            type=step.get('type'),
                            name=step.get('name'),
                            description=step.get('description'),
                            properties=step.get('properties', {}),
                            order=i
                        )
                        steps_to_insert.append(db_step)
            
            if steps_to_insert:
                db.bulk_save_objects(steps_to_insert)

        db.commit()

        return {
            "status": "saved",
            "message": "Fluxo salvo com sucesso",
            "flowId": flow_id,
            "projectId": project_id,
            "nodesCount": len(nodes),
            "edgesCount": len(edges),
            "cardsCount": len(card_data),
        }

    # get_stats and delete need update too? 
    # get_stats likely by project or flow? User requested flow stats usually.
    # But usually dashboard shows Project stats.
    # For now, let's leave get_stats aggregating the LATEST flow or refactor?
    # Refactor get_stats to take flow_id preferred, but keep current sig compatible?

    @staticmethod
    def get_stats(db: Session, project_id: int):
        # Return stats for ALL flows combined or list of flows?
        # Legacy: returned single object.
        # Let's return stats for the LATEST flow.
        flow = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            return {
                "exists": False,
                "message": "Nenhum fluxo encontrado para este projeto",
            }
        
        # ... logic as before but using 'flow' object ...
        # Can copy paste existing logic but use 'flow'
        nodes_count = len(flow.flow_nodes)
        edges_count = len(flow.flow_edges)
        cards_count = len(flow.flow_card_data)
        
        total_bdd = 0
        total_api = 0
        
        for card in flow.flow_card_data:
             total_bdd += len(card.bdd_scenarios or [])
             total_api += len(card.api_calls or [])

        return {
            "exists": True,
            "projectId": project_id,
            "nodes": nodes_count,
            "edges": edges_count,
            "cards": cards_count,
            "bddScenarios": total_bdd,
            "apiCalls": total_api,
            "lastUpdated": flow.updated_at,
            "flowCount": db.query(FlowDB).filter(FlowDB.project_id == project_id).count() 
        }

    @staticmethod
    def delete(db: Session, project_id: int, company_id: int):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            return False

        # DELETE ALL FLOWS FOR PROJECT
        flows = db.query(FlowDB).filter(FlowDB.project_id == project_id).all()
        if not flows:
            return False
            
        for flow in flows:
            db.delete(flow)
        
        db.commit()
        return True
