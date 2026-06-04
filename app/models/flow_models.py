from datetime import datetime
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class FlowDB(Base):
    __tablename__ = "flow_data"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("features.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    # New Columns for Multi-Flow Support
    name = Column(String(255), default="Fluxo Principal") # Default for migration
    flow_type = Column(String(50), default="api") # Distinguish between 'api' and 'frontend' flows
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())



    # Relationships
    flow_nodes = relationship("FlowNodeDB", back_populates="flow", cascade="all, delete-orphan")
    flow_edges = relationship("FlowEdgeDB", back_populates="flow", cascade="all, delete-orphan")
    flow_card_data = relationship("FlowCardDataDB", back_populates="flow", cascade="all, delete-orphan")


class FlowNodeDB(Base):
    __tablename__ = "flow_nodes"
    # Surrogate ID for DB to ensure clean relationships and easy referencing
    db_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    
    # The ID coming from the frontend (e.g., "node-1", "dndnode_0")
    client_id = Column(String(255), index=True)
    
    flow_id = Column(Integer, ForeignKey("flow_data.id", ondelete="CASCADE"), index=True)
    
    type = Column(String(50))
    position_x = Column(Float)
    position_y = Column(Float)
    width = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    parent_node_id = Column(String(255), nullable=True)
    
    # Light metadata stored in JSON to avoid over-engineering columns for now
    data = Column(JSON) 

    flow = relationship("FlowDB", back_populates="flow_nodes")


class FlowEdgeDB(Base):
    __tablename__ = "flow_edges"
    db_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client_id = Column(String(255), index=True)
    
    flow_id = Column(Integer, ForeignKey("flow_data.id", ondelete="CASCADE"), index=True)
    
    source = Column(String(255), index=True)
    target = Column(String(255), index=True)
    type = Column(String(50))
    animated = Column(Boolean, default=False)
    
    flow = relationship("FlowDB", back_populates="flow_edges")


class FlowCardDataDB(Base):
    __tablename__ = "flow_card_data"
    db_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    node_id = Column(String(255), index=True) # Corresponds to client_id of a node
    
    flow_id = Column(Integer, ForeignKey("flow_data.id", ondelete="CASCADE"), index=True)
    
    name = Column(String(255))
    description = Column(Text, nullable=True)
    color = Column(String(7))
    
    # Content
    bdd_scenarios = Column(JSON, default=list) # Changed from dict to list for better default consistency
    api_calls = Column(JSON, default=list)
    e2e_steps = Column(JSON, default=list)
    env_data = Column(JSON, default=dict)

    flow = relationship("FlowDB", back_populates="flow_card_data")
    e2e_steps_rel = relationship("FlowE2EStepDB", back_populates="card_data", cascade="all, delete-orphan", order_by="FlowE2EStepDB.order")


class FlowE2EStepDB(Base):
    __tablename__ = "flow_e2e_steps"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    card_db_id = Column(Integer, ForeignKey("flow_card_data.db_id", ondelete="CASCADE"), index=True)
    client_id = Column(String(255), index=True) # step.id from frontend
    
    type = Column(String(50))
    name = Column(String(255))
    description = Column(Text, nullable=True)
    properties = Column(JSON, default=dict)
    order = Column(Integer, default=0)

    card_data = relationship("FlowCardDataDB", back_populates="e2e_steps_rel")
