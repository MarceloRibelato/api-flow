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
            flow_name = data.name if data.name else f"Flow {str(data.flow_type).upper()}"
            flow = FlowDB(
                project_id=data.projectId,
                name=flow_name,
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

        # 3. Add Nodes (Bulk Insert)
        node_db_list = []
        for n in (data.nodes or []):
            node_data = n.data
            if hasattr(node_data, 'model_dump'):
                node_data = node_data.model_dump()
            elif hasattr(node_data, 'dict'):
                node_data = node_data.dict()

            node_db_list.append(FlowNodeDB(
                flow_id=flow.id,
                client_id=str(n.id),
                type=n.type,
                position_x=n.position.get('x', 0) if isinstance(n.position, dict) else 0,
                position_y=n.position.get('y', 0) if isinstance(n.position, dict) else 0,
                data=node_data
            ))
        if node_db_list:
            db.add_all(node_db_list)

        # 4. Add Edges (Bulk Insert)
        edge_db_list = []
        for e in (data.edges or []):
            edge_db_list.append(FlowEdgeDB(
                flow_id=flow.id,
                client_id=str(e.id),
                source=str(e.source),
                target=str(e.target),
                type=e.type,
                animated=e.animated
            ))
        if edge_db_list:
            db.add_all(edge_db_list)

        # 5. Add CardData
        e2e_steps_list_db = []
        valid_node_ids = {str(n.id) for n in (data.nodes or [])}
        
        for nid, c in (data.cardData or {}).items():
            if str(nid) not in valid_node_ids:
                continue # Ignora dados de cards órfãos (que foram deletados do frontend mas ficaram no cardData)
                
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
                db_queries=c.dbQueries or [],
                message_queues=[mq.model_dump() if hasattr(mq, 'model_dump') else mq for mq in (c.messageQueues or [])],
                env_data=env_data_dict
            )
            db.add(card_db)
            db.flush()

            # Prepare E2E Steps for Bulk Insert
            e2e_steps_list = getattr(c, 'e2eSteps', [])
            if e2e_steps_list:
                for idx, step in enumerate(e2e_steps_list):
                    e2e_steps_list_db.append(FlowE2EStepDB(
                        card_db_id=card_db.db_id,
                        client_id=step.get('id', str(idx)),
                        type=step.get('type', 'action'),
                        name=step.get('name', f'Step {idx}'),
                        properties=step.get('properties', {}),
                        order=idx
                    ))

        if e2e_steps_list_db:
            db.add_all(e2e_steps_list_db)

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
                "dbQueries": c.db_queries or [],
                "messageQueues": c.message_queues or [],
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
        from app.models.product_models import ProductModel
        
        results = db.query(FlowCardDataDB, FeatureModel, ProductModel).join(
            FlowDB, FlowDB.id == FlowCardDataDB.flow_id
        ).join(
            FeatureModel, FeatureModel.id == FlowDB.project_id
        ).join(
            ProductModel, ProductModel.id == FeatureModel.product_id
        ).filter(FlowDB.company_id == company_id).all()
        
        flow_ids = list({c.flow_id for c, f, p in results})
        edges_db = db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id.in_(flow_ids)).all() if flow_ids else []
        
        inventory_cards = []
        
        for c, feature, product in results:
            raw_type = (c.flow.flow_type if c.flow else None) or "api"

            # Normalize: keep 'mobile' as-is; map web/frontend/e2e → 'web'; rest → 'api'
            if raw_type == "mobile":
                f_type = "mobile"
            elif raw_type in ("web", "frontend", "e2e"):
                f_type = "web"
            else:
                f_type = "api"

            steps = []
            if c.e2e_steps_rel:
                steps = [{
                    "id": s.client_id,
                    "type": s.type,
                    "name": s.name,
                    "properties": s.properties
                } for s in sorted(c.e2e_steps_rel, key=lambda x: x.order)]
            elif c.e2e_steps:
                # fallback for legacy data if any
                steps = c.e2e_steps

            inventory_cards.append({
                "id": c.db_id,
                "node_id": c.node_id,
                "flow_id": c.flow_id,
                "name": c.name,
                "description": c.description,
                "color": c.color,
                "bddScenarios": c.bdd_scenarios,
                "apiCalls": c.api_calls,
                "e2eSteps": steps,
                "envData": c.env_data,
                "flowType": f_type,
                "sourceFlow": c.flow.name if c.flow else "Desconhecido",
                "productId": product.id,
                "productName": product.name,
                "featureId": feature.id,
                "featureName": feature.name
            })
            
        inventory_edges = [{
            "id": e.client_id,
            "source": e.source,
            "target": e.target,
            "flow_id": e.flow_id
        } for e in edges_db]
        
        return {
            "cards": inventory_cards,
            "edges": inventory_edges
        }

    @staticmethod
    def apply_healing(db: Session, project_id: int, company_id: int, flow_id: int, node_id: str, old_selector: str, new_selector: str):
        # 1. Carregar fluxo atual
        flow_data = FlowService.load(db, project_id, company_id, flow_id)
        if not flow_data:
            raise ValueError(f"Fluxo {flow_id} não encontrado")

        card_data = flow_data.get('cardData', {})
        if node_id not in card_data:
            raise ValueError(f"Nó {node_id} não encontrado no fluxo")

        node = card_data[node_id]
        e2e_steps = node.get('e2eSteps', [])
        
        # 2. Localizar e substituir o seletor
        replaced = False
        for step in e2e_steps:
            props = step.get('properties', {})
            db_selector = str(props.get('selector', '')).strip()
            target_selector = str(old_selector).strip()
            
            if db_selector == target_selector:
                props['selector'] = new_selector
                step['properties'] = props
                replaced = True
                break

        if not replaced:
            available = [str(s.get('properties', {}).get('selector', '')) for s in e2e_steps]
            raise ValueError(f"Passo com o seletor antigo '{old_selector}' não encontrado no nó {node_id}. Seletores neste nó: {available}")

        # 3. Preparar schema para salvar
        save_payload = FlowSaveSchema(
            projectId=project_id,
            flowId=flow_id,
            name=flow_data.get('name'),
            flow_type=flow_data.get('flow_type', 'api'),
            nodes=flow_data.get('nodes', []),
            edges=flow_data.get('edges', []),
            cardData=card_data
        )

        # 4. Salvar fluxo
        return FlowService.save(db, save_payload, company_id, 1)

