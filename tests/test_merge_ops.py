"""
tests/test_merge_ops.py
=======================

Testes do nucleo da aba "Merge de Rasters" (``merge_ops.py``).

Duas coisas sao exercitadas com cuidado especial:

1. **o agrupamento por nome.** O caso que motivou a aba tem duas camadas
   diferentes na mesma pasta, ``reclass_ndvi_stdDev_tau`` e
   ``reclass_ndvi_stdDev_tau_p``, cada uma em dois blocos. Agrupar por
   "prefixo comum" produziria UM mosaico de quatro blocos -- errado e em
   silencio. O teste exige dois conjuntos;
2. **o mosaico em si.** Um raster conhecido e cortado em quatro pedacos e
   remontado; o resultado tem que ser identico ao original, pixel a pixel,
   inclusive com faixa menor que o raster (que forca varias faixas) e com
   blocos fora de ordem.
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import merge_ops as mo
from utils import CancelledError

# ---------------------------------------------------------------------------
# Agrupamento por nome
# ---------------------------------------------------------------------------

EXEMPLO_DO_USUARIO = [
    "reclass_ndvi_stdDev_tau_p-0000046592-0000000000.tif",
    "reclass_ndvi_stdDev_tau_p-0000000000-0000000000.tif",
    "reclass_ndvi_stdDev_tau-0000046592-0000000000.tif",
    "reclass_ndvi_stdDev_tau-0000000000-0000000000.tif",
]


def test_base_sem_o_sufixo_de_bloco():
    base, motivo = mo.base_do_arquivo("reclass_ndvi_stdDev_tau-0000046592-0000000000.tif")
    assert base == "reclass_ndvi_stdDev_tau"
    assert motivo == mo.MOTIVO_GEE


def test_o_sufixo_p_faz_parte_da_base():
    base, _ = mo.base_do_arquivo("reclass_ndvi_stdDev_tau_p-0000046592-0000000000.tif")
    assert base == "reclass_ndvi_stdDev_tau_p"


def test_o_exemplo_do_usuario_vira_dois_conjuntos():
    grupos = mo.agrupar_para_merge(EXEMPLO_DO_USUARIO)
    assert [g.base for g in grupos] == [
        "reclass_ndvi_stdDev_tau", "reclass_ndvi_stdDev_tau_p"]
    assert all(g.quantidade == 2 for g in grupos)


def test_arquivo_avulso_nao_entra():
    """O pedido e carregar SO o que precisa de merge."""
    arquivos = EXEMPLO_DO_USUARIO + ["AC_CLIP_LULC_2025.tif", "bio_1.tif"]
    grupos = mo.agrupar_para_merge(arquivos)
    todos = {a for g in grupos for a in g.arquivos}
    assert "AC_CLIP_LULC_2025.tif" not in todos
    assert "bio_1.tif" not in todos


def test_bloco_unico_nao_forma_conjunto():
    grupos = mo.agrupar_para_merge(["so_um-0000000000-0000000000.tif"])
    assert grupos == []


def test_sequencia_numerica_simples_tambem_agrupa():
    grupos = mo.agrupar_para_merge(["mosaico-1.tif", "mosaico-2.tif", "mosaico-3.tif"])
    assert len(grupos) == 1
    assert grupos[0].base == "mosaico"
    assert grupos[0].motivo == mo.MOTIVO_SEQUENCIA


def test_o_caminho_completo_nao_atrapalha():
    grupos = mo.agrupar_para_merge([
        r"D:\dados\reclass_x-0000000000-0000000000.tif",
        r"D:\dados\reclass_x-0000046592-0000000000.tif",
    ])
    assert len(grupos) == 1 and grupos[0].base == "reclass_x"


def test_nome_de_saida_com_o_prefixo():
    grupo = mo.agrupar_para_merge(EXEMPLO_DO_USUARIO)[0]
    assert grupo.nome_de_saida() == "Merge_reclass_ndvi_stdDev_tau.tif"
    assert grupo.nome_de_saida("Mosaico_") == "Mosaico_reclass_ndvi_stdDev_tau.tif"
    assert grupo.nome_de_saida("") == "reclass_ndvi_stdDev_tau.tif"


def test_grupos_da_pasta(tmp_path):
    for nome in EXEMPLO_DO_USUARIO + ["avulso.tif"]:
        (tmp_path / nome).write_bytes(b"")
    grupos = mo.grupos_da_pasta(str(tmp_path))
    assert len(grupos) == 2


# ---------------------------------------------------------------------------
# Apoio para os testes de mosaico
# ---------------------------------------------------------------------------

RES = 0.01
X0, Y0 = -47.0, -15.0


def _gravar(caminho, dados, col_off=0, row_off=0, nodata=-9999, crs="EPSG:4326",
            res=RES):
    dados = np.asarray(dados)
    if dados.ndim == 2:
        dados = dados[np.newaxis, :, :]
    perfil = {
        "driver": "GTiff", "height": dados.shape[1], "width": dados.shape[2],
        "count": dados.shape[0], "dtype": dados.dtype.name, "crs": crs,
        "transform": from_origin(X0 + col_off * res, Y0 - row_off * res, res, res),
    }
    if nodata is not None:
        perfil["nodata"] = nodata
    with rasterio.open(caminho, "w", **perfil) as dst:
        dst.write(dados)
    return str(caminho)


def _ler(caminho):
    with rasterio.open(caminho) as src:
        return src.read(1), src


@pytest.fixture
def quadrantes(tmp_path):
    """Um raster 20x20 conhecido, cortado em quatro quadrantes 10x10."""
    inteiro = np.arange(400, dtype="int16").reshape(20, 20)
    caminhos = [
        _gravar(tmp_path / "cena-0000000000-0000000000.tif", inteiro[:10, :10], 0, 0),
        _gravar(tmp_path / "cena-0000000000-0000000010.tif", inteiro[:10, 10:], 10, 0),
        _gravar(tmp_path / "cena-0000000010-0000000000.tif", inteiro[10:, :10], 0, 10),
        _gravar(tmp_path / "cena-0000000010-0000000010.tif", inteiro[10:, 10:], 10, 10),
    ]
    return inteiro, caminhos


# ---------------------------------------------------------------------------
# Mosaico
# ---------------------------------------------------------------------------


def test_mosaico_reproduz_o_raster_original(tmp_path, quadrantes):
    inteiro, caminhos = quadrantes
    saida = tmp_path / "Merge_cena.tif"
    resultado = mo.executar_merge(mo.ParametrosMerge(tuple(caminhos), str(saida)))

    dados, src = _ler(str(saida))
    assert (resultado.largura, resultado.altura) == (20, 20)
    assert resultado.bandas == 1 and resultado.dtype == "int16"
    np.testing.assert_array_equal(dados, inteiro)
    assert src.crs.to_string() == "EPSG:4326"
    assert src.transform.c == pytest.approx(X0)
    assert src.transform.f == pytest.approx(Y0)
    assert resultado.pct_com_dado == pytest.approx(100.0)


def test_ordem_dos_blocos_nao_importa(tmp_path, quadrantes):
    inteiro, caminhos = quadrantes
    saida = tmp_path / "fora_de_ordem.tif"
    mo.executar_merge(mo.ParametrosMerge(tuple(reversed(caminhos)), str(saida)))
    dados, _ = _ler(str(saida))
    np.testing.assert_array_equal(dados, inteiro)


def test_varias_faixas_dao_o_mesmo_resultado(tmp_path, quadrantes):
    """Faixa minuscula forca o caminho de multiplas passagens -- o modo em que
    o mosaico de verdade (varios GB) sempre roda."""
    inteiro, caminhos = quadrantes
    saida = tmp_path / "em_faixas.tif"
    mo.executar_merge(mo.ParametrosMerge(tuple(caminhos), str(saida),
                                         memoria_por_faixa_mb=0.0001))
    dados, _ = _ler(str(saida))
    np.testing.assert_array_equal(dados, inteiro)


def test_lacuna_entre_blocos_fica_como_nodata(tmp_path):
    a = _gravar(tmp_path / "g-0000000000-0000000000.tif",
                np.ones((5, 5), dtype="int16"), 0, 0)
    b = _gravar(tmp_path / "g-0000000000-0000000010.tif",
                np.full((5, 5), 2, dtype="int16"), 10, 0)
    saida = tmp_path / "Merge_g.tif"
    resultado = mo.executar_merge(mo.ParametrosMerge((a, b), str(saida)))

    dados, src = _ler(str(saida))
    assert dados.shape == (5, 15)
    assert src.nodata == -9999
    assert (dados[:, 5:10] == -9999).all(), "a lacuna deveria ser NoData"
    assert (dados[:, :5] == 1).all() and (dados[:, 10:] == 2).all()
    assert resultado.pct_com_dado == pytest.approx(100 * 50 / 75)


def test_na_sobreposicao_vale_o_primeiro(tmp_path):
    a = _gravar(tmp_path / "s-0000000000-0000000000.tif",
                np.ones((5, 10), dtype="int16"), 0, 0)
    b = _gravar(tmp_path / "s-0000000000-0000000005.tif",
                np.full((5, 10), 2, dtype="int16"), 5, 0)
    saida = tmp_path / "Merge_s.tif"
    resultado = mo.executar_merge(mo.ParametrosMerge((a, b), str(saida)))

    dados, _ = _ler(str(saida))
    assert dados.shape == (5, 15)
    assert (dados[:, :10] == 1).all(), "o primeiro bloco deveria prevalecer"
    assert (dados[:, 10:] == 2).all()
    assert any("sobrep" in aviso for aviso in resultado.avisos)


def test_nodata_do_bloco_nao_apaga_o_vizinho(tmp_path):
    """Onde um bloco tem NoData e o outro tem dado, o dado vence."""
    esquerda = np.ones((5, 10), dtype="int16")
    esquerda[:, 5:] = -9999
    direita = np.full((5, 10), 7, dtype="int16")
    a = _gravar(tmp_path / "n-0000000000-0000000000.tif", esquerda, 0, 0)
    b = _gravar(tmp_path / "n-0000000000-0000000005.tif", direita, 5, 0)
    saida = tmp_path / "Merge_n.tif"
    mo.executar_merge(mo.ParametrosMerge((a, b), str(saida)))

    dados, _ = _ler(str(saida))
    assert (dados[:, :5] == 1).all()
    assert (dados[:, 5:] == 7).all()


def test_varias_bandas(tmp_path):
    dados = np.stack([np.arange(50, dtype="float32").reshape(5, 10),
                      np.arange(50, 100, dtype="float32").reshape(5, 10)])
    a = _gravar(tmp_path / "b-0000000000-0000000000.tif", dados[:, :, :5], 0, 0,
                nodata=None)
    b = _gravar(tmp_path / "b-0000000000-0000000005.tif", dados[:, :, 5:], 5, 0,
                nodata=None)
    saida = tmp_path / "Merge_b.tif"
    resultado = mo.executar_merge(mo.ParametrosMerge((a, b), str(saida)))

    assert resultado.bandas == 2
    with rasterio.open(str(saida)) as src:
        np.testing.assert_array_equal(src.read(), dados)


def test_float_sem_nodata_declarado_usa_nan(tmp_path):
    a = _gravar(tmp_path / "f-0000000000-0000000000.tif",
                np.ones((4, 4), dtype="float32"), 0, 0, nodata=None)
    b = _gravar(tmp_path / "f-0000000000-0000000008.tif",
                np.ones((4, 4), dtype="float32"), 8, 0, nodata=None)
    saida = tmp_path / "Merge_f.tif"
    mo.executar_merge(mo.ParametrosMerge((a, b), str(saida)))
    dados, src = _ler(str(saida))
    assert np.isnan(src.nodata)
    assert np.isnan(dados[:, 4:8]).all()


def test_inteiro_sem_nodata_nao_inventa_nodata(tmp_path):
    """Declarar nodata=0 num raster de classes apagaria a classe 0."""
    a = _gravar(tmp_path / "i-0000000000-0000000000.tif",
                np.zeros((4, 4), dtype="uint8"), 0, 0, nodata=None)
    b = _gravar(tmp_path / "i-0000000000-0000000004.tif",
                np.zeros((4, 4), dtype="uint8"), 4, 0, nodata=None)
    saida = tmp_path / "Merge_i.tif"
    resultado = mo.executar_merge(mo.ParametrosMerge((a, b), str(saida)))
    _dados, src = _ler(str(saida))
    assert src.nodata is None
    assert any("NoData" in aviso for aviso in resultado.avisos)


def test_saida_e_comprimida_e_em_tiles(tmp_path, quadrantes):
    _inteiro, caminhos = quadrantes
    saida = tmp_path / "Merge_cena.tif"
    mo.executar_merge(mo.ParametrosMerge(tuple(caminhos), str(saida)))
    with rasterio.open(str(saida)) as src:
        assert src.profile["compress"].lower() == "lzw"
        assert src.profile["tiled"] is True


# ---------------------------------------------------------------------------
# Recusas: o que NAO pode virar mosaico em silencio
# ---------------------------------------------------------------------------


def test_recusa_crs_diferente(tmp_path):
    a = _gravar(tmp_path / "c-0000000000-0000000000.tif",
                np.ones((4, 4), dtype="int16"), 0, 0)
    b = _gravar(tmp_path / "c-0000000000-0000000004.tif",
                np.ones((4, 4), dtype="int16"), 4, 0, crs="EPSG:31983")
    with pytest.raises(mo.MergeIncompativel, match="CRS"):
        mo.executar_merge(mo.ParametrosMerge((a, b), str(tmp_path / "x.tif")))


def test_recusa_resolucao_diferente(tmp_path):
    a = _gravar(tmp_path / "r-0000000000-0000000000.tif",
                np.ones((4, 4), dtype="int16"), 0, 0)
    b = _gravar(tmp_path / "r-0000000000-0000000004.tif",
                np.ones((4, 4), dtype="int16"), 0, 0, res=0.02)
    with pytest.raises(mo.MergeIncompativel, match="Resolucao"):
        mo.executar_merge(mo.ParametrosMerge((a, b), str(tmp_path / "x.tif")))


def test_recusa_tipo_de_dado_diferente(tmp_path):
    a = _gravar(tmp_path / "t-0000000000-0000000000.tif",
                np.ones((4, 4), dtype="int16"), 0, 0)
    b = _gravar(tmp_path / "t-0000000000-0000000004.tif",
                np.ones((4, 4), dtype="float32"), 4, 0)
    with pytest.raises(mo.MergeIncompativel, match="Tipo de dado"):
        mo.executar_merge(mo.ParametrosMerge((a, b), str(tmp_path / "x.tif")))


def test_recusa_numero_de_bandas_diferente(tmp_path):
    a = _gravar(tmp_path / "nb-0000000000-0000000000.tif",
                np.ones((1, 4, 4), dtype="int16"), 0, 0)
    b = _gravar(tmp_path / "nb-0000000000-0000000004.tif",
                np.ones((2, 4, 4), dtype="int16"), 4, 0)
    with pytest.raises(mo.MergeIncompativel, match="bandas"):
        mo.executar_merge(mo.ParametrosMerge((a, b), str(tmp_path / "x.tif")))


def test_recusa_grade_desalinhada(tmp_path):
    a = _gravar(tmp_path / "d-0000000000-0000000000.tif",
                np.ones((4, 4), dtype="int16"), 0, 0)
    deslocado = tmp_path / "d-0000000000-0000000004.tif"
    with rasterio.open(deslocado, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="int16", crs="EPSG:4326", nodata=-9999,
                       transform=from_origin(X0 + 4 * RES + RES / 3, Y0, RES, RES)) as dst:
        dst.write(np.ones((4, 4), dtype="int16"), 1)
    with pytest.raises(mo.MergeIncompativel, match="grade"):
        mo.executar_merge(mo.ParametrosMerge((a, str(deslocado)), str(tmp_path / "x.tif")))


def test_recusa_um_arquivo_so(tmp_path):
    a = _gravar(tmp_path / "u-0000000000-0000000000.tif",
                np.ones((4, 4), dtype="int16"), 0, 0)
    with pytest.raises(mo.MergeIncompativel, match="dois"):
        mo.executar_merge(mo.ParametrosMerge((a,), str(tmp_path / "x.tif")))


def test_nao_sobrescreve_quando_desligado(tmp_path, quadrantes):
    _inteiro, caminhos = quadrantes
    saida = tmp_path / "ja_existe.tif"
    saida.write_bytes(b"conteudo anterior")
    with pytest.raises(FileExistsError):
        mo.executar_merge(mo.ParametrosMerge(tuple(caminhos), str(saida),
                                             sobrescrever=False))
    assert saida.read_bytes() == b"conteudo anterior"


# ---------------------------------------------------------------------------
# Progresso e cancelamento
# ---------------------------------------------------------------------------


def test_progresso_vai_ate_cem(tmp_path, quadrantes):
    _inteiro, caminhos = quadrantes
    valores = []
    mo.executar_merge(
        mo.ParametrosMerge(tuple(caminhos), str(tmp_path / "p.tif"),
                           memoria_por_faixa_mb=0.0001),
        progresso=lambda valor, _etapa: valores.append(valor),
    )
    assert valores, "nenhum progresso reportado"
    assert valores == sorted(valores)
    assert valores[-1] == pytest.approx(100.0)


def test_cancelamento_remove_o_arquivo_parcial(tmp_path, quadrantes):
    """Um GeoTIFF truncado deixado para tras seria usado como se estivesse bom."""
    _inteiro, caminhos = quadrantes
    saida = tmp_path / "cancelado.tif"
    chamadas = {"n": 0}

    def cancelado():
        chamadas["n"] += 1
        return chamadas["n"] > 1

    with pytest.raises(CancelledError):
        mo.executar_merge(
            mo.ParametrosMerge(tuple(caminhos), str(saida), memoria_por_faixa_mb=0.0001),
            cancelado=cancelado,
        )
    assert not saida.exists()


def test_altura_da_faixa_respeita_a_memoria(tmp_path, quadrantes):
    _inteiro, caminhos = quadrantes
    plano = mo.planejar_merge(caminhos)
    assert mo.altura_da_faixa(plano, 64.0) == plano.altura        # cabe inteiro
    assert mo.altura_da_faixa(plano, 0.0001) < plano.altura       # precisa fatiar
    assert mo.altura_da_faixa(plano, 1e-9) == 1                   # nunca zero


# ---------------------------------------------------------------------------
# Filtro geometrico: sequencia numerica que nao e mosaico
# ---------------------------------------------------------------------------


def test_bioclim_nao_e_confundido_com_mosaico(tmp_path):
    """bio_1..bio_3 cobrem a MESMA area: sao variaveis, nao blocos."""
    for indice in (1, 2, 3):
        _gravar(tmp_path / f"wc_bio_{indice}.tif", np.ones((4, 4), dtype="int16"), 0, 0)
    grupos = mo.agrupar_para_merge([str(p) for p in sorted(tmp_path.glob("*.tif"))])
    assert len(grupos) == 1 and grupos[0].motivo == mo.MOTIVO_SEQUENCIA

    mantidos, descartados = mo.filtrar_mosaicos(grupos)
    assert mantidos == []
    assert len(descartados) == 1
    assert "mesma area" in descartados[0][1]


def test_sequencia_em_posicoes_diferentes_continua_sendo_mosaico(tmp_path):
    _gravar(tmp_path / "cena-1.tif", np.ones((4, 4), dtype="int16"), 0, 0)
    _gravar(tmp_path / "cena-2.tif", np.ones((4, 4), dtype="int16"), 4, 0)
    grupos = mo.agrupar_para_merge([str(p) for p in sorted(tmp_path.glob("*.tif"))])
    mantidos, descartados = mo.filtrar_mosaicos(grupos)
    assert len(mantidos) == 1 and descartados == []


def test_blocos_do_gee_nunca_sao_descartados_pelo_filtro(tmp_path, quadrantes):
    _inteiro, caminhos = quadrantes
    grupos = mo.agrupar_para_merge(caminhos)
    mantidos, descartados = mo.filtrar_mosaicos(grupos)
    assert len(mantidos) == 1 and descartados == []
