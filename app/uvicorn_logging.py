# app/uvicorn_logging.py
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s"},
        "access":  {"format": "%(asctime)s | %(levelname)s | %(name)s | %(client_addr)s - '%(request_line)s' %(status_code)s"},
    },
    "handlers": {
        "uvicorn_stream": {"class": "logging.StreamHandler", "formatter": "default"},
        "uvicorn_access": {"class": "logging.StreamHandler", "formatter": "access"},
    },
    "loggers": {
        # manter apenas uvicorn em stream; o resto do teu app vai para o root handlers do main.py
        "uvicorn":        {"handlers": ["uvicorn_stream"], "level": "INFO", "propagate": False},
        "uvicorn.error":  {"handlers": ["uvicorn_stream"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["uvicorn_access"], "level": "INFO", "propagate": False},
    },
}