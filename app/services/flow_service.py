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

        # 1. Get or Create Flow (with lock to prevent concurrent save deadlocks)
        flow = db.query(FlowDB).filter(
            FlowDB.project_id == data.projectId, 
            FlowDB.flow_type == data.flow_type
        ).with_for_update().first()
        
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
            db.flush()        # 2. Sync Logic (Proper Deletion Order to avoid FK Violations)
        # Collect existing nodes and card names before deletion to detect node removal/edits for Audit Trail
        existing_cards = db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow.id).all()
        card_names = {str(c.node_id): str(c.name) for c in existing_cards if c.node_id and c.name}

        existing_nodes = db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == flow.id).all()
        existing_node_map = {}
        for n in existing_nodes:
            if not n.client_id:
                continue
            n_data = n.data if isinstance(n.data, dict) else {}
            n_name = card_names.get(str(n.client_id)) or n_data.get("name") or n_data.get("label") or str(n.client_id)
            existing_node_map[str(n.client_id)] = str(n_name)

        new_node_ids = {str(n.id) for n in (data.nodes or [])}
        existing_node_ids = set(existing_node_map.keys())

        real_existing_node_ids = {nid for nid in existing_node_ids if str(nid) not in ("start", "startNode")}
        real_new_node_ids = {nid for nid in new_node_ids if str(nid) not in ("start", "startNode")}

        deleted_ids = real_existing_node_ids - real_new_node_ids
        deleted_node_names = [str(existing_node_map[nid]) for nid in deleted_ids if nid in existing_node_map and existing_node_map[nid] is not None]

        added_ids = real_new_node_ids - real_existing_node_ids
        added_node_names = []
        if real_existing_node_ids and added_ids:
            for n in (data.nodes or []):
                nid_str = str(n.id)
                if nid_str in added_ids:
                    n_data = n.data
                    if hasattr(n_data, 'model_dump'): n_data = n_data.model_dump()
                    elif hasattr(n_data, 'dict'): n_data = n_data.dict()
                    elif not isinstance(n_data, dict): n_data = {}
                    
                    c_name = None
                    if data.cardData and nid_str in data.cardData:
                        card_item = data.cardData[nid_str]
                        if hasattr(card_item, 'name'):
                            c_name = card_item.name
                        elif isinstance(card_item, dict):
                            c_name = card_item.get('name')
                            
                    name = c_name or n_data.get("name") or n_data.get("label") or nid_str
                    if name is not None:
                        added_node_names.append(str(name))

        # Detect modified nodes (nodes present both before and after save)
        common_ids = real_existing_node_ids & real_new_node_ids
        modified_node_names = []
        if real_existing_node_ids and common_ids:
            for n in (data.nodes or []):
                nid_str = str(n.id)
                if nid_str not in common_ids:
                    continue

                old_name = existing_node_map.get(nid_str)
                old_card = next((c for c in existing_cards if str(c.node_id) == nid_str), None)

                n_data = n.data
                if hasattr(n_data, 'model_dump'): n_data = n_data.model_dump()
                elif hasattr(n_data, 'dict'): n_data = n_data.dict()
                elif not isinstance(n_data, dict): n_data = {}

                c_name = None
                new_card = None
                if data.cardData and nid_str in data.cardData:
                    new_card = data.cardData[nid_str]
                    if hasattr(new_card, 'name'):
                        c_name = new_card.name
                    elif isinstance(new_card, dict):
                        c_name = new_card.get('name')

                new_name = c_name or n_data.get("name") or n_data.get("label") or nid_str

                is_changed = False
                if old_name and new_name and str(old_name) != str(new_name):
                    is_changed = True
                elif old_card and new_card:
                    old_desc = old_card.description or ""
                    old_api_cnt = len(old_card.api_calls or [])
                    old_db_cnt = len(old_card.db_queries or [])
                    old_mq_cnt = len(old_card.message_queues or [])
                    old_e2e_cnt = db.query(FlowE2EStepDB).filter(FlowE2EStepDB.card_db_id == old_card.db_id).count()

                    new_desc = getattr(new_card, 'description', '') if hasattr(new_card, 'description') else (new_card.get('description', '') if isinstance(new_card, dict) else '')
                    new_api_cnt = len(getattr(new_card, 'apiCalls', []) or []) if hasattr(new_card, 'apiCalls') else (len(new_card.get('apiCalls', []) or []) if isinstance(new_card, dict) else 0)
                    new_db_cnt = len(getattr(new_card, 'dbQueries', []) or []) if hasattr(new_card, 'dbQueries') else (len(new_card.get('dbQueries', []) or []) if isinstance(new_card, dict) else 0)
                    new_mq_cnt = len(getattr(new_card, 'messageQueues', []) or []) if hasattr(new_card, 'messageQueues') else (len(new_card.get('messageQueues', []) or []) if isinstance(new_card, dict) else 0)
                    new_e2e_cnt = len(getattr(new_card, 'e2eSteps', []) or []) if hasattr(new_card, 'e2eSteps') else (len(new_card.get('e2eSteps', []) or []) if isinstance(new_card, dict) else 0)

                    if (old_desc != new_desc) or (old_api_cnt != new_api_cnt) or (old_db_cnt != new_db_cnt) or (old_mq_cnt != new_mq_cnt) or (old_e2e_cnt != new_e2e_cnt):
                        is_changed = True

                if is_changed:
                    display_label = f"{old_name} → {new_name}" if (old_name and new_name and str(old_name) != str(new_name)) else str(new_name or old_name)
                    modified_node_names.append(display_label)

        # We delete all children (steps) first, then parents (cards, nodes, edges)
        card_db_ids = [c.db_id for c in existing_cards]
        if card_db_ids:
            db.query(FlowE2EStepDB).filter(FlowE2EStepDB.card_db_id.in_(card_db_ids)).delete(synchronize_session=False)

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
                animated=e.animated,
                label=e.label
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
                    if hasattr(step, 'model_dump'):
                        step_dict = step.model_dump()
                    elif hasattr(step, 'dict'):
                        step_dict = step.dict()
                    elif isinstance(step, dict):
                        step_dict = step
                    else:
                        step_dict = {}

                    e2e_steps_list_db.append(FlowE2EStepDB(
                        card_db_id=card_db.db_id,
                        client_id=str(step_dict.get('id', idx)),
                        type=str(step_dict.get('type', 'action')),
                        name=str(step_dict.get('name', f'Step {idx}')),
                        properties=step_dict.get('properties', {}) if isinstance(step_dict.get('properties'), dict) else {},
                        order=idx
                    ))

        if e2e_steps_list_db:
            db.add_all(e2e_steps_list_db)

        # Collect all active node names currently saved in the flow
        current_node_names = []
        for n in (data.nodes or []):
            nid_str = str(n.id)
            if nid_str in ("start", "startNode"):
                continue
            n_data = n.data
            if hasattr(n_data, 'model_dump'): n_data = n_data.model_dump()
            elif hasattr(n_data, 'dict'): n_data = n_data.dict()
            elif not isinstance(n_data, dict): n_data = {}
            
            c_name = None
            if data.cardData and nid_str in data.cardData:
                card_item = data.cardData[nid_str]
                if hasattr(card_item, 'name'):
                    c_name = card_item.name
                elif isinstance(card_item, dict):
                    c_name = card_item.get('name')
            name = c_name or n_data.get("name") or n_data.get("label") or f"Card #{nid_str}"
            current_node_names.append(str(name))

        flow.updated_at = datetime.utcnow()
        return {
            "id": flow.id, 
            "project_id": flow.project_id, 
            "status": "saved", 
            "deleted_nodes": deleted_node_names, 
            "added_nodes": added_node_names,
            "modified_nodes": modified_node_names,
            "current_nodes": current_node_names,
            "previous_nodes_count": len(real_existing_node_ids),
            "current_nodes_count": len(current_node_names)
        }

    @staticmethod
    def load(db: Session, project_id: int = None, company_id: int = None, flow_id: int = None, flow_type: str = None):
        query = db.query(FlowDB)
        if flow_id:
            query = query.filter(FlowDB.id == flow_id)
        elif project_id:
            query = query.filter(FlowDB.project_id == project_id)
            if flow_type:
                query = query.filter(FlowDB.flow_type == flow_type)
            query = query.order_by(FlowDB.updated_at.desc())
        else:
            return {"project_id": project_id, "nodes": [], "edges": [], "cardData": {}, "flow_type": flow_type or "api"}

        if company_id:
            query = query.filter(FlowDB.company_id == company_id)

        flow = query.first()
        if not flow and project_id and not flow_id and not flow_type:
            # Fallback only when flow_type is not specified: get latest flow for this project_id regardless of flow_type
            fallback_query = db.query(FlowDB).filter(FlowDB.project_id == project_id)
            if company_id:
                fallback_query = fallback_query.filter(FlowDB.company_id == company_id)
            flow = fallback_query.order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            return {"project_id": project_id, "nodes": [], "edges": [], "cardData": {}, "flow_type": flow_type or "api"}

        # Reconstruct JSON
        node_type_map = {n.client_id: (n.data.get('nodeType') if isinstance(n.data, dict) else None) for n in flow.flow_nodes}
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
            "animated": e.animated,
            "label": e.label
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
            
            node_type = node_type_map.get(c.node_id)
            if not node_type:
                if flow.flow_type == "mobile": node_type = "mobile"
                elif flow.flow_type in ("web", "e2e", "frontend"): node_type = "e2e"
                elif c.db_queries: node_type = "database"
                elif c.message_queues: node_type = "queue"
                else: node_type = "api"

            cardData[c.node_id] = {
                "name": c.name,
                "color": c.color,
                "nodeType": node_type,
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
            # 0. Delete AgentMemoryDB associated with this flow
            from app.models.agent_models import AgentMemoryDB
            db.query(AgentMemoryDB).filter(AgentMemoryDB.flow_id == f.id).delete(synchronize_session=False)

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

            node_type = None
            if c.flow and c.flow.flow_nodes:
                for fn in c.flow.flow_nodes:
                    if fn.client_id == c.node_id and isinstance(fn.data, dict):
                        node_type = fn.data.get('nodeType')
                        break

            if not node_type or node_type not in ('database', 'queue', 'mobile', 'e2e', 'web', 'api'):
                if c.db_queries and len(c.db_queries) > 0:
                    node_type = "database"
                elif c.message_queues and len(c.message_queues) > 0:
                    node_type = "queue"
                else:
                    node_type = "mobile" if f_type == "mobile" else ("e2e" if f_type == "web" else "api")

            inventory_cards.append({
                "id": c.db_id,
                "node_id": c.node_id,
                "flow_id": c.flow_id,
                "name": c.name,
                "description": c.description,
                "color": c.color,
                "nodeType": node_type,
                "bddScenarios": c.bdd_scenarios,
                "apiCalls": c.api_calls,
                "dbQueries": c.db_queries or [],
                "messageQueues": c.message_queues or [],
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
        
        # 2. Localizar e substituir o seletor em cardData
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

        # 3. Sincronizar a modificação na estrutura bruta de 'nodes' para consistência do DB
        nodes = flow_data.get('nodes', [])
        for n in nodes:
            if str(n.get('id')) == str(node_id):
                ndata = n.get('data', {})
                if 'e2eSteps' in ndata:
                    for nstep in ndata['e2eSteps']:
                        nprops = nstep.get('properties', {})
                        if str(nprops.get('selector', '')).strip() == str(old_selector).strip():
                            nprops['selector'] = new_selector
                            nstep['properties'] = nprops
                            break
                break

        # 4. Preparar schema para salvar
        save_payload = FlowSaveSchema(
            projectId=project_id,
            flowId=flow_id,
            name=flow_data.get('name'),
            flow_type=flow_data.get('flow_type', 'api'),
            nodes=nodes,
            edges=flow_data.get('edges', []),
            cardData=card_data
        )

        # 5. Salvar fluxo
        FlowService.save(db, save_payload, company_id, 1)
        return FlowService.load(db, project_id, company_id, flow_id)

