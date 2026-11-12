"""Interface grafica Tkinter da V5.

Cuidados que a V3 nao tomava:

* todos os campos sao lidos e validados **antes** de iniciar a thread, e o
  worker so recebe um objeto imutavel de parametros; a V3 lia widgets de
  dentro da thread e no fim da amostragem, o que quebrava se o usuario
  mexesse na tela durante o processamento;
* a analise fica ligada ao raster, banda e exclusoes que a produziram; trocar
  qualquer um deles invalida a analise em vez de gerar amostras incoerentes;
* os erros vao para um arquivo de log, porque quem executa por duplo clique
  nao tem terminal para ler o traceback;
* o progresso e limitado a uma atualizacao a cada 0,5 %, em vez de uma por
  bloco lido.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_TITLE, __version__
from .analise import OperacaoCancelada, ResultadoAnalise
from .config import (
    COORDENADAS_ORIGINAL,
    COORDENADAS_WGS84,
    GRUPOS_DE_SAIDA,
    SAIDAS_DISPONIVEIS,
    Faixa,
    ParametrosAmostragem,
    ParametrosAnalise,
    SelecaoDeSaidas,
    texto_para_conjunto,
    texto_para_float,
    texto_para_int,
)
from .pipeline import ResultadoExecucao, executar_amostragem, executar_analise

ARQUIVO_LOG = "amostragem_fogo.log"


def _milhar(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def _percentual(valor: float) -> str:
    return f"{valor:.2f}%".replace(".", ",")


def _numero(valor: float) -> str:
    if abs(valor - round(valor)) < 1e-9:
        return str(int(round(valor)))
    return f"{valor:.6g}"


class Aplicacao(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} ({__version__})")
        self.geometry("1380x940")
        self.minsize(1120, 780)

        self.analise: ResultadoAnalise | None = None
        self.eventos: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancelamento = threading.Event()
        self.worker: threading.Thread | None = None

        self.var_raster = tk.StringVar()
        self.var_saida = tk.StringVar()
        self.var_banda = tk.StringVar(value="1")
        self.var_anos = tk.StringVar(value="41")
        self.var_excluir = tk.StringVar(value="")
        self.var_p_min = tk.StringVar(value="4")
        self.var_p_max = tk.StringVar(value="")
        self.var_a_min = tk.StringVar(value="0")
        self.var_a_max = tk.StringVar(value="3")
        self.var_n_p = tk.StringVar(value="1000")
        self.var_n_a = tk.StringVar(value="1000")
        self.var_distancia = tk.StringVar(value="1000")
        self.var_semente = tk.StringVar(value="42")
        self.var_entre_classes = tk.BooleanVar(value=True)
        self.var_decimal = tk.StringVar(value=".")
        self.var_coordenadas = tk.StringVar(value=COORDENADAS_WGS84)
        self.var_valor_presenca = tk.StringVar(value="1")
        self.var_valor_ausencia = tk.StringVar(value="0")
        self.vars_saidas = {
            nome: tk.BooleanVar(value=True) for nome, _ in SAIDAS_DISPONIVEIS
        }
        self.var_progresso = tk.DoubleVar(value=0)
        self.var_percentual = tk.StringVar(value="0%")
        self.var_status = tk.StringVar(value="Selecione um raster para comecar.")
        self.var_resumo = tk.StringVar(value="")

        for variavel in (self.var_raster, self.var_banda, self.var_excluir, self.var_anos):
            variavel.trace_add("write", lambda *_: self._invalidar_analise())

        self._estilo()
        self._construir()
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.after(100, self._processar_eventos)

    # -- montagem -----------------------------------------------------------

    def _estilo(self) -> None:
        estilo = ttk.Style(self)
        if "vista" in estilo.theme_names():
            estilo.theme_use("vista")
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 16, "bold"))
        estilo.configure("Secao.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        estilo.configure("Treeview", rowheight=25, font=("Segoe UI", 9))
        estilo.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        estilo.configure("Acao.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        estilo.configure("Aviso.TLabel", foreground="#a03000")

    def _montar_rodape(self, pai: ttk.Frame) -> None:
        """Barra de progresso, percentual e mensagem da etapa em execucao.

        Ancorado com side="bottom" antes de tudo: no pack do Tk o espaco e
        distribuido na ordem em que os widgets sao empacotados, e foi assim que
        a barra deixou de ser mapeada quando a secao 6 cresceu.
        """
        rodape = ttk.Frame(pai)
        rodape.pack(side="bottom", fill="x", pady=(10, 0))
        linha = ttk.Frame(rodape)
        linha.pack(fill="x")
        self.barra_progresso = ttk.Progressbar(
            linha, variable=self.var_progresso, maximum=100, mode="determinate"
        )
        self.barra_progresso.pack(side="left", fill="x", expand=True)
        ttk.Label(
            linha, textvariable=self.var_percentual, width=7, anchor="e",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right", padx=(8, 0))
        ttk.Label(rodape, textvariable=self.var_status, anchor="w").pack(
            fill="x", pady=(4, 0)
        )

    def _rolar_com_a_roda(self, evento) -> None:
        """Roda do mouse rola a pagina, menos quando esta sobre a tabela."""
        widget = evento.widget
        while widget is not None:
            if widget is getattr(self, "tabela", None):
                return  # a Treeview tem a propria rolagem
            widget = getattr(widget, "master", None)
        self._canvas.yview_scroll(-1 if evento.delta > 0 else 1, "units")

    def _construir(self) -> None:
        externo = ttk.Frame(self, padding=14)
        externo.pack(fill="both", expand=True)

        # O rodape com a barra de progresso e criado PRIMEIRO e ancorado ao
        # fundo, para nunca perder espaco para o resto da tela. O conteudo fica
        # num canvas rolavel: as secoes 1 a 6 somam mais de 600 px fixos e, num
        # notebook de 1366x768, sem rolagem a tabela de limiares ficaria cortada.
        self._montar_rodape(externo)

        canvas = tk.Canvas(externo, highlightthickness=0, borderwidth=0)
        rolagem = ttk.Scrollbar(externo, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=rolagem.set)
        rolagem.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        raiz = ttk.Frame(canvas)
        janela = canvas.create_window((0, 0), window=raiz, anchor="nw")
        raiz.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(janela, width=e.width))
        self._canvas = canvas
        self.bind_all("<MouseWheel>", self._rolar_com_a_roda)

        ttk.Label(raiz, text=APP_TITLE, style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            raiz,
            text=(
                "Analisa a distribuicao das frequencias e sorteia presencas e ausencias "
                "com distancia minima em metros reais, sem reprojetar o raster."
            ),
        ).pack(anchor="w", pady=(2, 10))

        # 1. Arquivos
        arquivos = ttk.LabelFrame(raiz, text="1. Arquivos", style="Secao.TLabelframe", padding=10)
        arquivos.pack(fill="x")
        arquivos.columnconfigure(1, weight=1)

        ttk.Label(arquivos, text="Raster de frequencia:").grid(row=0, column=0, sticky="w")
        ttk.Entry(arquivos, textvariable=self.var_raster).grid(row=0, column=1, padx=8, sticky="ew")
        ttk.Button(
            arquivos, text="LOCALIZAR ARQUIVO RASTER...", command=self._escolher_raster,
            style="Acao.TButton",
        ).grid(row=0, column=2)

        ttk.Label(arquivos, text="Pasta de saida:").grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Entry(arquivos, textvariable=self.var_saida).grid(
            row=1, column=1, padx=8, pady=(8, 0), sticky="ew"
        )
        ttk.Button(
            arquivos, text="SELECIONAR PASTA DE SALVAMENTO...", command=self._escolher_saida,
            style="Acao.TButton",
        ).grid(row=1, column=2, pady=(8, 0))

        # 2 a 4
        painel = ttk.Frame(raiz)
        painel.pack(fill="x", pady=10)
        for coluna in range(4):
            painel.columnconfigure(coluna, weight=1)

        analise = ttk.LabelFrame(painel, text="2. Analise", style="Secao.TLabelframe", padding=10)
        analise.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self._campo(analise, "Banda do raster", self.var_banda, 0)
        self._campo(analise, "Anos observados", self.var_anos, 1)
        self._campo(analise, "Valores a excluir (ex.: 0; 99)", self.var_excluir, 2)
        ttk.Button(
            analise, text="INICIAR ANALISE DA FREQUENCIA", command=self._iniciar_analise,
            style="Acao.TButton",
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(9, 0))

        classes = ttk.LabelFrame(
            painel, text="3. Classes para amostragem", style="Secao.TLabelframe", padding=10
        )
        classes.grid(row=0, column=1, sticky="nsew", padx=5)
        self._campo(classes, "Presenca - minimo", self.var_p_min, 0)
        self._campo(classes, "Presenca - maximo", self.var_p_max, 1)
        self._campo(classes, "Ausencia - minimo", self.var_a_min, 2)
        self._campo(classes, "Ausencia - maximo", self.var_a_max, 3)

        amostragem = ttk.LabelFrame(
            painel, text="4. Amostragem", style="Secao.TLabelframe", padding=10
        )
        amostragem.grid(row=0, column=2, sticky="nsew", padx=5)
        self._campo(amostragem, "N de presencas", self.var_n_p, 0)
        self._campo(amostragem, "N de ausencias", self.var_n_a, 1)
        self._campo(amostragem, "Distancia minima (m)", self.var_distancia, 2)
        self._campo(amostragem, "Semente aleatoria", self.var_semente, 3)
        ttk.Checkbutton(
            amostragem, text="Aplicar distancia tambem entre classes",
            variable=self.var_entre_classes,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))

        formato = ttk.LabelFrame(
            painel, text="5. Formato", style="Secao.TLabelframe", padding=10
        )
        formato.grid(row=0, column=3, sticky="nsew", padx=(5, 0))

        ttk.Label(formato, text="Colunas x,y das amostras:").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            formato, text="Longitude e latitude (WGS84)",
            variable=self.var_coordenadas, value=COORDENADAS_WGS84,
        ).grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(
            formato, text="CRS original do raster",
            variable=self.var_coordenadas, value=COORDENADAS_ORIGINAL,
        ).grid(row=2, column=0, sticky="w")

        ttk.Label(formato, text="Codigo da coluna Value:").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )
        codigos = ttk.Frame(formato)
        codigos.grid(row=4, column=0, sticky="w")
        ttk.Label(codigos, text="presenca").grid(row=0, column=0, sticky="w")
        ttk.Entry(codigos, textvariable=self.var_valor_presenca, width=5).grid(
            row=0, column=1, padx=(6, 12)
        )
        ttk.Label(codigos, text="ausencia").grid(row=0, column=2, sticky="w")
        ttk.Entry(codigos, textvariable=self.var_valor_ausencia, width=5).grid(
            row=0, column=3, padx=(6, 0)
        )

        ttk.Label(formato, text="Separador decimal das demais tabelas:").grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Radiobutton(
            formato, text="Ponto  -  QGIS, R, Python", variable=self.var_decimal, value=".",
        ).grid(row=6, column=0, sticky="w")
        ttk.Radiobutton(
            formato, text="Virgula  -  Excel em portugues", variable=self.var_decimal, value=",",
        ).grid(row=7, column=0, sticky="w")

        # 6. Arquivos de saida
        arquivos_saida = ttk.LabelFrame(
            raiz, text="6. Arquivos de saida a salvar", style="Secao.TLabelframe", padding=10
        )
        arquivos_saida.pack(fill="x", pady=(0, 10))
        for coluna in range(len(GRUPOS_DE_SAIDA)):
            arquivos_saida.columnconfigure(coluna, weight=1)

        for coluna, (titulo, itens) in enumerate(GRUPOS_DE_SAIDA):
            grupo = ttk.Frame(arquivos_saida)
            grupo.grid(row=0, column=coluna, sticky="nw", padx=(0, 18))
            ttk.Label(grupo, text=titulo, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            for nome, rotulo in itens:
                ttk.Checkbutton(
                    grupo, text=rotulo, variable=self.vars_saidas[nome]
                ).pack(anchor="w")

        botoes_saida = ttk.Frame(arquivos_saida)
        botoes_saida.grid(
            row=1, column=0, columnspan=len(GRUPOS_DE_SAIDA), sticky="w", pady=(8, 0)
        )
        for texto, largura, acao in (
            ("Marcar todas", 14, lambda: self._marcar_saidas(True)),
            ("Desmarcar todas", 16, lambda: self._marcar_saidas(False)),
            ("So presencas", 14, self._somente_presencas),
            ("So ausencias", 14, self._somente_ausencias),
        ):
            ttk.Button(botoes_saida, text=texto, width=largura, command=acao).pack(
                side="left", padx=(0, 6)
            )
        ttk.Label(
            botoes_saida,
            text=(
                "   Classe e formato se combinam: marcar so Presencas e so x,y,Value "
                "grava apenas amostras_presencas.csv."
            ),
            foreground="#40506a",
        ).pack(side="left")

        # Acoes
        acoes = ttk.Frame(raiz)
        acoes.pack(fill="x", pady=(0, 10))
        self.btn_exportar = ttk.Button(
            acoes, text="Exportar tabela de limiares", command=self._exportar_limiares,
            state="disabled",
        )
        self.btn_exportar.pack(side="left")
        self.btn_limiar = ttk.Button(
            acoes, text="Usar limiar selecionado", command=self._usar_limiar, state="disabled",
        )
        self.btn_limiar.pack(side="left", padx=(8, 0))
        self.btn_amostrar = ttk.Button(
            acoes, text="GERAR E SALVAR AMOSTRAS", command=self._iniciar_amostragem,
            state="disabled", style="Acao.TButton",
        )
        self.btn_amostrar.pack(side="left", padx=8)
        self.btn_parar = ttk.Button(
            acoes, text="PARAR PROCESSAMENTO", command=self._pedir_parada, state="disabled",
            style="Acao.TButton",
        )
        self.btn_parar.pack(side="left")
        ttk.Button(acoes, text="Abrir pasta de saida", command=self._abrir_saida).pack(side="right")

        ttk.Label(raiz, textvariable=self.var_resumo, wraplength=1280).pack(anchor="w", pady=(0, 7))

        # Tabela
        tabela = ttk.Frame(raiz)
        tabela.pack(fill="both", expand=True, pady=(0, 4))
        colunas = ("limiar", "pixels", "ocorrencias", "retorno", "interpretacao")
        self.tabela = ttk.Treeview(tabela, columns=colunas, show="headings", height=12)
        for coluna, titulo, largura, ancora in (
            ("limiar", "Limiar", 80, "center"),
            ("pixels", "Pixels queimados mantidos", 210, "center"),
            ("ocorrencias", "Ocorrencias mantidas", 205, "center"),
            ("retorno", "Retorno medio (anos)", 145, "center"),
            ("interpretacao", "Interpretacao", 600, "w"),
        ):
            self.tabela.heading(coluna, text=titulo)
            self.tabela.column(coluna, width=largura, anchor=ancora, stretch=(coluna == "interpretacao"))
        barra = ttk.Scrollbar(tabela, orient="vertical", command=self.tabela.yview)
        self.tabela.configure(yscrollcommand=barra.set)
        self.tabela.bind("<Double-1>", lambda _e: self._usar_limiar())
        self.tabela.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
    @staticmethod
    def _campo(pai, rotulo: str, variavel: tk.StringVar, linha: int) -> None:
        ttk.Label(pai, text=rotulo + ":").grid(row=linha, column=0, sticky="w", pady=2)
        ttk.Entry(pai, textvariable=variavel, width=19).grid(
            row=linha, column=1, sticky="ew", padx=(8, 0), pady=2
        )
        pai.columnconfigure(1, weight=1)

    # -- acoes de arquivo ---------------------------------------------------

    def _escolher_raster(self) -> None:
        caminho = filedialog.askopenfilename(
            title="Selecione o raster de frequencia",
            filetypes=[("Raster GeoTIFF", "*.tif *.tiff"), ("Todos os arquivos", "*.*")],
        )
        if caminho:
            self.var_raster.set(caminho)
            if not self.var_saida.get().strip():
                self.var_saida.set(str(Path(caminho).parent / "amostras_fogo"))

    def _escolher_saida(self) -> None:
        caminho = filedialog.askdirectory(title="Selecione a pasta de saida")
        if caminho:
            self.var_saida.set(caminho)

    def _abrir_saida(self) -> None:
        texto = self.var_saida.get().strip()
        if not texto:
            messagebox.showwarning(APP_TITLE, "Selecione primeiro a pasta de saida.")
            return
        pasta = Path(texto)
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(pasta)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(pasta)], check=False)
            else:
                subprocess.run(["xdg-open", str(pasta)], check=False)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Nao foi possivel abrir a pasta:\n{exc}")

    # -- leitura dos campos -------------------------------------------------

    def _ler_parametros_analise(self) -> ParametrosAnalise:
        caminho = self.var_raster.get().strip()
        if not caminho:
            raise ValueError("Selecione o arquivo raster de frequencia.")
        return ParametrosAnalise(
            raster=Path(caminho),
            banda=texto_para_int(self.var_banda.get(), "Banda", minimo=1),
            anos_observados=texto_para_int(self.var_anos.get(), "Anos observados", minimo=1),
            valores_excluidos=texto_para_conjunto(self.var_excluir.get()),
        )

    def _ler_parametros_amostragem(self) -> ParametrosAmostragem:
        if self.analise is None:
            raise ValueError("Analise o raster antes de gerar as amostras.")
        par_analise = self._ler_parametros_analise()
        if par_analise.assinatura_esperada() != self.analise.assinatura():
            raise ValueError(
                "O raster, a banda ou a lista de exclusoes mudaram depois da analise.\n"
                "Clique novamente em INICIAR ANALISE DA FREQUENCIA antes de amostrar."
            )

        texto_saida = self.var_saida.get().strip()
        if not texto_saida:
            raise ValueError("Selecione a pasta de saida.")

        p_min = texto_para_float(self.var_p_min.get(), "Presenca - minimo")
        p_max = texto_para_float(self.var_p_max.get(), "Presenca - maximo", vazio_permitido=True)
        if p_max is None:
            p_max = self.analise.valor_maximo
        a_min = texto_para_float(self.var_a_min.get(), "Ausencia - minimo")
        a_max = texto_para_float(self.var_a_max.get(), "Ausencia - maximo")

        return ParametrosAmostragem(
            analise=par_analise,
            saida=Path(texto_saida),
            presenca=Faixa(float(p_min), float(p_max)),
            ausencia=Faixa(float(a_min), float(a_max)),
            n_presenca=texto_para_int(self.var_n_p.get(), "N de presencas", minimo=0),
            n_ausencia=texto_para_int(self.var_n_a.get(), "N de ausencias", minimo=0),
            distancia_minima_m=float(
                texto_para_float(self.var_distancia.get(), "Distancia minima")
            ),
            distancia_entre_classes=bool(self.var_entre_classes.get()),
            semente=texto_para_int(self.var_semente.get(), "Semente", minimo=0),
            separador_decimal=self.var_decimal.get(),
            saidas=self._ler_selecao_de_saidas(),
            coordenadas_xy=self.var_coordenadas.get(),
            valor_presenca=texto_para_int(
                self.var_valor_presenca.get(), "Codigo da presenca", minimo=-(10**9)
            ),
            valor_ausencia=texto_para_int(
                self.var_valor_ausencia.get(), "Codigo da ausencia", minimo=-(10**9)
            ),
        )

    def _ler_selecao_de_saidas(self) -> SelecaoDeSaidas:
        marcadas = [nome for nome, var in self.vars_saidas.items() if bool(var.get())]
        if not marcadas:
            raise ValueError(
                "Nenhum arquivo de saida esta marcado na secao 6.\n"
                "Marque pelo menos uma caixa antes de gerar as amostras."
            )
        return SelecaoDeSaidas.de_nomes(marcadas)

    def _marcar_saidas(self, ligado: bool) -> None:
        for var in self.vars_saidas.values():
            var.set(ligado)

    def _aplicar_selecao(self, selecao: SelecaoDeSaidas) -> None:
        ligadas = set(selecao.ligadas())
        for nome, var in self.vars_saidas.items():
            var.set(nome in ligadas)

    def _somente_presencas(self) -> None:
        self._aplicar_selecao(SelecaoDeSaidas.somente_presencas())

    def _somente_ausencias(self) -> None:
        self._aplicar_selecao(SelecaoDeSaidas.somente_ausencias())

    # -- execucao -----------------------------------------------------------

    def _ocupado(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _definir_ocupado(self, ocupado: bool) -> None:
        self.btn_parar.configure(state="normal" if ocupado else "disabled")
        estado = "disabled" if ocupado or self.analise is None else "normal"
        self.btn_exportar.configure(state=estado)
        self.btn_limiar.configure(state=estado)
        self.btn_amostrar.configure(state=estado)

    def _iniciar_analise(self) -> None:
        if self._ocupado():
            return
        try:
            parametros = self._ler_parametros_analise()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self._preparar_execucao("Iniciando a analise...")

        def tarefa() -> None:
            try:
                resultado = executar_analise(
                    parametros, self._enviar_progresso, self.cancelamento.is_set
                )
                self.eventos.put(("analise_ok", resultado))
            except OperacaoCancelada as exc:
                self.eventos.put(("cancelado", str(exc)))
            except Exception as exc:
                self.eventos.put(("erro", (str(exc), traceback.format_exc())))

        self.worker = threading.Thread(target=tarefa, daemon=True)
        self.worker.start()

    def _iniciar_amostragem(self) -> None:
        if self._ocupado():
            return
        try:
            parametros = self._ler_parametros_amostragem()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        if parametros.saida.exists() and any(parametros.saida.glob("amostras_*.csv")):
            if not messagebox.askyesno(
                APP_TITLE,
                f"Ja existem arquivos de amostras em:\n{parametros.saida}\n\n"
                "Eles serao substituidos. Deseja continuar?",
            ):
                return

        analise = self.analise
        self._preparar_execucao("Iniciando a amostragem...")

        def tarefa() -> None:
            try:
                resultado = executar_amostragem(
                    parametros, analise, self._enviar_progresso, self.cancelamento.is_set
                )
                self.eventos.put(("amostragem_ok", resultado))
            except OperacaoCancelada as exc:
                self.eventos.put(("cancelado", str(exc)))
            except Exception as exc:
                self.eventos.put(("erro", (str(exc), traceback.format_exc())))

        self.worker = threading.Thread(target=tarefa, daemon=True)
        self.worker.start()

    def _preparar_execucao(self, mensagem: str) -> None:
        self.cancelamento.clear()
        self.var_progresso.set(0)
        self.var_percentual.set("0%")
        self.var_status.set(mensagem)
        self._definir_ocupado(True)

    def _pedir_parada(self) -> None:
        self.cancelamento.set()
        self.var_status.set("Parada solicitada. Aguarde o fim da leitura em andamento...")
        self.btn_parar.configure(state="disabled")

    def _enviar_progresso(self, valor: float, mensagem: str) -> None:
        self.eventos.put(("progresso", (valor, mensagem)))

    # -- fila de eventos ----------------------------------------------------

    def _processar_eventos(self) -> None:
        try:
            while True:
                evento, carga = self.eventos.get_nowait()
                if evento == "progresso":
                    valor, mensagem = carga  # type: ignore[misc]
                    self.var_progresso.set(float(valor))
                    self.var_percentual.set(f"{float(valor):.0f}%")
                    self.var_status.set(str(mensagem))
                elif evento == "analise_ok":
                    self._concluir_analise(carga)  # type: ignore[arg-type]
                elif evento == "amostragem_ok":
                    self._concluir_amostragem(carga)  # type: ignore[arg-type]
                elif evento == "cancelado":
                    self.var_status.set(str(carga))
                    self.var_progresso.set(0)
                    self.var_percentual.set("0%")
                    self._definir_ocupado(False)
                elif evento == "erro":
                    self._mostrar_erro(carga)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        self.after(100, self._processar_eventos)

    def _invalidar_analise(self) -> None:
        if self.analise is None or self._ocupado():
            return
        self.analise = None
        self.tabela.delete(*self.tabela.get_children())
        self.var_resumo.set("")
        self.var_status.set("Os parametros mudaram. Rode a analise novamente.")
        self._definir_ocupado(False)

    def _concluir_analise(self, analise: ResultadoAnalise) -> None:
        self.analise = analise
        self.tabela.delete(*self.tabela.get_children())
        for linha in analise.linhas:
            retorno = (
                f"{linha.intervalo_retorno_anos:.1f}".replace(".", ",")
                if linha.intervalo_retorno_anos is not None
                else "-"
            )
            self.tabela.insert(
                "", "end", iid=_numero(linha.limiar),
                values=(
                    ">= " + _numero(linha.limiar),
                    f"{_milhar(linha.pixels_mantidos)} ({_percentual(linha.pixels_mantidos_pct)})",
                    f"{_milhar(linha.ocorrencias_mantidas)} ({_percentual(linha.ocorrencias_mantidas_pct)})",
                    retorno,
                    linha.interpretacao,
                ),
            )

        if not self.var_p_max.get().strip():
            self.var_p_max.set(_numero(analise.valor_maximo))

        self.var_resumo.set(
            f"CRS: {analise.crs_texto}  ({analise.unidade_crs})  |  "
            f"Dimensoes: {analise.largura} x {analise.altura}  |  "
            f"Pixels validos: {_milhar(analise.pixels_validos)}  |  "
            f"Pixels queimados: {_milhar(analise.pixels_queimados)}  |  "
            f"Ocorrencias: {_milhar(analise.ocorrencias_totais)}  |  "
            f"Faixa observada: {_numero(analise.valor_minimo)} a {_numero(analise.valor_maximo)}"
        )
        self.var_progresso.set(100)
        self.var_percentual.set("100%")
        self.var_status.set("Analise concluida. Defina as classes e gere as amostras.")
        self._definir_ocupado(False)

    def _concluir_amostragem(self, resultado: ResultadoExecucao) -> None:
        self._definir_ocupado(False)
        self.var_status.set("Amostragem concluida.")
        pasta = resultado.arquivos[0].parent if resultado.arquivos else Path()

        partes = [
            f"Arquivos salvos em:\n{pasta}",
            "",
            f"Presencas: {len(resultado.presenca)} de {resultado.presenca.solicitado}",
            f"Ausencias: {len(resultado.ausencia)} de {resultado.ausencia.solicitado}",
        ]
        modo = resultado.metadados.get("modo_metrico")
        if modo is not None:
            partes.append(f"Distancia medida por: {getattr(modo, 'descricao', '')}")
        if resultado.avisos:
            partes.extend(["", "ATENCAO:"])
            partes.extend(f"- {aviso}" for aviso in resultado.avisos)

        nomes = sorted(caminho.name for caminho in resultado.arquivos)
        partes.extend(["", f"{len(nomes)} arquivos gravados:"])
        partes.extend(f"  {nome}" for nome in nomes)

        if any(nome.endswith(".geojson") for nome in nomes):
            partes.extend(
                [
                    "",
                    "Para levar os pontos ao QGIS sem risco de erro de coordenada,",
                    "arraste o arquivo .geojson.",
                ]
            )
        if any(caminho.name == "LEIA-ME_SAIDA.txt" for caminho in resultado.arquivos):
            partes.append("O LEIA-ME_SAIDA.txt explica como abrir cada arquivo.")
        messagebox.showinfo(APP_TITLE, "\n".join(partes))

    def _mostrar_erro(self, carga: tuple[str, str]) -> None:
        mensagem, detalhes = carga
        self._definir_ocupado(False)
        self.var_status.set("A operacao terminou com erro.")
        caminho_log = self._gravar_log(detalhes)
        complemento = f"\n\nDetalhes tecnicos gravados em:\n{caminho_log}" if caminho_log else ""
        messagebox.showerror(APP_TITLE, f"{mensagem}{complemento}")

    def _gravar_log(self, detalhes: str) -> Path | None:
        destinos = []
        texto_saida = self.var_saida.get().strip()
        if texto_saida:
            destinos.append(Path(texto_saida))
        destinos.append(Path.home())
        for pasta in destinos:
            try:
                pasta.mkdir(parents=True, exist_ok=True)
                caminho = pasta / ARQUIVO_LOG
                with caminho.open("a", encoding="utf-8") as arquivo:
                    arquivo.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}\n{detalhes}\n")
                return caminho
            except OSError:
                continue
        return None

    # -- tabela de limiares -------------------------------------------------

    def _exportar_limiares(self) -> None:
        if self.analise is None:
            return
        destino = filedialog.asksaveasfilename(
            title="Salvar tabela de limiares", defaultextension=".csv",
            initialfile="tabela_limiares_frequencia.csv", filetypes=[("CSV", "*.csv")],
        )
        if not destino:
            return
        from .exportacao import escrever_csv_limiares

        escrever_csv_limiares(Path(destino), self.analise, self.var_decimal.get())
        messagebox.showinfo(APP_TITLE, f"Tabela salva em:\n{destino}")

    def _usar_limiar(self) -> None:
        if self.analise is None:
            return
        selecao = self.tabela.selection()
        if not selecao:
            messagebox.showwarning(APP_TITLE, "Selecione uma linha da tabela de limiares.")
            return
        limiar = float(selecao[0])
        abaixo = [valor for valor in self.analise.contagens if valor < limiar]
        if not abaixo:
            messagebox.showwarning(
                APP_TITLE,
                "Nao existe nenhum valor observado abaixo desse limiar para formar a ausencia.",
            )
            return
        a_min, a_max = min(abaixo), max(abaixo)
        self.var_p_min.set(_numero(limiar))
        self.var_p_max.set(_numero(self.analise.valor_maximo))
        self.var_a_min.set(_numero(a_min))
        self.var_a_max.set(_numero(a_max))

        # Mostrar quantos pixels cada classe tem evita descobrir so no fim que
        # a quantidade pedida era maior do que a faixa comporta.
        n_presenca = self.analise.pixels_na_faixa(limiar, self.analise.valor_maximo)
        n_ausencia = self.analise.pixels_na_faixa(a_min, a_max)
        self.var_status.set(
            f"Faixas atualizadas: presenca >= {_numero(limiar)} "
            f"({_milhar(n_presenca)} pixels elegiveis) e ausencia <= {_numero(a_max)} "
            f"({_milhar(n_ausencia)} pixels elegiveis)."
        )

    # -- encerramento -------------------------------------------------------

    def _fechar(self) -> None:
        if self._ocupado():
            if not messagebox.askyesno(
                APP_TITLE, "Ha um processamento em andamento. Deseja encerrar mesmo assim?"
            ):
                return
            self.cancelamento.set()
        self.destroy()


def main() -> int:
    try:
        aplicacao = Aplicacao()
    except tk.TclError as exc:
        print(f"Nao foi possivel abrir a interface grafica: {exc}", file=sys.stderr)
        return 1
    aplicacao.mainloop()
    return 0
