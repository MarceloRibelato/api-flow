import logging
import sys


def setup_logging():
    """
    Configura o sistema de logs da aplicação.
    Define formato, handlers (Console e Arquivo) e nível de log.
    """
    import os
    handlers = [logging.StreamHandler(sys.stdout)]
    
    from logging.handlers import RotatingFileHandler
    try:
        log_file = "/app/api.log" if os.path.exists("/app") else "api.log"
        file_handler = RotatingFileHandler(
            log_file, 
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3, 
            encoding="utf-8"
        )
        handlers.append(file_handler)
    except Exception:
        pass
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )

    # Reduzir ruído de bibliotecas de terceiros se necessário
    # logging.getLogger("multipart").setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.info("Logging configured successfully.")
    return logger
