"""
runtime_hook_geo.py
===================

Hook de runtime do PyInstaller, executado antes de qualquer import da
aplicacao no executavel congelado.

Por que e necessario
--------------------
O rasterio precisa dos dados do GDAL (tabelas EPSG, WKT) e o pyproj precisa da
base do PROJ. Duas coisas dao errado sem este hook:

1. **Nao achar os dados.** Fora do ambiente Python, as bibliotecas procuram
   esses arquivos em caminhos que nao existem na maquina de destino, e qualquer
   operacao com CRS falha com mensagens obscuras ("Cannot find proj.db").

2. **Achar os dados ERRADOS.** Se a maquina ja tem QGIS, ArcGIS, PostGIS ou
   Anaconda instalados, as variaveis `GDAL_DATA` e `PROJ_LIB` do sistema apontam
   para a instalacao deles. O executavel entao mistura a nossa versao das
   bibliotecas com a base de dados de outra, o que produz erro de projecao ou,
   pior, resultado silenciosamente errado. Por isso as variaveis herdadas do
   ambiente sao **sobrescritas**, nao apenas preenchidas quando vazias.

O hook so age no executavel congelado; rodando pelo Python, nada e alterado.
"""

import os
import sys


def _definir(variaveis, caminho):
    if caminho and os.path.isdir(caminho):
        for nome in variaveis:
            os.environ[nome] = caminho
        return True
    return False


def _configurar():
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return  # rodando pelo Python: respeitar o ambiente do usuario

    # --- dados do GDAL (rasterio) ---
    for relativo in (
        os.path.join("rasterio", "gdal_data"),
        os.path.join("share", "gdal"),
        "gdal_data",
    ):
        if _definir(("GDAL_DATA",), os.path.join(base, relativo)):
            break

    # --- base do PROJ (pyproj e GDAL) ---
    # PROJ_DATA e o nome usado pelo PROJ 9+; PROJ_LIB e o antigo, ainda lido por
    # versoes mais velhas do GDAL. Definir os dois cobre as duas gerações.
    for relativo in (
        os.path.join("pyproj", "proj_dir", "share", "proj"),
        os.path.join("pyproj", "proj"),
        os.path.join("rasterio", "proj_data"),
        os.path.join("share", "proj"),
        "proj_data",
    ):
        caminho = os.path.join(base, relativo)
        if os.path.isfile(os.path.join(caminho, "proj.db")):
            _definir(("PROJ_DATA", "PROJ_LIB"), caminho)
            break
    else:
        # Nenhum candidato tinha proj.db: procurar em profundidade, uma vez so.
        for raiz, _pastas, arquivos in os.walk(base):
            if "proj.db" in arquivos:
                _definir(("PROJ_DATA", "PROJ_LIB"), raiz)
                break

    # O pyproj guarda o caminho da base num modulo proprio; avisa-lo evita que
    # ele tente descobrir sozinho e acabe numa instalacao alheia.
    caminho_proj = os.environ.get("PROJ_DATA")
    if caminho_proj:
        try:
            import pyproj.datadir

            pyproj.datadir.set_data_dir(caminho_proj)
        except Exception:  # noqa: BLE001 - nunca impedir a aplicacao de abrir
            pass


_configurar()
