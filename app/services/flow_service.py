import json
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.flow_models import FlowDB
from app.schemas.flow_schemas import FlowSaveSchema


class FlowService:
    @staticmethod
    def list_by_project(db: Session, project_id: int):
        flows = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).all()
        return [
            {
                "id": f.id,
                "project_id": f.project_id,
                "name": f.name,
                "created_at": f.created_at,
                "updated_at": f.updated_at,
                "nodes_count": len(f.flow_nodes),
                "edges_count": len(f.flow_edges)
            } for f in flows
        ]

    @staticmethod
    def create(db: Session, project_id: int, name: str):
        new_flow = FlowDB(project_id=project_id, name=name)
        db.add(new_flow)
        db.commit()
        db.refresh(new_flow)
        return new_flow

    @staticmethod
    def load(db: Session, project_id: int, flow_id: int = None):
        if flow_id:
            flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        else:
            # Migration/Fallback: Load the most recently updated flow for the project
            flow = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            print(f"❌ FlowService.load: Flow not found for project_id={project_id}, flow_id={flow_id}")
            return {"nodes": [], "edges": [], "cardData": {}, "id": None, "name": ""}

        print(f"✅ FlowService.load: Loaded Flow ID={flow.id} Name='{flow.name}'")

        # Fetch Relational Data
        db_nodes = flow.flow_nodes
        db_edges = flow.flow_edges
        db_cards = flow.flow_card_data

        # --- Reconstruct Nodes ---
        formatted_nodes = []
        for node in db_nodes:
            formatted_node = {
                "id": node.client_id,
                "type": node.type,
                "position": {
                    "x": node.position_x, 
                    "y": node.position_y
                },
                "width": node.width,
                "height": node.height,
                "parentNode": node.parent_node_id,
                "data": node.data or {},
                "hidden": node.data.get("hidden", False) if node.data else False
            }
            formatted_nodes.append(formatted_node)

        # --- Reconstruct Edges ---
        formatted_edges = []
        for edge in db_edges:
            formatted_edge = {
                "id": edge.client_id,
                "source": edge.source,
                "target": edge.target,
                "type": edge.type,
                "animated": edge.animated
            }
            formatted_edges.append(formatted_edge)

        # --- Reconstruct Card Data ---
        card_data = {}
        for card in db_cards:
            node_id = card.node_id
            card_data[node_id] = {
                "name": card.name,
                "description": card.description,
                "color": card.color,
                "bddScenarios": card.bdd_scenarios or [],
                "apiCalls": card.api_calls or [],
                "envData": card.env_data or {}
            }

        return {
            "id": flow.id,
            "name": flow.name,
            "nodes": formatted_nodes,
            "edges": formatted_edges,
            "cardData": card_data,
        }

    @staticmethod
    def save(db: Session, data: FlowSaveSchema):
        project_id = data.projectId
        flow_id = data.flowId
        nodes = data.nodes
        edges = data.edges
        card_data = data.cardData

        flow = None
        if flow_id:
             flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        
        # Fallback: if no flowId but project has flows, take the last one? 
        # Or create new? Careful here. For now, if no flowId, try to find existing default or create.
        if not flow:
             # Try find ANY flow for project to update (Legacy mode) or create new primary
             flow = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).first()

        if not flow:
             flow = FlowDB(project_id=project_id, name=data.name or "Fluxo Principal")
             db.add(flow)
             db.commit()
             db.refresh(flow)
        
        # Update metadata if provided
        if data.name:
            flow.name = data.name
        
        flow.updated_at = datetime.utcnow()
        flow_id = flow.id

        # 2. Clear existing data (Full Replace Strategy)
        # Using delete() queries is more efficient than iterating objects
        from app.models.flow_models import FlowNodeDB, FlowEdgeDB, FlowCardDataDB
        
        db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == flow_id).delete()
        db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == flow_id).delete()
        db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).delete()

        # 3. Insert Nodes
        print(f"🛠️ FlowService.save: Inserting {len(nodes)} nodes and {len(edges)} edges for Flow ID {flow_id}")
        for node in nodes:
            # Handle Pydantic model dump or dict access
            # node is NodeSchema
            node_data = node.data.model_dump()
            
            db_node = FlowNodeDB(
                flow_id=flow_id,
                client_id=node.id,
                type=node.type,
                position_x=node.position['x'],
                position_y=node.position['y'],
                width=node.width,
                height=node.height,
                parent_node_id=node.parentNode, # Now supported in schema
                
                data={
                    "name": node_data.get("name"),
                    "color": node_data.get("color"),
                    "childCount": node_data.get("childCount"),
                    "isCollapsed": node_data.get("isCollapsed"),
                    "hidden": node.hidden # Store hidden in data JSON for convenience or add col
                }
            )
            db.add(db_node)

        # 4. Insert Edges
        for edge in edges:
            db_edge = FlowEdgeDB(
                client_id=edge.id, 
                flow_id=flow_id,
                source=edge.source,
                target=edge.target,
                type=edge.type,
                animated=edge.animated
            )
            db.add(db_edge)

        # 5. Insert Card Data
        for cid, cinfo in card_data.items():
            # cinfo is CardDataSchema
            # Prepare API calls list
            api_calls_payload = [api.model_dump() for api in cinfo.apiCalls]
            print(f"🛠️ Saving Card {cid}: APICalls Count={len(api_calls_payload)}")
            if len(api_calls_payload) > 0:
                print(f"   -> First API Call: {api_calls_payload[0].get('name')} (ID: {api_calls_payload[0].get('id')})")

            db_card = FlowCardDataDB(
                node_id=cid,
                flow_id=flow_id,
                name=cinfo.name,
                description=cinfo.description,
                color=cinfo.color,
                bdd_scenarios=cinfo.bddScenarios, # JSON
                api_calls=api_calls_payload, # JSON
                env_data={k: v.model_dump() for k, v in cinfo.envData.items()} # JSON
            )
            db.add(db_card)

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
    def delete(db: Session, project_id: int):
         # DELETE ALL FLOWS FOR PROJECT
        flows = db.query(FlowDB).filter(FlowDB.project_id == project_id).all()
        if not flows:
            return False
            
        for flow in flows:
            db.delete(flow)
        
        db.commit()
        return True
