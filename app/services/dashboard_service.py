from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.models.api_test_history_models import ApiExecutionHistory
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import io
import csv

class DashboardService:
    @staticmethod
    def _apply_filters(db: Session, query, project_id: Optional[int] = None, flow_id: Optional[int] = None, 
                       environment_id: Optional[int] = None,
                       start_date: Optional[datetime] = None, end_date: Optional[datetime] = None,
                       execution_type: Optional[str] = None,
                       search_term: Optional[str] = None,
                       status_code: Optional[str] = None,
                       last_execution_only: bool = False):
        if project_id:
            query = query.filter(ApiExecutionHistory.project_id == project_id)
        if flow_id:
            query = query.filter(ApiExecutionHistory.flow_id == flow_id)
        if environment_id:
            query = query.filter(ApiExecutionHistory.environment_id == environment_id)
        if execution_type:
            query = query.filter(ApiExecutionHistory.execution_type == execution_type)
        if search_term:
            query = query.filter(ApiExecutionHistory.api_name.ilike(f"%{search_term}%"))
        if status_code:
            query = query.filter(ApiExecutionHistory.status_code == int(status_code))
        
        # Ensure dates are timezone-aware (UTC) if they are naive
        if start_date and start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
            
        if start_date:
            query = query.filter(ApiExecutionHistory.created_at >= start_date)
        if end_date:
            query = query.filter(ApiExecutionHistory.created_at <= end_date)

        if last_execution_only:
            # Find the most recent batch_id for the given project/flow
            latest_batch_subq = db.query(ApiExecutionHistory.batch_id).filter(ApiExecutionHistory.batch_id.isnot(None))
            if project_id:
                latest_batch_subq = latest_batch_subq.filter(ApiExecutionHistory.project_id == project_id)
            if flow_id:
                latest_batch_subq = latest_batch_subq.filter(ApiExecutionHistory.flow_id == flow_id)
            latest_batch = latest_batch_subq.order_by(desc(ApiExecutionHistory.created_at)).first()
            
            if latest_batch and latest_batch[0]:
                query = query.filter(ApiExecutionHistory.batch_id == latest_batch[0])
            else:
                # If no batch_id, maybe just limit to the very last execution record time
                pass
            
        return query

    @staticmethod
    def get_summary_stats(db: Session, days: int = 7, project_id: int = None, flow_id: int = None, 
                          environment_id: int = None,
                          start_date: datetime = None, end_date: datetime = None,
                          execution_type: str = None,
                          search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> Dict[str, Any]:
        if not start_date and not end_date:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

        base_query = db.query(ApiExecutionHistory)
        base_query = DashboardService._apply_filters(db, base_query, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only)
        
        total_executions = base_query.count()
        failures = base_query.filter(ApiExecutionHistory.error_message != None).count()
        
        success_rate = 0.0
        if total_executions > 0:
            success_rate = ((total_executions - failures) / total_executions) * 100
            
        avg_time = base_query.with_entities(func.avg(ApiExecutionHistory.response_time)).scalar() or 0
        
        return {
            "total_executions": total_executions,
            "total_failures": failures,
            "success_rate": round(success_rate, 2),
            "avg_response_time": round(avg_time, 2),
            "period_days": days
        }

    @staticmethod
    def get_recent_failures(db: Session, limit: int = 5, project_id: int = None, flow_id: int = None,
                            environment_id: int = None,
                            start_date: datetime = None, end_date: datetime = None,
                            execution_type: str = None,
                            search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> List[Dict[str, Any]]:
        query = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.error_message != None)
        query = DashboardService._apply_filters(db, query, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only)
        
        failures = query.order_by(desc(ApiExecutionHistory.created_at)).limit(limit).all()
        return [
            {
                "id": f.id,
                "api_name": f.api_name or "Unknown API",
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
                              search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> List[Dict[str, Any]]:
        query = db.query(ApiExecutionHistory)
        query = DashboardService._apply_filters(db, query, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only)
        
        executions = query.order_by(desc(ApiExecutionHistory.created_at)).limit(limit).all()
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
                               search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> List[Dict[str, Any]]:
        query = db.query(ApiExecutionHistory)
        query = DashboardService._apply_filters(db, query, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only)
        
        slowest = query.order_by(desc(ApiExecutionHistory.response_time)).limit(limit).all()
        return [
            {
                "id": f.id,
                "api_name": f.api_name or "Unknown API",
                "flow_id": f.flow_id,
                "created_at": f.created_at,
                "response_time": f.response_time,
                "status_code": f.status_code,
                "environment_name": f.environment_name
            }
            for f in slowest
        ]

    @staticmethod
    def get_daily_stats(db: Session, days: int = 7, project_id: int = None, flow_id: int = None,
                        environment_id: int = None,
                        start_date: datetime = None, end_date: datetime = None,
                        execution_type: str = None,
                        search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> List[Dict[str, Any]]:
        if not start_date and not end_date:
            start_date_query = datetime.now(timezone.utc) - timedelta(days=days)
        else:
            start_date_query = start_date

        if start_date_query and start_date_query.tzinfo is None:
            start_date_query = start_date_query.replace(tzinfo=timezone.utc)

        query = db.query(
            ApiExecutionHistory.created_at,
            ApiExecutionHistory.status_code,
            ApiExecutionHistory.error_message
        )
        
        query = DashboardService._apply_filters(db, query, project_id, flow_id, environment_id, start_date_query, end_date, execution_type, search_term, status_code, last_execution_only)

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
            if row.status_code and row.status_code >= 400:
                is_failure = True
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
                             search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> List[Dict[str, Any]]:
        query = db.query(
            ApiExecutionHistory.api_name,
            func.count(ApiExecutionHistory.id).label('failure_count')
        ).filter(
             (ApiExecutionHistory.status_code >= 400) | (ApiExecutionHistory.error_message != None)
        )
        
        query = DashboardService._apply_filters(db, query, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only)
        
        top_failures = query.group_by(
            ApiExecutionHistory.api_name
        ).order_by(
            desc('failure_count')
        ).limit(limit).all()
        
        return [
            {
                "api_name": tf.api_name or "Unknown API",
                "failure_count": tf.failure_count
            }
            for tf in top_failures
        ]

    @staticmethod
    def export_data_csv(db: Session, days: int = 7, project_id: int = None, flow_id: int = None,
                        environment_id: int = None, start_date: datetime = None, end_date: datetime = None,
                        execution_type: str = None, search_term: str = None, status_code: str = None, last_execution_only: bool = False) -> str:
        
        if not start_date and not end_date:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

        query = db.query(ApiExecutionHistory)
        query = DashboardService._apply_filters(db, query, project_id, flow_id, environment_id, start_date, end_date, execution_type, search_term, status_code, last_execution_only)
        
        executions = query.order_by(desc(ApiExecutionHistory.created_at)).all()

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
