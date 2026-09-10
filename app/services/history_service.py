import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import distinct, func, case
from sqlalchemy.orm import Session

from app.models.api_test_history_models import (
    ApiExecutionHistory,
    WebExecutionHistory,
    MobileExecutionHistory,
    ApiHistoryDetails,
    WebHistoryDetails,
    MobileHistoryDetails
)
from app.models.user_models import UserDB
from app.schemas.history_schemas import ExecutionHistoryCreate


class HistoryService:
    @staticmethod
    def get_model(execution_type: Optional[str] = None):
        """Resolves the concrete partitioned table model based on execution_type."""
        t = (execution_type or "api").lower().strip()
        if t in ("web", "e2e"):
            return WebExecutionHistory
        if t in ("mobile", "app"):
            return MobileExecutionHistory
        return ApiExecutionHistory

    @staticmethod
    def save(db: Session, history: ExecutionHistoryCreate, user_id: int, commit: bool = True):
        execution_id = f"exec_{uuid.uuid4().hex[:10]}_{int(datetime.now().timestamp())}"

        # Filter variables: Saves only those that actually appear in the request
        filtered_vars = HistoryService._filter_variables(history)

        # Resolving trigger origin
        trigger_origin = history.trigger_origin or "manual"
        if trigger_origin == "manual" and history.schedule_id:
            from app.models.schedule_models import ScheduleModel
            sched = db.query(ScheduleModel).filter(ScheduleModel.id == history.schedule_id).first()
            if sched:
                if sched.name and (sched.name.startswith("CI/CD") or sched.name.startswith("[PIPELINE]")):
                    trigger_origin = "pipeline"
                else:
                    trigger_origin = "schedule"

        exec_type = history.execution_type
        if not exec_type and history.schedule_id:
            try:
                from app.models.schedule_models import ScheduleModel
                sched = db.query(ScheduleModel).filter(ScheduleModel.id == history.schedule_id).first()
                if sched and sched.flow_type:
                    exec_type = 'web' if sched.flow_type == 'e2e' else sched.flow_type
            except Exception:
                pass

        if not exec_type and history.flow_id:
            try:
                from app.models.flow import FlowDB
                flow_obj = db.query(FlowDB).filter(FlowDB.id == int(history.flow_id)).first()
                if flow_obj and flow_obj.flow_type:
                    exec_type = 'web' if flow_obj.flow_type == 'e2e' else flow_obj.flow_type
            except Exception:
                pass

        if not exec_type:
            exec_type = "api"

        ModelClass = HistoryService.get_model(exec_type)

        db_history = ModelClass(
            execution_id=execution_id,
            batch_id=history.batch_id,
            api_id=history.api_id,
            api_name=history.api_name,
            project_id=history.project_id,
            schedule_id=history.schedule_id,
            feature_name=history.feature_name,
            flow_id=history.flow_id,
            node_id=history.node_id,
            node_name=history.node_name,
            method=history.method,
            url=history.url,
            request_headers=history.request_headers,
            request_body=history.request_body,
            request_params=history.request_params,
            status_code=history.status_code,
            status_text=history.status_text,
            response_headers=history.response_headers,
            response_body=history.response_body,
            response_time=history.response_time,
            error_message=history.error_message,
            variables_used=filtered_vars,
            processed_url=history.processed_url,
            user_id=user_id,
            environment_id=history.environment_id,
            environment_name=history.environment_name,
            assertions=[a.model_dump() for a in history.assertions]
            if history.assertions
            else None,
            video_url=history.video_url,
            healed_selector=history.healed_selector,
            execution_type=exec_type,
            trigger_origin=trigger_origin,
            retry_count=getattr(history, 'retry_count', 0) or 0,
        )

        db.add(db_history)
        if commit:
            db.commit()
            db.refresh(db_history)
        return db_history

    @staticmethod
    def update_video_url_by_batch(db: Session, batch_id: str, video_url: str):
        if not batch_id or not video_url:
            return
        for ModelClass in (WebExecutionHistory, MobileExecutionHistory, ApiExecutionHistory):
            updated = db.query(ModelClass).filter(
                ModelClass.batch_id == batch_id,
                ModelClass.video_url == None
            ).update({"video_url": video_url})
            if updated:
                break
        db.commit()

    @staticmethod
    def save_batch(db: Session, histories: list[ExecutionHistoryCreate], user_id: int):
        if not histories:
            return

        db_objects = []
        timestamp = int(datetime.now().timestamp())
        schedule_origin_cache = {}

        for i, history in enumerate(histories):
            execution_id = f"exec_{uuid.uuid4().hex[:10]}_{timestamp}_{i}"
            filtered_vars = HistoryService._filter_variables(history)
            exec_type = history.execution_type
            if not exec_type and history.schedule_id:
                try:
                    from app.models.schedule_models import ScheduleModel
                    sched = db.query(ScheduleModel).filter(ScheduleModel.id == history.schedule_id).first()
                    if sched and sched.flow_type:
                        exec_type = 'web' if sched.flow_type == 'e2e' else sched.flow_type
                except Exception:
                    pass

            if not exec_type and history.flow_id:
                try:
                    from app.models.flow import FlowDB
                    flow_obj = db.query(FlowDB).filter(FlowDB.id == int(history.flow_id)).first()
                    if flow_obj and flow_obj.flow_type:
                        exec_type = 'web' if flow_obj.flow_type == 'e2e' else flow_obj.flow_type
                except Exception:
                    pass

            if not exec_type:
                exec_type = "api"

            ModelClass = HistoryService.get_model(exec_type)

            trigger_origin = history.trigger_origin or "manual"
            if trigger_origin == "manual" and history.schedule_id:
                if history.schedule_id not in schedule_origin_cache:
                    from app.models.schedule_models import ScheduleModel
                    sched = db.query(ScheduleModel).filter(ScheduleModel.id == history.schedule_id).first()
                    if sched:
                        if sched.name and (sched.name.startswith("CI/CD") or sched.name.startswith("[PIPELINE]")):
                            schedule_origin_cache[history.schedule_id] = "pipeline"
                        else:
                            schedule_origin_cache[history.schedule_id] = "schedule"
                    else:
                        schedule_origin_cache[history.schedule_id] = "manual"
                trigger_origin = schedule_origin_cache[history.schedule_id]

            db_history = ModelClass(
                execution_id=execution_id,
                batch_id=history.batch_id,
                api_id=history.api_id,
                api_name=history.api_name,
                project_id=history.project_id,
                schedule_id=history.schedule_id,
                feature_name=history.feature_name,
                flow_id=history.flow_id,
                node_id=history.node_id,
                node_name=history.node_name,
                method=history.method,
                url=history.url,
                request_headers=history.request_headers,
                request_body=history.request_body,
                request_params=history.request_params,
                status_code=history.status_code,
                status_text=history.status_text,
                response_headers=history.response_headers,
                response_body=history.response_body,
                response_time=history.response_time,
                error_message=history.error_message,
                variables_used=filtered_vars,
                processed_url=history.processed_url,
                user_id=user_id,
                environment_id=history.environment_id,
                environment_name=history.environment_name,
                assertions=[a.model_dump() for a in history.assertions] if history.assertions else None,
                video_url=history.video_url,
                healed_selector=history.healed_selector,
                execution_type=exec_type,
                trigger_origin=trigger_origin,
                retry_count=getattr(history, 'retry_count', 0) or 0,
            )
            db_objects.append(db_history)

        db.bulk_save_objects(db_objects)
        db.commit()

    @staticmethod
    def _filter_variables(history: ExecutionHistoryCreate):
        filtered_vars = {}
        if history.variables_used:
            search_space = (
                f"{history.url} {history.processed_url} {history.request_body or ''} "
            )
            search_space += str(history.request_headers or "") + " "
            search_space += str(history.request_params or "")

            for var_name, var_value in history.variables_used.items():
                if f"{{{{{var_name}}}}}" in search_space:
                    filtered_vars[var_name] = var_value
        return filtered_vars

    @staticmethod
    def get_all(
        db: Session,
        company_id: int,  
        page: int = 1,
        limit: int = 20,
        api_id: Optional[int] = None,
        project_id: Optional[int] = None,
        flow_id: Optional[int] = None,
        environment_id: Optional[int] = None,
        schedule_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        node_id: Optional[str] = None,
        execution_type: Optional[str] = None,
        batch_id: Optional[str] = None,
        sort_by: str = 'created_at',
        order: str = 'desc'
    ):
        from app.models.user_models import UserDB

        # Automatic execution_type detection if not explicitly provided
        if not execution_type:
            if schedule_id is not None:
                from app.models.schedule_models import ScheduleModel
                sched = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
                if sched and getattr(sched, 'flow_type', None):
                    execution_type = sched.flow_type
            elif flow_id is not None:
                from app.models.flow_models import FlowDB
                f = db.query(FlowDB).filter(FlowDB.id == str(flow_id)).first()
                if f and getattr(f, 'flow_type', None):
                    execution_type = f.flow_type

        ModelClass = HistoryService.get_model(execution_type)
        query = db.query(ModelClass)

        # Always join UserDB to ensure company scoping
        query = query.join(UserDB, ModelClass.user_id == UserDB.id).filter(
            UserDB.company_id == company_id
        )

        if schedule_id is not None:
            query = query.filter(ModelClass.schedule_id == schedule_id)

        if project_id is not None:
            query = query.filter(ModelClass.project_id == project_id)
        if api_id is not None: 
            query = query.filter(ModelClass.api_id == str(api_id))
        if flow_id is not None: 
            query = query.filter(ModelClass.flow_id == str(flow_id))
        if environment_id is not None: 
            query = query.filter(ModelClass.environment_id == environment_id)
        if node_id:
            query = query.filter(ModelClass.node_id == str(node_id))
        if execution_type:
            query = query.filter(ModelClass.execution_type == execution_type)
        if method:
            query = query.filter(ModelClass.method == method.upper())
        if status_code is not None:
            query = query.filter(ModelClass.status_code == status_code)
        if batch_id:
            query = query.filter(ModelClass.batch_id.startswith(batch_id))
        
        # Exact date filter to isolate batches sharing the same ID
        if start_date and start_date == end_date:
            if isinstance(start_date, str):
                dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                query = query.filter(ModelClass.created_at == dt)
            else:
                query = query.filter(ModelClass.created_at == start_date)
            start_date = None
            end_date = None

        if start_date:
            query = query.filter(ModelClass.created_at >= start_date)
        if end_date:
            if isinstance(end_date, datetime) and end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
                dt_end = datetime.combine(end_date.date(), time(23, 59, 59))
            else:
                dt_end = end_date
            query = query.filter(ModelClass.created_at <= dt_end)

        total = query.count()

        # Fallback check across tables if querying for a specific schedule_id or batch_id returned 0
        if total == 0 and (schedule_id is not None or batch_id is not None):
            for AltModel in [WebExecutionHistory, ApiExecutionHistory, MobileExecutionHistory]:
                if AltModel != ModelClass:
                    alt_q = db.query(AltModel).join(UserDB, AltModel.user_id == UserDB.id).filter(
                        UserDB.company_id == company_id
                    )
                    if schedule_id is not None:
                        alt_q = alt_q.filter(AltModel.schedule_id == schedule_id)
                    if batch_id is not None:
                        alt_q = alt_q.filter(AltModel.batch_id.startswith(batch_id))
                    alt_count = alt_q.count()
                    if alt_count > 0:
                        ModelClass = AltModel
                        query = alt_q
                        total = alt_count
                        break

        total_pages = (total + limit - 1) // limit if limit > 0 else 0

        sort_column = getattr(ModelClass, sort_by, ModelClass.created_at)
        if order == 'asc':
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

        skip = (page - 1) * limit
        from sqlalchemy.orm import load_only
        query = query.options(load_only(
            ModelClass.id, ModelClass.execution_id, ModelClass.feature_name,
            ModelClass.method, ModelClass.url, ModelClass.status_code,
            ModelClass.status_text, ModelClass.response_time, ModelClass.created_at,
            ModelClass.processed_url, ModelClass.environment_id, ModelClass.environment_name,
            ModelClass.node_name, ModelClass.api_name, ModelClass.error_message,
            ModelClass.assertions, ModelClass.node_id, ModelClass.batch_id,
            ModelClass.video_url, ModelClass.execution_type, ModelClass.response_body,
            ModelClass.flow_id, ModelClass.api_id, ModelClass.retry_count,
            ModelClass.schedule_id, ModelClass.project_id
        ))

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "items": query.offset(skip).limit(limit).all(),
        }

    @staticmethod
    def get_batches(
        db: Session,
        company_id: int,
        page: int = 1,
        limit: int = 10,
        project_id: Optional[int] = None,
        flow_id: Optional[int] = None,
        schedule_type: Optional[str] = None,
        execution_type: Optional[str] = None
    ):
        from app.models.user_models import UserDB
        from app.models.schedule_models import ScheduleModel

        # Automatic execution_type detection if not explicitly provided
        if not execution_type and flow_id is not None:
            from app.models.flow_models import FlowDB
            f = db.query(FlowDB).filter(FlowDB.id == str(flow_id)).first()
            if f and getattr(f, 'flow_type', None):
                execution_type = f.flow_type

        ModelClass = HistoryService.get_model(execution_type)
        clean_batch_expr = func.regexp_replace(ModelClass.batch_id, '(_retry|_path).*$', '')
        
        query = db.query(
            clean_batch_expr.label('batch_id'),
            func.min(ModelClass.created_at).label('started_at'),
            func.count().label('total_requests'),
            func.sum(case((ModelClass.error_message == None, 1), else_=0)).label('success_requests'),
            func.sum(case((ModelClass.error_message != None, 1), else_=0)).label('failed_requests'),
            func.sum(ModelClass.response_time).label('duration_ms'),
            func.max(ModelClass.execution_type).label('execution_type')
        ).join(UserDB, ModelClass.user_id == UserDB.id).filter(
            UserDB.company_id == company_id,
            ModelClass.batch_id != None
        )

        if schedule_type is not None:
            query = query.join(ScheduleModel, ModelClass.schedule_id == ScheduleModel.id).filter(
                ScheduleModel.type == schedule_type
            )
            if schedule_type == 'suite' and project_id is not None:
                query = query.filter(ScheduleModel.target_id == project_id)
            elif project_id is not None:
                query = query.filter(ModelClass.project_id == project_id)
        elif project_id is not None:
            query = query.filter(ModelClass.project_id == project_id)
        
        if flow_id is not None:
            query = query.filter(ModelClass.flow_id == str(flow_id))

        query = query.group_by(clean_batch_expr)
        
        total = query.count()

        # Fallback check across tables if querying for a specific flow_id returned 0
        if total == 0 and flow_id is not None:
            for AltModel in [WebExecutionHistory, ApiExecutionHistory, MobileExecutionHistory]:
                if AltModel != ModelClass:
                    alt_clean = func.regexp_replace(AltModel.batch_id, '(_retry|_path).*$', '')
                    alt_q = db.query(
                        alt_clean.label('batch_id'),
                        func.min(AltModel.created_at).label('started_at'),
                        func.count().label('total_requests'),
                        func.sum(case((AltModel.error_message == None, 1), else_=0)).label('success_requests'),
                        func.sum(case((AltModel.error_message != None, 1), else_=0)).label('failed_requests'),
                        func.sum(AltModel.response_time).label('duration_ms'),
                        func.max(AltModel.execution_type).label('execution_type')
                    ).join(UserDB, AltModel.user_id == UserDB.id).filter(
                        UserDB.company_id == company_id,
                        AltModel.batch_id != None,
                        AltModel.flow_id == str(flow_id)
                    ).group_by(alt_clean)
                    alt_total = alt_q.count()
                    if alt_total > 0:
                        ModelClass = AltModel
                        query = alt_q
                        total = alt_total
                        break

        total_pages = (total + limit - 1) // limit if limit > 0 else 0
        
        skip = (page - 1) * limit
        results = query.order_by(func.min(ModelClass.created_at).desc()).offset(skip).limit(limit).all()
        
        batches = []
        for r in results:
            batches.append({
                "id": r.batch_id,
                "started_at": r.started_at,
                "total_requests": r.total_requests,
                "success_requests": r.success_requests or 0,
                "failed_requests": r.failed_requests or 0,
                "duration_ms": r.duration_ms or 0,
                "execution_type": r.execution_type or (execution_type or 'api')
            })
            
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "batches": batches
        }

    @staticmethod
    def get_by_id(db: Session, execution_id: str, company_id: int, execution_type: Optional[str] = None):
        models = [HistoryService.get_model(execution_type)] if execution_type else [ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory]
        for ModelClass in models:
            query = db.query(ModelClass).join(UserDB, ModelClass.user_id == UserDB.id).filter(
                UserDB.company_id == company_id
            )
            if execution_id.isdigit():
                history = query.filter(ModelClass.id == int(execution_id)).first()
                if history:
                    return history
            history = query.filter(ModelClass.execution_id == execution_id).first()
            if history:
                return history
        return None

    @staticmethod
    def delete(db: Session, execution_id: str, company_id: int):
        from app.models.user_models import UserDB
        for ModelClass in (ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory):
            ids_to_delete = db.query(ModelClass.id).outerjoin(UserDB).filter(
                ModelClass.execution_id == execution_id,
                (UserDB.company_id == company_id) | (ModelClass.user_id == None)
            ).all()
            
            ids_list = [row[0] for row in ids_to_delete]
            if ids_list:
                deleted_count = db.query(ModelClass).filter(
                    ModelClass.id.in_(ids_list)
                ).delete(synchronize_session=False)
                db.commit()
                return deleted_count > 0
        return False

    @staticmethod
    def delete_batch(db: Session, batch_id: str, company_id: int):
        from app.models.user_models import UserDB
        total_deleted = 0
        for ModelClass in (ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory):
            ids_to_delete = db.query(ModelClass.id).outerjoin(UserDB).filter(
                ModelClass.batch_id.startswith(batch_id),
                (UserDB.company_id == company_id) | (ModelClass.user_id == None)
            ).all()
            
            ids_list = [row[0] for row in ids_to_delete]
            if ids_list:
                deleted_count = db.query(ModelClass).filter(
                    ModelClass.id.in_(ids_list)
                ).delete(synchronize_session=False)
                total_deleted += deleted_count
        db.commit()
        return total_deleted > 0

    @staticmethod
    def clear_all(db: Session, company_id: int, execution_type: Optional[str] = None):
        from app.models.user_models import UserDB
        models = [HistoryService.get_model(execution_type)] if execution_type else [ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory]
        for ModelClass in models:
            ids_to_delete = db.query(ModelClass.id).outerjoin(UserDB).filter(
                (UserDB.company_id == company_id) | (ModelClass.user_id == None)
            ).all()
            
            ids_list = [row[0] for row in ids_to_delete]
            if ids_list:
                batch_size = 5000
                for i in range(0, len(ids_list), batch_size):
                    batch_ids = ids_list[i:i + batch_size]
                    db.query(ModelClass).filter(
                        ModelClass.id.in_(batch_ids)
                    ).delete(synchronize_session=False)
        db.commit()

    @staticmethod
    def get_unique_apis(db: Session, company_id: int, execution_type: Optional[str] = None):
        ModelClass = HistoryService.get_model(execution_type)
        results = (
            db.query(distinct(ModelClass.method).label("method"), ModelClass.url)
            .join(UserDB, ModelClass.user_id == UserDB.id)
            .filter(UserDB.company_id == company_id)
            .group_by(ModelClass.method, ModelClass.url)
            .all()
        )
        return [{"method": r.method, "url": r.url} for r in results]

    @staticmethod
    def permanent_delete_old_records(db: Session, days: int, company_id: Optional[int] = None):
        """
        Permanently deletes execution records older than specified days across all execution tables.
        """
        threshold = datetime.now(timezone.utc) - timedelta(days=days)
        total_deleted = 0
        try:
            for ModelClass in (ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory):
                query = db.query(ModelClass).filter(ModelClass.created_at < threshold)
                if company_id is not None:
                    query = query.join(UserDB, ModelClass.user_id == UserDB.id).filter(UserDB.company_id == company_id)
                    ids = [r[0] for r in query.with_entities(ModelClass.id).all()]
                    if ids:
                        deleted = db.query(ModelClass).filter(ModelClass.id.in_(ids)).delete(synchronize_session=False)
                        total_deleted += deleted
                else:
                    deleted = query.delete(synchronize_session=False)
                    total_deleted += deleted
            db.commit()
            return total_deleted
        except Exception as e:
            db.rollback()
            raise e

    # Backwards compatibility dummy methods
    @staticmethod
    def archive_old_records(db: Session, days: int, company_id: Optional[int] = None):
        return HistoryService.permanent_delete_old_records(db, days, company_id)

    @staticmethod
    def purge_archived_records(db: Session, days: int, company_id: Optional[int] = None):
        return 0

    @staticmethod
    def get_history_averages(db: Session, schedule_id: Optional[int] = None, flow_id: Optional[int] = None, api_id: Optional[int] = None, execution_type: Optional[str] = None):
        if not any([schedule_id, flow_id, api_id]):
            return []

        ModelClass = HistoryService.get_model(execution_type)

        filters = []
        if schedule_id:
            filters.append(ModelClass.schedule_id == schedule_id)
        elif flow_id:
            filters.append(ModelClass.flow_id == flow_id)
        elif api_id:
            filters.append(ModelClass.api_id == api_id)

        query = db.query(
            ModelClass.feature_name, ModelClass.node_name, ModelClass.api_name, ModelClass.url,
            func.avg(ModelClass.response_time).label('avg_time'),
            func.count(ModelClass.id).label('total_runs'),
            func.sum(case((ModelClass.error_message.is_(None), 1), else_=0)).label('pass_count')
        ).filter(*filters).group_by(
            ModelClass.feature_name, ModelClass.node_name, ModelClass.api_name, ModelClass.url
        )
        results = query.all()
        
        trends = db.query(
            ModelClass.feature_name, ModelClass.node_name, ModelClass.api_name, ModelClass.url, ModelClass.response_time
        ).filter(*filters).order_by(ModelClass.created_at.desc()).limit(1000).all()
        
        trends_map = {}
        for tr in trends:
            key = (tr.feature_name or 'Flow Principal', tr.node_name or 'Sem Nó', tr.api_name or tr.url or 'Passo')
            if key not in trends_map:
                trends_map[key] = []
            if len(trends_map[key]) < 10:
                trends_map[key].append(tr.response_time)
        
        stats = []
        for r in results:
            f_name, n_name, s_name = r.feature_name or 'Flow Principal', r.node_name or 'Sem Nó', r.api_name or r.url or 'Passo'
            stats.append({
                "feature_name": f_name, "node_name": n_name, "step_name": s_name,
                "avg_time": float(r.avg_time) if r.avg_time is not None else 0,
                "total_runs": r.total_runs, "pass_count": int(r.pass_count) if r.pass_count is not None else 0,
                "history": list(reversed(trends_map.get((f_name, n_name, s_name), [])))
            })
        return stats
