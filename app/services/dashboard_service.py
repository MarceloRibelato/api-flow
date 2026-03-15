from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.models.api_test_history_models import ApiExecutionHistory
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

class DashboardService:
    @staticmethod
    def _apply_filters(query, project_id: Optional[int] = None, flow_id: Optional[int] = None, 
                       environment_id: Optional[int] = None,
                       start_date: Optional[datetime] = None, end_date: Optional[datetime] = None,
                       execution_type: Optional[str] = None):
        if project_id:
            query = query.filter(ApiExecutionHistory.project_id == project_id)
        if flow_id:
            query = query.filter(ApiExecutionHistory.flow_id == flow_id)
        if environment_id:
            query = query.filter(ApiExecutionHistory.environment_id == environment_id)
        if execution_type:
            query = query.filter(ApiExecutionHistory.execution_type == execution_type)
        
        # Ensure dates are timezone-aware (UTC) if they are naive
        if start_date and start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
            
        if start_date:
            query = query.filter(ApiExecutionHistory.created_at >= start_date)
        if end_date:
            query = query.filter(ApiExecutionHistory.created_at <= end_date)
            
        return query

    @staticmethod
    def get_summary_stats(db: Session, days: int = 7, project_id: int = None, flow_id: int = None, 
                          environment_id: int = None,
                          start_date: datetime = None, end_date: datetime = None,
                          execution_type: str = None) -> Dict[str, Any]:
        """
        Returns summary metrics for the period/filters.
        """
        # Determine start_date if not specific range provided
        if not start_date and not end_date:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

        base_query = db.query(ApiExecutionHistory)
        base_query = DashboardService._apply_filters(base_query, project_id, flow_id, environment_id, start_date, end_date, execution_type)
        
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
                            execution_type: str = None) -> List[Dict[str, Any]]:
        query = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.error_message != None)
        query = DashboardService._apply_filters(query, project_id, flow_id, environment_id, start_date, end_date, execution_type)
        
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
    def get_slowest_executions(db: Session, limit: int = 5, project_id: int = None, flow_id: int = None,
                               environment_id: int = None,
                               start_date: datetime = None, end_date: datetime = None,
                               execution_type: str = None) -> List[Dict[str, Any]]:
        query = db.query(ApiExecutionHistory)
        query = DashboardService._apply_filters(query, project_id, flow_id, environment_id, start_date, end_date, execution_type)
        
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
                        execution_type: str = None) -> List[Dict[str, Any]]:
        """
        Returns execution counts (success/failure) grouped by day.
        Aggregation is done in Python.
        """
        if not start_date and not end_date:
            start_date_query = datetime.now(timezone.utc) - timedelta(days=days)
        else:
            start_date_query = start_date

        # Ensure start_date_query is aware
        if start_date_query and start_date_query.tzinfo is None:
            start_date_query = start_date_query.replace(tzinfo=timezone.utc)

        query = db.query(
            ApiExecutionHistory.created_at,
            ApiExecutionHistory.status_code,
            ApiExecutionHistory.error_message
        )
        
        # Apply filters (Note: start_date handling matches the logic above)
        if project_id:
            query = query.filter(ApiExecutionHistory.project_id == project_id)
        if flow_id:
            query = query.filter(ApiExecutionHistory.flow_id == flow_id)
        if environment_id:
            query = query.filter(ApiExecutionHistory.environment_id == environment_id)
        
        if execution_type:
            query = query.filter(ApiExecutionHistory.execution_type == execution_type)
            
        if start_date_query:
            query = query.filter(ApiExecutionHistory.created_at >= start_date_query)
        if end_date:
            query = query.filter(ApiExecutionHistory.created_at <= end_date)

        raw_data = query.all()
        
        # Aggregate in Python
        stats_map = {}
        
        # Determine date range for pre-filling 0s
        # If using 'days', simple range. If custom range, iterate between start/end.
        # Simplification: Just iterate based on input 'days' if start/end not custom, 
        # or calculate delta if custom.
        
        iteration_days = days
        current = datetime.now(timezone.utc)
        if start_date and end_date:
            # Calculate days between
            delta = end_date - start_date
            iteration_days = delta.days + 1
            current = end_date

        # Initialize last N days with 0 (approximate for custom range, logic can be refined)
        # For simplicity in this iteration, we trust the raw data dates for custom ranges 
        # but ensure at least 7 days default structure.
        
        # Better approach: Just use raw data grouping, and maybe fill gaps later?
        # Let's stick to the previous reliable loop for default case, and dynamic for custom.
        
        if not start_date or not end_date:
             for i in range(days):
                day_str = (datetime.now(timezone.utc) - timedelta(days=i)).strftime('%Y-%m-%d')
                stats_map[day_str] = {"total": 0, "failures": 0, "success": 0, "date": day_str}
        else:
             # Pre-fill for custom range
             delta = (end_date - start_date).days
             for i in range(delta + 1):
                 day_str = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
                 stats_map[day_str] = {"total": 0, "failures": 0, "success": 0, "date": day_str}

        for row in raw_data:
            day_str = row.created_at.strftime('%Y-%m-%d')
            # Initialize if not exists (should be covered by above but safe fallback)
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
                             execution_type: str = None) -> List[Dict[str, Any]]:
        query = db.query(
            ApiExecutionHistory.api_name,
            func.count(ApiExecutionHistory.id).label('failure_count')
        ).filter(
             (ApiExecutionHistory.status_code >= 400) | (ApiExecutionHistory.error_message != None)
        )
        
        query = DashboardService._apply_filters(query, project_id, flow_id, environment_id, start_date, end_date, execution_type)
        
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
