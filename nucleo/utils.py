"""
utils.py
========

Utilitarios genericos: logging, cancelamento seguro de processamento em
segundo plano e formatacao de tempo decorrido/estimado. Nenhuma logica de
processamento geoespacial fica aqui.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional


def setup_logging(log_path: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("georastertools")
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(level)
    logger.addHandler(stream_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("georastertools")


class CancelledError(Exception):
    """Levantada quando o usuario clica em 'Parar' durante o processamento."""


class CancellationToken:
    """Token compartilhado entre a GUI (thread principal) e o processamento
    (thread de trabalho) para permitir cancelamento seguro."""

    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self):
        if self._cancelled:
            raise CancelledError("Processamento interrompido pelo usuario.")


def ts_msg(msg: str) -> str:
    """Equivalente a ts_msg() em R: prefixa a mensagem com [HH:MM:SS]."""
    return f"[{time.strftime('%H:%M:%S')}] {msg}"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds != seconds or seconds < 0 or seconds == float("inf"):
        return "--:--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def list_tif_files(directory: str) -> list:
    """Lista arquivos .tif/.tiff em um diretorio (nao recursivo), ordenados,
    equivalente a list.files(dir, pattern='\\.tif$', ignore.case=TRUE)."""
    if not directory or not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        low = name.lower()
        if low.endswith(".tif") or low.endswith(".tiff"):
            out.append(os.path.join(directory, name))
    return out
