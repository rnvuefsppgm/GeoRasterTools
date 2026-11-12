"""
tests/test_reamostragem_zeros.py
================================

Testes do defeito que zerou a amostragem do Acre.

A aba Reamostrar tinha a opcao "Converter zeros (0) para NA" marcada por padrao,
herdada do app em R, onde 0 significa ausencia de dado nos rasters do MapBiomas.
Hoje ela vem desmarcada; estes testes travam esse padrao.
Em raster de DISTANCIA o zero e o valor mais informativo -- "dentro da classe" --
e converte-lo em NA apaga exatamente as areas de interesse.

Medido nos dados reais do Acre: `Dist_VEGET.tif` tinha 38,2 % de zeros e saiu da
reamostragem com apenas 7,2 % da grade valida (o esperado pela mascara do estado
era 45 %). `Dist_AGRO` tinha 6,9 % de zeros e saiu com 38,5 %. Com cinco rasters
assim, a intersecao dos preditores ficava vazia e a amostragem devolvia 0 de 1000.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import raster_ops as ro


@pytest.fixture
def distancia_e_modelo(tmp_path):
    """Raster de distancia 100 % valido, com muitos zeros, e um modelo grosso."""
    largura = altura = 600
    dados = np.full((altura, largura), 500, dtype="uint16")
    dados[:, : largura // 2] = 0  # metade da area esta "dentro da classe"

    origem = from_origin(-47.0, -15.0, 0.0005, 0.0005)
    distancia = tmp_path / "Dist_VEGET.tif"
    with rasterio.open(
        distancia, "w", driver="GTiff", height=altura, width=largura, count=1,
        dtype="uint16", crs="EPSG:4326", transform=origem, nodata=None,
    ) as dst:
        dst.write(dados, 1)

    lado = 30
    modelo = tmp_path / "modelo.tif"
    with rasterio.open(
        modelo, "w", driver="GTiff", height=lado, width=lado, count=1,
        dtype="float32", crs="EPSG:4326", nodata=-3.4e38,
        transform=from_origin(-47.0, -15.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.ones((lado, lado), dtype="float32"), 1)

    return distancia, ro.read_meta(str(modelo)), tmp_path


def _pct_valido(caminho) -> float:
    with rasterio.open(caminho) as src:
        dados = src.read(1).astype("float64")
        nodata = src.nodata
        if nodata is None:
            valido = np.isfinite(dados)
        elif np.isnan(nodata):
            valido = np.isfinite(dados)
        else:
            valido = np.isfinite(dados) & (dados != nodata)
    return 100.0 * valido.mean()


def test_zeros_para_na_destroi_raster_de_distancia(distancia_e_modelo):
    """Reproduz o defeito: entrada 100 % valida, saida com metade apagada."""
    origem, modelo, tmp = distancia_e_modelo
    saida = tmp / "com_zeros_na.tif"
    stats = ro.resample_raster(str(origem), modelo, str(saida),
                               method="near", dtype="FLT4S", zeros_to_na=True)
    assert stats["pct_zeros_entrada"] == pytest.approx(50.0, abs=1.0)
    assert _pct_valido(saida) < 60.0, "metade da grade deveria ter sido apagada"


def test_sem_zeros_para_na_o_raster_sobrevive(distancia_e_modelo):
    """Desmarcando a opcao, o raster de distancia chega inteiro."""
    origem, modelo, tmp = distancia_e_modelo
    saida = tmp / "sem_zeros_na.tif"
    stats = ro.resample_raster(str(origem), modelo, str(saida),
                               method="near", dtype="FLT4S", zeros_to_na=False)
    assert stats["zeros_convertidos"] == 0
    assert _pct_valido(saida) == pytest.approx(100.0, abs=1.0)


def test_aviso_quando_muitos_zeros_viram_na(distancia_e_modelo):
    """A perda tem de ser visivel; antes ela passava em silencio."""
    origem, modelo, tmp = distancia_e_modelo
    saida = tmp / "avisa.tif"
    stats = ro.resample_raster(str(origem), modelo, str(saida),
                               method="near", dtype="FLT4S", zeros_to_na=True)
    avisos = ro.avisos_da_reamostragem(saida.name, stats)
    assert avisos, "deveria avisar que metade dos pixels virou NA"
    assert "DISTANCIA" in avisos[0]


def test_sem_aviso_quando_nao_ha_zeros(tmp_path):
    largura = altura = 200
    dados = np.full((altura, largura), 7.5, dtype="float32")
    origem = tmp_path / "continuo.tif"
    with rasterio.open(
        origem, "w", driver="GTiff", height=altura, width=largura, count=1,
        dtype="float32", crs="EPSG:4326", nodata=-9999.0,
        transform=from_origin(-47.0, -15.0, 0.001, 0.001),
    ) as dst:
        dst.write(dados, 1)

    lado = 20
    modelo_path = tmp_path / "modelo.tif"
    with rasterio.open(
        modelo_path, "w", driver="GTiff", height=lado, width=lado, count=1,
        dtype="float32", crs="EPSG:4326", nodata=-9999.0,
        transform=from_origin(-47.0, -15.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.ones((lado, lado), dtype="float32"), 1)

    saida = tmp_path / "saida.tif"
    stats = ro.resample_raster(str(origem), ro.read_meta(str(modelo_path)), str(saida),
                               method="bilinear", dtype="FLT4S", zeros_to_na=True)
    assert ro.avisos_da_reamostragem(saida.name, stats) == []


def test_modelo_com_nodata_nan_agora_mascara(tmp_path):
    """`dado != nan` e sempre verdadeiro: a mascara do modelo nunca agia."""
    lado = 40
    modelo_path = tmp_path / "modelo_nan.tif"
    modelo_dados = np.ones((lado, lado), dtype="float32")
    modelo_dados[:, : lado // 2] = np.nan  # metade do modelo e NoData
    with rasterio.open(
        modelo_path, "w", driver="GTiff", height=lado, width=lado, count=1,
        dtype="float32", crs="EPSG:4326", nodata=float("nan"),
        transform=from_origin(-47.0, -15.0, 0.01, 0.01),
    ) as dst:
        dst.write(modelo_dados, 1)

    origem = tmp_path / "cheio.tif"
    with rasterio.open(
        origem, "w", driver="GTiff", height=400, width=400, count=1,
        dtype="float32", crs="EPSG:4326", nodata=-9999.0,
        transform=from_origin(-47.0, -15.0, 0.001, 0.001),
    ) as dst:
        dst.write(np.full((400, 400), 3.0, dtype="float32"), 1)

    saida = tmp_path / "mascarado.tif"
    ro.resample_raster(str(origem), ro.read_meta(str(modelo_path)), str(saida),
                       method="near", dtype="FLT4S", zeros_to_na=False)
    pct = _pct_valido(saida)
    assert pct == pytest.approx(50.0, abs=5.0), (
        f"a mascara do modelo com NoData NaN nao foi aplicada ({pct:.1f}% valido)"
    )


def test_padrao_e_nao_converter_zeros(distancia_e_modelo):
    """O padrao tem de ser DESMARCADO, na funcao e na tela.

    Com o padrao ligado, um raster de distancia 100 % valido saia da
    reamostragem com 7 % de pixels uteis e a amostragem seguinte devolvia
    0 de 1000 sem explicar por que.
    """
    import inspect

    assinatura = inspect.signature(ro.resample_raster)
    assert assinatura.parameters["zeros_to_na"].default is False

    origem, modelo, tmp = distancia_e_modelo
    saida = tmp / "padrao.tif"
    stats = ro.resample_raster(str(origem), modelo, str(saida),
                               method="near", dtype="FLT4S")
    assert stats["zeros_convertidos"] == 0
    assert _pct_valido(saida) == pytest.approx(100.0, abs=1.0)
    assert ro.avisos_da_reamostragem(saida.name, stats) == []


def test_checkbox_da_aba_comeca_desmarcado(app_georaster):
    """O padrao e lido da fotografia tirada logo apos construir a janela."""
    assert app_georaster.padroes_iniciais["res_zeros_na"] is False


def test_estatisticas_devolvidas(distancia_e_modelo):
    origem, modelo, tmp = distancia_e_modelo
    stats = ro.resample_raster(str(origem), modelo, str(tmp / "s.tif"),
                               method="near", dtype="FLT4S", zeros_to_na=False)
    for chave in ("validos_antes", "zeros_convertidos", "validos_depois",
                  "total_saida", "pct_valido_saida", "pct_zeros_entrada"):
        assert chave in stats
