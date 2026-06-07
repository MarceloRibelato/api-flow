from sqlalchemy import Column, Integer, String, DateTime, JSON, Float
from sqlalchemy.sql import func
from app.database import Base

class PerformanceTestResult(Base):
    __tablename__ = "performance_test_results"

    id = Column(String(50), primary_key=True, index=True) # UUID string
    flow_id = Column(Integer, index=True, nullable=True)
    company_id = Column(Integer, index=True, nullable=True)
    user_id = Column(Integer, index=True, nullable=True)
    status = Column(String(20), default="running") # running, completed, error
    
    test_name = Column(String(255), nullable=True)
    target_url = Column(String(500), nullable=True)
    
    # Configuration
    virtual_users = Column(Integer, default=1)
    iterations = Column(Integer, default=1)
    duration_seconds = Column(Integer, nullable=True)
    ramp_up_seconds = Column(Integer, default=0)
    
    # Metrics
    total_requests = Column(Integer, default=0)
    success_requests = Column(Integer, default=0)
    failed_requests = Column(Integer, default=0)
    
    avg_latency = Column(Float, default=0.0)
    min_latency = Column(Float, default=0.0)
    max_latency = Column(Float, default=0.0)
    p50_latency = Column(Float, default=0.0)
    p90_latency = Column(Float, default=0.0)
    p95_latency = Column(Float, default=0.0)
    p99_latency = Column(Float, default=0.0)
    
    requests_per_second = Column(Float, default=0.0)
    
    # Detailed time series stats for charting (JSON)
    # [{"timestamp": 12345, "rps": 10, "avg_latency": 120, "errors": 0}, ...]
    time_series_data = Column(JSON, default=list) 

    # Detailed final stats per API (JSON)
    api_stats = Column(JSON, nullable=True)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
