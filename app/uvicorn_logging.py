import logging

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s"},
        "access":  {"format": "%(asctime)s | %(levelname)s | %(name)s | %(client_addr)s - '%(request_line)s' %(status_code)s"},
    },
    "handlers": {
        # não abrimos ficheiros aqui—delegamos aos handlers criados por setup_logging("uvicorn")
        "uvicorn_stream": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "loggers": {
        # estes nomes são os que o uvicorn usa internamente
        "uvicorn":        {"handlers": ["uvicorn_stream"], "level": "INFO", "propagate": True},
        "uvicorn.error":  {"handlers": ["uvicorn_stream"], "level": "INFO", "propagate": True},
        "uvicorn.access": {"handlers": ["uvicorn_stream"], "level": "INFO", "propagate": True},
    },
}