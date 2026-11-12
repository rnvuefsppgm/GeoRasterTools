#!/usr/bin/env python3
"""
main.py
=======

Ponto de entrada do GeoRaster Tools V3.

Uso:

    python main.py                 abre a interface grafica
    python main.py --autoteste     confere o ambiente e encerra

O modo ``--autoteste`` existe por causa do executavel: ele e compilado sem
console (``console=False``), entao um erro de empacotamento -- tipicamente os
dados do GDAL ou do PROJ que nao foram junto -- apareceria so como uma janela
que nao abre, sem nenhuma mensagem. O autoteste exercita justamente as partes
que dependem desses dados e grava o resultado num arquivo ao lado do executavel.

Toda a logica de processamento geoespacial esta em raster_ops.py, em
merge_ops.py e no pacote amostragem_fogo; a interface esta em gui.py,
tab_merge.py e tab_amostragem.py.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime


def _pasta_do_programa() -> str:
    """Pasta do executavel quando congelado; a do script caso contrario."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def autoteste() -> int:
    """Confere que tudo de que a aplicacao depende funciona neste computador."""
    linhas: list[str] = []
    falhas = 0

    def checar(rotulo: str, funcao) -> None:
        nonlocal falhas
        try:
            detalhe = funcao()
            linhas.append(f"[ok]    {rotulo}: {detalhe}")
        except Exception as exc:  # noqa: BLE001
            falhas += 1
            linhas.append(f"[FALHA] {rotulo}: {exc.__class__.__name__}: {exc}")
            linhas.append("        " + traceback.format_exc().replace("\n", "\n        "))

    bits = 64 if sys.maxsize > 2**32 else 32
    linhas.append("GeoRaster Tools V3 - autoteste do ambiente")
    linhas.append(f"Data ......... {datetime.now():%d/%m/%Y %H:%M:%S}")
    linhas.append(f"Executavel ... {sys.executable}")
    linhas.append(f"Congelado .... {bool(getattr(sys, 'frozen', False))}")
    linhas.append(f"Python ....... {sys.version.split()[0]} ({bits} bits)")
    linhas.append(f"Plataforma ... {sys.platform}")
    linhas.append(f"GDAL_DATA .... {os.environ.get('GDAL_DATA', '(nao definido)')}")
    linhas.append(f"PROJ_DATA .... {os.environ.get('PROJ_DATA', '(nao definido)')}")
    linhas.append("")

    def _numpy():
        import numpy as np

        return f"{np.__version__}, soma de teste = {np.arange(10).sum()}"

    def _rasterio():
        import rasterio

        return f"{rasterio.__version__} (GDAL {rasterio.__gdal_version__})"

    def _gdal_data():
        # Ler um CRS pelo codigo EPSG falha se os dados do GDAL nao vieram junto.
        from rasterio.crs import CRS as RioCRS

        return f"EPSG:4326 reconhecido ({RioCRS.from_epsg(4326).to_string()})"

    def _pyproj():
        import pyproj

        return f"{pyproj.__version__}, base PROJ em {pyproj.datadir.get_data_dir()}"

    def _transformacao():
        # Exercita a base do PROJ de verdade: geografico -> geocentrico, que e
        # o calculo usado pela distancia minima da aba 6.
        from pyproj import CRS, Transformer

        t = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(4978), always_xy=True)
        x, y, z = t.transform(-47.0, -15.0, 0.0)
        if not all(abs(valor) > 1.0 for valor in (x, y, z)):
            raise RuntimeError(f"resultado suspeito: {x}, {y}, {z}")
        return f"WGS84 -> ECEF ok ({x:.1f}, {y:.1f}, {z:.1f})"

    def _shapefile():
        import shapefile

        return f"pyshp {shapefile.__version__}"

    def _tkinter():
        import tkinter as tk

        raiz = tk.Tk()
        raiz.withdraw()
        versao = raiz.tk.call("info", "patchlevel")
        raiz.destroy()
        return f"Tcl/Tk {versao}"

    def _modulos_da_aplicacao():
        import config
        import merge_ops  # noqa: F401
        import raster_ops  # noqa: F401
        import tab_amostragem  # noqa: F401
        import tab_merge  # noqa: F401
        from amostragem_fogo import __version__ as versao_nucleo

        return f"GeoRaster Tools {config.APP_VERSION}, nucleo de amostragem {versao_nucleo}"

    def _janela_completa():
        import tkinter as tk

        from gui import GeoRasterToolsApp

        raiz = tk.Tk()
        raiz.withdraw()
        try:
            app = GeoRasterToolsApp(raiz)
            raiz.update_idletasks()
            abas = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
            if len(abas) != 7:
                raise RuntimeError(f"esperadas 7 abas, encontradas {len(abas)}: {abas}")
            if not hasattr(app, "aba_amostragem"):
                raise RuntimeError("a aba de amostragem nao montou (pyproj ausente?)")
            if not hasattr(app, "aba_merge"):
                raise RuntimeError("a aba de merge nao montou")

            # Barra de progresso em TODAS as abas (exigencia da revisao da V3).
            donos = {
                "Recortar Rasters": getattr(app, "clip_rodape", None),
                "Reclassificar": getattr(app, "rcl_rodape", None),
                "Separar Bandas": getattr(app, "bands_rodape", None),
                "Reamostrar": getattr(app, "res_rodape", None),
                "Merge de Rasters": getattr(app, "aba_merge", None),
                "Verificar Alinhamento": getattr(app, "ver_rodape", None),
                "Amostragem": getattr(app, "aba_amostragem", None),
            }
            sem_barra = [nome for nome, dono in donos.items()
                         if getattr(dono, "barra", None) is None]
            if sem_barra:
                raise RuntimeError("abas sem barra de progresso: " + ", ".join(sem_barra))

            return (f"{len(abas)} abas, todas com barra de progresso: "
                    + ", ".join(abas))
        finally:
            raiz.destroy()

    def _processamento_real():
        """Grava um raster, reamostra, confere o alinhamento e sorteia amostras.

        Importar as bibliotecas nao prova que o executavel consegue trabalhar:
        so um ciclo completo exercita a escrita de GeoTIFF, a reamostragem, a
        leitura de CRS e a distancia geocentrica de uma vez.
        """
        import shutil
        import tempfile

        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        import raster_ops as ro
        from amostragem_fogo.config import (Faixa, ParametrosAmostragem,
                                             ParametrosAnalise)
        from amostragem_fogo.pipeline import executar_completo
        from amostragem_fogo.sdm import FiltroSDM

        pasta = tempfile.mkdtemp(prefix="georaster_autoteste_")
        try:
            fogo = os.path.join(pasta, "fogo.tif")
            rng = np.random.default_rng(1)
            dados = rng.integers(0, 8, size=(400, 400)).astype("int16")
            with rasterio.open(
                fogo, "w", driver="GTiff", height=400, width=400, count=1,
                dtype="int16", crs="EPSG:4326", nodata=-9999,
                transform=from_origin(-47.0, -15.0, 0.001, 0.001),
            ) as dst:
                dst.write(dados, 1)

            modelo_path = os.path.join(pasta, "modelo.tif")
            with rasterio.open(
                modelo_path, "w", driver="GTiff", height=40, width=40, count=1,
                dtype="float32", crs="EPSG:4326", nodata=-9999.0,
                transform=from_origin(-47.0, -15.0, 0.01, 0.01),
            ) as dst:
                dst.write(np.ones((40, 40), dtype="float32"), 1)

            modelo = ro.read_meta(modelo_path)
            reamostrado = os.path.join(pasta, "res_fogo.tif")
            ro.resample_raster(fogo, modelo, reamostrado, method="bilinear", dtype="FLT4S")

            alinhamento = ro.verificar_alinhamento(reamostrado, modelo)
            if alinhamento["Status"] != "OK":
                raise RuntimeError(f"alinhamento falhou: {alinhamento['Detalhes']}")

            saida = os.path.join(pasta, "amostras")
            from pathlib import Path

            resultado = executar_completo(ParametrosAmostragem(
                analise=ParametrosAnalise(
                    raster=Path(fogo), banda=1, anos_observados=41,
                    valores_excluidos=frozenset({-9999.0}),
                ),
                saida=Path(saida), presenca=Faixa(4, 7), ausencia=Faixa(0, 3),
                n_presenca=50, n_ausencia=50, distancia_minima_m=500.0, semente=1,
                filtro_sdm=FiltroSDM(preditores=(Path(modelo_path),)),
            ))
            if len(resultado.presenca) != 50:
                raise RuntimeError(
                    f"esperadas 50 presencas, obtidas {len(resultado.presenca)}"
                )
            csv_saida = os.path.join(saida, "amostras_presencas.csv")
            with open(csv_saida, encoding="utf-8") as arquivo:
                cabecalho = arquivo.readline().strip()
            if cabecalho != "x,y,Value":
                raise RuntimeError(f"cabecalho inesperado: {cabecalho!r}")
            return (
                f"raster gravado, reamostrado, alinhamento OK, "
                f"{len(resultado.presenca)} presencas em formato {cabecalho}"
            )
        finally:
            shutil.rmtree(pasta, ignore_errors=True)

    def _merge_de_blocos():
        """Corta um raster em dois blocos e remonta, conferindo pixel a pixel.

        A aba de merge grava GeoTIFF em tiles com LZW e BigTIFF condicional --
        combinacao que depende de drivers que o empacotador pode deixar para
        tras. Importar o rasterio nao prova que isso funciona; este ciclo prova.
        """
        import shutil
        import tempfile

        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        import merge_ops as mo

        pasta = tempfile.mkdtemp(prefix="georaster_merge_")
        try:
            inteiro = np.arange(800, dtype="int16").reshape(20, 40)
            caminhos = []
            for indice, (col, dados) in enumerate(
                ((0, inteiro[:, :20]), (20, inteiro[:, 20:]))
            ):
                caminho = os.path.join(
                    pasta, f"bloco-{indice:010d}-{col:010d}.tif")
                with rasterio.open(
                    caminho, "w", driver="GTiff", height=20, width=20, count=1,
                    dtype="int16", crs="EPSG:4326", nodata=-9999,
                    transform=from_origin(-47.0 + col * 0.01, -15.0, 0.01, 0.01),
                ) as dst:
                    dst.write(dados, 1)
                caminhos.append(caminho)

            grupos = mo.agrupar_para_merge(caminhos)
            if len(grupos) != 1 or grupos[0].quantidade != 2:
                raise RuntimeError(f"agrupamento inesperado: {grupos}")

            saida = os.path.join(pasta, grupos[0].nome_de_saida())
            resultado = mo.executar_merge(
                mo.ParametrosMerge(arquivos=grupos[0].arquivos, saida=saida))

            with rasterio.open(saida) as src:
                refeito = src.read(1)
            if not np.array_equal(refeito, inteiro):
                raise RuntimeError("o mosaico nao reproduziu o raster original")
            return (
                f"2 blocos unidos em {os.path.basename(saida)} "
                f"({resultado.resumo()})"
            )
        finally:
            shutil.rmtree(pasta, ignore_errors=True)

    checar("numpy", _numpy)
    checar("rasterio", _rasterio)
    checar("dados do GDAL", _gdal_data)
    checar("pyproj", _pyproj)
    checar("base do PROJ", _transformacao)
    checar("pyshp", _shapefile)
    checar("tkinter", _tkinter)
    checar("modulos da aplicacao", _modulos_da_aplicacao)
    checar("janela com as 7 abas", _janela_completa)
    checar("merge de blocos", _merge_de_blocos)
    checar("processamento de ponta a ponta", _processamento_real)

    linhas.append("")
    linhas.append("=" * 60)
    linhas.append("RESULTADO: TUDO OK" if falhas == 0 else f"RESULTADO: {falhas} FALHA(S)")

    texto = "\n".join(linhas)
    destino = os.path.join(_pasta_do_programa(), "autoteste.txt")
    try:
        with open(destino, "w", encoding="utf-8") as arquivo:
            arquivo.write(texto + "\n")
    except OSError:
        destino = None

    print(texto)
    if destino:
        print(f"\nRelatorio gravado em: {destino}")
    return 0 if falhas == 0 else 1


def main() -> int:
    if "--autoteste" in sys.argv[1:]:
        return autoteste()
    try:
        from gui import launch_app

        launch_app()
        return 0
    except Exception:  # noqa: BLE001
        print("Erro fatal ao iniciar o GeoRaster Tools:", file=sys.stderr)
        traceback.print_exc()
        # Sem console, um traceback impresso se perde. Grava ao lado do .exe.
        try:
            destino = os.path.join(_pasta_do_programa(), "erro_inicializacao.txt")
            with open(destino, "w", encoding="utf-8") as arquivo:
                arquivo.write(f"{datetime.now():%d/%m/%Y %H:%M:%S}\n\n")
                arquivo.write(traceback.format_exc())
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
