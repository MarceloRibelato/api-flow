import logging
import sys


def setup_logging():
    """
    Configura o sistema de logs da aplicação.
    Define formato, handlers (Console e Arquivo) e nível de log.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),  # Log no console (stdout)
            logging.FileHandler("api.log", encoding="utf-8"),  # Log em arquivo
        ],
    )

    # Reduzir ruído de bibliotecas de terceiros se necessário
    # logging.getLogger("multipart").setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.info("Logging configured successfully.")
    return logger
