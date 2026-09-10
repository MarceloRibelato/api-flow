import logging
import os
import re
import shutil
import subprocess
import threading
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.models.product_models import ProductMobileDeviceDB

logger = logging.getLogger(__name__)


class MobileDevicePoolService:
    """
    Gerenciador de Pool de Dispositivos e Alocação Inteligente de Portas.
    Coordena o acesso a dispositivos móveis físicos e emuladores,
    garantindo que execuções concorrentes utilizem portas isoladas (systemPort e mjpegServerPort)
    e não haja colisão de sessões do UiAutomator2 / XCUITest.
    """

    _lock = threading.Lock()
    _locked_devices: set[int] = set()
    _allocated_system_ports: set[int] = set()
    _allocated_mjpeg_ports: set[int] = set()

    SYSTEM_PORT_MIN = 8200
    SYSTEM_PORT_MAX = 8299
    MJPEG_PORT_MIN = 7810
    MJPEG_PORT_MAX = 7899

    @classmethod
    def _find_adb_binary(cls) -> str:
        """Localiza o binário do ADB no PATH ou em caminhos padrão do Android SDK."""
        found = shutil.which("adb")
        if found:
            return found

        android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        candidates = []
        if android_home:
            candidates.append(os.path.join(android_home, "platform-tools", "adb"))

        candidates.extend([
            os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
            "/home/server/Android/Sdk/platform-tools/adb",
            "/usr/bin/adb",
            "/usr/local/bin/adb",
            "/opt/android-sdk/platform-tools/adb",
            "/opt/android-sdk-linux/platform-tools/adb",
        ])
        for c in candidates:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        return "adb"

    @classmethod
    def get_connected_adb_devices(cls) -> List[Dict[str, Any]]:
        """
        Executa 'adb devices -l' e retorna a lista de aparelhos físicos/emuladores
        conectados e prontos para teste.
        """
        adb_bin = cls._find_adb_binary()
        devices = []
        try:
            res = subprocess.run(
                [adb_bin, "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=6
            )
            lines = res.stdout.strip().split("\n")
            # Primeira linha é 'List of devices attached'
            for line in lines[1:]:
                line = line.strip()
                if not line or line.startswith("*"):
                    continue

                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    state = parts[1]

                    details = {"serial": serial, "state": state, "raw": line}
                    for token in parts[2:]:
                        if ":" in token:
                            k, v = token.split(":", 1)
                            details[k] = v

                    devices.append(details)
                    logger.info(f"📱 [Device Pool] Detected connected ADB device: {serial} ({state})")
        except Exception as e:
            logger.warning(f"⚠️ [Device Pool] Could not list connected ADB devices via {adb_bin}: {e}")

        return devices

    @classmethod
    def acquire_device(
        cls,
        db: Optional[Session] = None,
        product_id: Optional[int] = None,
        device_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Aloca um dispositivo e reserva portas dedicadas (systemPort e mjpegServerPort).
        Retorna informações para injeção nas capabilities do Appium.
        """
        with cls._lock:
            selected_device = None

            # 1. Busca por device_id específico
            if db and product_id and device_id:
                dev = db.query(ProductMobileDeviceDB).filter(
                    ProductMobileDeviceDB.id == device_id,
                    ProductMobileDeviceDB.product_id == product_id
                ).first()
                if dev:
                    selected_device = dev
                    cls._locked_devices.add(dev.id)
                    logger.info(f"📱 [Device Pool] Locked specified device ID {dev.id} ({dev.label})")

            # 2. Se nenhum especificado, busca um ativo disponível no banco
            elif db and product_id:
                active_devices = db.query(ProductMobileDeviceDB).filter(
                    ProductMobileDeviceDB.product_id == product_id,
                    ProductMobileDeviceDB.is_active == True
                ).all()

                for dev in active_devices:
                    if dev.id not in cls._locked_devices:
                        selected_device = dev
                        cls._locked_devices.add(dev.id)
                        logger.info(f"📱 [Device Pool] Auto-allocated idle device ID {dev.id} ({dev.label})")
                        break

            # 3. Alocação de portas dinâmicas
            allocated_sys_port = None
            for port in range(cls.SYSTEM_PORT_MIN, cls.SYSTEM_PORT_MAX + 1):
                if port not in cls._allocated_system_ports:
                    allocated_sys_port = port
                    cls._allocated_system_ports.add(port)
                    break
            if not allocated_sys_port:
                allocated_sys_port = cls.SYSTEM_PORT_MIN

            allocated_mjpeg_port = None
            for port in range(cls.MJPEG_PORT_MIN, cls.MJPEG_PORT_MAX + 1):
                if port not in cls._allocated_mjpeg_ports:
                    allocated_mjpeg_port = port
                    cls._allocated_mjpeg_ports.add(port)
                    break
            if not allocated_mjpeg_port:
                allocated_mjpeg_port = cls.MJPEG_PORT_MIN

            logger.info(
                f"🔌 [Device Pool] Assigned ports: systemPort={allocated_sys_port}, "
                f"mjpegServerPort={allocated_mjpeg_port} (Device: {selected_device.label if selected_device else 'Default/Product'})"
            )

            return {
                "device": selected_device,
                "device_id": selected_device.id if selected_device else None,
                "system_port": allocated_sys_port,
                "systemPort": allocated_sys_port,
                "mjpeg_port": allocated_mjpeg_port,
                "mjpegServerPort": allocated_mjpeg_port,
                "device_name": selected_device.device_name if selected_device else None,
                "platform": getattr(selected_device, "platform", "android") if selected_device else None,
                "platform_version": getattr(selected_device, "platform_version", None) if selected_device else None,
            }

    @classmethod
    def release_device(
        cls,
        device_id: Optional[Any] = None,
        system_port: Optional[int] = None,
        mjpeg_port: Optional[int] = None
    ):
        """Libera o dispositivo e as portas alocadas. Aceita IDs/portas individuais ou o dict retornado por acquire_device."""
        if isinstance(device_id, dict):
            info = device_id
            device_id = info.get("device_id")
            system_port = info.get("system_port") or info.get("systemPort")
            mjpeg_port = info.get("mjpeg_port") or info.get("mjpegServerPort")

        with cls._lock:
            if device_id and device_id in cls._locked_devices:
                cls._locked_devices.remove(device_id)
                logger.info(f"📱 [Device Pool] Released device ID {device_id}")

            if system_port and system_port in cls._allocated_system_ports:
                cls._allocated_system_ports.remove(system_port)
                logger.info(f"🔌 [Device Pool] Released systemPort {system_port}")

            if mjpeg_port and mjpeg_port in cls._allocated_mjpeg_ports:
                cls._allocated_mjpeg_ports.remove(mjpeg_port)
                logger.info(f"🔌 [Device Pool] Released mjpegServerPort {mjpeg_port}")

    @classmethod
    def get_pool_status(cls, db: Session, product_id: int) -> List[Dict[str, Any]]:
        """Retorna o estado de todos os dispositivos do produto (idle, busy, portas)."""
        with cls._lock:
            devices = db.query(ProductMobileDeviceDB).filter(
                ProductMobileDeviceDB.product_id == product_id
            ).all()

            status_list = []
            for d in devices:
                is_busy = d.id in cls._locked_devices
                status_list.append({
                    "id": d.id,
                    "label": d.label,
                    "device_name": d.device_name,
                    "platform": d.platform,
                    "platform_version": d.platform_version,
                    "is_active": d.is_active,
                    "status": "busy" if is_busy else "idle"
                })
            return status_list

