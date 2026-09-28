import re

with open('app/routes/api_test_history_routes.py', 'r') as f:
    content = f.read()

route_old = """
    schedule_type: Optional[str] = None,
    execution_type: Optional[str] = Query(None, pattern="^(api|web|mobile)$"),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    \"\"\"Retorna os batches agrupados e paginados\"\"\"
    try:
        return HistoryService.get_batches(
            db=db,
            company_id=current_user.company_id,
            page=page,
            limit=limit,
            project_id=project_id,
            flow_id=flow_id,
            schedule_type=schedule_type,
            execution_type=execution_type
        )
"""
route_new = """
    schedule_type: Optional[str] = None,
    execution_type: Optional[str] = Query(None, pattern="^(api|web|mobile)$"),
    exclude_api: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    \"\"\"Retorna os batches agrupados e paginados\"\"\"
    try:
        return HistoryService.get_batches(
            db=db,
            company_id=current_user.company_id,
            page=page,
            limit=limit,
            project_id=project_id,
            flow_id=flow_id,
            schedule_type=schedule_type,
            execution_type=execution_type,
            exclude_api=exclude_api
        )
"""
content = content.replace(route_old, route_new)
with open('app/routes/api_test_history_routes.py', 'w') as f:
    f.write(content)

with open('app/services/history_service.py', 'r') as f:
    content = f.read()

service_def_old = """
        flow_id: Optional[Union[int, str]] = None,
        schedule_type: Optional[str] = None,
        execution_type: Optional[str] = None
    ):
"""
service_def_new = """
        flow_id: Optional[Union[int, str]] = None,
        schedule_type: Optional[str] = None,
        execution_type: Optional[str] = None,
        exclude_api: bool = False
    ):
"""
content = content.replace(service_def_old, service_def_new)

filter_old = """
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
"""
filter_new = """
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

        if exclude_api:
            from sqlalchemy import not_, or_, and_
            methods = ['GET %', 'POST %', 'PUT %', 'PATCH %', 'DELETE %', 'HEAD %', 'OPTIONS %']
            api_conditions = []
            if hasattr(ModelClass, 'api_name'):
                api_conditions.append(or_(*[ModelClass.api_name.like(m) for m in methods]))
            if hasattr(ModelClass, 'url'):
                api_conditions.append(or_(*[ModelClass.url.like(m) for m in methods]))
            if api_conditions:
                query = query.filter(not_(or_(*api_conditions)))

        if schedule_type is not None:
"""
content = content.replace(filter_old, filter_new)

with open('app/services/history_service.py', 'w') as f:
    f.write(content)

