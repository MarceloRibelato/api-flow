from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.product_service import ProductService
from app.schemas.product_schemas import ProductCreate, ProductResponse, ProductMobileSettingsCreate, ProductMobileSettingsResponse
from pydantic import BaseModel, ConfigDict
from typing import Optional
import requests

router = APIRouter(
    prefix="/products",
    tags=["Products"],
    responses={404: {"description": "Produto não encontrado"}},
)

from app.auth import get_current_user
from app.models.user_models import UserDB
from app.services.audit_service import AuditService

# --- Schemas inline for Device Matrix ---
class MobileDeviceCreate(BaseModel):
    label: str
    device_name: str
    platform: str = "android"
    platform_version: Optional[str] = None
    is_active: bool = True
    provider_override: Optional[str] = None
    server_url: Optional[str] = None
    auth_user: Optional[str] = None
    auth_token: Optional[str] = None
    app_identifier: Optional[str] = None
    custom_capabilities: Optional[str] = None

class MobileDeviceResponse(MobileDeviceCreate):
    id: int
    product_id: int
    model_config = ConfigDict(from_attributes=True)

class MobilePingPayload(BaseModel):
    server_url: Optional[str] = None
    provider: Optional[str] = None
    auth_user: Optional[str] = None
    auth_token: Optional[str] = None

# ... (router def)

@router.get("/", response_model=List[ProductResponse])
def list_products(
    skip: int = 0,
    limit: int = 100,
    team_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Listar todos os produtos da empresa do usuário (opcionalmente filtrados por time).
    """
    return ProductService.list(db, company_id=current_user.company_id, skip=skip, limit=limit, team_id=team_id)

@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Criar um novo produto para a empresa.
    """

    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão para criar produtos")
         
    # Check max projects limit based on license
    from app.models.company_models import CompanyDB
    from app.models.product_models import ProductModel
    from jose import jwt
    
    company = db.query(CompanyDB).filter(CompanyDB.id == current_user.company_id).first()
    max_projects = 2 # Basic default
    if company and company.license_key:
        try:
            with open("public_key.pem", "rb") as key_file:
                pub_key = key_file.read()
            payload = jwt.decode(company.license_key, pub_key, algorithms=["RS256"])
            max_projects = payload.get("max_projects", 2)
            if payload.get("plan") == "enterprise":
                max_projects = 999
        except Exception:
            pass
            
    current_projects_count = db.query(ProductModel).filter(ProductModel.company_id == current_user.company_id).count()
    if current_projects_count >= max_projects:
        raise HTTPException(status_code=402, detail="Project limit reached for your current plan. Please upgrade to Enterprise.")

         
    created = ProductService.create(db, product, company_id=current_user.company_id)
    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="CREATE_PRODUCT",
        resource_type="product",
        resource_id=str(created.id),
        resource_name=created.name,
        details={
            "product_id": created.id,
            "product_name": created.name,
            "description": created.description
        }
    )
    return created

@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Obter detalhes de um produto específico.
    """
    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return db_product

@router.put("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Atualizar um produto existente.
    """
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão para editar")

    db_product = ProductService.update(db, product_id, product, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="UPDATE_PRODUCT",
        resource_type="product",
        resource_id=str(db_product.id),
        resource_name=db_product.name,
        details={
            "product_id": db_product.id,
            "product_name": db_product.name,
            "description": db_product.description
        }
    )
    return db_product

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Remover um produto.
    """
    if current_user.role != 'admin':
         raise HTTPException(status_code=403, detail="Apenas administradores podem deletar produtos")

    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    prod_name = db_product.name if db_product else str(product_id)

    success = ProductService.delete(db, product_id, company_id=current_user.company_id)
    if not success:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="DELETE_PRODUCT",
        resource_type="product",
        resource_id=str(product_id),
        resource_name=prod_name,
        details={
            "product_id": product_id,
            "product_name": prod_name
        }
    )
    return 

@router.get("/{product_id}/mobile-settings", response_model=ProductMobileSettingsResponse)
def get_product_mobile_settings(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Obter as configurações de mobile do produto.
    """
    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    
    return ProductService.get_mobile_settings(db, product_id)

