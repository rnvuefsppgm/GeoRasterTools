"""
tab_amostragem.py
=================

Aba "Amostragem" do GeoRaster Tools V3 (chamava-se "Amostragem de Fogo
(SDM)" ate a V2; o rotulo encurtou, o conteudo e o mesmo).

Traz para dentro do GeoRaster Tools o nucleo da aplicacao V5 de amostragem da
frequencia de fogo (pacote ``amostragem_fogo``), com o preparo dos pontos para
o SDM_APP.

Por que o preparo para SDM existe
---------------------------------
O SDM_APP le os pontos assim (``app_sdm_multi_v6.R``)::

    p_coords  <- utils::read.csv(cfg$pres_csv)[, c("x", "y")]
    bg_coords <- utils::read.csv(cfg$bg_csv)[, c("x", "y")]

ou seja: CSV com virgula, ponto decimal, colunas chamadas ``x`` e ``y``, e
arquivos separados para presenca e background. A V5 ja gravava nesse formato,
mas sorteava olhando apenas o raster de fogo. Numa medicao com preditor de
~1 km e 18 % de NoData, de 500 presencas pedidas so 343 chegavam ao modelo:
21 % caiam em NoData de preditor (descartadas em silencio pelo SDMtune) e 14 %
repetiam celula do preditor (pseudo-replicacao). Com o filtro desta aba, as
500 pedidas chegam como 500 utilizaveis.

A aba usa exatamente o mesmo ``pipeline`` do pacote que os testes exercitam.
"""

from __future__ import annotations

import os
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from nucleo import config
from amostragem_fogo import __version__ as versao_amostragem
from amostragem_fogo.analise import OperacaoCancelada
from amostragem_fogo.config import (COORDENADAS_ORIGINAL, COORDENADAS_WGS84,
                                     GRUPOS_DE_SAIDA, Faixa,
                                     ParametrosAmostragem, ParametrosAnalise,
                                     SelecaoDeSaidas, texto_para_conjunto,
                                     texto_para_float, texto_para_int)
from amostragem_fogo.pipeline import executar_amostragem, executar_analise
from amostragem_fogo.sdm import FiltroSDM
from nucleo.utils import CancellationToken


