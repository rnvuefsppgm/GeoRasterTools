"""
tests/test_alinhamento_fase.py
==============================

Testes da aba "Verificar Alinhamento".

O defeito corrigido: a origem comparada era a FASE da grade (resto da divisao
do canto pela resolucao, como `terra::origin()`). Fase e grandeza circular --
fase ~0 e fase ~res descrevem o mesmo alinhamento. Comparadas com `abs(a - b)`,
dois rasters cujos cantos diferem em 1,4e-14 grau (1,5 nanometro no terreno)
apareciam afastados de uma celula inteira, e a aba acusava "Origem divergente"
em arquivos perfeitamente alinhados.

Caso real: `res_AC_CLIP_Dist_*.tif`, reamostrados sobre
`Presente/AC_wc2.1_30s_bio_1.tif` (canto -73.99166666666667) e conferidos
contra `HadGEM/126/Hadgem3_126_21_40_Bio1.tif` (canto -73.99166666666666).
Mesma grade, origens separadas por um ulp.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import raster_ops as ro

RES = 0.008333333333333333
LARGURA, ALTURA = 40, 30


def _gravar(caminho: Path, canto_x: float, canto_y: float, res: float = RES,
            largura: int = LARGURA, altura: int = ALTURA) -> Path:
    transform = Affine(res, 0.0, canto_x, 0.0, -res, canto_y)
    with rasterio.open(
        caminho, "w", driver="GTiff", height=altura, width=largura, count=1,
        dtype="float32", crs="EPSG:4326", nodata=-9999.0, transform=transform,
    ) as dst:
        dst.write(np.ones((altura, largura), dtype="float32"), 1)
    return caminho


# ---------------------------------------------------------------------------
# A funcao de fase
# ---------------------------------------------------------------------------


def test_fase_e_circular():
    """Fase ~0 e fase ~res sao o MESMO alinhamento, nao extremos opostos."""
    assert ro.diferenca_de_fase(0.0, RES - 1e-16, RES) < 1e-15
    assert ro.diferenca_de_fase(1e-16, RES, RES) < 1e-15
    # Meia celula de deslocamento continua sendo meia celula.
    assert ro.diferenca_de_fase(0.0, RES / 2, RES) == pytest.approx(RES / 2)
    # Resolucao zero nao pode dividir por zero.
    assert ro.diferenca_de_fase(3.0, 1.0, 0.0) == 2.0


# ---------------------------------------------------------------------------
# O caso real
# ---------------------------------------------------------------------------


def test_diferenca_de_um_ulp_no_canto_continua_alinhado(tmp_path):
    """Reproduz o caso do Acre: cantos separados por 1,4e-14 grau."""
    modelo_path = _gravar(tmp_path / "modelo.tif", -73.99166666666666, -7.1083333333333325)
    saida_path = _gravar(tmp_path / "saida.tif", -73.99166666666667, -7.108333333333334)

    modelo = ro.read_meta(str(modelo_path))
    saida = ro.read_meta(str(saida_path))

    # As duas fases caem em extremos opostos do intervalo [0, res).
    assert abs(saida.origin[0] - modelo.origin[0]) > RES * 0.9, (
        "o cenario precisa reproduzir a virada do modulo"
    )
    # Mas a distancia circular entre elas e desprezivel.
    assert ro.diferenca_de_fase(saida.origin[0], modelo.origin[0], RES) < RES * 1e-9

    resultado = ro.verificar_alinhamento(str(saida_path), modelo)
    assert resultado["Status"] == "OK", resultado["Detalhes"]


def test_canto_expoe_a_coordenada_real(tmp_path):
    caminho = _gravar(tmp_path / "m.tif", -73.99166666666666, -7.1083333333333325)
    meta = ro.read_meta(str(caminho))
    assert meta.canto == (-73.99166666666666, -7.1083333333333325)
    assert meta.origin != meta.canto, "origin e a fase, canto e a coordenada"


# ---------------------------------------------------------------------------
# O que ainda tem de falhar
# ---------------------------------------------------------------------------


def test_meio_pixel_de_deslocamento_e_erro(tmp_path):
    modelo_path = _gravar(tmp_path / "modelo.tif", -73.991666666, -7.108333333)
    torto = _gravar(tmp_path / "torto.tif", -73.991666666 + RES / 2, -7.108333333)
    resultado = ro.verificar_alinhamento(str(torto), ro.read_meta(str(modelo_path)))
    assert resultado["Status"] == "ERRO"
    assert "Origem divergente" in resultado["Detalhes"]
    assert "0.5" in resultado["Detalhes"], "a mensagem deve dizer o deslocamento"


def test_um_pixel_inteiro_de_deslocamento_e_erro(tmp_path):
    """Deslocar uma celula mantem a fase, mas muda a extensao."""
    modelo_path = _gravar(tmp_path / "modelo.tif", -73.991666666, -7.108333333)
    torto = _gravar(tmp_path / "torto.tif", -73.991666666 + RES, -7.108333333)
    resultado = ro.verificar_alinhamento(str(torto), ro.read_meta(str(modelo_path)))
    assert resultado["Status"] == "ERRO"
    assert "Extensao divergente" in resultado["Detalhes"]


def test_resolucao_diferente_e_erro(tmp_path):
    modelo_path = _gravar(tmp_path / "modelo.tif", -73.991666666, -7.108333333)
    outro = _gravar(tmp_path / "outro.tif", -73.991666666, -7.108333333, res=RES * 2)
    resultado = ro.verificar_alinhamento(str(outro), ro.read_meta(str(modelo_path)))
    assert resultado["Status"] == "ERRO"
    assert "Res:" in resultado["Detalhes"]


def test_dimensoes_diferentes_sao_erro(tmp_path):
    modelo_path = _gravar(tmp_path / "modelo.tif", -73.991666666, -7.108333333)
    menor = _gravar(tmp_path / "menor.tif", -73.991666666, -7.108333333, largura=20)
    resultado = ro.verificar_alinhamento(str(menor), ro.read_meta(str(modelo_path)))
    assert resultado["Status"] == "ERRO"
    assert "Colunas" in resultado["Detalhes"]


# ---------------------------------------------------------------------------
# Tolerancia relativa a resolucao
# ---------------------------------------------------------------------------


def test_tolerancia_acompanha_a_unidade_do_crs(tmp_path):
    """Um raster em metros nao pode usar a mesma tolerancia absoluta que graus."""
    res_m = 30.0
    modelo_path = tmp_path / "modelo_utm.tif"
    torto_path = tmp_path / "torto_utm.tif"
    for caminho, deslocamento in ((modelo_path, 0.0), (torto_path, res_m / 2)):
        transform = Affine(res_m, 0.0, 200000.0 + deslocamento, 0.0, -res_m, 8350000.0)
        with rasterio.open(
            caminho, "w", driver="GTiff", height=20, width=20, count=1,
            dtype="float32", crs="EPSG:31983", nodata=-9999.0, transform=transform,
        ) as dst:
            dst.write(np.ones((20, 20), dtype="float32"), 1)

    modelo = ro.read_meta(str(modelo_path))
    assert ro.verificar_alinhamento(str(modelo_path), modelo)["Status"] == "OK"
    ruim = ro.verificar_alinhamento(str(torto_path), modelo)
    assert ruim["Status"] == "ERRO"
    assert "Origem divergente" in ruim["Detalhes"]


def test_saida_da_reamostragem_alinha_com_o_modelo(tmp_path):
    """Fluxo real: reamostrar e conferir tem de dar OK."""
    modelo_path = _gravar(tmp_path / "modelo.tif", -73.99166666666666, -7.1083333333333325)
    origem = tmp_path / "origem.tif"
    transform = Affine(0.0005, 0.0, -73.99, 0.0, -0.0005, -7.11)
    with rasterio.open(
        origem, "w", driver="GTiff", height=300, width=300, count=1,
        dtype="uint16", crs="EPSG:4326", nodata=None, transform=transform,
    ) as dst:
        dst.write(np.full((300, 300), 42, dtype="uint16"), 1)

    modelo = ro.read_meta(str(modelo_path))
    saida = tmp_path / "res_origem.tif"
    ro.resample_raster(str(origem), modelo, str(saida), method="bilinear", dtype="FLT4S")
    resultado = ro.verificar_alinhamento(str(saida), modelo)
    assert resultado["Status"] == "OK", resultado["Detalhes"]
