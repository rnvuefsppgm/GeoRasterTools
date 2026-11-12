"""
tests/test_rodape_e_dtypes.py
=============================

Duas exigencias da revisao da V3:

1. **barra de progresso em TODAS as abas** -- nao basta existir: ela tem que
   estar mapeada e dentro da janela nos tamanhos em que a aplicacao roda, e
   tem que de fato andar durante o processamento (o worker precisa emitir
   ``progress``);
2. **o tipo de dado com bits e nome numpy** em todo lugar onde se escolhe o
   tipo de saida: "INT2U - 16 bits - uint16". O que vai para o
   processamento continua sendo so o codigo ("INT2U") -- se o rotulo
   vazasse para o ``raster_ops``, todo raster sairia com erro de dtype.
"""

from __future__ import annotations

import queue

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import config

TAMANHOS = ((1400, 900), (1150, 720), (1366, 700))

# (atributo do rodape, aba do notebook) de cada uma das sete abas
RODAPES = [
    ("clip_rodape", "tab_clip"),
    ("rcl_rodape", "tab_reclassify"),
    ("bands_rodape", "tab_bands"),
    ("res_rodape", "tab_resample"),
    ("ver_rodape", "tab_verify"),
]


# ---------------------------------------------------------------------------
# Rotulos de dtype
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("codigo, bits, numpy_nome", [
    ("INT1U", 8, "uint8"),
    ("BYTE", 8, "uint8"),
    ("INT2U", 16, "uint16"),
    ("INT2S", 16, "int16"),
    ("INT4S", 32, "int32"),
    ("INT4U", 32, "uint32"),
    ("FLT4S", 32, "float32"),
    ("FLT8S", 64, "float64"),
])
def test_rotulo_traz_codigo_bits_e_tipo_numpy(codigo, bits, numpy_nome):
    assert config.rotulo_de_dtype(codigo) == f"{codigo} - {bits} bits - {numpy_nome}"


def test_todos_os_dtypes_das_abas_tem_bits_declarados():
    for codigo in set(config.RECLASSIFY_DTYPES) | set(config.RESAMPLE_DTYPES):
        assert codigo in config.DTYPE_BITS, codigo
        assert codigo in config.DTYPE_MAP, codigo
        # bits declarados batem com o tipo numpy correspondente
        assert config.DTYPE_BITS[codigo] == np.dtype(config.DTYPE_MAP[codigo]).itemsize * 8


def test_volta_do_rotulo_para_o_codigo():
    for codigo in config.RESAMPLE_DTYPES:
        assert config.dtype_do_rotulo(config.rotulo_de_dtype(codigo)) == codigo
    # codigo digitado na mao continua valendo
    assert config.dtype_do_rotulo("flt4s") == "FLT4S"
    assert config.dtype_do_rotulo("  INT2U  ") == "INT2U"
    assert config.dtype_do_rotulo("") == ""


def test_rotulos_preservam_a_ordem_da_lista():
    rotulos = config.rotulos_de_dtype(config.RESAMPLE_DTYPES)
    assert [config.dtype_do_rotulo(r) for r in rotulos] == list(config.RESAMPLE_DTYPES)


# ---------------------------------------------------------------------------
# A barra em todas as abas
# ---------------------------------------------------------------------------


def test_todas_as_abas_tem_rodape_com_barra(app_georaster):
    for atributo, _aba in RODAPES:
        rodape = getattr(app_georaster, atributo, None)
        assert rodape is not None, f"{atributo} nao existe"
        assert rodape.barra is not None
    # as duas abas que ja tinham barra propria
    assert app_georaster.aba_merge.barra is not None
    assert app_georaster.aba_amostragem.barra is not None


def test_barra_visivel_e_dentro_da_janela_em_todas_as_abas(app_georaster, tk_root):
    tk_root.deiconify()
    try:
        for atributo, nome_aba in RODAPES:
            rodape = getattr(app_georaster, atributo)
            for largura, altura in TAMANHOS:
                tk_root.geometry(f"{largura}x{altura}")
                app_georaster.notebook.select(getattr(app_georaster, nome_aba))
                tk_root.update_idletasks()
                tk_root.update()
                barra = rodape.barra
                base = (barra.winfo_rooty() - tk_root.winfo_rooty()) + barra.winfo_height()
                assert barra.winfo_ismapped(), f"{atributo} nao mapeada em {largura}x{altura}"
                assert base <= tk_root.winfo_height(), (
                    f"{atributo} fora da janela em {largura}x{altura}")
    finally:
        tk_root.withdraw()


def test_rodape_formata_porcentagem_e_etapa(app_georaster):
    rodape = app_georaster.clip_rodape
    rodape.iniciar("comecando")
    assert rodape.v_pct.get() == "0%" and rodape.v_status.get() == "comecando"
    rodape.progresso(42.4)
    assert rodape.v_pct.get() == "42%"
    rodape.progresso(250)          # nunca passa de 100
    assert rodape.v_progresso.get() == 100.0
    rodape.progresso(-5)           # nem abaixo de zero
    assert rodape.v_progresso.get() == 0.0
    rodape.concluir("fim")
    assert rodape.v_pct.get() == "100%" and rodape.v_status.get() == "fim"


