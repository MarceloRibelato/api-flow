import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from app.models.audit_models import AuditLogDB
from app.models.user_models import UserDB

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    def log_action(
        db: Session,
        company_id: int,
        user: Optional[UserDB],
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        resource_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None
    ) -> Optional[AuditLogDB]:
        """Registra uma ação de auditoria no banco de dados com isolamento multi-tenant por empresa."""
        if not company_id:
            logger.warning("AuditService.log_action chamado sem company_id validado.")
            return None

        try:
            user_id = user.id if user else None
            user_name = getattr(user, 'full_name', None) or getattr(user, 'username', 'Sistema') if user else 'Sistema'
            user_email = getattr(user, 'email', None) if user else None

            log_entry = AuditLogDB(
                company_id=company_id,
                user_id=user_id,
                user_name=user_name,
                user_email=user_email,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id is not None else None,
                resource_name=resource_name,
                details=details or {},
                ip_address=ip_address
            )
            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)
            return log_entry
        except Exception as e:
            logger.error(f"Erro ao salvar registro de auditoria: {str(e)}")
            db.rollback()
            return None

    @staticmethod
    def list_logs(
        db: Session,
        company_id: int,
        page: int = 1,
        per_page: int = 20,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        search: Optional[str] = None
    ):
        """Retorna registros de auditoria paginados para a empresa com informações de projeto e funcionalidade."""
        query = db.query(AuditLogDB).filter(AuditLogDB.company_id == company_id)

        if action and action != 'all':
            query = query.filter(AuditLogDB.action == action)

        if resource_type and resource_type != 'all':
            query = query.filter(AuditLogDB.resource_type == resource_type)

        if search:
            search_pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    AuditLogDB.user_name.ilike(search_pattern),
                    AuditLogDB.user_email.ilike(search_pattern),
                    AuditLogDB.resource_name.ilike(search_pattern),
                    AuditLogDB.action.ilike(search_pattern),
                    AuditLogDB.resource_type.ilike(search_pattern)
                )
            )

        total = query.count()
        offset = (page - 1) * per_page
        logs = query.order_by(desc(AuditLogDB.created_at)).offset(offset).limit(per_page).all()

        # Batch resolve feature, product, and flow information for logs
        feature_ids = set()
        product_ids = set()
        flow_ids = set()

        for log in logs:
            det = log.details or {}
            p_id = det.get("project_id") or det.get("feature_id")
            if p_id and str(p_id).isdigit():
                feature_ids.add(int(p_id))

            prod_id = det.get("product_id")
            if prod_id and str(prod_id).isdigit():
                product_ids.add(int(prod_id))

            fl_id = det.get("flow_id")
            if not fl_id and log.resource_type == "flow" and log.resource_id and log.resource_id.isdigit():
                fl_id = int(log.resource_id)
            if fl_id and str(fl_id).isdigit():
                flow_ids.add(int(fl_id))

        from app.models.feature_models import FeatureModel
        from app.models.product_models import ProductModel
        from app.models.flow_models import FlowDB

        features_map = {}
        if feature_ids:
            feats = db.query(FeatureModel).filter(FeatureModel.id.in_(list(feature_ids))).all()
            for f in feats:
                features_map[f.id] = {
                    "feature_name": f.name,
                    "product_name": f.product.name if f.product else "Projeto Geral",
                    "product_id": f.product_id
                }

        flows_map = {}
        if flow_ids:
            flows = db.query(FlowDB).filter(FlowDB.id.in_(list(flow_ids))).all()
            for fl in flows:
                flows_map[fl.id] = {
                    "feature_id": fl.project_id,
                    "flow_type": fl.flow_type,
                    "name": fl.name
                }
                if fl.project_id and fl.project_id not in features_map:
                    feature_ids.add(fl.project_id)

            missing_feats = [fid for fid in feature_ids if fid not in features_map]
            if missing_feats:
                feats = db.query(FeatureModel).filter(FeatureModel.id.in_(missing_feats)).all()
                for f in feats:
                    features_map[f.id] = {
                        "feature_name": f.name,
                        "product_name": f.product.name if f.product else "Projeto Geral",
                        "product_id": f.product_id
                    }

        products_map = {}
        if product_ids:
            prods = db.query(ProductModel).filter(ProductModel.id.in_(list(product_ids))).all()
            for p in prods:
                products_map[p.id] = p.name

        def _generate_action_description(act: str, res_name: str, res_type: str, det: dict) -> str:
            flow_type = (det.get("flow_type") or "").upper()
            nodes_count = det.get("nodes_count")
            deleted_nodes = det.get("deleted_nodes")
            added_nodes = det.get("added_nodes")
            
            if act == "SAVE_FLOW":
                current_nodes = det.get("current_nodes") or []
                if isinstance(current_nodes, list) and current_nodes:
                    node_summary = ", ".join(current_nodes[:3])
                    if len(current_nodes) > 3:
                        node_summary += f" (+{len(current_nodes)-3})"
                    return f"Salvou o fluxo {flow_type} ({nodes_count or len(current_nodes)} nós: {node_summary})"
                elif flow_type and nodes_count is not None:
                    return f"Salvou o fluxo {flow_type} ({nodes_count} nós)"
                elif flow_type:
                    return f"Salvou o fluxo {flow_type}"
                return f"Salvou o fluxo: {res_name or 'Sem nome'}"
            elif act == "DELETE_FLOW":
                return f"Excluiu o fluxo do projeto"
            elif act == "ADD_NODE":
                if isinstance(added_nodes, list) and added_nodes:
                    names = ", ".join(str(n) for n in added_nodes if n is not None)
                else:
                    names = str(res_name) if res_name else ""
                return f"Adicionou card(s): {names}"
            elif act == "DELETE_NODE":
                if isinstance(deleted_nodes, list) and deleted_nodes:
                    names = ", ".join(str(n) for n in deleted_nodes if n is not None)
                else:
                    names = str(res_name) if res_name else ""
                return f"Removeu card(s): {names}"
            elif act == "UPDATE_NODE":
                modified_nodes = det.get("modified_nodes")
                if isinstance(modified_nodes, list) and modified_nodes:
                    names = ", ".join(str(n) for n in modified_nodes if n is not None)
                else:
                    names = str(res_name) if res_name else ""
                return f"Alterou card(s): {names}"
            elif act == "HEAL_STEP":
                return f"Aplicou auto-healing no passo"
            elif act == "RUN_EXECUTION":
                return f"Iniciou execução de testes: {res_name or 'Suite'}"
            elif act == "EXECUTE_AI_SKILL":
                return f"Executou IA: {res_name or 'Skill'}"
            elif act == "SAVE_AI_GENERATED_FLOW":
                return f"Aplicou fluxo de IA: {res_name or 'Novo Fluxo'}"
            elif act == "CREATE_VARIABLE":
                return f"Criou variável: {res_name or 'Nova Variável'}"
            elif act == "UPDATE_VARIABLE":
                return f"Atualizou variável: {res_name}"
            elif act == "DELETE_VARIABLE":
                return f"Excluiu variável: {res_name}"
            elif act == "BULK_CREATE_VARIABLES":
                return f"Importou variáveis: {res_name}"
            elif act == "CREATE_PRODUCT":
                return f"Criou o projeto: {res_name or 'Novo Projeto'}"
            elif act == "UPDATE_PRODUCT":
                return f"Atualizou o projeto: {res_name}"
            elif act == "DELETE_PRODUCT":
                return f"Excluiu o projeto: {res_name}"
            elif act == "UPDATE_PRODUCT_MOBILE_SETTINGS":
                return f"Atualizou configurações mobile do projeto: {res_name}"
            elif act == "CREATE_FEATURE":
                return f"Criou a funcionalidade: {res_name or 'Nova Funcionalidade'}"
            elif act == "UPDATE_FEATURE":
                return f"Atualizou a funcionalidade: {res_name}"
            elif act == "DELETE_FEATURE":
                return f"Excluiu a funcionalidade: {res_name}"
            elif act == "REORDER_FEATURES":
                return f"Reordenou as funcionalidades do projeto"
            elif act == "CREATE_SCHEDULE":
                return f"Criou agendamento: {res_name or 'Novo Agendamento'}"
            elif act == "DELETE_SCHEDULE":
                return f"Excluiu agendamento: {res_name or 'Agendamento'}"
            elif act == "PAUSE_SCHEDULE":
                return f"Pausou agendamento: {res_name or 'Agendamento'}"
            elif act == "RESUME_SCHEDULE":
                return f"Reativou agendamento: {res_name or 'Agendamento'}"
            elif act == "EXECUTE_PIPELINE":
                return f"Disparou execução via Pipeline CI/CD: {res_name or 'Pipeline'}"
            elif act == "CREATE_SERVICE_TOKEN":
                return f"Criou token de serviço CI/CD: {res_name}"
            elif act == "DELETE_SERVICE_TOKEN":
                return f"Revogou token de serviço CI/CD: {res_name}"
            elif act == "RUN_SCHEDULED_EXECUTION":
                return f"Execução agendada automática iniciada: {res_name or 'Agendamento'}"
            elif act == "RETRY_EXECUTION":
                return f"Re-executou testes com falha: {res_name or 'Re-execução'}"
            return res_name or act

        items = []
        for log in logs:
            det = dict(log.details or {})
            feat_id = det.get("project_id") or det.get("feature_id")
            fl_id = det.get("flow_id") or (int(log.resource_id) if (log.resource_type == "flow" and log.resource_id and log.resource_id.isdigit()) else None)

            if not feat_id and fl_id and fl_id in flows_map:
                feat_id = flows_map[fl_id]["feature_id"]

            resolved_feature_name = det.get("feature_name")
            resolved_project_name = det.get("project_name") or det.get("product_name")

            if feat_id and int(feat_id) in features_map:
                f_info = features_map[int(feat_id)]
                if not resolved_feature_name:
                    resolved_feature_name = f_info["feature_name"]
                if not resolved_project_name:
                    resolved_project_name = f_info["product_name"]

            prod_id = det.get("product_id")
            if prod_id and int(prod_id) in products_map and not resolved_project_name:
                resolved_project_name = products_map[int(prod_id)]

            if not resolved_feature_name:
                resolved_feature_name = log.resource_name or "Geral"
            if not resolved_project_name:
                resolved_project_name = "Projeto Geral"

            action_desc = _generate_action_description(log.action, log.resource_name, log.resource_type, det)

            items.append({
                "id": log.id,
                "user_id": log.user_id,
                "user_name": log.user_name or "Sistema",
                "user_email": log.user_email or "",
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "resource_name": log.resource_name,
                "project_name": resolved_project_name,
                "feature_name": resolved_feature_name,
                "action_description": action_desc,
                "details": det,
                "ip_address": log.ip_address,
                "created_at": log.created_at.isoformat() if log.created_at else None
            })

        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if per_page > 0 else 1
        }

    @staticmethod
    def get_action_types(db: Session, company_id: int):
        """Retorna a lista de ações únicas gravadas para a empresa para os filtros da UI."""
        actions = db.query(AuditLogDB.action).filter(AuditLogDB.company_id == company_id).distinct().all()
        return [a[0] for a in actions if a[0]]
