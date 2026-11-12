"""
tests/test_tab_merge.py
=======================

Testes da aba "Merge de Rasters" (interface).

A conta esta coberta em ``test_merge_ops.py``; aqui o que importa e o
comportamento que o usuario pediu e so se ve na tela: a pasta carregada
sozinha trazendo apenas os conjuntos que precisam de merge, a saida
apontando para a mesma pasta por padrao, o prefixo refletido no nome de
saida, as marcacoes por clique e a barra de progresso cabendo na janela nos
tamanhos em que a aplicacao roda.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BLOCOS = [
    "reclass_ndvi_stdDev_tau_p-0000046592-0000000000.tif",
    "reclass_ndvi_stdDev_tau_p-0000000000-0000000000.tif",
    "reclass_ndvi_stdDev_tau-0000046592-0000000000.tif",
    "reclass_ndvi_stdDev_tau-0000000000-0000000000.tif",
]


@pytest.fixture
def aba(app_georaster):
    """A aba de merge, devolvida em estado limpo para cada teste."""
    aba = app_georaster.aba_merge
    aba.v_entrada.set("")
    aba.v_saida.set("")
    aba.v_prefixo.set("Merge_")
    aba.v_sobrescrever.set(True)
    aba._carregar_grupos([])
    return aba


@pytest.fixture
def pasta_com_blocos(tmp_path):
    """Quatro blocos reais (dois conjuntos) mais um raster avulso."""
    def gravar(nome, col_off):
        caminho = tmp_path / nome
        with rasterio.open(caminho, "w", driver="GTiff", height=8, width=8, count=1,
                           dtype="int16", crs="EPSG:4326", nodata=-9999,
                           transform=from_origin(-47.0 + col_off * 0.01, -15.0,
                                                 0.01, 0.01)) as dst:
            dst.write(np.ones((8, 8), dtype="int16"), 1)
        return caminho

    for nome in BLOCOS:
        gravar(nome, 8 if "0000046592" in nome else 0)
    gravar("AC_CLIP_LULC_2025.tif", 0)
    return tmp_path


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def test_a_aba_montou_com_barra_e_log(app_georaster):
    aba = app_georaster.aba_merge
    assert aba.barra is not None
    assert aba.log is not None
    assert aba.tree_grupos is not None and aba.tree_blocos is not None


def test_valores_padrao(aba):
    assert aba.v_prefixo.get() == "Merge_"
    assert aba.v_sobrescrever.get() is True
    assert float(aba.v_memoria.get()) == 64.0


def test_barra_de_progresso_visivel_em_varios_tamanhos(app_georaster, tk_root):
    tk_root.deiconify()
    aba = app_georaster.aba_merge
    try:
        for largura, altura in ((1400, 900), (1150, 720), (1366, 700)):
            tk_root.geometry(f"{largura}x{altura}")
            app_georaster.notebook.select(app_georaster.tab_merge)
            tk_root.update_idletasks()
            tk_root.update()
            barra = aba.barra
            base = (barra.winfo_rooty() - tk_root.winfo_rooty()) + barra.winfo_height()
            assert barra.winfo_ismapped(), f"barra nao mapeada em {largura}x{altura}"
            assert base <= tk_root.winfo_height(), f"barra fora da janela em {largura}x{altura}"
    finally:
        tk_root.withdraw()


# ---------------------------------------------------------------------------
# Leitura da pasta
# ---------------------------------------------------------------------------


def test_escanear_carrega_so_o_que_precisa_de_merge(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    bases = [g["base"] for g in aba.grupos]
    assert bases == ["reclass_ndvi_stdDev_tau", "reclass_ndvi_stdDev_tau_p"]
    assert all(len(g["arquivos"]) == 2 for g in aba.grupos)
    todos = [os.path.basename(c) for g in aba.grupos for c in g["arquivos"]]
    assert "AC_CLIP_LULC_2025.tif" not in todos


def test_a_saida_vem_igual_a_pasta_de_entrada(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._ao_escolher_pasta()
    assert aba.v_saida.get() == str(pasta_com_blocos)


def test_saida_escolhida_pelo_usuario_nao_e_sobrescrita(aba, pasta_com_blocos, tmp_path):
    outra = tmp_path / "saida"
    outra.mkdir()
    aba.v_saida.set(str(outra))
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._ao_escolher_pasta()
    assert aba.v_saida.get() == str(outra)


def test_tabela_mostra_um_conjunto_por_linha(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    linhas = aba.tree_grupos.get_children()
    assert len(linhas) == 2
    valores = aba.tree_grupos.item(linhas[0], "values")
    assert valores[0] == "[x]"                       # marcado por padrao
    assert valores[1] == "reclass_ndvi_stdDev_tau"
    assert valores[2] == "2/2"
    assert valores[4] == "Merge_reclass_ndvi_stdDev_tau.tif"


def test_blocos_do_conjunto_aparecem_com_metadados(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba.tree_grupos.selection_set(aba.tree_grupos.get_children()[0])
    aba._mostrar_blocos()
    linhas = aba.tree_blocos.get_children()
    assert len(linhas) == 2
    valores = aba.tree_blocos.item(linhas[0], "values")
    assert valores[0] == "[x]"
    assert valores[2] == "8 x 8"
    # O tipo tambem mostra os bits, como nas abas em que se escolhe o tipo.
    assert valores[4] == "int16 (16 bits)"


# ---------------------------------------------------------------------------
# Nome de saida
# ---------------------------------------------------------------------------


def test_prefixo_novo_renomeia_as_saidas(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba.v_prefixo.set("Mosaico_")
    assert aba.grupos[0]["saida"] == "Mosaico_reclass_ndvi_stdDev_tau.tif"


def test_prefixo_vazio_deixa_so_o_nome_do_conjunto(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba.v_prefixo.set("")
    assert aba.grupos[0]["saida"] == "reclass_ndvi_stdDev_tau.tif"


def test_nome_editado_a_mao_sobrevive_a_troca_de_prefixo(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba.grupos[0]["saida"] = "NDVI_tau_mosaico.tif"
    aba.grupos[0]["saida_manual"] = True
    aba.v_prefixo.set("Outro_")
    assert aba.grupos[0]["saida"] == "NDVI_tau_mosaico.tif"
    assert aba.grupos[1]["saida"].startswith("Outro_")


# ---------------------------------------------------------------------------
# Selecao
# ---------------------------------------------------------------------------


def test_desmarcar_todos_tira_os_conjuntos_da_execucao(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba._marcar_grupos(False)
    assert all(not g["usar"] for g in aba.grupos)
    assert aba.tree_grupos.item(aba.tree_grupos.get_children()[0], "values")[0] == "[ ]"
    aba._marcar_grupos(True)
    assert all(g["usar"] for g in aba.grupos)


def test_bloco_desmarcado_sai_da_contagem_e_da_tarefa(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    grupo = aba.grupos[0]
    fora = grupo["arquivos"][0]
    grupo["usar_arquivo"][fora] = False
    aba._atualizar_grupos()
    assert aba.tree_grupos.item(aba.tree_grupos.get_children()[0], "values")[2] == "1/2"

    # Com um unico bloco o conjunto e pulado, nao executado pela metade.
    aba.grupos[1]["usar"] = False
    assert aba._tarefas() is None or all(
        t["base"] != "reclass_ndvi_stdDev_tau" for t in (aba._tarefas() or []))


def test_tarefas_montam_caminho_completo_da_saida(aba, pasta_com_blocos, tmp_path):
    destino = tmp_path / "saida"
    destino.mkdir()
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba.v_saida.set(str(destino))
    tarefas = aba._tarefas()
    assert len(tarefas) == 2
    assert tarefas[0]["saida"] == str(destino / "Merge_reclass_ndvi_stdDev_tau.tif")
    assert len(tarefas[0]["arquivos"]) == 2
    assert tarefas[0]["memoria"] == 64.0


def test_sem_pasta_de_saida_usa_a_pasta_de_entrada(aba, pasta_com_blocos):
    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    aba.v_saida.set("")
    tarefas = aba._tarefas()
    assert tarefas[0]["saida"] == str(pasta_com_blocos / "Merge_reclass_ndvi_stdDev_tau.tif")


def test_pasta_sem_conjuntos_nao_quebra(aba, tmp_path):
    (tmp_path / "sozinho.tif").write_bytes(b"")
    aba.v_entrada.set(str(tmp_path))
    aba._escanear()
    assert aba.grupos == []
    assert aba.tree_grupos.get_children() == ()


# ---------------------------------------------------------------------------
# Execucao ponta a ponta pela aba
# ---------------------------------------------------------------------------


def test_execucao_gera_o_arquivo_com_o_nome_padrao(aba, pasta_com_blocos, monkeypatch):
    """Roda o worker da aba direto (sem thread) e confere o arquivo gravado."""
    import queue

    from interface import tab_merge

    monkeypatch.setattr(tab_merge.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(tab_merge.messagebox, "showwarning", lambda *a, **k: None)

    aba.v_entrada.set(str(pasta_com_blocos))
    aba._escanear()
    tarefas = aba._tarefas()

    capturado = {}

    def falso_rodar(worker, ao_terminar, _ao_falhar, _botoes):
        fila = queue.Queue()
        worker(fila)
        while not fila.empty():
            tipo, carga = fila.get()
            if tipo == "done":
                capturado["resultados"] = carga

    monkeypatch.setattr(aba, "_rodar", falso_rodar)
    aba._executar()

    assert len(capturado["resultados"]) == 2
    assert all(status == "OK" for _base, status, _info in capturado["resultados"])
    for tarefa in tarefas:
        gerado = tarefa["saida"]
        assert os.path.exists(gerado), gerado
        with rasterio.open(gerado) as src:
            assert (src.width, src.height) == (16, 8)   # dois blocos 8x8 lado a lado
            assert src.crs.to_string() == "EPSG:4326"
