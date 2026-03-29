import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import distinct, func, case
from sqlalchemy.orm import Session

from app.models.api_test_history_models import ApiExecutionHistory, ApiExecutionHistoryArchive
from app.models.user_models import UserDB
from app.schemas.history_schemas import ExecutionHistoryCreate


class HistoryService:
    @staticmethod
    def save(db: Session, history: ExecutionHistoryCreate, user_id: int, commit: bool = True):
        execution_id = f"exec_{uuid.uuid4().hex[:10]}_{int(datetime.now().timestamp())}"

        # Filtro de variáveis: Salva apenas as que realmente aparecem no request
        filtered_vars = HistoryService._filter_variables(history)

        db_history = ApiExecutionHistory(
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
            execution_type=history.execution_type or "api",
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
        db.query(ApiExecutionHistory).filter(ApiExecutionHistory.batch_id == batch_id, ApiExecutionHistory.video_url == None).update({"video_url": video_url})
        db.commit()

    @staticmethod
    def save_batch(db: Session, histories: list[ExecutionHistoryCreate], user_id: int):
        if not histories:
            return

        db_objects = []
        timestamp = int(datetime.now().timestamp())
        
        for i, history in enumerate(histories):
            # Generate unique ID for each item in batch
            execution_id = f"exec_{uuid.uuid4().hex[:10]}_{timestamp}_{i}"
            
            filtered_vars = HistoryService._filter_variables(history)
            
            db_history = ApiExecutionHistory(
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
                execution_type=history.execution_type or "api",
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
        from app.models.schedule_models import ScheduleModel
        from app.models.product_models import ProductModel
        from app.models.user_models import UserDB
        from app.models.api_test_history_models import ApiExecutionHistory

        query = db.query(ApiExecutionHistory)

        if schedule_id is not None:
             query = query.join(ScheduleModel, ApiExecutionHistory.schedule_id == ScheduleModel.id).filter(
                  ScheduleModel.id == schedule_id,
                  ScheduleModel.company_id == company_id
             )
        elif project_id is not None:
             query = query.join(ProductModel, ApiExecutionHistory.project_id == ProductModel.id).filter(
                  ProductModel.id == project_id,
                  ProductModel.company_id == company_id
             )
        else:
             query = query.join(UserDB, ApiExecutionHistory.user_id == UserDB.id).filter(
                  UserDB.company_id == company_id
             )

        if api_id is not None: query = query.filter(ApiExecutionHistory.api_id == api_id)
        if flow_id is not None: query = query.filter(ApiExecutionHistory.flow_id == flow_id)
        if environment_id is not None: query = query.filter(ApiExecutionHistory.environment_id == environment_id)
        if node_id: query = query.filter(ApiExecutionHistory.node_id == node_id)
        if execution_type: query = query.filter(ApiExecutionHistory.execution_type == execution_type)
        if method: query = query.filter(ApiExecutionHistory.method == method.upper())
        if status_code is not None: query = query.filter(ApiExecutionHistory.status_code == status_code)
        if batch_id: query = query.filter(ApiExecutionHistory.batch_id == batch_id)
        
        # New: Exact date filter to isolate batches sharing the same ID
        if start_date and start_date == end_date:
            if isinstance(start_date, str):
                dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                query = query.filter(ApiExecutionHistory.created_at == dt)
            else:
                query = query.filter(ApiExecutionHistory.created_at == start_date)
            # Skip the range filtering below
            start_date = None
            end_date = None

        if start_date:
            query = query.filter(ApiExecutionHistory.created_at >= start_date)
        if end_date:
            # Only truncate to 23:59:59 if it's a date object (no time provided)
            # If it's a full datetime (with hours/minutes), keep the precision from the frontend
            if isinstance(end_date, datetime) and end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
                dt_end = datetime.combine(end_date.date(), time(23, 59, 59))
            else:
                dt_end = end_date
            query = query.filter(ApiExecutionHistory.created_at <= dt_end)

        total = query.count()
        total_pages = (total + limit - 1) // limit if limit > 0 else 0

        sort_column = getattr(ApiExecutionHistory, sort_by, ApiExecutionHistory.created_at)
        if order == 'asc': query = query.order_by(sort_column.asc())
        else: query = query.order_by(sort_column.desc())

        skip = (page - 1) * limit
        from sqlalchemy.orm import load_only
        query = query.options(load_only(
            ApiExecutionHistory.id, ApiExecutionHistory.execution_id, ApiExecutionHistory.feature_name,
            ApiExecutionHistory.method, ApiExecutionHistory.url, ApiExecutionHistory.status_code,
            ApiExecutionHistory.status_text, ApiExecutionHistory.response_time, ApiExecutionHistory.created_at,
            ApiExecutionHistory.processed_url, ApiExecutionHistory.environment_id, ApiExecutionHistory.environment_name,
            ApiExecutionHistory.node_name, ApiExecutionHistory.api_name, ApiExecutionHistory.error_message,
            ApiExecutionHistory.assertions, ApiExecutionHistory.node_id, ApiExecutionHistory.batch_id,
            ApiExecutionHistory.video_url, ApiExecutionHistory.execution_type, ApiExecutionHistory.response_body,
            ApiExecutionHistory.flow_id, ApiExecutionHistory.api_id
        ))

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "items": query.offset(skip).limit(limit).all(),
        }

    @staticmethod
    def get_by_id(db: Session, execution_id: str, company_id: int):
        query = db.query(ApiExecutionHistory).join(UserDB, ApiExecutionHistory.user_id == UserDB.id).filter(
            UserDB.company_id == company_id
        )
        if execution_id.isdigit():
            history = query.filter(ApiExecutionHistory.id == int(execution_id)).first()
            if history: return history
        return query.filter(ApiExecutionHistory.execution_id == execution_id).first()

    @staticmethod
    def delete(db: Session, execution_id: str, company_id: int):
        history = HistoryService.get_by_id(db, execution_id, company_id)
        if not history: return False
        db.delete(history)
        db.commit()
        return True

    @staticmethod
    def clear_all(db: Session, company_id: int):
        db.query(ApiExecutionHistory).filter(
            ApiExecutionHistory.id.in_(
                db.query(ApiExecutionHistory.id).join(UserDB).filter(UserDB.company_id == company_id)
            )
        ).delete(synchronize_session=False)
        db.commit()

    @staticmethod
    def get_unique_apis(db: Session, company_id: int):
        results = (
            db.query(distinct(ApiExecutionHistory.method).label("method"), ApiExecutionHistory.url)
            .join(UserDB, ApiExecutionHistory.user_id == UserDB.id)
            .filter(UserDB.company_id == company_id)
            .group_by(ApiExecutionHistory.method, ApiExecutionHistory.url)
            .all()
        )
        return [{"method": r.method, "url": r.url} for r in results]

    @staticmethod
    def archive_old_records(db: Session, days: int):
        threshold = datetime.utcnow() - timedelta(days=days)
        try:
            records = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.created_at < threshold).all()
            if not records: return 0
            archive_objects = [
                ApiExecutionHistoryArchive(
                    execution_id=r.execution_id, api_id=r.api_id, api_name=r.api_name, project_id=r.project_id,
                    flow_id=r.flow_id, node_id=r.node_id, schedule_id=r.schedule_id, feature_name=r.feature_name,
                    node_name=r.node_name, user_id=r.user_id, environment_id=r.environment_id, environment_name=r.environment_name,
                    method=r.method, url=r.url, request_headers=r.request_headers, request_body=r.request_body,
                    request_params=r.request_params, status_code=r.status_code, status_text=r.status_text,
                    response_headers=r.response_headers, response_body=r.response_body, response_time=r.response_time,
                    error_message=r.error_message, variables_used=r.variables_used, processed_url=r.processed_url,
                    assertions=r.assertions, video_url=r.video_url, execution_type=r.execution_type, created_at=r.created_at
                ) for r in records
            ]
            db.bulk_save_objects(archive_objects)
            db.query(ApiExecutionHistory).filter(ApiExecutionHistory.id.in_([r.id for r in records])).delete(synchronize_session=False)
            db.commit()
            return len(records)
        except Exception as e:
            db.rollback()
            raise e

    @staticmethod
    def purge_archived_records(db: Session, days: int):
        threshold = datetime.utcnow() - timedelta(days=days)
        try:
            delete_query = db.query(ApiExecutionHistoryArchive).filter(ApiExecutionHistoryArchive.created_at < threshold)
            count = delete_query.count()
            if count > 0:
                delete_query.delete(synchronize_session=False)
                db.commit()
            return count
        except Exception as e:
            db.rollback()
            raise e

    @staticmethod
    def get_history_averages(db: Session, schedule_id: Optional[int] = None, flow_id: Optional[int] = None, api_id: Optional[int] = None):
        if not any([schedule_id, flow_id, api_id]):
            return []

        # Filter criteria
        filters = []
        if schedule_id: filters.append(ApiExecutionHistory.schedule_id == schedule_id)
        elif flow_id: filters.append(ApiExecutionHistory.flow_id == flow_id)
        elif api_id: filters.append(ApiExecutionHistory.api_id == api_id)

        query = db.query(
            ApiExecutionHistory.feature_name, ApiExecutionHistory.node_name, ApiExecutionHistory.api_name, ApiExecutionHistory.url,
            func.avg(ApiExecutionHistory.response_time).label('avg_time'),
            func.count(ApiExecutionHistory.id).label('total_runs'),
            func.sum(case((ApiExecutionHistory.error_message.is_(None), 1), else_=0)).label('pass_count')
        ).filter(*filters).group_by(
            ApiExecutionHistory.feature_name, ApiExecutionHistory.node_name, ApiExecutionHistory.api_name, ApiExecutionHistory.url
        )
        results = query.all()
        
        # Trends (limit to 1000 records to process last 10 for each step)
        trends = db.query(
            ApiExecutionHistory.feature_name, ApiExecutionHistory.node_name, ApiExecutionHistory.api_name, ApiExecutionHistory.url, ApiExecutionHistory.response_time
        ).filter(*filters).order_by(ApiExecutionHistory.created_at.desc()).limit(1000).all()
        
        trends_map = {}
        for tr in trends:
            key = (tr.feature_name or 'Flow Principal', tr.node_name or 'Sem Nó', tr.api_name or tr.url or 'Passo')
            if key not in trends_map: trends_map[key] = []
            if len(trends_map[key]) < 10: trends_map[key].append(tr.response_time)
        
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