@router.put("/{product_id}/mobile-settings", response_model=ProductMobileSettingsResponse)
def update_product_mobile_settings(
    product_id: int,
    settings_data: ProductMobileSettingsCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Atualizar as configurações de mobile do produto.
    """
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão para editar")

    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    updated_settings = ProductService.update_mobile_settings(db, product_id, settings_data)
    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="UPDATE_PRODUCT_MOBILE_SETTINGS",
        resource_type="product",
        resource_id=str(product_id),
        resource_name=db_product.name,
        details={
            "product_id": product_id,
            "product_name": db_product.name
        }
    )
    return updated_settings

# ─────────────────────────────────────────────────────────
# Device Matrix & Utilities
# ─────────────────────────────────────────────────────────

@router.post("/mobile-ping")
def ping_mobile_server(
    payload: MobilePingPayload,
    current_user: UserDB = Depends(get_current_user)
):
    """
    Test Appium / Mobile Server connectivity from the backend.
    """
    try:
        url = payload.server_url or "http://localhost:4723"
        url = url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        
        # Adjust URL to point to /status
        if url.endswith("/wd/hub"):
            url = url[:-7]
        elif url.endswith("/wd/hub/"):
            url = url[:-8]
            
        url = url.rstrip("/")
        status_url = url + "/status"
        
        auth = None
        if payload.provider in ("browserstack", "saucelabs") and payload.auth_user and payload.auth_token:
            auth = (payload.auth_user, payload.auth_token)
            
        res = requests.get(status_url, auth=auth, timeout=5)
        if res.status_code not in (200, 304, 301, 302):
            return {"status": "error", "message": f"HTTP {res.status_code}"}
            
        # If local appium, attempt to capture device by starting a temporary session
        captured_device = None
        warning_msg = None
        if payload.provider == 'appium_local':
            try:
                session_url = url + "/session"
                session_payload = {
                    "capabilities": {
                        "alwaysMatch": {
                            "platformName": "Android",
                            "appium:automationName": "UiAutomator2"
                        }
                    }
                }
                session_res = requests.post(session_url, json=session_payload, timeout=10)
                if session_res.status_code == 200:
                    data = session_res.json()
                    session_id = data.get("value", {}).get("sessionId")
                    caps = data.get("value", {}).get("capabilities", {})
                    
                    device_name = caps.get("deviceName") or caps.get("deviceUDID") or caps.get("deviceModel")
                    platform_version = caps.get("platformVersion")
                    
                    if device_name:
                        captured_device = {
                            "device_name": device_name,
                            "platform_version": platform_version or ""
                        }
                    
                    # Delete the temporary session
                    if session_id:
                        requests.delete(f"{session_url}/{session_id}", timeout=5)
                else:
                    # Appium returned an error when creating the session
                    try:
                        error_data = session_res.json()
                        error_msg = error_data.get("value", {}).get("message", session_res.text)
                        warning_msg = f"Appium está online, mas falhou ao tentar obter o dispositivo: {error_msg}"
                    except:
                        warning_msg = f"Appium está online, mas falhou ao tentar obter o dispositivo (HTTP {session_res.status_code})"
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to capture device info during ping: {e}")
                warning_msg = f"Erro ao tentar capturar dispositivo: {str(e)}"

        return {
            "status": "ok",
            "captured_device": captured_device,
            "warning": warning_msg
        }
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": str(e)}

@router.get("/{product_id}/mobile-devices/adb-connected")
def get_connected_adb_devices(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Listar dispositivos físicos e emuladores detectados via ADB no servidor e status do pool."""
    from app.services.mobile_device_pool_service import MobileDevicePoolService
    devices = MobileDevicePoolService.get_connected_adb_devices()
    pool_status = MobileDevicePoolService.get_pool_status(db, product_id)
    return {
        "adb_devices": devices,
        "configured_devices": pool_status
    }

@router.get("/{product_id}/mobile-devices", response_model=List[MobileDeviceResponse])
def list_mobile_devices(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Listar dispositivos da matriz de execução mobile."""
    from app.models.product_models import ProductMobileDeviceDB
    return db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.product_id == product_id
    ).all()

@router.post("/{product_id}/mobile-devices/validate")
def validate_mobile_device(
    product_id: int,
    device: MobileDeviceCreate,
    exclude_device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Validar configurações de um dispositivo antes de salvar na matriz."""
    from app.models.product_models import ProductMobileDeviceDB
    from app.services.product_service import ProductService
    from app.services.mobile_device_pool_service import MobileDevicePoolService
    import json
    import subprocess
    import requests as _requests

    checks = []
    errors = []
    warnings = []

    dev_name = (device.device_name or "").strip()
    label = (device.label or "").strip()
    if not label or not dev_name:
        errors.append("Nome de exibição e Device Name (UDID) são obrigatórios.")
        return {"valid": False, "checks": checks, "errors": errors, "warnings": warnings}

    # 1. Duplicidade na matriz
    dup_q = db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.product_id == product_id,
        ProductMobileDeviceDB.device_name == dev_name
    )
    if exclude_device_id is not None:
        dup_q = dup_q.filter(ProductMobileDeviceDB.id != exclude_device_id)
    if dup_q.first():
        msg = f"O dispositivo '{dev_name}' já está cadastrado neste produto."
        errors.append(msg)
        checks.append({"name": "duplicate", "ok": False, "message": msg})

    # 2. Custom Capabilities JSON
    if device.custom_capabilities and str(device.custom_capabilities).strip():
        try:
            parsed_caps = json.loads(device.custom_capabilities)
            if not isinstance(parsed_caps, dict):
                raise ValueError("Deve ser um objeto JSON")
            checks.append({"name": "custom_capabilities", "ok": True, "message": "Custom Capabilities JSON válido."})
        except Exception as json_err:
            msg = f"JSON inválido em Custom Capabilities: {json_err}"
            errors.append(msg)
            checks.append({"name": "custom_capabilities", "ok": False, "message": msg})

    settings_obj = ProductService.get_mobile_settings(db, product_id)
    eff_provider = (device.provider_override or getattr(settings_obj, "provider", None) or "appium_local").strip()
    eff_server_url = (device.server_url or getattr(settings_obj, "server_url", None) or "http://localhost:4723").strip()
    eff_platform = (device.platform or "android").lower()
    eff_app_id = (device.app_identifier or getattr(settings_obj, "app_identifier", None) or "").strip()

    # 3. Provider & Servidor Appium
    appium_ok = False
    if eff_provider == "appium_local":
        check_url = eff_server_url
        if "localhost" in check_url or "127.0.0.1" in check_url:
            check_url = check_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        try:
            resp = _requests.get(f"{check_url.rstrip('/')}/status", timeout=3)
            if resp.status_code < 500:
                appium_ok = True
                checks.append({"name": "appium_server", "ok": True, "message": f"Servidor Appium acessível ({eff_server_url})."})
            else:
                msg = f"Servidor Appium respondeu com erro HTTP {resp.status_code} em {eff_server_url}."
                errors.append(msg)
                checks.append({"name": "appium_server", "ok": False, "message": msg})
        except Exception:
            msg = f"Não foi possível conectar ao servidor Appium em {eff_server_url}. Verifique se o Appium está iniciado."
            errors.append(msg)
            checks.append({"name": "appium_server", "ok": False, "message": msg})

        # 4. ADB device & App instalado (Android)
        if eff_platform == "android":
            adb_available = MobileDevicePoolService.is_adb_available()
            adb_devices = MobileDevicePoolService.get_connected_adb_devices() if adb_available else []
            real_adb_devices = [
                d for d in adb_devices
                if d.get("serial") and d.get("serial") != "USB-DETECTED"
            ]

            if not adb_available or (len(real_adb_devices) == 0 and appium_ok):
                warnings.append(
                    f"ADB não está acessível diretamente neste servidor (API em container). A presença de '{dev_name}' e o app instalado serão validados pelo Appium ({eff_server_url}) na execução."
                )
                checks.append({
                    "name": "adb_device",
                    "ok": True,
                    "message": f"Verificação ADB delegada ao Appium ({eff_server_url})."
                })
            else:
                matched_adb = next((d for d in real_adb_devices if d.get("serial") == dev_name), None)
                if not matched_adb:
                    msg = f"Dispositivo '{dev_name}' não encontrado no 'adb devices'."
                    errors.append(msg)
                    checks.append({"name": "adb_device", "ok": False, "message": msg})
                elif matched_adb.get("state") != "device":
                    msg = f"Dispositivo '{dev_name}' está com status '{matched_adb.get('state')}' (precisa estar como 'device')."
                    errors.append(msg)
                    checks.append({"name": "adb_device", "ok": False, "message": msg})
                else:
                    checks.append({"name": "adb_device", "ok": True, "message": f"Dispositivo '{dev_name}' conectado via ADB."})

                    is_app_file = (
                        eff_app_id.lower().endswith((".apk", ".aab", ".ipa"))
                        or "\\" in eff_app_id
                        or (eff_app_id.startswith("/") and not eff_app_id.startswith(("com.", "br.", "io.")))
                    )
                    if eff_app_id and not is_app_file:
                        pkg_base = eff_app_id.split("/", 1)[0].strip()
                        adb_bin = MobileDevicePoolService._find_adb_binary() or "adb"
                        try:
                            pm_res = subprocess.run(
                                [adb_bin, "-s", dev_name, "shell", "pm", "path", pkg_base],
                                capture_output=True,
                                text=True,
                                timeout=4
                            )
                            if pm_res.returncode == 0 and (pm_res.stdout or "").strip():
                                checks.append({"name": "app_installed", "ok": True, "message": f"App '{pkg_base}' instalado em '{dev_name}'."})
                            else:
                                msg = f"App '{pkg_base}' não está instalado no dispositivo '{dev_name}'."
                                errors.append(msg)
                                checks.append({"name": "app_installed", "ok": False, "message": msg})
                        except Exception as pm_err:
                            warnings.append(f"Não foi possível verificar instalação do app '{pkg_base}': {pm_err}")
    else:
        eff_user = (device.auth_user or getattr(settings_obj, "auth_user", None) or "").strip()
        eff_token = (device.auth_token or getattr(settings_obj, "auth_token", None) or "").strip()
        if not eff_user or not eff_token:
            msg = f"Credenciais (usuário e chave) obrigatórias para o provedor '{eff_provider}'."
            errors.append(msg)
            checks.append({"name": "cloud_credentials", "ok": False, "message": msg})
        else:
            checks.append({"name": "cloud_credentials", "ok": True, "message": f"Credenciais configuradas para '{eff_provider}'."})

    return {
        "valid": len(errors) == 0,
        "checks": checks,
        "errors": errors,
        "warnings": warnings
    }


@router.post("/{product_id}/mobile-devices", response_model=MobileDeviceResponse, status_code=status.HTTP_201_CREATED)
def create_mobile_device(
    product_id: int,
    device: MobileDeviceCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Adicionar dispositivo à matriz."""
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
    from app.models.product_models import ProductMobileDeviceDB
    db_device = ProductMobileDeviceDB(product_id=product_id, **device.dict())
    db.add(db_device)
    db.commit()
    db.refresh(db_device)
    return db_device

@router.put("/{product_id}/mobile-devices/{device_id}", response_model=MobileDeviceResponse)
def update_mobile_device(
    product_id: int,
    device_id: int,
    device: MobileDeviceCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Atualizar um dispositivo da matriz."""
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
    from app.models.product_models import ProductMobileDeviceDB
    db_device = db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.id == device_id,
        ProductMobileDeviceDB.product_id == product_id
    ).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    for k, v in device.dict().items():
        setattr(db_device, k, v)
    db.commit()
    db.refresh(db_device)
    return db_device

@router.delete("/{product_id}/mobile-devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mobile_device(
    product_id: int,
    device_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Remover dispositivo da matriz."""
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
    from app.models.product_models import ProductMobileDeviceDB
    db_device = db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.id == device_id,
        ProductMobileDeviceDB.product_id == product_id
    ).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    db.delete(db_device)
    db.commit()
    return
