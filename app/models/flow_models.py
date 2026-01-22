from datetime import datetime
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class FlowDB(Base):
    __tablename__ = "flow_data"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    # New Columns for Multi-Flow Support
    name = Column(String(255), default="Fluxo Principal") # Default for migration
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Deprecated JSON columns (kept for migration safety, ignore in new logic)
    nodes = Column(JSON, nullable=True)
    edges = Column(JSON, nullable=True)
    card_data = Column(JSON, nullable=True, default=dict)

    # Relationships
    flow_nodes = relationship("FlowNodeDB", back_populates="flow", cascade="all, delete-orphan")
    flow_edges = relationship("FlowEdgeDB", back_populates="flow", cascade="all, delete-orphan")
    flow_card_data = relationship("FlowCardDataDB", back_populates="flow", cascade="all, delete-orphan")


class FlowNodeDB(Base):
    __tablename__ = "flow_nodes"
    id = Column(String(255), primary_key=True)  # Using String ID from frontend (e.g. "node-1")
    flow_id = Column(Integer, ForeignKey("flow_data.id"), primary_key=True) # Composite PK might be better or just use flow_id + id as logical unique
    # Note: Frontend Ids might not be globally unique, so composite PK (flow_id, id) is safer.
    # But SQLAlchemy limitations with composite PKs on relationships can be tricky.
    # Let's use a synthetic Int ID? No, validation relies on string IDs.
    # Let's make "uuid" primary key and string "node_id" a column?
    # Or just trust (flow_id, id) composite.
    
    # Let's go with single PK using a surrogate key to avoid issues, 
    # OR composite PK (flow_id, client_node_id).
    
    # Simplified: Surrogate ID for DB, keep client_id.
    db_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client_id = Column(String(255), index=True)
    
    flow_id = Column(Integer, ForeignKey("flow_data.id"), index=True)
    
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
    
    flow_id = Column(Integer, ForeignKey("flow_data.id"), index=True)
    
    source = Column(String(255), index=True)
    target = Column(String(255), index=True)
    type = Column(String(50))
    animated = Column(Boolean, default=False)
    
    flow = relationship("FlowDB", back_populates="flow_edges")


class FlowCardDataDB(Base):
    __tablename__ = "flow_card_data"
    db_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    node_id = Column(String(255), index=True) # Corresponds to client_id of a node
    
    flow_id = Column(Integer, ForeignKey("flow_data.id"), index=True)
    
    name = Column(String(255))
    description = Column(Text, nullable=True)
    color = Column(String(7))
    
    # Content
    bdd_scenarios = Column(JSON, default=dict) # Should be list, but schema says list[dict]
    api_calls = Column(JSON, default=dict)
    env_data = Column(JSON, default=dict)

    flow = relationship("FlowDB", back_populates="flow_card_data")
