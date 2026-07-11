from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.database import Base

class ProductModel(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    platform = Column(String(20), default='web', nullable=False) # web, mobile, both
    notification_urls = Column(Text, nullable=True) # Webhook URLs for this product
    channel_type = Column(String(50), default='webhook', nullable=True) # webhook, teams, slack
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Tenant
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    company_rel = relationship("CompanyDB", back_populates="products")

    # Relationship to features
    features = relationship("FeatureModel", back_populates="product", cascade="all, delete-orphan")

    # Mobile Settings
    mobile_settings = relationship("ProductMobileSettingsDB", back_populates="product", uselist=False, cascade="all, delete-orphan")

    # Mobile Device Matrix
    mobile_devices = relationship("ProductMobileDeviceDB", back_populates="product", cascade="all, delete-orphan")

class ProductMobileSettingsDB(Base):
    __tablename__ = "product_mobile_settings"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), unique=True, nullable=False)
    
    provider = Column(String(50), default="appium_local") # appium_local, browserstack, saucelabs, playwright_adb
    server_url = Column(String(255), nullable=True)
    auth_user = Column(String(100), nullable=True)
    auth_token = Column(String(255), nullable=True)
    device_name = Column(String(100), nullable=True)
    platform_version = Column(String(50), nullable=True)
    app_identifier = Column(String(255), nullable=True) # Usually the path or BS app id
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to product
    product = relationship("ProductModel", back_populates="mobile_settings")


class ProductMobileDeviceDB(Base):
    """Dispositivos na Matriz de Execução Mobile."""
    __tablename__ = "product_mobile_devices"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    # Identity
    label = Column(String(100), nullable=False)           # ex: "Galaxy S22 (Android 12)"
    device_name = Column(String(100), nullable=False)
    platform = Column(String(10), default="android")      # android | ios
    platform_version = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True)

    # Provider override (inherits product settings if null)
    provider_override = Column(String(50), nullable=True) # null = use product default
    server_url = Column(String(255), nullable=True)
    auth_user = Column(String(100), nullable=True)
    auth_token = Column(String(255), nullable=True)
    app_identifier = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    product = relationship("ProductModel", back_populates="mobile_devices")
