"""
tests/test_tab_amostragem.py
============================

Testes da aba de Amostragem (nucleo SDM) do GeoRaster Tools V3.

Cobrem o que a aba promete e o que o SDM_APP exige:

* o CSV de saida abre com ``read.csv`` do R: virgula, ponto decimal, sem BOM,
  colunas chamadas exatamente ``x`` e ``y``;
* presenca e background em arquivos separados;
* o filtro de preditores elimina os dois defeitos medidos na V5 -- pontos em
  NoData do preditor e pontos repetindo celula do preditor;
* a janela monta as seis abas e a barra de progresso fica visivel.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amostragem_fogo.config import (Faixa, ParametrosAmostragem,
                                     ParametrosAnalise, SelecaoDeSaidas)
from amostragem_fogo.pipeline import executar_completo
from amostragem_fogo.sdm import FiltroSDM


# ---------------------------------------------------------------------------
# Rasters sinteticos
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cenario(tmp_path_factory):
    """Raster de fogo fino + preditor grosso com NoData, como WorldClim."""
    base = tmp_path_factory.mktemp("sdm")
    rng = np.random.default_rng(11)

    largura = altura = 900
    fogo = rng.integers(0, 8, size=(altura, largura)).astype("int16")
    fogo[rng.random((altura, largura)) < 0.25] = 0
    caminho_fogo = base / "frequencia_fogo.tif"
    with rasterio.open(
        caminho_fogo, "w", driver="GTiff", height=altura, width=largura, count=1,
        dtype="int16", crs="EPSG:4326", nodata=-9999,
        transform=from_origin(-47.0, -15.0, 0.00027, 0.00027),
        tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(fogo, 1)

    lado = 34
    pred = rng.normal(24.0, 3.0, size=(lado, lado)).astype("float32")
    pred[rng.random((lado, lado)) < 0.18] = -3.4e38
    caminho_pred = base / "bio01.tif"
    with rasterio.open(
        caminho_pred, "w", driver="GTiff", height=lado, width=lado, count=1,
        dtype="float32", crs="EPSG:4326", nodata=-3.4e38,
        transform=from_origin(-47.0, -15.0, 0.0083, 0.0083),
    ) as dst:
        dst.write(pred, 1)

    return {"base": base, "fogo": caminho_fogo, "preditor": caminho_pred}


def _parametros(cenario, saida, **extras):
    base = dict(
        analise=ParametrosAnalise(
            raster=cenario["fogo"], banda=1, anos_observados=41,
            valores_excluidos=frozenset({-9999.0}),
        ),
        saida=saida,
        presenca=Faixa(4, 7),
        ausencia=Faixa(0, 3),
        n_presenca=200,
        n_ausencia=200,
        distancia_minima_m=200.0,
        semente=42,
    )
    base.update(extras)
    return ParametrosAmostragem(**base)


def _ler_xy(caminho: Path):
    with open(caminho, encoding="utf-8") as fh:
        linhas = list(csv.DictReader(fh))
    return [(float(r["x"]), float(r["y"])) for r in linhas], linhas


def _diagnostico(pontos, preditor: Path):
    with rasterio.open(preditor) as src:
        valores = np.array([v[0] for v in src.sample(pontos)], dtype="float64")
        ruins = (~np.isfinite(valores)) | (valores < -1e30)
        celulas = [src.index(x, y) for x, y in pontos]
    return int(ruins.sum()), len(pontos) - len(set(celulas))


# ---------------------------------------------------------------------------
# Contrato de leitura do SDM_APP
# ---------------------------------------------------------------------------


def test_csv_no_formato_que_o_sdm_le(cenario, tmp_path):
    """read.csv(pres)[, c("x","y")] tem de funcionar sem nenhuma opcao extra."""
    saida = tmp_path / "contrato"
    executar_completo(_parametros(cenario, saida))

    bruto = (saida / "amostras_presencas.csv").read_bytes()
    assert not bruto.startswith(b"\xef\xbb\xbf"), (
        "o arquivo tem BOM; read.csv do R leria a primeira coluna como 'ï..x'"
    )

    texto = bruto.decode("utf-8")
    linhas = texto.strip().splitlines()
    assert linhas[0] == "x,y,Value"
    assert ";" not in texto
    assert all(len(linha.split(",")) == 3 for linha in linhas[1:])

    pontos, registros = _ler_xy(saida / "amostras_presencas.csv")
    assert set(registros[0].keys()) == {"x", "y", "Value"}
    assert all(r["Value"] == "1" for r in registros)
    assert all(-47.3 < x < -46.7 and -15.3 < y < -14.9 for x, y in pontos)


def test_presenca_e_background_em_arquivos_separados(cenario, tmp_path):
    saida = tmp_path / "separados"
    executar_completo(_parametros(cenario, saida))
    presencas, _ = _ler_xy(saida / "amostras_presencas.csv")
    ausencias, registros = _ler_xy(saida / "amostras_ausencias.csv")
    assert len(presencas) == 200 and len(ausencias) == 200
    assert all(r["Value"] == "0" for r in registros)
    assert not (set(presencas) & set(ausencias)), "um ponto nao pode ser presenca e background"


# ---------------------------------------------------------------------------
# O defeito que a V2 corrige
# ---------------------------------------------------------------------------


def test_sem_filtro_os_pontos_se_perdem_no_preditor(cenario, tmp_path):
    """Documenta o comportamento da V5: parte dos pontos e inutil para o SDM."""
    saida = tmp_path / "sem_filtro"
    executar_completo(_parametros(cenario, saida))
    pontos, _ = _ler_xy(saida / "amostras_presencas.csv")
    em_nodata, repetidos = _diagnostico(pontos, cenario["preditor"])
    assert em_nodata > 0, "o cenario precisa ter NoData para o teste fazer sentido"
    assert repetidos > 0, "o cenario precisa ter celulas repetidas"


def test_filtro_entrega_a_quantidade_pedida_toda_utilizavel(cenario, tmp_path):
    saida = tmp_path / "com_filtro"
    resultado = executar_completo(_parametros(
        cenario, saida, filtro_sdm=FiltroSDM(preditores=(cenario["preditor"],))))

    for nome in ("amostras_presencas.csv", "amostras_ausencias.csv"):
        pontos, _ = _ler_xy(saida / nome)
        em_nodata, repetidos = _diagnostico(pontos, cenario["preditor"])
        assert len(pontos) == 200, f"{nome}: quantidade pedida nao foi atingida"
        assert em_nodata == 0, f"{nome}: {em_nodata} pontos em NoData do preditor"
        assert repetidos == 0, f"{nome}: {repetidos} pontos repetindo celula"

    estatisticas = resultado.metadados["estatisticas_filtro"]
    assert estatisticas["Presenca"].nodata_no_preditor > 0
    assert estatisticas["Presenca"].celula_repetida > 0
    assert estatisticas["Presenca"].aceitos == 200


def test_filtro_pode_ser_afrouxado_por_opcao(cenario, tmp_path):
    """Cada uma das duas regras pode ser desligada de forma independente."""
    saida = tmp_path / "so_nodata"
    executar_completo(_parametros(cenario, saida, filtro_sdm=FiltroSDM(
        preditores=(cenario["preditor"],), uma_amostra_por_celula=False)))
    pontos, _ = _ler_xy(saida / "amostras_presencas.csv")
    em_nodata, repetidos = _diagnostico(pontos, cenario["preditor"])
    assert em_nodata == 0
    assert repetidos > 0, "sem a regra de celula, a repeticao deve voltar"

    saida = tmp_path / "so_celula"
    executar_completo(_parametros(cenario, saida, filtro_sdm=FiltroSDM(
        preditores=(cenario["preditor"],), exigir_preditor_valido=False)))
    pontos, _ = _ler_xy(saida / "amostras_presencas.csv")
    em_nodata, repetidos = _diagnostico(pontos, cenario["preditor"])
    assert repetidos == 0
    assert em_nodata > 0, "sem a regra de NoData, os pontos invalidos devem voltar"


def test_filtro_desligado_quando_nao_ha_preditor(cenario, tmp_path):
    filtro = FiltroSDM()
    assert not filtro.ativo
    saida = tmp_path / "sem_preditor"
    executar_completo(_parametros(cenario, saida, filtro_sdm=filtro))
    assert (saida / "amostras_presencas.csv").is_file()


def test_preditor_inexistente_e_recusado(tmp_path):
    with pytest.raises(ValueError, match="nao encontrado"):
        FiltroSDM(preditores=(tmp_path / "nao_existe.tif",))


def test_relatorio_registra_o_preparo(cenario, tmp_path):
    saida = tmp_path / "relatorio"
    executar_completo(_parametros(
        cenario, saida, filtro_sdm=FiltroSDM(preditores=(cenario["preditor"],))))
    texto = (saida / "relatorio_amostragem.txt").read_text(encoding="utf-8")
    assert "PREPARO PARA SDM" in texto
    assert "bio01.tif" in texto
    assert "Recusados por estarem em NoData do raster de referencia" in texto


def test_aviso_quando_o_preparo_nao_foi_aplicado(cenario, tmp_path):
    saida = tmp_path / "relatorio_sem"
    executar_completo(_parametros(cenario, saida))
    texto = (saida / "relatorio_amostragem.txt").read_text(encoding="utf-8")
    assert "Nenhum filtro de preditores foi aplicado" in texto


def test_so_presencas_grava_um_arquivo(cenario, tmp_path):
    saida = tmp_path / "so_presencas"
    executar_completo(_parametros(
        cenario, saida, saidas=SelecaoDeSaidas.somente_presencas()))
    assert sorted(p.name for p in saida.iterdir()) == ["amostras_presencas.csv"]


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


@pytest.fixture
def janela(tk_root, app_georaster):
    """A janela unica da sessao (ver tests/conftest.py)."""
    return tk_root, app_georaster


def test_janela_tem_as_sete_abas(janela):
    _root, app = janela
    abas = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
    assert abas == [
        "Recortar Rasters", "Reclassificar", "Separar Bandas", "Reamostrar",
        "Merge de Rasters", "Verificar Alinhamento", "Amostragem",
    ]
    assert hasattr(app, "aba_amostragem"), "a aba de amostragem nao montou"
    assert hasattr(app, "aba_merge"), "a aba de merge nao montou"


def test_o_rotulo_da_amostragem_perdeu_o_de_fogo(janela):
    """Pedido da V3: a aba se chama so "Amostragem"."""
    _root, app = janela
    abas = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
    assert "Amostragem" in abas
    assert not any("Fogo" in aba or "fogo" in aba for aba in abas)


def test_barra_de_progresso_visivel_em_varios_tamanhos(janela):
    root, app = janela
    root.deiconify()
    aba = app.aba_amostragem
    try:
        for largura, altura in ((1400, 900), (1150, 720), (1366, 700)):
            root.geometry(f"{largura}x{altura}")
            app.notebook.select(app.tab_sampling)
            root.update_idletasks()
            root.update()
            barra = aba.barra
            base = (barra.winfo_rooty() - root.winfo_rooty()) + barra.winfo_height()
            assert barra.winfo_ismapped(), f"barra nao mapeada em {largura}x{altura}"
            assert base <= root.winfo_height(), f"barra fora da janela em {largura}x{altura}"
    finally:
        root.withdraw()


def test_validacao_dos_campos_antes_da_thread(janela, cenario):
    _root, app = janela
    aba = app.aba_amostragem
    aba.v_raster.set("")
    with pytest.raises(ValueError, match="raster"):
        aba._ler_analise()
    aba.v_raster.set(str(cenario["fogo"]))
    aba.v_excluir.set("-9999")
    assert aba._ler_analise().banda == 1
    with pytest.raises(ValueError, match="Analise"):
        aba._ler_amostragem()


def test_botoes_de_atalho_das_saidas(janela):
    _root, app = janela
    aba = app.aba_amostragem
    assert len(aba.v_saidas) == 11
    aba._marcar(False)
    assert not any(v.get() for v in aba.v_saidas.values())
    aba._so_presencas()
    assert sorted(n for n, v in aba.v_saidas.items() if v.get()) == ["formato_xyv", "presencas"]
    aba._so_ausencias()
    assert sorted(n for n, v in aba.v_saidas.items() if v.get()) == ["ausencias", "formato_xyv"]
    aba._marcar(True)
    assert all(v.get() for v in aba.v_saidas.values())


def test_referencia_unica_e_nao_a_pasta(janela, cenario):
    """A secao 5 recebe UM raster; exigir a pasta inteira zerava a amostra."""
    _root, app = janela
    aba = app.aba_amostragem
    aba.v_pred_dir.set(str(cenario["preditor"]))
    aba._definir_referencia()
    assert [p.name for p in aba._preditores] == ["bio01.tif"]
    assert "bio01.tif" in aba.v_pred_info.get()
    filtro = aba._montar_filtro()
    assert filtro.ativo and filtro.referencia.name == "bio01.tif"
    aba._limpar_preditores()
    assert aba._preditores == ()
    assert not aba._montar_filtro().ativo
    assert "Nenhuma referencia" in aba.v_pred_info.get()


def test_sondagem_detecta_referencia_de_outra_area(cenario, tmp_path):
    """Raster de outra area tem de ser apontado antes de rodar tudo."""
    from rasterio.transform import from_origin

    from amostragem_fogo.geo import abrir_sistema
    from amostragem_fogo.sdm import sondar_referencia

    fora = tmp_path / "outra_area.tif"
    with rasterio.open(
        fora, "w", driver="GTiff", height=30, width=30, count=1, dtype="float32",
        crs="EPSG:4326", nodata=-9999.0, transform=from_origin(-40.0, -5.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.ones((30, 30), dtype="float32"), 1)

    with rasterio.open(cenario["fogo"]) as src:
        sistema = abrir_sistema(src)
        x, y = sistema.xy(np.arange(0, 500, 5), np.arange(0, 500, 5))

    ruim = sondar_referencia(FiltroSDM(preditores=(fora,)), x, y, sistema.crs)
    assert ruim.dentro == 0
    avisos = ruim.diagnostico(1000)
    assert avisos and "NENHUM candidato" in avisos[0]

    boa = sondar_referencia(FiltroSDM(preditores=(cenario["preditor"],)), x, y, sistema.crs)
    assert boa.dentro == boa.avaliados
    assert boa.crs_iguais
    assert boa.diagnostico(1000) == []


def test_zero_amostras_explica_o_motivo(cenario, tmp_path):
    """Com referencia de outra area o resultado e 0, mas com diagnostico."""
    from rasterio.transform import from_origin

    fora = tmp_path / "fora.tif"
    with rasterio.open(
        fora, "w", driver="GTiff", height=30, width=30, count=1, dtype="float32",
        crs="EPSG:4326", nodata=-9999.0, transform=from_origin(-40.0, -5.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.ones((30, 30), dtype="float32"), 1)

    # Referencia inviavel para agora, com explicacao, em vez de varrer todos
    # os candidatos por minutos para terminar em zero.
    with pytest.raises(ValueError) as erro:
        executar_completo(_parametros(
            cenario, tmp_path / "vazio", filtro_sdm=FiltroSDM(preditores=(fora,))))
    mensagem = str(erro.value)
    assert "nao aproveita nenhum candidato" in mensagem
    assert "Conferir referencia" in mensagem