# ---------------------------------------------------------------------------
# O progresso anda de verdade
# ---------------------------------------------------------------------------


@pytest.fixture
def pasta_com_rasters(tmp_path):
    for indice in range(3):
        caminho = tmp_path / f"entrada_{indice}.tif"
        with rasterio.open(caminho, "w", driver="GTiff", height=8, width=8, count=1,
                           dtype="int16", crs="EPSG:4326", nodata=-9999,
                           transform=from_origin(-47.0, -15.0, 0.01, 0.01)) as dst:
            dst.write(np.full((8, 8), indice + 1, dtype="int16"), 1)
    return tmp_path


def _coletar(monkeypatch, app, aba_metodo):
    """Roda o worker da aba sem thread e devolve o que foi para a fila."""
    import gui

    capturado = {"progress": [], "status": [], "done": None}

    def falso_run(root, worker_fn, log_cb, done_cb, error_cb, botoes,
                  progress_cb=None, status_cb=None):
        fila: queue.Queue = queue.Queue()
        worker_fn(fila)
        while not fila.empty():
            tipo, carga = fila.get()
            if tipo in capturado and tipo != "done":
                capturado[tipo].append(carga)
            elif tipo == "done":
                capturado["done"] = carga

    monkeypatch.setattr(gui, "run_background_task", falso_run)
    aba_metodo()
    return capturado


def test_progresso_anda_na_aba_reamostrar(app_georaster, pasta_com_rasters, tmp_path,
                                          monkeypatch):
    modelo = pasta_com_rasters / "entrada_0.tif"
    saida = tmp_path / "saida"
    saida.mkdir()
    app = app_georaster
    app.res_modelo_path.set(str(modelo))
    app.res_dir_in.set(str(pasta_com_rasters))
    app.res_dir_out.set(str(saida))
    app._on_res_scan()
    assert len(app.res_rows) == 3

    capturado = _coletar(monkeypatch, app, app._on_res_run)
    assert capturado["progress"] == [pytest.approx(100 * i / 3) for i in (1, 2, 3)]
    assert len(capturado["status"]) == 3


def test_progresso_anda_na_aba_verificar(app_georaster, pasta_com_rasters, monkeypatch):
    app = app_georaster
    app.ver_modelo_path.set(str(pasta_com_rasters / "entrada_0.tif"))
    app.ver_dir_in.set(str(pasta_com_rasters))
    capturado = _coletar(monkeypatch, app, app._on_ver_run)
    assert capturado["progress"][-1] == pytest.approx(100.0)
    assert len(capturado["progress"]) == 3


def test_progresso_anda_na_aba_separar_bandas(app_georaster, pasta_com_rasters,
                                              tmp_path, monkeypatch):
    saida = tmp_path / "bandas"
    saida.mkdir()
    app = app_georaster
    app.bands_dir_in.set(str(pasta_com_rasters))
    app.bands_dir_out.set(str(saida))
    capturado = _coletar(monkeypatch, app, app._on_bands_run)
    assert capturado["progress"][-1] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# O rotulo nao pode vazar para o processamento
# ---------------------------------------------------------------------------


def test_aba_reamostrar_guarda_o_codigo_e_mostra_o_rotulo(app_georaster,
                                                          pasta_com_rasters):
    app = app_georaster
    app.res_dir_in.set(str(pasta_com_rasters))
    app.res_dtype_default.set(config.rotulo_de_dtype("INT2U"))
    app._on_res_scan()

    # memoria: codigo puro (e o que vai para raster_ops.resample_raster)
    assert {linha["Dtype"] for linha in app.res_rows} == {"INT2U"}
    # tela: rotulo completo
    primeira = app.res_tree.item(app.res_tree.get_children()[0], "values")
    assert primeira[4] == "INT2U - 16 bits - uint16"
    # e o tipo do arquivo de entrada tambem mostra os bits
    assert primeira[1] == "int16 (16 bits)"


def test_edicao_do_dtype_na_tabela_aceita_rotulo_e_codigo(app_georaster,
                                                          pasta_com_rasters):
    app = app_georaster
    app.res_dir_in.set(str(pasta_com_rasters))
    app._on_res_scan()
    assert config.dtype_do_rotulo("BYTE - 8 bits - uint8") == "BYTE"
    assert config.dtype_do_rotulo("BYTE") == "BYTE"


def test_aba_reclassificar_converte_o_rotulo_antes_de_processar(app_georaster,
                                                                pasta_com_rasters,
                                                                tmp_path, monkeypatch):
    import raster_ops

    app = app_georaster
    saida = tmp_path / "reclass"
    saida.mkdir()
    app.rcl_dir_in.set(str(pasta_com_rasters))
    app.rcl_dir_out.set(str(saida))
    app._on_rcl_scan()
    app.rcl_dtype.set(config.rotulo_de_dtype("INT2S"))

    usados = []
    original = raster_ops.reclassify_raster

    def espiao(*args, **kwargs):
        usados.append(kwargs.get("dtype"))
        return original(*args, **kwargs)

    monkeypatch.setattr(raster_ops, "reclassify_raster", espiao)
    capturado = _coletar(monkeypatch, app, app._on_rcl_run)

    assert usados and set(usados) == {"INT2S"}, usados
    assert capturado["progress"][-1] == pytest.approx(100.0)
