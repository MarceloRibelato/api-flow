import logging
import sys


def setup_logging():
    """
    Configura o sistema de logs da aplicação.
    Define formato, handlers (Console e Arquivo) e nível de log.
    """
    # Configuração de rotação de logs (5MB, 3 backups)
    from logging.handlers import RotatingFileHandler
    
    file_handler = RotatingFileHandler(
        "api.log", 
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3, 
        encoding="utf-8"
    )
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),  # Log no console (stdout)
            file_handler,  # Log em arquivo rotativo
        ],
    )

    # Reduzir ruído de bibliotecas de terceiros se necessário
    # logging.getLogger("multipart").setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.info("Logging configured successfully.")
    return logger
