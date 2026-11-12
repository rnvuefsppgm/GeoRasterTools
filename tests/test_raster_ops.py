"""
tests/test_raster_ops.py
=========================

Testes unitarios de raster_ops.py usando rasters sinteticos gerados em
tempo de execucao (nao dependem de arquivos externos). Cobrem:

    - reclassificacao (regras default estilo MapBiomas)
    - nomenclatura de bandas ("bioclim" e "generico")
    - parse/gravacao de regras em .txt (round-trip)
    - reamostragem (near/bilinear) para grade de um raster modelo
    - deteccao automatica do metodo de reamostragem
    - verificacao de alinhamento (raster alinhado vs. desalinhado)
    - recorte por shapefile (clip)
"""

import os

import numpy as np
import pytest
import rasterio
import shapefile as pyshp
from rasterio.transform import from_origin

import raster_ops as ro


@pytest.fixture
def tmp_rasters(tmp_path):
    return tmp_path


def test_reclassify_mapbiomas_default(tmp_rasters):
    data = np.array(
        [[1, 3, 4, 5], [6, 49, 10, 11], [12, 32, 29, 50], [14, 15, 18, 19]],
        dtype=np.uint8,
    )
    transform = from_origin(0, 4, 1, 1)
    profile = dict(driver="GTiff", height=4, width=4, count=1, dtype="uint8",
                    crs="EPSG:4326", transform=transform, nodata=255)
    src_path = str(tmp_rasters / "mapbiomas.tif")
    with rasterio.open(src_path, "w", **profile) as dst:
        dst.write(data, 1)

    out_path = str(tmp_rasters / "Reclass_mapbiomas.tif")
    ro.reclassify_raster(src_path, ro.default_ruleset(), out_path, dtype="INT1U")

    with rasterio.open(out_path) as src:
        out = src.read(1)

    expected = np.array([[1, 1, 1, 1], [1, 1, 1, 1], [1, 1, 1, 1], [14, 14, 14, 14]])
    assert np.array_equal(out, expected)


def test_gerar_nome_banda_bioclim():
    name = ro.gerar_nome_banda("bioc_ACCESS-CM2_ssp245_2041-2060.tif", 3, padrao="bioclim")
    assert name == "Access_245_41_60_Bio3.tif"


def test_gerar_nome_banda_generico():
    name = ro.gerar_nome_banda("arquivo_qualquer.tif", 2, padrao="generico")
    assert name == "arquivo_qualquer_Band2.tif"


def test_gerar_nome_banda_invalid_pattern_raises():
    with pytest.raises(ValueError):
        ro.gerar_nome_banda("sem_padrao_reconhecido.tif", 1, padrao="bioclim")


def test_split_bands(tmp_rasters):
    band = np.ones((3, 3), dtype=np.float32)
    multi = np.stack([band, band * 2])
    transform = from_origin(0, 3, 1, 1)
    profile = dict(driver="GTiff", height=3, width=3, count=2, dtype="float32",
                    crs="EPSG:4326", transform=transform, nodata=None)
    src_path = str(tmp_rasters / "bioc_ACCESS-CM2_ssp245_2041-2060.tif")
    with rasterio.open(src_path, "w", **profile) as dst:
        dst.write(multi)

    out_dir = str(tmp_rasters / "bands_out")
    n, names = ro.split_bands(src_path, out_dir, padrao="bioclim")
    assert n == 2
    assert names == ["Access_245_41_60_Bio1.tif", "Access_245_41_60_Bio2.tif"]
    for name in names:
        assert os.path.exists(os.path.join(out_dir, name))


def test_rules_txt_round_trip(tmp_rasters):
    rules = ro.default_ruleset()
    path = str(tmp_rasters / "regras.txt")
    ro.save_rules_txt(rules, path, regra_nome="Teste")
    loaded = ro.parse_rules_txt(path)
    assert loaded == rules


