from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case
from app.models.api_test_history_models import (
    ApiExecutionHistory,
    WebExecutionHistory,
    MobileExecutionHistory
)
from app.services.history_service import HistoryService
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import io
import csv


class DashboardService:
    @staticmethod
    def _apply_filters(db: Session, query, ModelClass, project_id: Optional[int] = None, flow_id: Optional[int] = None, 
                       environment_id: Optional[int] = None,
                       start_date: Optional[datetime] = None, end_date: Optional[datetime] = None,
                       execution_type: Optional[str] = None,
                       search_term: Optional[str] = None,
                       status_code: Optional[str] = None,
                       last_execution_only: bool = False,
                       trigger_origin: Optional[str] = None,
                       company_id: Optional[int] = None):
        if project_id:
            from app.models.feature_models import FeatureModel
            features = db.query(FeatureModel.id).filter(FeatureModel.product_id == project_id).all()
            feature_ids = [f.id for f in features] if features else []
            query = query.filter(
                (ModelClass.project_id.in_(feature_ids)) | 
                (ModelClass.project_id == project_id)
            )
            
        if flow_id:
            from app.models.flow_models import FlowDB
            flows = db.query(FlowDB.id).filter(FlowDB.project_id == flow_id).all()
            flow_ids = [str(f.id) for f in flows] if flows else []
            query = query.filter(
                (ModelClass.project_id == flow_id) | 
                (ModelClass.flow_id.in_(flow_ids)) |
                (ModelClass.flow_id == str(flow_id))
            )
            
        if environment_id:
            query = query.filter(ModelClass.environment_id == environment_id)
        if execution_type:
            query = query.filter(ModelClass.execution_type == execution_type)
        if search_term:
            query = query.filter(ModelClass.api_name.ilike(f"%{search_term}%"))
        if status_code:
            query = query.filter(ModelClass.status_code == int(status_code))
        if trigger_origin:
            query = query.filter(ModelClass.trigger_origin == trigger_origin)
        
        if company_id:
            from app.models.user_models import UserDB
            query = query.join(UserDB, ModelClass.user_id == UserDB.id).filter(UserDB.company_id == company_id)

        # Ensure dates are timezone-aware (UTC) if they are naive
        if start_date and start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
            
        if start_date:
            query = query.filter(ModelClass.created_at >= start_date)
        if end_date:
            query = query.filter(ModelClass.created_at <= end_date)

        if last_execution_only:
            latest_batch_subq = db.query(ModelClass.batch_id).filter(ModelClass.batch_id.isnot(None))
            if company_id:
                from app.models.user_models import UserDB
                latest_batch_subq = latest_batch_subq.join(UserDB, ModelClass.user_id == UserDB.id).filter(UserDB.company_id == company_id)
            if project_id:
                latest_batch_subq = latest_batch_subq.filter(
                    (ModelClass.project_id.in_(feature_ids)) | 
                    (ModelClass.project_id == project_id)
                )
            if flow_id:
                latest_batch_subq = latest_batch_subq.filter(
                    (ModelClass.project_id == flow_id) | 
                    (ModelClass.flow_id.in_(flow_ids)) |
                    (ModelClass.flow_id == str(flow_id))
                )
            latest_batch = latest_batch_subq.order_by(desc(ModelClass.created_at)).first()
            
            if latest_batch and latest_batch[0]:
                full_batch_id = latest_batch[0]
                if "_path_" in full_batch_id:
                    base_batch_id = full_batch_id.split("_path_")[0]
                    query = query.filter(ModelClass.batch_id.startswith(base_batch_id))
                else:
                    query = query.filter(ModelClass.batch_id == full_batch_id)
            
        return query

    @staticmethod
    def get_summary_stats(db: Session, days: Any = 7, project_id: int = None, flow_id: int = None, 
                          environment_id: int = None,
                          start_date: datetime = None, end_date: datetime = None,
                          execution_type: str = None,
                          search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                          company_id: Optional[int] = None) -> Dict[str, Any]:
        if str(days).lower() == "today":
            days = 1
            if not start_date:
                start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            try:
                days = int(days)
            except (ValueError, TypeError):
                days = 7

        if not start_date and not end_date:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

        ModelClass = HistoryService.get_model(execution_type)
        base_query = db.query(ModelClass)
        base_query = DashboardService._apply_filters(db, base_query, ModelClass, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)
        
        agg_result = base_query.with_entities(
            func.count(ModelClass.id),
            func.count(case((ModelClass.error_message.isnot(None), 1))),
            func.avg(ModelClass.response_time)
        ).first()

        total_executions = agg_result[0] if agg_result and agg_result[0] is not None else 0
        failures = agg_result[1] if agg_result and agg_result[1] is not None else 0
        avg_time = agg_result[2] if agg_result and agg_result[2] is not None else 0
        
        success_rate = 0.0
        if total_executions > 0:
            success_rate = ((total_executions - failures) / total_executions) * 100
            
        return {
            "total_executions": total_executions,
            "success_rate": round(success_rate, 2),
            "total_failures": failures,
            "failures": failures,
            "avg_response_time": round(float(avg_time), 2)
        }

    @staticmethod
    def get_recent_failures(db: Session, limit: int = 5, project_id: int = None, flow_id: int = None,
                            environment_id: int = None,
                            start_date: datetime = None, end_date: datetime = None,
                            execution_type: str = None,
                            search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                            company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        ModelClass = HistoryService.get_model(execution_type)
        query = db.query(ModelClass).filter(ModelClass.error_message != None)
        query = DashboardService._apply_filters(db, query, ModelClass, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)
        
        failures = query.order_by(desc(ModelClass.created_at)).limit(limit).all()
        return [
            {
                "id": f.id,
                "api_name": f"{f.feature_name} (Web)" if f.execution_type == 'web' and f.feature_name else (f.api_name or "Unknown API"),
                "flow_id": f.flow_id,
                "created_at": f.created_at,
                "status_code": f.status_code,
                "error_message": f.error_message,
                "environment_name": f.environment_name
            }
            for f in failures
        ]

    @staticmethod
    def get_recent_executions(db: Session, limit: int = 5, project_id: int = None, flow_id: int = None,
                              environment_id: int = None,
                              start_date: datetime = None, end_date: datetime = None,
                              execution_type: str = None,
                              search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                              company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        ModelClass = HistoryService.get_model(execution_type)
        query = db.query(ModelClass)
        query = DashboardService._apply_filters(db, query, ModelClass, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)
        
        executions = query.order_by(desc(ModelClass.created_at)).limit(limit).all()
        return [
            {
                "id": e.id,
                "api_name": e.api_name or "Unknown API",
                "flow_id": e.flow_id,
                "created_at": e.created_at,
                "status_code": e.status_code,
                "response_time": e.response_time,
                "error_message": e.error_message,
                "environment_name": e.environment_name
            }
            for e in executions
        ]

    @staticmethod
    def get_slowest_executions(db: Session, limit: int = 5, project_id: int = None, flow_id: int = None,
                               environment_id: int = None,
                               start_date: datetime = None, end_date: datetime = None,
                               execution_type: str = None,
                               search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                               company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        ModelClass = HistoryService.get_model(execution_type)
        query = db.query(ModelClass)
        query = DashboardService._apply_filters(db, query, ModelClass, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)
        
        slowest = query.order_by(desc(ModelClass.response_time)).limit(limit * 5).all()
        
        results = []
        seen_batches = set()
        
        for f in slowest:
            if f.execution_type == 'web':
                if f.batch_id and f.batch_id in seen_batches:
                    continue
                if f.batch_id:
                    seen_batches.add(f.batch_id)
                api_name = f"{f.feature_name} (Web)" if f.feature_name else "Web Flow"
            else:
                api_name = f.api_name or "Unknown API"
                
            results.append({
                "id": f.id,
                "api_name": api_name,
                "flow_id": f.flow_id,
                "created_at": f.created_at,
                "response_time": f.response_time,
                "status_code": f.status_code,
                "environment_name": f.environment_name
            })
            
            if len(results) >= limit:
                break
                
        return results

    @staticmethod
    def get_daily_stats(db: Session, days: Any = 7, project_id: int = None, flow_id: int = None,
                        environment_id: int = None,
                        start_date: datetime = None, end_date: datetime = None,
                        execution_type: str = None,
                        search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                        company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if str(days).lower() == "today":
            days = 1
            if not start_date:
                start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            try:
                days = int(days)
            except (ValueError, TypeError):
                days = 7

        if not start_date and not end_date:
            start_date_query = datetime.now(timezone.utc) - timedelta(days=days)
        else:
            start_date_query = start_date

        if start_date_query and start_date_query.tzinfo is None:
            start_date_query = start_date_query.replace(tzinfo=timezone.utc)

        ModelClass = HistoryService.get_model(execution_type)
        query = db.query(
            ModelClass.created_at,
            ModelClass.status_code,
            ModelClass.error_message
        )
        
        query = DashboardService._apply_filters(db, query, ModelClass, project_id, flow_id, environment_id, start_date_query, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)

        raw_data = query.all()
        stats_map = {}
        
        if not start_date or not end_date:
             for i in range(days):
                day_str = (datetime.now(timezone.utc) - timedelta(days=i)).strftime('%Y-%m-%d')
                stats_map[day_str] = {"total": 0, "failures": 0, "success": 0, "date": day_str}
        else:
             delta = (end_date - start_date).days
             for i in range(delta + 1):
                 day_str = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
                 stats_map[day_str] = {"total": 0, "failures": 0, "success": 0, "date": day_str}

        for row in raw_data:
            day_str = row.created_at.strftime('%Y-%m-%d')
            if day_str not in stats_map:
                 stats_map[day_str] = {"total": 0, "failures": 0, "success": 0, "date": day_str}
            
            stats_map[day_str]["total"] += 1
            
            is_failure = False
            if row.error_message:
                is_failure = True
            
            if is_failure:
                stats_map[day_str]["failures"] += 1
            else:
                stats_map[day_str]["success"] += 1
        
        stats_list = list(stats_map.values())
        stats_list.sort(key=lambda x: x['date'])
        return stats_list

    @staticmethod
    def get_top_failing_apis(db: Session, limit: int = 5, project_id: int = None, flow_id: int = None,
                             environment_id: int = None,
                             start_date: datetime = None, end_date: datetime = None,
                             execution_type: str = None,
                             search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                             company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        ModelClass = HistoryService.get_model(execution_type)
        
        api_name_expr = case(
            (ModelClass.execution_type == 'web', func.coalesce(ModelClass.feature_name, 'Web Flow')),
            else_=func.coalesce(ModelClass.api_name, 'Unknown API')
        ).label('api_name')

        query = db.query(
            api_name_expr,
            func.count(ModelClass.id).label('failure_count')
        ).filter(
             ModelClass.error_message != None
        )
        
        query = DashboardService._apply_filters(db, query, ModelClass, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)
        
        top_failures = query.group_by(
            api_name_expr
        ).order_by(
            desc('failure_count')
        ).limit(limit).all()
        
        return [
            {
                "api_name": tf.api_name,
                "failure_count": tf.failure_count
            }
            for tf in top_failures
        ]

    @staticmethod
    def export_data_csv(db: Session, days: Any = 7, project_id: int = None, flow_id: int = None,
                        environment_id: int = None, start_date: datetime = None, end_date: datetime = None,
                        execution_type: str = None, search_term: str = None, status_code: str = None, last_execution_only: bool = False, trigger_origin: str = None,
                        company_id: Optional[int] = None) -> str:
        
        if str(days).lower() == "today":
            days = 1
            if not start_date:
                start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            try:
                days = int(days)
            except (ValueError, TypeError):
                days = 7

        if not start_date and not end_date:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

        ModelClass = HistoryService.get_model(execution_type)
        query = db.query(ModelClass)
        query = DashboardService._apply_filters(db, query, ModelClass, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only, trigger_origin, company_id=company_id)
        
        executions = query.order_by(desc(ModelClass.created_at)).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'API Name', 'Status Code', 'Response Time (ms)', 'Environment', 'Created At', 'Error Message'])

        for e in executions:
            writer.writerow([
                e.id,
                e.api_name,
                e.status_code,
                e.response_time,
                e.environment_name,
                e.created_at.strftime('%Y-%m-%d %H:%M:%S') if e.created_at else '',
                e.error_message or ''
            ])

        return output.getvalue()
