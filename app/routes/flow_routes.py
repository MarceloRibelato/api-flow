from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.flow_schemas import FlowSaveSchema
from app.services.flow_service import FlowService

from ..database import get_db

from ..auth import get_current_user
from ..models.user_models import UserDB

router = APIRouter(prefix="/flow", tags=["Flow"])


@router.get("/list/{project_id}")
def list_flows(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Lista todos os fluxos de um projeto"""
    return FlowService.list_by_project(db, project_id, company_id=current_user.company_id)


@router.post("/create")
def create_flow(
    data: dict, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
): 
    """Cria um novo fluxo vazio no projeto"""
    from app.exceptions import InsufficientPermissionsError, ValidationError, NotFoundError
    
    if current_user.role == 'viewer':
        raise InsufficientPermissionsError()

    project_id = data.get("projectId")
    name = data.get("name")
    if not project_id or not name:
         raise ValidationError(detail="projectId e name são obrigatórios")
         
    res = FlowService.create(db, project_id, name, company_id=current_user.company_id)
    if not res:
         raise NotFoundError(resource="Projeto")
    return res


@router.get("/load/{project_id}")
def load_flow(
    project_id: int, 
    flow_id: int = None, 
    flow_type: str = "api", # New param
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Carrega o fluxo completo."""
    return FlowService.load(db, project_id, company_id=current_user.company_id, flow_id=flow_id, flow_type=flow_type)


@router.post("/save")
def save_flow(
    data: FlowSaveSchema, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Salva o fluxo completo"""
    from app.exceptions import InsufficientPermissionsError, ValidationError
    from app.services.audit_service import AuditService
    from app.models.feature_models import FeatureModel
    
    if current_user.role == 'viewer':
        raise InsufficientPermissionsError()

    if not data.projectId or data.projectId <= 0:
        raise ValidationError(detail="projectId deve ser um número positivo")

    try:
        res = FlowService.save(db, data, company_id=current_user.company_id, user_id=current_user.id)
        deleted_nodes = res.get("deleted_nodes", []) if isinstance(res, dict) else []
        added_nodes = res.get("added_nodes", []) if isinstance(res, dict) else []

        feature = db.query(FeatureModel).filter(FeatureModel.id == data.projectId).first()
        feat_name = feature.name if feature else f"Funcionalidade #{data.projectId}"
        prod_name = feature.product.name if (feature and feature.product) else "Projeto Geral"

        deleted_nodes = res.get("deleted_nodes", []) if isinstance(res, dict) else []
        added_nodes = res.get("added_nodes", []) if isinstance(res, dict) else []
        modified_nodes = res.get("modified_nodes", []) if isinstance(res, dict) else []
        current_nodes_list = [str(n) for n in res.get("current_nodes", [])] if isinstance(res, dict) and res.get("current_nodes") else []
        
        clean_deleted_nodes = [str(n) for n in deleted_nodes if n is not None]
        clean_added_nodes = [str(n) for n in added_nodes if n is not None]
        clean_modified_nodes = [str(n) for n in modified_nodes if n is not None]

        if clean_deleted_nodes:
            AuditService.log_action(
                db=db,
                company_id=current_user.company_id,
                user=current_user,
                action="DELETE_NODE",
                resource_type="node",
                resource_id=str(res.get("id")) if isinstance(res, dict) else str(data.projectId),
                resource_name=", ".join(clean_deleted_nodes),
                details={
                    "project_id": data.projectId,
                    "feature_name": feat_name,
                    "project_name": prod_name,
                    "flow_type": data.flow_type,
                    "deleted_nodes": clean_deleted_nodes,
                    "added_nodes": clean_added_nodes,
                    "modified_nodes": clean_modified_nodes,
                    "current_nodes": current_nodes_list,
                    "previous_nodes_count": res.get("previous_nodes_count", 0) if isinstance(res, dict) else 0,
                    "current_nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0,
                    "nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0
                }
            )

        if clean_added_nodes:
            AuditService.log_action(
                db=db,
                company_id=current_user.company_id,
                user=current_user,
                action="ADD_NODE",
                resource_type="node",
                resource_id=str(res.get("id")) if isinstance(res, dict) else str(data.projectId),
                resource_name=", ".join(clean_added_nodes),
                details={
                    "project_id": data.projectId,
                    "feature_name": feat_name,
                    "project_name": prod_name,
                    "flow_type": data.flow_type,
                    "added_nodes": clean_added_nodes,
                    "deleted_nodes": clean_deleted_nodes,
                    "modified_nodes": clean_modified_nodes,
                    "current_nodes": current_nodes_list,
                    "previous_nodes_count": res.get("previous_nodes_count", 0) if isinstance(res, dict) else 0,
                    "current_nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0,
                    "nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0
                }
            )

        if clean_modified_nodes:
            AuditService.log_action(
                db=db,
                company_id=current_user.company_id,
                user=current_user,
                action="UPDATE_NODE",
                resource_type="node",
                resource_id=str(res.get("id")) if isinstance(res, dict) else str(data.projectId),
                resource_name=", ".join(clean_modified_nodes),
                details={
                    "project_id": data.projectId,
                    "feature_name": feat_name,
                    "project_name": prod_name,
                    "flow_type": data.flow_type,
                    "modified_nodes": clean_modified_nodes,
                    "deleted_nodes": clean_deleted_nodes,
                    "added_nodes": clean_added_nodes,
                    "current_nodes": current_nodes_list,
                    "previous_nodes_count": res.get("previous_nodes_count", 0) if isinstance(res, dict) else 0,
                    "current_nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0,
                    "nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0
                }
            )

        if not clean_deleted_nodes and not clean_added_nodes and not clean_modified_nodes:
            AuditService.log_action(
                db=db,
                company_id=current_user.company_id,
                user=current_user,
                action="SAVE_FLOW",
                resource_type="flow",
                resource_id=str(res.get("id")) if isinstance(res, dict) else str(data.projectId),
                resource_name=data.name or f"Fluxo {data.flow_type.upper()}",
                details={
                    "project_id": data.projectId,
                    "feature_name": feat_name,
                    "project_name": prod_name,
                    "flow_type": data.flow_type,
                    "current_nodes": current_nodes_list,
                    "previous_nodes_count": res.get("previous_nodes_count", 0) if isinstance(res, dict) else 0,
                    "current_nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0,
                    "nodes_count": res.get("current_nodes_count", 0) if isinstance(res, dict) else 0
                }
            )
        return res
    except ValueError as e:
        raise ValidationError(detail=str(e))
    except Exception as e:
        import traceback
        with open("save_error.log", "w") as f:
            f.write(traceback.format_exc())
        raise


@router.delete("/{project_id}")
def delete_flow(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Remove o fluxo de um projeto"""
    from app.exceptions import InsufficientPermissionsError, NotFoundError
    from app.services.audit_service import AuditService
    
    if current_user.role == 'viewer':
        raise InsufficientPermissionsError()

    success = FlowService.delete(db, project_id, company_id=current_user.company_id)
    if not success:
        raise NotFoundError(resource="Fluxo do projeto")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="DELETE_FLOW",
        resource_type="flow",
        resource_id=str(project_id),
        resource_name=f"Fluxo Projeto #{project_id}",
        details={"project_id": project_id}
    )

    return {
        "status": "deleted",
        "message": f"Fluxo do projeto {project_id} removido com sucesso",
        "projectId": project_id,
    }
@router.get("/stats/{project_id}")
def get_flow_stats(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Retorna estatísticas do fluxo (nós, arestas, cards, bdd, api)"""
    return FlowService.get_stats(db, project_id)


@router.get("/cards/inventory")
def get_cards_inventory(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Retorna o inventário de cards da empresa"""
    return FlowService.get_cards_inventory(db, company_id=current_user.company_id)

from app.schemas.flow_schemas import TestDbSchema
from app.services.database_executor_service import DatabaseExecutorService
from app.services.variable_service import VariableService

@router.post("/test-db")
def test_db_connection(
    data: TestDbSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Testa uma consulta de banco de dados e retorna o resultado"""
    variables_dict = {}
    
    if data.environmentId:
        from app.models.variable_model import Variable
        vars_db = db.query(Variable).filter(Variable.environment_id == data.environmentId).all()
        variables_dict = {v.name: v.value for v in vars_db}

    step_data = {
        "connection_string": data.connectionString,
        "query": data.query,
        "timeout": 10,
        "assertions": data.assertions or [],
        "extracts": data.extracts or []
    }
    
    return DatabaseExecutorService.execute_db_step(step_data, variables_dict)

from app.schemas.flow_schemas import HealStepSchema
@router.put("/heal-step")
def heal_flow_step(
    data: HealStepSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Aplica uma correção de auto-healing ao fluxo persistido"""
    from app.services.audit_service import AuditService
    try:
        updated_flow = FlowService.apply_healing(db, data.project_id, current_user.company_id, data.flow_id, data.node_id, data.old_selector, data.new_selector)
        AuditService.log_action(
            db=db,
            company_id=current_user.company_id,
            user=current_user,
            action="HEAL_STEP",
            resource_type="flow_step",
            resource_id=str(data.node_id),
            resource_name=f"Nó {data.node_id}",
            details={"project_id": data.project_id, "flow_id": data.flow_id, "old_selector": data.old_selector, "new_selector": data.new_selector}
        )
        return {"status": "success", "message": "Healing applied successfully", "flow": updated_flow}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