def test_parse_rules_txt_ignores_comments_and_blank_lines(tmp_rasters):
    path = str(tmp_rasters / "regras_manual.txt")
    with open(path, "w") as fh:
        fh.write("# comentario\n\n1,1,10\n2;2;20\n3\t3\t30\n")
    loaded = ro.parse_rules_txt(path)
    assert loaded == [(1.0, 1.0, 10.0), (2.0, 2.0, 20.0), (3.0, 3.0, 30.0)]


def test_resample_and_alignment(tmp_rasters):
    model_transform = from_origin(0, 4, 0.5, 0.5)
    model_profile = dict(driver="GTiff", height=8, width=8, count=1, dtype="float32",
                          crs="EPSG:4326", transform=model_transform, nodata=-9999)
    model_path = str(tmp_rasters / "model.tif")
    with rasterio.open(model_path, "w", **model_profile) as dst:
        dst.write(np.ones((8, 8), dtype=np.float32), 1)

    rng = np.random.default_rng(0)
    cont = rng.normal(50, 15, size=(4, 4)).astype(np.float32)
    transform = from_origin(0, 4, 1, 1)
    profile = dict(driver="GTiff", height=4, width=4, count=1, dtype="float32",
                    crs="EPSG:4326", transform=transform, nodata=None)
    cont_path = str(tmp_rasters / "continuous.tif")
    with rasterio.open(cont_path, "w", **profile) as dst:
        dst.write(cont, 1)

    model_meta = ro.read_meta(model_path)
    out_path = str(tmp_rasters / "res_continuous.tif")
    ro.resample_raster(cont_path, model_meta, out_path, method="bilinear", dtype="FLT4S")

    aligned = ro.verificar_alinhamento(out_path, model_meta)
    assert aligned["Status"] == "OK"

    misaligned = ro.verificar_alinhamento(cont_path, model_meta)
    assert misaligned["Status"] == "ERRO"


def test_detectar_metodo_resample_categorical(tmp_rasters):
    data = np.array([[1, 2], [3, 4]], dtype=np.uint8)
    transform = from_origin(0, 2, 1, 1)
    profile = dict(driver="GTiff", height=2, width=2, count=1, dtype="uint8",
                    crs="EPSG:4326", transform=transform, nodata=None)
    path = str(tmp_rasters / "cat.tif")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    assert ro.detectar_metodo_resample(path) == "near"


def test_detectar_metodo_resample_continuous(tmp_rasters):
    rng = np.random.default_rng(1)
    data = rng.normal(50, 15, size=(20, 20)).astype(np.float32)
    transform = from_origin(0, 20, 1, 1)
    profile = dict(driver="GTiff", height=20, width=20, count=1, dtype="float32",
                    crs="EPSG:4326", transform=transform, nodata=None)
    path = str(tmp_rasters / "cont_big.tif")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    assert ro.detectar_metodo_resample(path) == "bilinear"


def test_clip_raster(tmp_rasters):
    data = np.arange(100).reshape(10, 10).astype(np.float32)
    transform = from_origin(0, 10, 1, 1)
    profile = dict(driver="GTiff", height=10, width=10, count=1, dtype="float32",
                    crs="EPSG:4326", transform=transform, nodata=-9999)
    raster_path = str(tmp_rasters / "big.tif")
    with rasterio.open(raster_path, "w", **profile) as dst:
        dst.write(data, 1)

    shp_path = str(tmp_rasters / "clip_area.shp")
    with pyshp.Writer(shp_path, shapeType=pyshp.POLYGON) as w:
        w.field("id", "N")
        w.poly([[[3, 3], [3, 7], [7, 7], [7, 3], [3, 3]]])
        w.record(1)
    with open(str(tmp_rasters / "clip_area.prj"), "w") as f:
        f.write(rasterio.crs.CRS.from_epsg(4326).to_wkt())

    out_path = str(tmp_rasters / "MAT_big.tif")
    ro.clip_raster(raster_path, shp_path, out_path)

    with rasterio.open(out_path) as src:
        out = src.read(1)
    assert out.shape != (10, 10)
    assert out.shape[0] < 10 and out.shape[1] < 10
