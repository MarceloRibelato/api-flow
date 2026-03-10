import logging
import sys


def setup_logging():
    """
    Configura o sistema de logs da aplicação.
    Define formato, handlers (Console e Arquivo) e nível de log.
    """
    import os
    handlers = [logging.StreamHandler(sys.stdout)]
    
    # Only use file logging if explicitly enabled (to avoid slow volume mounts in Docker/Windows)
    if os.getenv("ENABLE_FILE_LOGGING", "false").lower() == "true":
        file_handler = RotatingFileHandler(
            "api.log", 
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3, 
            encoding="utf-8"
        )
        handlers.append(file_handler)
    
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
