import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, joinedload

from app.models.flow_models import FlowDB, FlowNodeDB, FlowEdgeDB, FlowCardDataDB, FlowE2EStepDB, FlowVersionDB
from app.schemas.flow_schemas import FlowSaveSchema
from app.models.feature_models import FeatureModel

class FlowService:
    MAX_VERSIONS_PER_FLOW = 30

    @staticmethod
    def _ensure_version_table(db: Session):
        conn = db.connection()
        if conn.dialect.name == "postgresql":
            cols = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'flow_versions'"
                    )
                ).fetchall()
            }
            if not cols:
                FlowVersionDB.__table__.create(bind=conn, checkfirst=True)
                return
            if "is_definitive" not in cols:
                conn.execute(text("ALTER TABLE flow_versions ADD COLUMN IF NOT EXISTS is_definitive BOOLEAN NOT NULL DEFAULT TRUE"))
            return

        FlowVersionDB.__table__.create(bind=conn, checkfirst=True)
        cols = {col["name"] for col in inspect(conn).get_columns("flow_versions")}
        if "is_definitive" not in cols:
            conn.execute(text("ALTER TABLE flow_versions ADD COLUMN is_definitive BOOLEAN NOT NULL DEFAULT TRUE"))

    @staticmethod
    def _compute_snapshot_hash(snapshot: dict) -> str:
        payload = {
            "name": snapshot.get("name"),
            "flow_type": snapshot.get("flow_type"),
            "nodes": snapshot.get("nodes") or [],
            "edges": snapshot.get("edges") or [],
            "cardData": snapshot.get("cardData") or {},
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_current_snapshot(db: Session, flow: FlowDB) -> dict:
        node_rows = db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == flow.id).all()
        edge_rows = db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == flow.id).all()
        card_rows = (
            db.query(FlowCardDataDB)
            .options(joinedload(FlowCardDataDB.e2e_steps_rel))
            .filter(FlowCardDataDB.flow_id == flow.id)
            .all()
        )
        if not node_rows and not card_rows:
            return {}

        card_by_node_id = {c.node_id: c for c in card_rows}
        node_data_map = {}
        nodes = []
        for n in node_rows:
            ndata = dict(n.data) if isinstance(n.data, dict) else {}
            if not ndata.get("name"):
                card_match = card_by_node_id.get(n.client_id)
                ndata["name"] = (card_match.name if card_match and card_match.name else n.client_id)
            node_data_map[n.client_id] = ndata
            nodes.append({
                "id": n.client_id,
                "type": n.type or "custom",
                "position": {"x": float(n.position_x or 0), "y": float(n.position_y or 0)},
                "width": n.width,
                "height": n.height,
                "hidden": bool(ndata.get("hidden", False)),
                "parentNode": n.parent_node_id,
                "data": ndata,
            })

        edges = []
        for e in edge_rows:
            edge_obj = {
                "id": e.client_id,
                "source": e.source,
                "target": e.target,
                "type": e.type or "buttonedge",
                "animated": bool(e.animated) if e.animated is not None else True,
                "label": e.label,
            }
            if hasattr(e, "source_handle") and getattr(e, "source_handle", None):
                edge_obj["sourceHandle"] = e.source_handle
            edges.append(edge_obj)

        card_data = {}
        for c in card_rows:
            if c.e2e_steps_rel:
                steps = [{
                    "id": s.client_id,
                    "type": s.type,
                    "name": s.name,
                    "properties": s.properties or {},
                } for s in sorted(c.e2e_steps_rel, key=lambda x: x.order)]
            else:
                steps = list(c.e2e_steps or [])

            ndata = node_data_map.get(c.node_id, {})
            raw_env = c.env_data if isinstance(c.env_data, dict) else {}
            clean_env = {
                k: v for k, v in raw_env.items()
                if not str(k).startswith("__") and isinstance(v, dict)
            }
            node_type = ndata.get("nodeType")
            if not node_type:
                if flow.flow_type == "mobile":
                    node_type = "mobile"
                elif flow.flow_type in ("web", "e2e", "frontend"):
                    node_type = "e2e"
                elif c.db_queries:
                    node_type = "database"
                elif c.message_queues:
                    node_type = "queue"
                else:
                    node_type = "api"

            card_data[c.node_id] = {
                "name": c.name or ndata.get("name") or c.node_id,
                "color": c.color or ndata.get("color") or "#10b981",
                "nodeType": node_type,
                "description": c.description or "",
                "isMainFlow": bool(ndata.get("isMainFlow", False)),
                "bddScenarios": c.bdd_scenarios or [],
                "apiCalls": c.api_calls or [],
                "e2eSteps": steps,
                "dbQueries": c.db_queries or [],
                "messageQueues": c.message_queues or [],
                "conditionConfig": ndata.get("conditionConfig") or raw_env.get("__conditionConfig"),
                "loopConfig": ndata.get("loopConfig") or raw_env.get("__loopConfig"),
                "tryCatchConfig": ndata.get("tryCatchConfig") or raw_env.get("__tryCatchConfig"),
                "envData": clean_env,
            }

        return {
            "projectId": flow.project_id,
            "flowId": flow.id,
            "name": flow.name,
            "flow_type": flow.flow_type or "api",
            "nodes": nodes,
            "edges": edges,
            "cardData": card_data,
        }

    @staticmethod
    def _build_save_snapshot(data: FlowSaveSchema, flow: FlowDB) -> dict:
        snapshot = data.model_dump(mode="json")
        snapshot.pop("is_definitive", None)
        snapshot["projectId"] = flow.project_id
        snapshot["flowId"] = flow.id
        snapshot["name"] = flow.name
        snapshot["flow_type"] = flow.flow_type or "api"
        return snapshot

    @staticmethod
    def _compute_diff_against_snapshot(base_snapshot: dict, current_snapshot: dict):
        """Calcula diferenças acumuladas (adicionados, modificados, removidos) contra uma versão base."""
        def extract_map(snap: dict):
            card_map = snap.get("cardData") if isinstance(snap.get("cardData"), dict) else {}
            names = {}
            sigs = {}
            for n in (snap.get("nodes") or []):
                if not isinstance(n, dict):
                    continue
                cid = str(n.get("id") or "")
                if not cid or cid in ("start", "startNode", "node_start"):
                    continue
                ndata = n.get("data") if isinstance(n.get("data"), dict) else {}
                cobj = card_map.get(cid) if isinstance(card_map.get(cid), dict) else {}
                label = cobj.get("name") or ndata.get("name") or f"Card #{cid}"
                names[cid] = str(label)
                sigs[cid] = json.dumps(
                    {
                        "name": label,
                        "nodeData": ndata,
                        "card": cobj,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
            return names, sigs

        base_names, base_sigs = extract_map(base_snapshot or {})
        cur_names, cur_sigs = extract_map(current_snapshot or {})

        added = [cur_names[nid] for nid in cur_names if nid not in base_names]
        deleted = [base_names[nid] for nid in base_names if nid not in cur_names]
        modified = []
        for nid in cur_names:
            if nid in base_names and cur_sigs[nid] != base_sigs[nid]:
                if cur_names[nid] != base_names[nid]:
                    modified.append(f"{base_names[nid]} → {cur_names[nid]}")
                else:
                    modified.append(cur_names[nid])
        return added, modified, deleted

    @staticmethod
    def _build_version_summary(added_nodes: list, modified_nodes: list, deleted_nodes: list, is_initial: bool = False) -> str:
        parts = []
        if added_nodes:
            names = ", ".join(added_nodes[:2]) + (f" (+{len(added_nodes) - 2})" if len(added_nodes) > 2 else "")
            parts.append(f"+{len(added_nodes)} novo(s): {names}")
        if modified_nodes:
            names = ", ".join(modified_nodes[:2]) + (f" (+{len(modified_nodes) - 2})" if len(modified_nodes) > 2 else "")
            parts.append(f"{len(modified_nodes)} editado(s): {names}")
        if deleted_nodes:
            names = ", ".join(deleted_nodes[:2]) + (f" (+{len(deleted_nodes) - 2})" if len(deleted_nodes) > 2 else "")
            parts.append(f"-{len(deleted_nodes)} removido(s): {names}")
        if not parts:
            return "Versão inicial salva" if is_initial else "Layout ou conexões atualizadas"
        return " • ".join(parts)[:480]

    @staticmethod
    def _record_version_snapshot(
        db: Session,
        flow: FlowDB,
        snapshot: dict,
        summary: str,
        user_id: int = None,
        user_name: str = None,
        is_definitive: bool = False,
        force: bool = False,
        ensure_table: bool = True,
    ):
        if ensure_table:
            FlowService._ensure_version_table(db)
        snap_hash = FlowService._compute_snapshot_hash(snapshot)
        latest = (
            db.query(FlowVersionDB)
            .filter(FlowVersionDB.flow_id == flow.id)
            .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
            .first()
        )
        latest_definitive = (
            db.query(FlowVersionDB)
            .filter(
                FlowVersionDB.flow_id == flow.id,
                FlowVersionDB.is_definitive.is_(True),
            )
            .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
            .first()
        )

        # If there is an open non-definitive draft version, consolidate edits into it
        if latest and not latest.is_definitive:
            if (
                is_definitive
                and latest_definitive
                and latest_definitive.snapshot_hash == snap_hash
                and not force
            ):
                # Reverted manually back to the last definitive snapshot: remove redundant draft
                db.delete(latest)
                db.flush()
                return latest_definitive

            latest.snapshot = snapshot
            latest.snapshot_hash = snap_hash
            latest.nodes_count = len(snapshot.get("nodes") or [])
            latest.edges_count = len(snapshot.get("edges") or [])
            latest.summary = (summary or "")[:500]
            latest.created_by = user_id
            latest.created_by_name = user_name
            latest.created_at = datetime.now(timezone.utc)
            if is_definitive:
                latest.is_definitive = True
            db.flush()
            return latest

        # Otherwise latest is None or already definitive
        if latest and not force and latest.snapshot_hash == snap_hash:
            return latest if is_definitive else None

        next_version_number = (latest.version_number + 1) if latest else 1
        version = FlowVersionDB(
            flow_id=flow.id,
            project_id=flow.project_id,
            company_id=flow.company_id,
            version_number=next_version_number,
            is_definitive=bool(is_definitive),
            summary=(summary or "")[:500],
            snapshot=snapshot,
            snapshot_hash=snap_hash,
            nodes_count=len(snapshot.get("nodes") or []),
            edges_count=len(snapshot.get("edges") or []),
            created_by=user_id,
            created_by_name=user_name,
        )
        db.add(version)
        db.flush()

        stale_rows = (
            db.query(FlowVersionDB.id)
            .filter(FlowVersionDB.flow_id == flow.id)
            .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
            .offset(FlowService.MAX_VERSIONS_PER_FLOW)
            .all()
        )
        stale_ids = [r[0] for r in stale_rows]
        if stale_ids:
            db.query(FlowVersionDB).filter(FlowVersionDB.id.in_(stale_ids)).delete(synchronize_session=False)

        return version
    @staticmethod
    def is_definitive_only_schedule(db: Session, schedule_or_id) -> bool:
        """Retorna True para agendamentos e pipelines CI/CD (executam apenas versão definitiva)."""
        if schedule_or_id is None:
            return False
        from app.models.schedule_models import ScheduleModel

        schedule = schedule_or_id
        if isinstance(schedule_or_id, int):
            if db is None:
                return False
            schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_or_id).first()

        if schedule is None or not isinstance(schedule, ScheduleModel):
            return False
        if not bool(schedule.is_manual):
            return True
        if schedule.cron_expression:
            return True
        sched_name = str(schedule.name or "").strip().upper()
        if sched_name.startswith("[PIPELINE]") or sched_name.startswith("CI/CD"):
            return True
        return False

    @staticmethod
    def _verify_project_ownership(db: Session, project_id: int, company_id: int) -> bool:
        from app.models.product_models import ProductModel
        feature = db.query(FeatureModel).join(ProductModel).filter(
            FeatureModel.id == project_id,
            ProductModel.company_id == company_id
        ).first()
        return feature is not None

    @staticmethod
    def list_by_project(db: Session, project_id: int, company_id: int, definitive_only: bool = False):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            return []

        if definitive_only:
            FlowService._ensure_version_table(db)

        flows = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).all()
        result = []
        for f in flows:
            if definitive_only:
                latest_def = (
                    db.query(FlowVersionDB)
                    .filter(
                        FlowVersionDB.flow_id == f.id,
                        FlowVersionDB.is_definitive.is_(True),
                    )
                    .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
                    .first()
                )
                if latest_def:
                    snap = latest_def.snapshot if isinstance(latest_def.snapshot, dict) else {}
                    snap_cards = snap.get("cardData") if isinstance(snap.get("cardData"), dict) else {}
                    steps_count = 0
                    for cid, cdict in snap_cards.items():
                        if str(cid) in ("start", "startNode", "node_start") or not isinstance(cdict, dict):
                            continue
                        e2e_cnt = len(cdict.get("e2eSteps") or [])
                        api_cnt = len(cdict.get("apiCalls") or [])
                        db_cnt = len(cdict.get("dbQueries") or [])
                        mq_cnt = len(cdict.get("messageQueues") or [])
                        if f.flow_type in ("e2e", "web", "mobile"):
                            steps_count += e2e_cnt + db_cnt + mq_cnt
                        else:
                            steps_count += api_cnt + db_cnt + mq_cnt + e2e_cnt
                    result.append({
                        "id": f.id,
                        "project_id": f.project_id,
                        "name": snap.get("name") or f.name,
                        "flow_type": snap.get("flow_type") or f.flow_type,
                        "created_at": f.created_at,
                        "updated_at": latest_def.created_at or f.updated_at,
                        "nodes_count": len(snap.get("nodes") or []),
                        "edges_count": len(snap.get("edges") or []),
                        "steps_count": steps_count,
                        "is_definitive": True,
                    })
                    continue

                has_any_ver = db.query(FlowVersionDB.id).filter(FlowVersionDB.flow_id == f.id).first() is not None
                if has_any_ver:
                    result.append({
                        "id": f.id,
                        "project_id": f.project_id,
                        "name": f.name,
                        "flow_type": f.flow_type,
                        "created_at": f.created_at,
                        "updated_at": f.updated_at,
                        "nodes_count": 0,
                        "edges_count": 0,
                        "steps_count": 0,
                        "is_definitive": False,
                    })
                    continue

            steps_count = 0
            for c in (f.flow_card_data or []):
                if str(c.node_id) in ("start", "startNode", "node_start"):
                    continue
                e2e_cnt = len(c.e2e_steps_rel or []) or len(c.e2e_steps or [])
                api_cnt = len(c.api_calls or [])
                db_cnt = len(c.db_queries or [])
                mq_cnt = len(c.message_queues or [])
                if f.flow_type in ("e2e", "web", "mobile"):
                    steps_count += e2e_cnt + db_cnt + mq_cnt
                else:
                    steps_count += api_cnt + db_cnt + mq_cnt + e2e_cnt
            result.append({
                "id": f.id,
                "project_id": f.project_id,
                "name": f.name,
                "flow_type": f.flow_type,
                "created_at": f.created_at,
                "updated_at": f.updated_at,
                "nodes_count": len(f.flow_nodes),
                "edges_count": len(f.flow_edges),
                "steps_count": steps_count,
                **({"is_definitive": True} if definitive_only else {}),
            })
        return result

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
    def save(db: Session, data: FlowSaveSchema, company_id: int, user_id: int, custom_summary: str = None, user_name: str = None, force_version: bool = False, record_version: bool = True):
        if not FlowService._verify_project_ownership(db, data.projectId, company_id):
             raise ValueError("Projeto não pertence à sua empresa")
        FlowService._ensure_version_table(db)

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

        # Snapshot baseline v1 (definitive) if this pre-existing flow had no recorded versions before overwrite
        if record_version:
            had_versions = db.query(FlowVersionDB.id).filter(FlowVersionDB.flow_id == flow.id).first() is not None
            if not had_versions:
                pre_snapshot = FlowService._build_current_snapshot(db, flow)
                if pre_snapshot:
                    FlowService._record_version_snapshot(
                        db,
                        flow=flow,
                        snapshot=pre_snapshot,
                        summary="Versão inicial (antes da alteração)",
                        user_id=user_id,
                        user_name=user_name,
                        is_definitive=True,
                        force=True,
                        ensure_table=False,
                    )

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
                    if env_name in ('__conditionConfig', '__loopConfig', '__tryCatchConfig'):
                        continue
                    if hasattr(env_val, 'model_dump'): env_data_dict[env_name] = env_val.model_dump()
                    else: env_data_dict[env_name] = env_val
            if getattr(c, 'conditionConfig', None):
                env_data_dict['__conditionConfig'] = c.conditionConfig
            if getattr(c, 'loopConfig', None):
                env_data_dict['__loopConfig'] = c.loopConfig
            if getattr(c, 'tryCatchConfig', None):
                env_data_dict['__tryCatchConfig'] = c.tryCatchConfig

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

        is_definitive_save = bool(getattr(data, "is_definitive", False))
        recorded_version = None
        if record_version:
            new_snapshot = FlowService._build_save_snapshot(data, flow)
            has_any_version = db.query(FlowVersionDB.id).filter(FlowVersionDB.flow_id == flow.id).first() is not None
            latest_definitive = (
                db.query(FlowVersionDB)
                .filter(
                    FlowVersionDB.flow_id == flow.id,
                    FlowVersionDB.is_definitive.is_(True),
                )
                .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
                .first()
            )
            if custom_summary:
                version_summary = custom_summary
            elif latest_definitive and isinstance(latest_definitive.snapshot, dict):
                cum_added, cum_modified, cum_deleted = FlowService._compute_diff_against_snapshot(
                    latest_definitive.snapshot,
                    new_snapshot,
                )
                version_summary = FlowService._build_version_summary(
                    cum_added,
                    cum_modified,
                    cum_deleted,
                    is_initial=False,
                )
            else:
                version_summary = FlowService._build_version_summary(
                    added_node_names,
                    modified_node_names,
                    deleted_node_names,
                    is_initial=not has_any_version,
                )

            recorded_version = FlowService._record_version_snapshot(
                db,
                flow=flow,
                snapshot=new_snapshot,
                summary=version_summary,
                user_id=user_id,
                user_name=user_name,
                is_definitive=is_definitive_save,
                force=force_version,
                ensure_table=False,
            )

        if recorded_version is not None:
            has_uncommitted_draft = not bool(recorded_version.is_definitive)
        else:
            latest_ver = (
                db.query(FlowVersionDB)
                .filter(FlowVersionDB.flow_id == flow.id)
                .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
                .first()
            )
            has_uncommitted_draft = bool(latest_ver and not getattr(latest_ver, "is_definitive", True))

        flow.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(flow)
        return {
            "id": flow.id, 
            "project_id": flow.project_id, 
            "status": "saved", 
            "deleted_nodes": deleted_node_names, 
            "added_nodes": added_node_names,
            "modified_nodes": modified_node_names,
            "current_nodes": current_node_names,
            "previous_nodes_count": len(real_existing_node_ids),
            "current_nodes_count": len(current_node_names),
            "version_id": recorded_version.id if recorded_version else None,
            "version_number": recorded_version.version_number if recorded_version else None,
            "is_definitive": bool(recorded_version.is_definitive) if recorded_version else is_definitive_save,
            "has_uncommitted_draft": has_uncommitted_draft,
        }

    @staticmethod
    def list_versions(
        db: Session,
        project_id: int,
        company_id: int,
        flow_id: int = None,
        flow_type: str = None,
    ):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            raise ValueError("Projeto não pertence à sua empresa")

        FlowService._ensure_version_table(db)

        query = db.query(FlowDB).filter(
            FlowDB.project_id == project_id,
            FlowDB.company_id == company_id,
        )
        if flow_id:
            query = query.filter(FlowDB.id == flow_id)
        elif flow_type:
            query = query.filter(FlowDB.flow_type == flow_type)
        flow = query.order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            return {"flow_id": None, "flow_name": None, "flow_type": flow_type, "has_uncommitted_draft": False, "versions": []}

        versions = (
            db.query(FlowVersionDB)
            .filter(FlowVersionDB.flow_id == flow.id)
            .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
            .all()
        )

        # Backfill current state as v1 definitive if the flow already exists without recorded history yet
        if not versions:
            baseline_snap = FlowService._build_current_snapshot(db, flow)
            if baseline_snap:
                baseline = FlowService._record_version_snapshot(
                    db,
                    flow=flow,
                    snapshot=baseline_snap,
                    summary="Versão inicial salva",
                    user_id=flow.user_id,
                    is_definitive=True,
                    force=True,
                )
                if baseline:
                    db.commit()
                    db.refresh(baseline)
                    versions = [baseline]

        items = []
        for idx, v in enumerate(versions):
            snap = v.snapshot if isinstance(v.snapshot, dict) else {}
            card_data_map = snap.get("cardData") if isinstance(snap.get("cardData"), dict) else {}
            node_names = []
            for n in (snap.get("nodes") or []):
                if not isinstance(n, dict):
                    continue
                cid = n.get("id")
                if cid in ("start", "startNode", "node_start"):
                    continue
                ndata = n.get("data") if isinstance(n.get("data"), dict) else {}
                cname = (card_data_map.get(cid) or {}).get("name") if isinstance(card_data_map.get(cid), dict) else None
                label = cname or ndata.get("name")
                if label:
                    node_names.append(str(label))
            items.append({
                "id": v.id,
                "flow_id": v.flow_id,
                "project_id": v.project_id,
                "version_number": v.version_number,
                "is_definitive": bool(getattr(v, "is_definitive", True)),
                "summary": v.summary,
                "nodes_count": v.nodes_count,
                "edges_count": v.edges_count,
                "node_names": node_names[:8],
                "total_named_nodes": len(node_names),
                "created_by": v.created_by,
                "created_by_name": v.created_by_name,
                "created_at": v.created_at,
                "is_current": idx == 0,
            })

        return {
            "flow_id": flow.id,
            "flow_name": flow.name,
            "flow_type": flow.flow_type,
            "has_uncommitted_draft": any(not item["is_definitive"] for item in items),
            "versions": items,
        }

    @staticmethod
    def restore_version(
        db: Session,
        version_id: int,
        company_id: int,
        user_id: int,
        user_name: str = None,
    ):
        FlowService._ensure_version_table(db)
        version = db.query(FlowVersionDB).filter(FlowVersionDB.id == version_id).first()
        if not version:
            raise ValueError("Versão não encontrada")

        if version.company_id != company_id or not FlowService._verify_project_ownership(db, version.project_id, company_id):
            raise ValueError("Versão não pertence à sua empresa")

        if not getattr(version, "is_definitive", True):
            raise ValueError("O rollback só é permitido para versões definitivas")

        flow = db.query(FlowDB).filter(FlowDB.id == version.flow_id).first()
        if not flow:
            raise ValueError("Fluxo associado não encontrado")

        payload = dict(version.snapshot or {})
        payload["projectId"] = flow.project_id
        payload["flowId"] = flow.id
        payload["name"] = payload.get("name") or flow.name
        payload["flow_type"] = payload.get("flow_type") or flow.flow_type or "api"
        payload["is_definitive"] = False
        payload.setdefault("nodes", [])
        payload.setdefault("edges", [])
        payload.setdefault("cardData", {})

        restore_schema = FlowSaveSchema(**payload)
        save_res = FlowService.save(
            db,
            restore_schema,
            company_id=company_id,
            user_id=user_id,
            custom_summary=f"Restaurada para validação a partir da v{version.version_number} (salve definitivo para confirmar)",
            user_name=user_name,
            force_version=True,
        )

        return {
            "status": "restored",
            "restored_from_version_id": version.id,
            "restored_from_version_number": version.version_number,
            "flow_id": flow.id,
            "project_id": flow.project_id,
            "flow_type": flow.flow_type,
            "new_version_id": save_res.get("version_id") or version.id,
            "new_version_number": save_res.get("version_number") or version.version_number,
            "is_definitive": False,
            "has_uncommitted_draft": True,
            "flow": FlowService.load(
                db,
                project_id=flow.project_id,
                company_id=company_id,
                flow_id=flow.id,
                flow_type=flow.flow_type,
            ),
        }

    @staticmethod
    def discard_draft(
        db: Session,
        project_id: int,
        company_id: int,
        user_id: int,
        flow_id: int = None,
        flow_type: str = None,
        user_name: str = None,
    ):
        if not FlowService._verify_project_ownership(db, project_id, company_id):
            raise ValueError("Projeto não pertence à sua empresa")

        FlowService._ensure_version_table(db)

        query = db.query(FlowDB).filter(
            FlowDB.project_id == project_id,
            FlowDB.company_id == company_id,
        )
        if flow_id:
            query = query.filter(FlowDB.id == flow_id)
        elif flow_type:
            query = query.filter(FlowDB.flow_type == flow_type)
        flow = query.order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            return {
                "status": "discarded",
                "flow_id": None,
                "project_id": project_id,
                "flow_type": flow_type or "api",
                "restored_to_version_number": None,
                "has_uncommitted_draft": False,
                "flow": {
                    "project_id": project_id,
                    "nodes": [],
                    "edges": [],
                    "cardData": {},
                    "flow_type": flow_type or "api",
                    "has_uncommitted_draft": False,
                },
            }

        latest_definitive = (
            db.query(FlowVersionDB)
            .filter(
                FlowVersionDB.flow_id == flow.id,
                FlowVersionDB.is_definitive.is_(True),
            )
            .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
            .first()
        )

        # Remove any uncommitted draft snapshots before reverting active flow state
        db.query(FlowVersionDB).filter(
            FlowVersionDB.flow_id == flow.id,
            FlowVersionDB.is_definitive.is_(False),
        ).delete(synchronize_session=False)
        db.flush()

        if latest_definitive and isinstance(latest_definitive.snapshot, dict):
            payload = dict(latest_definitive.snapshot)
            if payload.get("name"):
                flow.name = payload["name"]
        else:
            payload = {
                "nodes": [],
                "edges": [],
                "cardData": {},
            }

        payload["projectId"] = flow.project_id
        payload["flowId"] = flow.id
        payload["name"] = payload.get("name") or flow.name
        payload["flow_type"] = payload.get("flow_type") or flow.flow_type or "api"
        payload["is_definitive"] = False
        payload.setdefault("nodes", [])
        payload.setdefault("edges", [])
        payload.setdefault("cardData", {})

        restore_schema = FlowSaveSchema(**payload)
        FlowService.save(
            db,
            restore_schema,
            company_id=company_id,
            user_id=user_id,
            user_name=user_name,
            record_version=False,
        )

        return {
            "status": "discarded",
            "flow_id": flow.id,
            "project_id": flow.project_id,
            "flow_type": flow.flow_type,
            "restored_to_version_number": latest_definitive.version_number if latest_definitive else None,
            "has_uncommitted_draft": False,
            "flow": FlowService.load(
                db,
                project_id=flow.project_id,
                company_id=company_id,
                flow_id=flow.id,
                flow_type=flow.flow_type,
            ),
        }

    @staticmethod
    def load(
        db: Session,
        project_id: int = None,
        company_id: int = None,
        flow_id: int = None,
        flow_type: str = None,
        definitive_only: bool = False,
    ):
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
        if not flow and project_id and not flow_id:
            # Fallback: try matching FlowDB.id == project_id first (when caller passed flow.id in the path),
            # then fall back to latest flow for this project_id regardless of flow_type
            by_id_query = db.query(FlowDB).filter(FlowDB.id == project_id)
            if company_id:
                by_id_query = by_id_query.filter(FlowDB.company_id == company_id)
            flow = by_id_query.first()
            if not flow:
                fallback_query = db.query(FlowDB).filter(FlowDB.project_id == project_id)
                if company_id:
                    fallback_query = fallback_query.filter(FlowDB.company_id == company_id)
                flow = fallback_query.order_by(FlowDB.updated_at.desc()).first()

        if not flow:
            return {"project_id": project_id, "nodes": [], "edges": [], "cardData": {}, "flow_type": flow_type or "api"}

        if definitive_only:
            FlowService._ensure_version_table(db)
            latest_def = (
                db.query(FlowVersionDB)
                .filter(
                    FlowVersionDB.flow_id == flow.id,
                    FlowVersionDB.is_definitive.is_(True),
                )
                .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
                .first()
            )
            if latest_def:
                snap = dict(latest_def.snapshot or {})
                return {
                    "id": flow.id,
                    "project_id": flow.project_id,
                    "name": snap.get("name") or flow.name,
                    "flow_type": snap.get("flow_type") or flow.flow_type,
                    "nodes": snap.get("nodes") or [],
                    "edges": snap.get("edges") or [],
                    "cardData": snap.get("cardData") or {},
                    "updated_at": latest_def.created_at or flow.updated_at,
                    "version_number": latest_def.version_number,
                    "is_definitive": True,
                }
            has_any_ver = db.query(FlowVersionDB.id).filter(FlowVersionDB.flow_id == flow.id).first() is not None
            if has_any_ver:
                return {
                    "id": flow.id,
                    "project_id": flow.project_id,
                    "name": flow.name,
                    "flow_type": flow.flow_type,
                    "nodes": [],
                    "edges": [],
                    "cardData": {},
                    "updated_at": flow.updated_at,
                    "is_definitive": False,
                }

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

            node_data_obj = next((n.data for n in flow.flow_nodes if n.client_id == c.node_id and isinstance(n.data, dict)), {})
            cond_cfg = node_data_obj.get("conditionConfig") or (c.env_data.get("__conditionConfig") if isinstance(c.env_data, dict) else None)
            loop_cfg = node_data_obj.get("loopConfig") or (c.env_data.get("__loopConfig") if isinstance(c.env_data, dict) else None)
            try_catch_cfg = node_data_obj.get("tryCatchConfig") or (c.env_data.get("__tryCatchConfig") if isinstance(c.env_data, dict) else None)

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
                "conditionConfig": cond_cfg,
                "loopConfig": loop_cfg,
                "tryCatchConfig": try_catch_cfg,
                "envData": c.env_data or {}
            }

        has_uncommitted_draft = False
        if not definitive_only and isinstance(flow, FlowDB):
            FlowService._ensure_version_table(db)
            latest_ver = (
                db.query(FlowVersionDB)
                .filter(FlowVersionDB.flow_id == flow.id)
                .order_by(FlowVersionDB.version_number.desc(), FlowVersionDB.id.desc())
                .first()
            )
            has_uncommitted_draft = bool(latest_ver and not getattr(latest_ver, "is_definitive", True))

        return {
            "id": flow.id,
            "project_id": flow.project_id,
            "name": flow.name,
            "flow_type": flow.flow_type,
            "nodes": nodes,
            "edges": edges,
            "cardData": cardData,
            "updated_at": flow.updated_at,
            "has_uncommitted_draft": has_uncommitted_draft,
            **({"is_definitive": True} if definitive_only else {}),
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
            
            # 2. Delete cards, nodes, edges, versions
            FlowService._ensure_version_table(db)
            db.query(FlowVersionDB).filter(FlowVersionDB.flow_id == f.id).delete(synchronize_session=False)
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

