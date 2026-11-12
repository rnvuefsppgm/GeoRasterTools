"""
tests/conftest.py
=================

Infraestrutura compartilhada dos testes de interface.

Criar e destruir varios ``tk.Tk()`` ao longo de uma sessao de pytest e
instavel: a partir da segunda criacao ela falha de forma intermitente, e os
testes de janela apareciam ora como "passou", ora como "pulado -- sem ambiente
grafico". Uma unica raiz por sessao resolve.

O estado inicial dos controles e fotografado logo apos a construcao, para que
testes de valor padrao continuem valendo mesmo com a janela compartilhada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def tk_root():
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sem ambiente grafico")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:  # noqa: BLE001 - encerrar nunca deve derrubar a suite
        pass


@pytest.fixture(scope="session")
def app_georaster(tk_root):
    """A janela completa, com as sete abas, construida uma unica vez."""
    from interface.gui import GeoRasterToolsApp

    app = GeoRasterToolsApp(tk_root)
    tk_root.update_idletasks()

    # Fotografia dos padroes, tirada antes de qualquer teste mexer na tela.
    app.padroes_iniciais = {
        "res_zeros_na": bool(app.res_zeros_na.get()),
        "coordenadas_xy": app.aba_amostragem.v_coord.get(),
        "separador_decimal": app.aba_amostragem.v_decimal.get(),
        "saidas_marcadas": {
            nome: bool(var.get()) for nome, var in app.aba_amostragem.v_saidas.items()
        },
    }
    return app


@pytest.fixture(autouse=True)
def sem_caixas_de_dialogo(monkeypatch):
    """Nenhum teste pode abrir uma caixa de dialogo.

    Uma delas e modal: a suite inteira para e fica esperando um clique que
    ninguem vai dar -- e o ``build_windows.bat`` roda a suite antes de gerar o
    executavel, entao o build travaria junto, sem nenhuma janela visivel para
    explicar o motivo.
    """
    from tkinter import messagebox

    registro = []
    for nome, resposta in (("showinfo", "ok"), ("showwarning", "ok"),
                           ("showerror", "ok"), ("askyesno", False),
                           ("askokcancel", False), ("askretrycancel", False)):
        monkeypatch.setattr(
            messagebox, nome,
            lambda *a, _n=nome, _r=resposta, **k: (registro.append((_n, a)), _r)[1],
            raising=False,
        )
    return registro
