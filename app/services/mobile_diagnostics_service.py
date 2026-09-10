import logging
import os
import re
import uuid
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class MobileDiagnosticsService:
    """
    Serviço de Diagnóstico Forense e Observabilidade para Automação Mobile.
    Captura e correlaciona logs de sistema nativos (logcat no Android / syslog no iOS)
    em caso de falha de teste ou queda do aplicativo, além de detectar e desobstruir
    diálogos nativos bloqueantes do sistema operacional.
    """

    CRASH_KEYWORDS = [
        'FATAL EXCEPTION',
        'AndroidRuntime',
        'ANR in',
        'CRASH',
        'SIGSEGV',
        'SIGBUS',
        'NullPointerException',
        'ReactNativeJS',
        'uncaught exception',
        'Process: '
    ]

    @classmethod
    def extract_crash_diagnostics(cls, driver, max_lines: int = 80) -> Optional[Dict[str, Any]]:
        """
        Extrai e analisa os logs nativos do dispositivo após uma falha de passo.
        Retorna um dicionário com o resumo da stack trace e URL do arquivo de log salvo.
        """
        if not driver:
            return None

        try:
            platform = str(driver.capabilities.get('platformName', 'android')).lower()
            raw_logs: List[Dict[str, Any]] = []

            # Tenta obter logcat (Android) ou syslog (iOS) via WebDriver logs API
            try:
                available_types = driver.log_types or []
                if 'logcat' in available_types:
                    raw_logs = driver.get_log('logcat')
                elif 'syslog' in available_types:
                    raw_logs = driver.get_log('syslog')
                elif 'crashlog' in available_types:
                    raw_logs = driver.get_log('crashlog')
            except Exception as log_fetch_err:
                logger.debug(f"[Diagnostics] driver.get_log notice: {log_fetch_err}")

            if not raw_logs:
                return None

            # Filtra linhas com severidade alta ou palavras-chave de crash
            crash_entries = []
            recent_logs = raw_logs[-max_lines:]

            for entry in recent_logs:
                msg = entry.get('message', '')
                level = entry.get('level', '')
                if any(kw in msg for kw in cls.CRASH_KEYWORDS) or level in ('SEVERE', 'ERROR', 'FATAL'):
                    crash_entries.append(msg)

            if not crash_entries:
                return None

            # Formata o resumo do crash (até 8 linhas principais para o relatório)
            summary_lines = crash_entries[:8]
            summary_text = "\n".join(summary_lines)

            # Salva o arquivo de log completo na pasta de screenshots/evidências
            from app.main import VIDEO_DIR
            screenshot_dir = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
            os.makedirs(screenshot_dir, exist_ok=True)

            log_filename = f"crash_{uuid.uuid4().hex[:8]}.log"
            log_path = os.path.join(screenshot_dir, log_filename)

            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"=== MOBILE CRASH FORENSIC LOG ({platform.upper()}) ===\n\n")
                for entry in crash_entries:
                    f.write(f"{entry}\n")

            log_url = f"/screenshots/{log_filename}"
            logger.info(f"📋 [Diagnostics] Crash diagnostics saved to {log_url}")

            return {
                "summary": summary_text,
                "log_url": log_url,
                "log_path": log_url,
                "total_crash_lines": len(crash_entries)
            }

        except Exception as e:
            logger.debug(f"[Diagnostics] Error extracting crash diagnostics: {e}")
            return None

    @classmethod
    def dismiss_system_alerts_if_present(cls, driver) -> bool:
        """
        Detecta e fecha defensivamente alertas nativos do SO (avisos de bateria,
        permissões pendentes, diálogos de 'App não está respondendo' / ANR).
        Retorna True se algum diálogo foi fechado.
        """
        if not driver:
            return False

        # Estratégia 1: driver.switch_to.alert padrão W3C
        try:
            alert = driver.switch_to.alert
            alert_text = alert.text
            if alert_text:
                logger.info(f"⚠️ [Diagnostics] Native system alert detected: '{alert_text}'. Auto-dismissing...")
                try:
                    alert.accept()
                except Exception:
                    alert.dismiss()
                return True
        except Exception:
            pass

        # Estratégia 2: Diálogos comuns do Android (ANR 'Aguardar / Fechar app', permissões)
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            # Botões padrão de diálogos de sistema do Android
            system_buttons = driver.find_elements(
                AppiumBy.XPATH,
                "//android.widget.Button[@resource-id='android:id/button1' or @resource-id='android:id/button2' or "
                "@text='Aguardar' or @text='Wait' or @text='OK' or @text='Permitir' or @text='Allow']"
            )
            if system_buttons:
                # Se for um diálogo de sistema do pacote android:id
                first_btn = system_buttons[0]
                btn_text = first_btn.text or first_btn.get_attribute('text') or 'System Button'
                logger.info(f"⚠️ [Diagnostics] System dialog button detected ('{btn_text}'). Dismissing to unblock flow...")
                first_btn.click()
                return True
        except Exception:
            pass

        return False