def _milhar(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def _pct(valor: float) -> str:
    return f"{valor:.2f}%".replace(".", ",")


def _num(valor: float) -> str:
    if abs(valor - round(valor)) < 1e-9:
        return str(int(round(valor)))
    return f"{valor:.6g}"


def _quadro_rolavel(pai: ttk.Frame, largura: int = 430) -> ttk.Frame:
    """Coluna esquerda rolavel: sao muitos controles para caber em 1080 px.

    A implementacao foi para ``gui.quadro_rolavel`` na V3, para a aba de merge
    usar exatamente a mesma; o import tardio evita import circular."""
    from interface.gui import quadro_rolavel

    return quadro_rolavel(pai, largura)


class AbaAmostragemFogo:
    """Constroi e opera a aba. Instanciada uma vez por aplicacao."""

    def __init__(self, app, frame: ttk.Frame):
        self.app = app
        self.root = app.root
        self.frame = frame
        self.analise = None
        self.token: CancellationToken | None = None

        self._criar_variaveis()
        self._construir()

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def _criar_variaveis(self) -> None:
        self.v_raster = tk.StringVar()
        self.v_saida = tk.StringVar()
        self.v_banda = tk.StringVar(value="1")
        self.v_anos = tk.StringVar(value="41")
        self.v_excluir = tk.StringVar(value="")
        self.v_p_min = tk.StringVar(value="4")
        self.v_p_max = tk.StringVar(value="")
        self.v_a_min = tk.StringVar(value="0")
        self.v_a_max = tk.StringVar(value="3")
        self.v_n_p = tk.StringVar(value="1000")
        self.v_n_a = tk.StringVar(value="1000")
        self.v_dist = tk.StringVar(value="1000")
        self.v_semente = tk.StringVar(value="42")
        self.v_entre_classes = tk.BooleanVar(value=True)

        self.v_pred_dir = tk.StringVar()
        self.v_pred_info = tk.StringVar(value="Nenhum preditor selecionado (filtro desligado).")
        self.v_pred_valido = tk.BooleanVar(value=True)
        self.v_pred_celula = tk.BooleanVar(value=True)

        self.v_coord = tk.StringVar(value=COORDENADAS_WGS84)
        self.v_val_p = tk.StringVar(value="1")
        self.v_val_a = tk.StringVar(value="0")
        self.v_decimal = tk.StringVar(value=".")

        self.v_saidas = {nome: tk.BooleanVar(value=True) for grupo in GRUPOS_DE_SAIDA
                          for nome, _ in grupo[1]}
        self.v_progresso = tk.DoubleVar(value=0.0)
        self.v_pct = tk.StringVar(value="0%")
        self.v_status = tk.StringVar(value="Selecione o raster de frequencia para comecar.")
        self.v_resumo = tk.StringVar(value="")
        self._preditores: tuple[Path, ...] = ()

    # ------------------------------------------------------------------
    # Montagem da interface
    # ------------------------------------------------------------------

    def _construir(self) -> None:
        from interface.gui import LogPanel, make_result_tree, path_row, select_directory, select_file

        direita = ttk.Frame(self.frame)
        direita.pack(side="right", fill="both", expand=True, padx=(4, 8), pady=8)
        esquerda = _quadro_rolavel(self.frame)

        # ---- 1. Arquivos ------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="1. Arquivos")
        box.pack(fill="x", pady=4)
        path_row(box, "Raster de frequencia de fogo (.tif)", "file",
                 lambda lbl: (select_file(self.v_raster, lbl, "Selecionar raster de frequencia",
                                          [("GeoTIFF", "*.tif *.tiff"), ("Todos", "*.*")]),
                              self._sugerir_saida(), self._invalidar()))
        path_row(box, "Pasta de saida", "dir",
                 lambda lbl: select_directory(self.v_saida, lbl, "Selecionar pasta de saida"))

        # ---- 2. Analise -------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="2. Analise da frequencia")
        box.pack(fill="x", pady=4)
        self._campo(box, "Banda do raster", self.v_banda, invalida=True)
        self._campo(box, "Anos observados", self.v_anos, invalida=True)
        self._campo(box, "Valores a excluir (ex.: 0; 99)", self.v_excluir, invalida=True)
        self.btn_analisar = ttk.Button(box, text="ANALISAR FREQUENCIA", command=self._analisar)
        self.btn_analisar.pack(fill="x", pady=(6, 4), padx=4)

        # ---- 3. Classes -------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="3. Classes para amostragem")
        box.pack(fill="x", pady=4)
        self._campo(box, "Presenca - minimo", self.v_p_min)
        self._campo(box, "Presenca - maximo (vazio = maior valor)", self.v_p_max)
        self._campo(box, "Ausencia - minimo", self.v_a_min)
        self._campo(box, "Ausencia - maximo", self.v_a_max)
        self.btn_limiar = ttk.Button(box, text="Usar limiar selecionado na tabela",
                                      command=self._usar_limiar, state="disabled")
        self.btn_limiar.pack(fill="x", pady=(6, 4), padx=4)

        # ---- 4. Amostragem ----------------------------------------------
        box = ttk.LabelFrame(esquerda, text="4. Amostragem")
        box.pack(fill="x", pady=4)
        self._campo(box, "N de presencas", self.v_n_p)
        self._campo(box, "N de ausencias (background)", self.v_n_a)
        self._campo(box, "Distancia minima (m)", self.v_dist)
        self._campo(box, "Semente aleatoria", self.v_semente)
        ttk.Checkbutton(box, text="Aplicar distancia tambem entre classes",
                        variable=self.v_entre_classes).pack(anchor="w", padx=4, pady=(2, 4))

        # ---- 5. Preparo para SDM ----------------------------------------
        box = ttk.LabelFrame(esquerda, text="5. Preparo para o SDM (raster de referencia)")
        box.pack(fill="x", pady=4)
        ttk.Label(
            box, wraplength=390, foreground="#40506a", font=("Segoe UI", 8), justify="left",
            text=("Escolha UM raster preditor como referencia da grade, de "
                  "preferencia um bioclimatico ou o de uso do solo. Nao use a pasta "
                  "inteira: exigir validade em todos os preditores ao mesmo tempo "
                  "zera a amostra quando os rasters de distancia tem vazios de NaN."),
        ).pack(anchor="w", padx=4, pady=(2, 4))
        path_row(box, "Raster de referencia (.tif)", "file",
                 lambda lbl: (select_file(self.v_pred_dir, lbl, "Selecionar raster de referencia",
                                          [("GeoTIFF", "*.tif *.tiff"), ("Todos", "*.*")]),
                              self._definir_referencia()))
        ttk.Label(box, textvariable=self.v_pred_info, wraplength=390,
                  foreground="#40506a", font=("Segoe UI", 8), justify="left").pack(
            anchor="w", padx=4)
        ttk.Checkbutton(box, text="Descartar pontos em NoData da referencia",
                        variable=self.v_pred_valido).pack(anchor="w", padx=4)
        ttk.Checkbutton(box, text="No maximo um ponto por celula da referencia",
                        variable=self.v_pred_celula).pack(anchor="w", padx=4, pady=(0, 4))
        linha = ttk.Frame(box)
        linha.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(linha, text="Conferir referencia", command=self._conferir_referencia).pack(
            side="left", expand=True, fill="x", padx=(0, 2))
        ttk.Button(linha, text="Limpar", command=self._limpar_preditores).pack(
            side="left", expand=True, fill="x", padx=(2, 0))

        # ---- 6. Formato --------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="6. Formato das amostras")
        box.pack(fill="x", pady=4)
        ttk.Label(box, text="Colunas x,y:").pack(anchor="w", padx=4)
        ttk.Radiobutton(box, text="Longitude/latitude WGS84 (esperado pelo SDM_APP)",
                        variable=self.v_coord, value=COORDENADAS_WGS84).pack(anchor="w", padx=4)
        ttk.Radiobutton(box, text="CRS original do raster", variable=self.v_coord,
                        value=COORDENADAS_ORIGINAL).pack(anchor="w", padx=4)
        linha = ttk.Frame(box)
        linha.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(linha, text="Value presenca:").pack(side="left")
        ttk.Entry(linha, textvariable=self.v_val_p, width=5).pack(side="left", padx=(4, 10))
        ttk.Label(linha, text="ausencia:").pack(side="left")
        ttk.Entry(linha, textvariable=self.v_val_a, width=5).pack(side="left", padx=4)
        ttk.Label(box, text="Separador decimal das demais tabelas:").pack(
            anchor="w", padx=4, pady=(6, 0))
        linha = ttk.Frame(box)
        linha.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Radiobutton(linha, text="Ponto", variable=self.v_decimal, value=".").pack(side="left")
        ttk.Radiobutton(linha, text="Virgula (Excel pt-BR)", variable=self.v_decimal,
                        value=",").pack(side="left", padx=8)

        # ---- 7. Arquivos de saida ----------------------------------------
        box = ttk.LabelFrame(esquerda, text="7. Arquivos de saida a salvar")
        box.pack(fill="x", pady=4)
        for titulo, itens in GRUPOS_DE_SAIDA:
            ttk.Label(box, text=titulo, font=("Segoe UI", 8, "bold")).pack(
                anchor="w", padx=4, pady=(4, 0))
            for nome, rotulo in itens:
                ttk.Checkbutton(box, text=rotulo, variable=self.v_saidas[nome]).pack(
                    anchor="w", padx=12)
        botoes = ttk.Frame(box)
        botoes.pack(fill="x", padx=4, pady=(6, 4))
        for texto, acao in (("Todas", lambda: self._marcar(True)),
                            ("Nenhuma", lambda: self._marcar(False)),
                            ("So presencas", self._so_presencas),
                            ("So ausencias", self._so_ausencias)):
            ttk.Button(botoes, text=texto, width=12, command=acao).pack(side="left", padx=1)

        # ---- 8. Execucao --------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="8. Executar")
        box.pack(fill="x", pady=4)
        self.btn_gerar = ttk.Button(box, text="GERAR E SALVAR AMOSTRAS",
                                     command=self._gerar, state="disabled")
        self.btn_gerar.pack(fill="x", padx=4, pady=(4, 2))
        self.btn_parar = ttk.Button(box, text="PARAR PROCESSAMENTO",
                                     command=self._parar, state="disabled")
        self.btn_parar.pack(fill="x", padx=4, pady=2)
        ttk.Button(box, text="Abrir pasta de saida", command=self._abrir_saida).pack(
            fill="x", padx=4, pady=(2, 4))

        # ================= coluna direita =================
        ttk.Label(direita, textvariable=self.v_resumo, wraplength=760,
                  foreground="#2c3e50").pack(anchor="w", pady=(0, 4))

        # Rodape ancorado ANTES da tabela: no pack do Tk o espaco e distribuido
        # na ordem de empacotamento, e a barra ficaria sem espaco em telas menores.
        rodape = ttk.Frame(direita)
        rodape.pack(side="bottom", fill="x", pady=(6, 0))
        linha = ttk.Frame(rodape)
        linha.pack(fill="x")
        self.barra = ttk.Progressbar(linha, variable=self.v_progresso, maximum=100,
                                      mode="determinate")
        self.barra.pack(side="left", fill="x", expand=True)
        ttk.Label(linha, textvariable=self.v_pct, width=6, anchor="e",
                  font=("Segoe UI", 9, "bold")).pack(side="right", padx=(6, 0))
        ttk.Label(rodape, textvariable=self.v_status, anchor="w").pack(fill="x", pady=(2, 0))

        caixa = ttk.LabelFrame(direita, text="Tabela de limiares de frequencia")
        caixa.pack(fill="both", expand=True)
        self.tree = make_result_tree(caixa, {
            "Limiar": "Limiar",
            "Pixels": "Pixels queimados mantidos",
            "Ocorrencias": "Ocorrencias mantidas",
            "Retorno": "Retorno medio (anos)",
            "Interpretacao": "Interpretacao",
        }, height=12)
        self.tree.bind("<Double-1>", lambda _e: self._usar_limiar())

        caixa = ttk.LabelFrame(direita, text="Log")
        caixa.pack(fill="both", expand=True, pady=(6, 0))
        self.log = LogPanel(caixa)
        self.log.add(f"Nucleo de amostragem v{versao_amostragem} carregado.")

    def _campo(self, pai, rotulo: str, variavel: tk.StringVar, invalida: bool = False) -> None:
        linha = ttk.Frame(pai)
        linha.pack(fill="x", padx=4, pady=1)
        ttk.Label(linha, text=rotulo + ":", width=30, anchor="w").pack(side="left")
        entrada = ttk.Entry(linha, textvariable=variavel, width=12)
        entrada.pack(side="left", fill="x", expand=True)
        if invalida:
            variavel.trace_add("write", lambda *_: self._invalidar())

    # ------------------------------------------------------------------
    # Acoes auxiliares
    # ------------------------------------------------------------------

    def _sugerir_saida(self) -> None:
        caminho = self.v_raster.get().strip()
        if caminho and not self.v_saida.get().strip():
            self.v_saida.set(str(Path(caminho).parent / "amostras_fogo"))

    def _definir_referencia(self) -> None:
        """Le os metadados do raster escolhido e mostra na tela."""
        caminho = self.v_pred_dir.get().strip()
        if not caminho or not Path(caminho).is_file():
            self._limpar_preditores()
            return
        self._preditores = (Path(caminho),)
        try:
            import rasterio

            with rasterio.open(caminho) as src:
                self.v_pred_info.set(
                    f"Referencia: {Path(caminho).name}\n"
                    f"{src.width} x {src.height} | res {abs(src.transform.a):g} | "
                    f"{src.crs} | NoData {src.nodata}"
                )
                self.log.add(
                    f"Raster de referencia: {Path(caminho).name} "
                    f"({src.crs}, {src.width}x{src.height})."
                )
        except Exception as exc:  # noqa: BLE001
            self.v_pred_info.set(f"Nao foi possivel ler o raster: {exc}")
            self._preditores = ()

    def _limpar_preditores(self) -> None:
        self._preditores = ()
        self.v_pred_dir.set("")
        self.v_pred_info.set("Nenhuma referencia selecionada (filtro desligado).")

    def _amostra_de_sondagem(self, caminho: str):
        """Pega pontos queimados da janela central do raster de fogo."""
        import numpy as np
        import rasterio
        from rasterio.windows import Window

        from amostragem_fogo.geo import abrir_sistema

        with rasterio.open(caminho) as src:
            sistema = abrir_sistema(src)
            lado = min(1500, src.width, src.height)
            janela = Window((src.width - lado) // 2, (src.height - lado) // 2, lado, lado)
            dados = src.read(1, window=janela).astype("float64")
            linhas, colunas = np.nonzero(np.isfinite(dados) & (dados > 0))
            if linhas.size == 0:
                linhas, colunas = np.nonzero(np.isfinite(dados))
            if linhas.size == 0:
                return sistema, None, None
            passo = np.linspace(0, linhas.size - 1, min(3000, linhas.size)).astype(int)
            x, y = sistema.xy(linhas[passo] + int(janela.row_off),
                               colunas[passo] + int(janela.col_off))
        return sistema, x, y

    def _conferir_referencia(self) -> None:
        """Sonda a referencia contra o raster de fogo, antes de processar tudo."""
        if not self._preditores:
            messagebox.showwarning("Aviso", "Selecione o raster de referencia primeiro.")
            return
        caminho = self.v_raster.get().strip()
        if not caminho:
            messagebox.showwarning("Aviso", "Selecione o raster de frequencia de fogo primeiro.")
            return
        try:
            from amostragem_fogo.sdm import sondar_referencia

            sistema, x, y = self._amostra_de_sondagem(caminho)
            if x is None:
                messagebox.showwarning("Aviso", "A janela central do raster nao tem dados.")
                return
            sondagem = sondar_referencia(self._montar_filtro(), x, y, sistema.crs)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro ao conferir", str(exc))
            return

        iguais = "iguais" if sondagem.crs_iguais else "diferentes (convertido em memoria)"
        linhas = [
            f"Amostra sondada: {sondagem.avaliados} pontos do centro do raster de fogo.",
            "",
            f"CRS do fogo ......... {sistema.crs_texto}",
            f"CRS da referencia ... {sondagem.crs_referencia}  ({iguais})",
            "",
            f"Caem dentro ......... {sondagem.dentro} ({sondagem.taxa_dentro:.0%})",
            f"Em celula valida .... {sondagem.validos} ({sondagem.taxa_valida:.0%})",
            f"Celulas distintas ... {sondagem.celulas_distintas}",
        ]
        for texto in linhas:
            self.log.add(texto)
        avisos = sondagem.diagnostico(0)
        if avisos:
            for texto in avisos:
                self.log.add(f"AVISO: {texto}")
            messagebox.showwarning("Referencia com problema",
                                    "\n".join(linhas + [""] + avisos))
        else:
            messagebox.showinfo("Referencia adequada", "\n".join(linhas))

    def _montar_filtro(self) -> FiltroSDM:
        return FiltroSDM(
            preditores=self._preditores,
            exigir_preditor_valido=bool(self.v_pred_valido.get()),
            uma_amostra_por_celula=bool(self.v_pred_celula.get()),
        )

    def _marcar(self, ligado: bool) -> None:
        for var in self.v_saidas.values():
            var.set(ligado)

    def _so_presencas(self) -> None:
        alvo = set(SelecaoDeSaidas.somente_presencas().ligadas())
        for nome, var in self.v_saidas.items():
            var.set(nome in alvo)

    def _so_ausencias(self) -> None:
        alvo = set(SelecaoDeSaidas.somente_ausencias().ligadas())
        for nome, var in self.v_saidas.items():
            var.set(nome in alvo)

    def _abrir_saida(self) -> None:
        pasta = self.v_saida.get().strip()
        if not pasta:
            messagebox.showwarning("Aviso", "Selecione primeiro a pasta de saida.")
            return
        try:
            os.makedirs(pasta, exist_ok=True)
            os.startfile(pasta)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", f"Nao foi possivel abrir a pasta:\n{exc}")

    def _invalidar(self) -> None:
        if self.analise is None:
            return
        self.analise = None
        self.tree.delete(*self.tree.get_children())
        self.v_resumo.set("")
        self.btn_limiar.config(state="disabled")
        self.btn_gerar.config(state="disabled")
        self.v_status.set("Os parametros mudaram. Rode a analise novamente.")

    def _parar(self) -> None:
        if self.token is not None:
            self.token.cancel()
        self.v_status.set("Parada solicitada. Aguarde o fim da leitura em andamento...")
        self.btn_parar.config(state="disabled")

    def _progresso(self, valor: float) -> None:
        self.v_progresso.set(float(valor))
        self.v_pct.set(f"{float(valor):.0f}%")

    # ------------------------------------------------------------------
    # Leitura dos parametros (sempre na thread principal)
    # ------------------------------------------------------------------

    def _ler_analise(self) -> ParametrosAnalise:
        caminho = self.v_raster.get().strip()
        if not caminho:
            raise ValueError("Selecione o raster de frequencia de fogo.")
        return ParametrosAnalise(
            raster=Path(caminho),
            banda=texto_para_int(self.v_banda.get(), "Banda", minimo=1),
            anos_observados=texto_para_int(self.v_anos.get(), "Anos observados", minimo=1),
            valores_excluidos=texto_para_conjunto(self.v_excluir.get()),
        )

    def _ler_amostragem(self) -> ParametrosAmostragem:
        if self.analise is None:
            raise ValueError("Analise o raster antes de gerar as amostras.")
        par_analise = self._ler_analise()
        if par_analise.assinatura_esperada() != self.analise.assinatura():
            raise ValueError("Os parametros da analise mudaram. Rode a analise novamente.")

        saida = self.v_saida.get().strip()
        if not saida:
            raise ValueError("Selecione a pasta de saida.")

        p_max = texto_para_float(self.v_p_max.get(), "Presenca - maximo", vazio_permitido=True)
        if p_max is None:
            p_max = self.analise.valor_maximo

        marcadas = [n for n, v in self.v_saidas.items() if bool(v.get())]
        if not marcadas:
            raise ValueError("Marque pelo menos um arquivo de saida na secao 7.")

        return ParametrosAmostragem(
            analise=par_analise,
            saida=Path(saida),
            presenca=Faixa(float(texto_para_float(self.v_p_min.get(), "Presenca - minimo")),
                           float(p_max)),
            ausencia=Faixa(float(texto_para_float(self.v_a_min.get(), "Ausencia - minimo")),
                           float(texto_para_float(self.v_a_max.get(), "Ausencia - maximo"))),
            n_presenca=texto_para_int(self.v_n_p.get(), "N de presencas", minimo=0),
            n_ausencia=texto_para_int(self.v_n_a.get(), "N de ausencias", minimo=0),
            distancia_minima_m=float(texto_para_float(self.v_dist.get(), "Distancia minima")),
            distancia_entre_classes=bool(self.v_entre_classes.get()),
            semente=texto_para_int(self.v_semente.get(), "Semente", minimo=0),
            separador_decimal=self.v_decimal.get(),
            saidas=SelecaoDeSaidas.de_nomes(marcadas),
            coordenadas_xy=self.v_coord.get(),
            valor_presenca=texto_para_int(self.v_val_p.get(), "Value da presenca",
                                           minimo=-(10 ** 9)),
            valor_ausencia=texto_para_int(self.v_val_a.get(), "Value da ausencia",
                                           minimo=-(10 ** 9)),
            filtro_sdm=self._montar_filtro(),
        )

    # ------------------------------------------------------------------
    # Analise
    # ------------------------------------------------------------------

    def _analisar(self) -> None:
        from interface.gui import run_background_task

        try:
            parametros = self._ler_analise()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Parametros invalidos", str(exc))
            return

        self.token = CancellationToken()
        token = self.token
        self._progresso(0)
        self.v_status.set("Iniciando a analise...")
        self.log.add("=== ANALISE DA FREQUENCIA INICIADA ===")
        self.log.add(f"Raster: {parametros.raster.name} | banda {parametros.banda}")
        self.btn_parar.config(state="normal")

        def worker(q: queue.Queue):
            def progresso(valor, mensagem):
                q.put(("progress", valor))
                q.put(("status", mensagem))
            try:
                resultado = executar_analise(parametros, progresso, token.is_cancelled)
            except OperacaoCancelada as exc:
                q.put(("done", ("cancelado", str(exc))))
                return
            q.put(("done", ("ok", resultado)))

        def on_done(payload):
            tipo, dado = payload
            self.btn_parar.config(state="disabled")
            if tipo == "cancelado":
                self.v_status.set(str(dado))
                self.log.add(f"ANALISE CANCELADA: {dado}")
                self._progresso(0)
                return
            self._preencher_tabela(dado)

        def on_error(msg):
            self.btn_parar.config(state="disabled")
            self.v_status.set("A analise terminou com erro.")
            self.log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro na analise", msg)

        self._rodar(worker, on_done, on_error, [self.btn_analisar, self.btn_gerar])

    def _preencher_tabela(self, analise) -> None:
        self.analise = analise
        self.tree.delete(*self.tree.get_children())
        for linha in analise.linhas:
            retorno = ("-" if linha.intervalo_retorno_anos is None
                       else f"{linha.intervalo_retorno_anos:.1f}".replace(".", ","))
            self.tree.insert("", "end", iid=_num(linha.limiar), values=(
                ">= " + _num(linha.limiar),
                f"{_milhar(linha.pixels_mantidos)} ({_pct(linha.pixels_mantidos_pct)})",
                f"{_milhar(linha.ocorrencias_mantidas)} ({_pct(linha.ocorrencias_mantidas_pct)})",
                retorno,
                linha.interpretacao,
            ))
        if not self.v_p_max.get().strip():
            self.v_p_max.set(_num(analise.valor_maximo))
        self.v_resumo.set(
            f"CRS: {analise.crs_texto} ({analise.unidade_crs})  |  "
            f"{analise.largura} x {analise.altura} pixels  |  "
            f"validos: {_milhar(analise.pixels_validos)}  |  "
            f"queimados: {_milhar(analise.pixels_queimados)}  |  "
            f"faixa observada: {_num(analise.valor_minimo)} a {_num(analise.valor_maximo)}"
        )
        self._progresso(100)
        self.v_status.set("Analise concluida. Defina as classes e gere as amostras.")
        self.btn_limiar.config(state="normal")
        self.btn_gerar.config(state="normal")
        self.log.add(f"=== ANALISE CONCLUIDA | {len(analise.linhas)} limiares ===")

    def _usar_limiar(self) -> None:
        if self.analise is None:
            return
        selecao = self.tree.selection()
        if not selecao:
            messagebox.showwarning("Aviso", "Selecione uma linha da tabela de limiares.")
            return
        limiar = float(selecao[0])
        abaixo = [v for v in self.analise.contagens if v < limiar]
        if not abaixo:
            messagebox.showwarning("Aviso", "Nao existe valor observado abaixo desse limiar.")
            return
        a_min, a_max = min(abaixo), max(abaixo)
        self.v_p_min.set(_num(limiar))
        self.v_p_max.set(_num(self.analise.valor_maximo))
        self.v_a_min.set(_num(a_min))
        self.v_a_max.set(_num(a_max))
        n_p = self.analise.pixels_na_faixa(limiar, self.analise.valor_maximo)
        n_a = self.analise.pixels_na_faixa(a_min, a_max)
        self.v_status.set(
            f"Presenca >= {_num(limiar)} ({_milhar(n_p)} pixels elegiveis) e "
            f"ausencia <= {_num(a_max)} ({_milhar(n_a)} pixels elegiveis)."
        )
        self.log.add(f"Limiar {_num(limiar)} aplicado as faixas.")

    # ------------------------------------------------------------------
    # Amostragem
    # ------------------------------------------------------------------

    def _gerar(self) -> None:
        try:
            parametros = self._ler_amostragem()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Parametros invalidos", str(exc))
            return

        if parametros.saida.exists() and any(parametros.saida.glob("amostras_*.csv")):
            if not messagebox.askyesno(
                "Sobrescrever",
                f"Ja existem amostras em:\n{parametros.saida}\n\nSubstituir?",
            ):
                return

        self.token = CancellationToken()
        token = self.token
        analise = self.analise
        self._progresso(0)
        self.v_status.set("Iniciando a amostragem...")
        self.log.add("=== AMOSTRAGEM INICIADA ===")
        if parametros.filtro_sdm.ativo:
            self.log.add(f"Filtro SDM ligado. Referencia: {parametros.filtro_sdm.referencia.name}")
        else:
            self.log.add("AVISO: sem raster de referencia, os pontos podem cair em "
                          "NoData dos preditores e o SDM os descartaria em silencio.")
        self.btn_parar.config(state="normal")

        def worker(q: queue.Queue):
            def progresso(valor, mensagem):
                q.put(("progress", valor))
                q.put(("status", mensagem))
            try:
                resultado = executar_amostragem(parametros, analise, progresso, token.is_cancelled)
            except OperacaoCancelada as exc:
                q.put(("done", ("cancelado", str(exc))))
                return
            q.put(("done", ("ok", resultado)))

        def on_done(payload):
            tipo, dado = payload
            self.btn_parar.config(state="disabled")
            if tipo == "cancelado":
                self.v_status.set(str(dado))
                self.log.add(f"AMOSTRAGEM CANCELADA: {dado}")
                self._progresso(0)
                return
            self._concluir(dado, parametros)

        def on_error(msg):
            self.btn_parar.config(state="disabled")
            self.v_status.set("A amostragem terminou com erro.")
            self.log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro na amostragem", msg)

        self._rodar(worker, on_done, on_error, [self.btn_analisar, self.btn_gerar])

    def _concluir(self, resultado, parametros) -> None:
        self._progresso(100)
        self.v_status.set("Amostragem concluida.")
        self.log.add(f"Presencas: {len(resultado.presenca)} de {resultado.presenca.solicitado}")
        self.log.add(f"Ausencias: {len(resultado.ausencia)} de {resultado.ausencia.solicitado}")

        estatisticas = resultado.metadados.get("estatisticas_filtro", {})
        for classe, dados in estatisticas.items():
            if dados.avaliados:
                for texto in dados.resumo():
                    self.log.add(f"  {classe}: {texto}")

        for aviso in resultado.avisos:
            self.log.add(f"AVISO: {aviso}")

        for caminho in resultado.arquivos:
            self.log.add(f"  gravado: {caminho.name}")
        self.log.add(f"=== CONCLUIDO | {len(resultado.arquivos)} arquivo(s) ===")

        total = len(resultado.presenca) + len(resultado.ausencia)
        partes = [
            f"Arquivos salvos em:\n{parametros.saida}", "",
            f"Presencas: {len(resultado.presenca)} de {resultado.presenca.solicitado}",
            f"Ausencias: {len(resultado.ausencia)} de {resultado.ausencia.solicitado}",
        ]

        # Nao afirmar que os pontos foram filtrados com sucesso quando nenhum
        # ponto saiu: a mensagem anterior dizia "caem em celula valida" mesmo
        # com 0 de 1000, o que escondia o problema em vez de apontar.
        if total == 0:
            partes.extend([
                "",
                "NENHUMA AMOSTRA FOI GERADA.",
            ])
            for classe, dados in resultado.metadados.get("estatisticas_filtro", {}).items():
                if dados.avaliados:
                    partes.append(f"  {classe}: {dados.motivo_dominante()}")
            partes.extend([
                "",
                "Sugestoes:",
                "  - use o botao 'Conferir referencia' na secao 5;",
                "  - escolha UM raster de referencia com poucos vazios",
                "    (bioclimatico ou uso do solo, nao os de distancia);",
                "  - ou desmarque as opcoes da secao 5 para amostrar sem filtro.",
            ])
        elif parametros.filtro_sdm.ativo:
            referencia = parametros.filtro_sdm.referencia
            partes.append("")
            partes.append(f"Filtrados contra a referencia {referencia.name}:")
            partes.append("caem em celula valida e nao repetem celula.")

        if resultado.avisos:
            partes.append("")
            partes.extend(f"- {a}" for a in resultado.avisos)

        if total == 0:
            messagebox.showwarning("Nenhuma amostra gerada", "\n".join(partes))
        else:
            messagebox.showinfo("Concluido", "\n".join(partes))

    # ------------------------------------------------------------------
    # Execucao em segundo plano
    # ------------------------------------------------------------------

    def _rodar(self, worker, on_done, on_error, botoes) -> None:
        """Igual ao run_background_task do GeoRaster Tools, com o acrescimo da
        mensagem de etapa ("status"), que a versao original nao encaminha."""
        import threading

        q: queue.Queue = queue.Queue()
        for botao in botoes:
            botao.config(state="disabled")

        def runner():
            try:
                worker(q)
            except Exception as exc:  # noqa: BLE001
                q.put(("fatal", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

        def liberar():
            for botao in botoes:
                botao.config(state="normal")
            if self.analise is None:
                self.btn_gerar.config(state="disabled")
                self.btn_limiar.config(state="disabled")

        def poll():
            try:
                while True:
                    tipo, carga = q.get_nowait()
                    if tipo == "log":
                        self.log.add(carga)
                    elif tipo == "progress":
                        self._progresso(carga)
                    elif tipo == "status":
                        self.v_status.set(str(carga))
                    elif tipo == "done":
                        on_done(carga)
                        liberar()
                        return
                    elif tipo == "fatal":
                        on_error(carga)
                        liberar()
                        return
            except queue.Empty:
                pass
            self.root.after(100, poll)

        self.root.after(100, poll)


def build_tab(app, frame: ttk.Frame) -> AbaAmostragemFogo:
    """Ponto de entrada usado por gui.py."""
    return AbaAmostragemFogo(app, frame)
