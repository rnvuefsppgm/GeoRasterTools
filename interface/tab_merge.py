"""
tab_merge.py
============

Aba "Merge de Rasters" do GeoRaster Tools V3.

A conta esta em ``merge_ops.py``; aqui so fica a interface. O fluxo que a aba
propoe e o mesmo das outras: escolher a pasta, conferir o que foi detectado,
ajustar e executar -- com barra de progresso e log identicos aos das demais
abas.

Duas decisoes de interface que valem registro:

* **a pasta e lida sozinha.** Ao escolher a pasta, a aba ja mostra apenas os
  conjuntos que precisam de merge (dois ou mais blocos com a mesma base). Os
  arquivos avulsos nem aparecem, que e o pedido: nao obrigar o usuario a
  garimpar quais dos 40 .tif da pasta formam mosaico;
* **mas nada e obrigatorio.** Cada conjunto e cada bloco tem uma coluna
  "Usar" que liga/desliga com um clique, o nome de saida e editavel com duplo
  clique, e o botao "Selecionar arquivos manualmente" aceita qualquer
  combinacao de arquivos, inclusive de nomes que nao seguem padrao nenhum.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from nucleo import merge_ops
from nucleo.utils import CancellationToken, CancelledError, list_tif_files

COLUNAS_GRUPOS = ("Usar", "Conjunto", "Blocos", "Deteccao", "Arquivo de saida")
COLUNAS_BLOCOS = ("Usar", "Arquivo", "Dimensoes", "Bandas", "Tipo", "NoData")

MARCADO = "[x]"
DESMARCADO = "[ ]"


class AbaMergeRaster:
    """Constroi e opera a aba. Instanciada uma vez por aplicacao."""

    def __init__(self, app, frame: ttk.Frame):
        self.app = app
        self.root = app.root
        self.frame = frame
        self.grupos: List[Dict] = []
        self.token: Optional[CancellationToken] = None
        self._infos: Dict[str, merge_ops.InfoDoBloco] = {}

        self._criar_variaveis()
        self._construir()

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def _criar_variaveis(self) -> None:
        self.v_entrada = tk.StringVar()
        self.v_saida = tk.StringVar()
        self.v_prefixo = tk.StringVar(value=merge_ops.PREFIXO_PADRAO)
        self.v_sobrescrever = tk.BooleanVar(value=True)
        self.v_memoria = tk.StringVar(value=f"{merge_ops.MEMORIA_POR_FAIXA_MB:.0f}")
        self.v_progresso = tk.DoubleVar(value=0.0)
        self.v_pct = tk.StringVar(value="0%")
        self.v_status = tk.StringVar(value="Escolha a pasta dos blocos para comecar.")
        self.v_resumo = tk.StringVar(value="")
        self.v_prefixo.trace_add("write", lambda *_: self._renomear_saidas())

    # ------------------------------------------------------------------
    # Construcao da interface
    # ------------------------------------------------------------------

    def _construir(self) -> None:
        from interface.gui import LogPanel, path_row, quadro_rolavel, select_directory

        # A direita entra primeiro para o Tk reservar o espaco dela; a coluna
        # da esquerda e rolavel porque, em 1150x720 (tamanho minimo), as
        # quatro secoes nao cabem na altura da janela.
        direita = ttk.Frame(self.frame)
        direita.pack(side="right", fill="both", expand=True, padx=(4, 8), pady=8)
        esquerda = quadro_rolavel(self.frame, largura=400)

        # ---- 1. Entrada --------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="1. Blocos a unir")
        box.pack(fill="x", pady=4)
        path_row(box, "Pasta dos rasters (.tif)", "dir",
                 lambda lbl: (select_directory(self.v_entrada, lbl, "Selecionar pasta dos blocos"),
                              self._ao_escolher_pasta()))
        ttk.Label(
            box, wraplength=360, justify="left", foreground="#40506a", font=("Segoe UI", 8),
            text=("A aba carrega sozinha apenas os arquivos que precisam de merge: "
                  "dois ou mais blocos com o mesmo nome antes do sufixo numerico "
                  "(ex.: reclass_ndvi_stdDev_tau-0000046592-0000000000.tif). "
                  "Arquivos avulsos sao ignorados."),
        ).pack(anchor="w", padx=4, pady=(0, 4))
        self.btn_reescanear = ttk.Button(box, text="Reescanear pasta", command=self._escanear)
        self.btn_reescanear.pack(fill="x", padx=4, pady=2)
        self.btn_manual = ttk.Button(box, text="Selecionar arquivos manualmente...",
                                      command=self._selecionar_manual)
        self.btn_manual.pack(fill="x", padx=4, pady=(0, 4))

        # ---- 2. Saida ----------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="2. Saida")
        box.pack(fill="x", pady=4)
        _, self.lbl_saida = path_row(
            box, "Pasta de saida", "dir",
            lambda lbl: select_directory(self.v_saida, lbl, "Selecionar pasta de saida"))
        ttk.Label(box, text="(padrao: a mesma pasta dos blocos)", foreground="#6c757d",
                  font=("Segoe UI", 8)).pack(anchor="w", padx=4)
        ttk.Label(box, text="Prefixo do arquivo de saida:").pack(anchor="w", padx=4, pady=(6, 0))
        ttk.Entry(box, textvariable=self.v_prefixo).pack(fill="x", padx=4, pady=2)
        ttk.Label(box, wraplength=360, justify="left", foreground="#40506a",
                  font=("Segoe UI", 8),
                  text=("O nome sai como prefixo + nome do conjunto, ex.: "
                        "Merge_reclass_ndvi_stdDev_tau.tif. Duplo clique na coluna "
                        "\"Arquivo de saida\" edita o nome de um conjunto."),
                  ).pack(anchor="w", padx=4, pady=(0, 4))

        # ---- 3. Opcoes ---------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="3. Opcoes")
        box.pack(fill="x", pady=4)
        ttk.Checkbutton(box, text="Sobrescrever arquivos existentes",
                        variable=self.v_sobrescrever).pack(anchor="w", padx=4, pady=2)
        linha = ttk.Frame(box)
        linha.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Label(linha, text="Memoria por faixa (MB):").pack(side="left")
        ttk.Entry(linha, textvariable=self.v_memoria, width=7).pack(side="left", padx=4)
        ttk.Label(box, wraplength=360, justify="left", foreground="#40506a",
                  font=("Segoe UI", 8),
                  text=("O mosaico e montado em faixas de linhas, entao mosaicos de "
                        "varios GB cabem na memoria. Aumente so se a maquina tiver "
                        "folga."),
                  ).pack(anchor="w", padx=4, pady=(0, 4))

        # ---- 4. Executar --------------------------------------------------
        box = ttk.LabelFrame(esquerda, text="4. Executar")
        box.pack(fill="x", pady=4)
        self.btn_executar = ttk.Button(box, text="EXECUTAR MERGE", command=self._executar)
        self.btn_executar.pack(fill="x", padx=4, pady=(4, 2))
        self.btn_parar = ttk.Button(box, text="PARAR PROCESSAMENTO",
                                     command=self._parar, state="disabled")
        self.btn_parar.pack(fill="x", padx=4, pady=2)
        ttk.Button(box, text="Abrir pasta de saida", command=self._abrir_saida).pack(
            fill="x", padx=4, pady=2)
        ttk.Button(box, text="Limpar Log", command=lambda: self.log.clear()).pack(
            fill="x", padx=4, pady=(2, 4))

        # ================= coluna direita =================
        ttk.Label(direita, textvariable=self.v_resumo, wraplength=760,
                  foreground="#2c3e50").pack(anchor="w", pady=(0, 4))

        # Rodape ancorado antes das tabelas: no pack do Tk o espaco e
        # distribuido na ordem de empacotamento e a barra ficaria sem espaco
        # em telas menores (mesmo motivo da aba de amostragem).
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

        caixa = ttk.LabelFrame(direita, text="Conjuntos detectados")
        caixa.pack(fill="both", expand=True)
        barra_botoes = ttk.Frame(caixa)
        barra_botoes.pack(fill="x", pady=2)
        ttk.Button(barra_botoes, text="Marcar todos",
                   command=lambda: self._marcar_grupos(True)).pack(side="left", padx=2)
        ttk.Button(barra_botoes, text="Desmarcar todos",
                   command=lambda: self._marcar_grupos(False)).pack(side="left", padx=2)
        ttk.Label(barra_botoes, text="Clique na coluna \"Usar\" para ligar/desligar.",
                  foreground="#6c757d", font=("Segoe UI", 8)).pack(side="left", padx=8)

        self.tree_grupos = self._tabela(caixa, COLUNAS_GRUPOS, altura=5)
        self.tree_grupos.bind("<<TreeviewSelect>>", lambda _e: self._mostrar_blocos())
        self.tree_grupos.bind("<Button-1>", self._clique_grupo)
        self.tree_grupos.bind("<Double-1>", self._duplo_clique_grupo)

        caixa = ttk.LabelFrame(direita, text="Blocos do conjunto selecionado")
        caixa.pack(fill="both", expand=True, pady=(8, 0))
        self.tree_blocos = self._tabela(caixa, COLUNAS_BLOCOS, altura=5)
        self.tree_blocos.bind("<Button-1>", self._clique_bloco)

        caixa = ttk.LabelFrame(direita, text="Log de Execucao")
        caixa.pack(fill="both", expand=True, pady=(8, 0))
        self.log = LogPanel(caixa)

    # Largura e comportamento de cada coluna: (largura, estica, alinhamento).
    # So as colunas de nome esticam -- deixar todas esticaveis empurrava
    # "Arquivo de saida" para fora da janela em telas de 1366 px.
    _COLUNAS = {
        "Usar": (50, False, "center"),
        "Conjunto": (200, True, "w"),
        "Blocos": (65, False, "center"),
        "Deteccao": (140, False, "center"),
        "Arquivo de saida": (210, True, "w"),
        "Arquivo": (250, True, "w"),
        "Dimensoes": (100, False, "center"),
        "Bandas": (55, False, "center"),
        "Tipo": (110, False, "center"),
        "NoData": (70, False, "center"),
    }

    def _tabela(self, pai, colunas, altura: int) -> ttk.Treeview:
        quadro = ttk.Frame(pai)
        quadro.pack(fill="both", expand=True)
        tree = ttk.Treeview(quadro, columns=colunas, show="headings", height=altura)
        for coluna in colunas:
            largura, estica, alinhamento = self._COLUNAS.get(coluna, (110, False, "center"))
            tree.heading(coluna, text=coluna)
            tree.column(coluna, width=largura, minwidth=50, stretch=estica,
                        anchor=alinhamento)
        tree.pack(side="left", fill="both", expand=True)
        barra = ttk.Scrollbar(quadro, orient="vertical", command=tree.yview)
        barra.pack(side="right", fill="y")
        tree.configure(yscrollcommand=barra.set)
        tree.tag_configure("ligado", foreground="#155724")
        tree.tag_configure("desligado", foreground="#9aa4ae")
        return tree

    # ------------------------------------------------------------------
    # Leitura da pasta
    # ------------------------------------------------------------------

    def _ao_escolher_pasta(self) -> None:
        pasta = self.v_entrada.get().strip()
        if pasta and not self.v_saida.get().strip():
            self.v_saida.set(pasta)
            self.lbl_saida.config(text=pasta)
        self._escanear()

    def _escanear(self) -> None:
        pasta = self.v_entrada.get().strip()
        if not pasta:
            messagebox.showwarning("Aviso", "Selecione a pasta dos rasters.")
            return
        arquivos = list_tif_files(pasta)
        grupos, descartados = merge_ops.filtrar_mosaicos(
            merge_ops.agrupar_para_merge(arquivos))
        self.log.add(f"=== SCAN: {len(arquivos)} arquivo(s) .tif na pasta ===")
        for grupo, motivo in descartados:
            self.log.add(f"{grupo.base}: {grupo.quantidade} arquivos ignorados - {motivo}")
        if not grupos:
            self._carregar_grupos([])
            self.log.add("Nenhum conjunto de blocos encontrado (nada com sufixo "
                         "numerico repetido). Use a selecao manual se precisar.")
            self.v_status.set("Nenhum conjunto para merge nesta pasta.")
            return
        self._carregar_grupos(grupos)
        for grupo in grupos:
            self.log.add(f"{grupo.base}: {grupo.quantidade} blocos ({grupo.motivo})")
        soltos = len(arquivos) - sum(g.quantidade for g in grupos)
        self.v_status.set(
            f"{len(grupos)} conjunto(s) para merge; {soltos} arquivo(s) avulso(s) ignorado(s).")

    def _selecionar_manual(self) -> None:
        caminhos = filedialog.askopenfilenames(
            title="Selecionar os rasters a unir",
            initialdir=self.v_entrada.get().strip() or None,
            filetypes=[("GeoTIFF", "*.tif *.tiff"), ("Todos", "*.*")],
        )
        if not caminhos:
            return
        caminhos = list(caminhos)
        if not self.v_entrada.get().strip():
            self.v_entrada.set(os.path.dirname(caminhos[0]))
        if not self.v_saida.get().strip():
            self.v_saida.set(os.path.dirname(caminhos[0]))
            self.lbl_saida.config(text=self.v_saida.get())

        grupos = merge_ops.agrupar_para_merge(caminhos)
        agrupados = {c for g in grupos for c in g.arquivos}
        avulsos = [c for c in caminhos if c not in agrupados]
        if avulsos:
            # Escolha manual e escolha: se o usuario marcou arquivos sem padrao
            # de bloco, eles viram um conjunto unico em vez de sumirem.
            base = _base_comum([os.path.splitext(os.path.basename(c))[0] for c in avulsos])
            grupos.append(merge_ops.GrupoDeMerge(
                base=base, arquivos=tuple(sorted(avulsos)), motivo="selecao manual"))
        self._carregar_grupos(grupos)
        self.log.add(f"=== SELECAO MANUAL: {len(caminhos)} arquivo(s), "
                     f"{len(grupos)} conjunto(s) ===")
        self.v_status.set(f"{len(grupos)} conjunto(s) vindos da selecao manual.")

    def _carregar_grupos(self, grupos) -> None:
        prefixo = self.v_prefixo.get()
        self.grupos = [{
            "base": g.base,
            "motivo": g.motivo,
            "arquivos": list(g.arquivos),
            "usar": True,
            "usar_arquivo": {c: True for c in g.arquivos},
            "saida": merge_ops.nome_de_saida(g.base, prefixo),
            "saida_manual": False,
        } for g in grupos]
        self._infos.clear()
        self._atualizar_grupos()
        self.tree_blocos.delete(*self.tree_blocos.get_children())
        self._resumo()

    # ------------------------------------------------------------------
    # Tabelas
    # ------------------------------------------------------------------

    def _atualizar_grupos(self) -> None:
        selecionado = self._indice_selecionado()
        self.tree_grupos.delete(*self.tree_grupos.get_children())
        for grupo in self.grupos:
            usados = sum(1 for c in grupo["arquivos"] if grupo["usar_arquivo"][c])
            self.tree_grupos.insert(
                "", "end",
                values=(MARCADO if grupo["usar"] else DESMARCADO, grupo["base"],
                        f"{usados}/{len(grupo['arquivos'])}", grupo["motivo"], grupo["saida"]),
                tags=("ligado" if grupo["usar"] else "desligado",),
            )
        filhos = self.tree_grupos.get_children()
        if filhos:
            alvo = filhos[selecionado] if 0 <= selecionado < len(filhos) else filhos[0]
            self.tree_grupos.selection_set(alvo)
        self._resumo()

    def _indice_selecionado(self) -> int:
        selecao = self.tree_grupos.selection()
        if not selecao:
            return -1
        return self.tree_grupos.index(selecao[0])

    def _mostrar_blocos(self) -> None:
        self.tree_blocos.delete(*self.tree_blocos.get_children())
        indice = self._indice_selecionado()
        if not (0 <= indice < len(self.grupos)):
            return
        grupo = self.grupos[indice]
        for caminho in grupo["arquivos"]:
            info = self._info(caminho)
            if info is None:
                valores = (MARCADO if grupo["usar_arquivo"][caminho] else DESMARCADO,
                           os.path.basename(caminho), "nao foi possivel ler", "-", "-", "-")
            else:
                from interface.gui import _tipo_de_entrada

                valores = (MARCADO if grupo["usar_arquivo"][caminho] else DESMARCADO,
                           info.nome, info.dimensoes, str(info.bandas),
                           _tipo_de_entrada(info.dtype),
                           "-" if info.nodata is None else f"{info.nodata:g}")
            self.tree_blocos.insert(
                "", "end", values=valores,
                tags=("ligado" if grupo["usar_arquivo"][caminho] else "desligado",))

    def _info(self, caminho: str) -> Optional[merge_ops.InfoDoBloco]:
        if caminho not in self._infos:
            try:
                self._infos[caminho] = merge_ops.ler_info(caminho)
            except Exception as exc:  # noqa: BLE001
                self.log.add(f"AVISO: nao foi possivel ler {os.path.basename(caminho)}: {exc}")
                self._infos[caminho] = None  # type: ignore[assignment]
        return self._infos.get(caminho)

    def _clique_grupo(self, evento) -> None:
        if self.tree_grupos.identify("region", evento.x, evento.y) != "cell":
            return
        if self.tree_grupos.identify_column(evento.x) != "#1":
            return
        linha = self.tree_grupos.identify_row(evento.y)
        if not linha:
            return
        indice = self.tree_grupos.index(linha)
        self.grupos[indice]["usar"] = not self.grupos[indice]["usar"]
        self._atualizar_grupos()

    def _duplo_clique_grupo(self, evento) -> None:
        from interface.gui import edit_treeview_cell

        def ao_confirmar(_linha, _coluna, novo_valor):
            indice = self._indice_selecionado()
            if not (0 <= indice < len(self.grupos)):
                return
            nome = novo_valor.strip()
            if not nome:
                return
            if not nome.lower().endswith((".tif", ".tiff")):
                nome += ".tif"
            self.grupos[indice]["saida"] = nome
            self.grupos[indice]["saida_manual"] = True
            self._atualizar_grupos()

        edit_treeview_cell(self.tree_grupos, evento,
                           editable_columns={"Arquivo de saida"}, on_commit=ao_confirmar)

    def _clique_bloco(self, evento) -> None:
        if self.tree_blocos.identify("region", evento.x, evento.y) != "cell":
            return
        if self.tree_blocos.identify_column(evento.x) != "#1":
            return
        linha = self.tree_blocos.identify_row(evento.y)
        if not linha:
            return
        indice_grupo = self._indice_selecionado()
        if not (0 <= indice_grupo < len(self.grupos)):
            return
        grupo = self.grupos[indice_grupo]
        caminho = grupo["arquivos"][self.tree_blocos.index(linha)]
        grupo["usar_arquivo"][caminho] = not grupo["usar_arquivo"][caminho]
        self._mostrar_blocos()
        self._atualizar_grupos()

    def _marcar_grupos(self, ligado: bool) -> None:
        for grupo in self.grupos:
            grupo["usar"] = ligado
        self._atualizar_grupos()

    def _renomear_saidas(self) -> None:
        """O prefixo mudou: renomeia as saidas que o usuario nao editou."""
        prefixo = self.v_prefixo.get()
        for grupo in self.grupos:
            if not grupo["saida_manual"]:
                grupo["saida"] = merge_ops.nome_de_saida(grupo["base"], prefixo)
        if self.grupos:
            self._atualizar_grupos()

    def _resumo(self) -> None:
        ativos = [g for g in self.grupos if g["usar"]]
        blocos = sum(sum(1 for c in g["arquivos"] if g["usar_arquivo"][c]) for g in ativos)
        if not self.grupos:
            self.v_resumo.set("")
            return
        self.v_resumo.set(
            f"{len(self.grupos)} conjunto(s) detectado(s) | {len(ativos)} marcado(s) para "
            f"execucao | {blocos} bloco(s) a unir")

    # ------------------------------------------------------------------
    # Execucao
    # ------------------------------------------------------------------

    def _parar(self) -> None:
        if self.token is not None:
            self.token.cancel()
        self.v_status.set("Parada solicitada. Aguarde o fim da faixa em andamento...")
        self.btn_parar.config(state="disabled")

    def _progresso(self, valor: float) -> None:
        self.v_progresso.set(float(valor))
        self.v_pct.set(f"{float(valor):.0f}%")

    def _abrir_saida(self) -> None:
        pasta = self.v_saida.get().strip() or self.v_entrada.get().strip()
        if not pasta:
            messagebox.showwarning("Aviso", "Nenhuma pasta de saida definida.")
            return
        try:
            os.makedirs(pasta, exist_ok=True)
            os.startfile(pasta)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", f"Nao foi possivel abrir a pasta:\n{exc}")

    def _tarefas(self) -> Optional[List[Dict]]:
        """Monta a lista de merges a executar, ja validada."""
        pasta_saida = self.v_saida.get().strip() or self.v_entrada.get().strip()
        if not pasta_saida:
            messagebox.showwarning("Aviso", "Selecione a pasta de saida.")
            return None

        try:
            memoria = float(self.v_memoria.get().replace(",", "."))
            if memoria <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Aviso", "Memoria por faixa invalida. Use um numero "
                                            "de MB, por exemplo 64.")
            return None

        tarefas = []
        for grupo in self.grupos:
            if not grupo["usar"]:
                continue
            arquivos = [c for c in grupo["arquivos"] if grupo["usar_arquivo"][c]]
            if len(arquivos) < 2:
                self.log.add(f"AVISO: {grupo['base']} ficou com {len(arquivos)} bloco(s) "
                             f"marcado(s); merge precisa de dois ou mais. Conjunto pulado.")
                continue
            tarefas.append({
                "base": grupo["base"],
                "arquivos": arquivos,
                "saida": os.path.join(pasta_saida, grupo["saida"]),
                "memoria": memoria,
            })

        if not tarefas:
            messagebox.showwarning(
                "Aviso", "Nenhum conjunto marcado com pelo menos dois blocos.")
            return None
        return tarefas

    def _executar(self) -> None:
        tarefas = self._tarefas()
        if tarefas is None:
            return

        sobrescrever = self.v_sobrescrever.get()
        existentes = [t["saida"] for t in tarefas
                      if os.path.exists(t["saida"]) and not sobrescrever]
        if existentes:
            messagebox.showwarning(
                "Aviso",
                "Estes arquivos ja existem e a opcao de sobrescrever esta "
                "desligada:\n\n" + "\n".join(os.path.basename(c) for c in existentes))
            return

        self.token = CancellationToken()
        self._progresso(0)
        self.log.add(f"=== MERGE INICIADO | {len(tarefas)} conjunto(s) ===")

        def worker(q: queue.Queue) -> None:
            resultados = []
            total = len(tarefas)
            for indice, tarefa in enumerate(tarefas):
                if self.token.is_cancelled():
                    break
                q.put(("status", f"[{indice + 1}/{total}] {tarefa['base']}"))
                q.put(("log", f"[{indice + 1}/{total}] {tarefa['base']}: "
                              f"{len(tarefa['arquivos'])} blocos -> "
                              f"{os.path.basename(tarefa['saida'])}"))

                def progresso(valor: float, etapa: str, _i=indice) -> None:
                    q.put(("progress", 100.0 * (_i + valor / 100.0) / total))
                    q.put(("status", f"[{_i + 1}/{total}] {tarefa['base']} - {etapa}"))

                try:
                    resultado = merge_ops.executar_merge(
                        merge_ops.ParametrosMerge(
                            arquivos=tuple(tarefa["arquivos"]), saida=tarefa["saida"],
                            sobrescrever=sobrescrever,
                            memoria_por_faixa_mb=tarefa["memoria"],
                        ),
                        progresso=progresso,
                        cancelado=self.token.is_cancelled,
                    )
                    for aviso in resultado.avisos:
                        q.put(("log", f"  AVISO: {aviso}"))
                    q.put(("log", f"  OK -> {os.path.basename(resultado.caminho)} "
                                  f"({resultado.resumo()}, {resultado.segundos:.1f}s)"))
                    resultados.append((tarefa["base"], "OK", resultado))
                except CancelledError:
                    q.put(("log", "  CANCELADO pelo usuario (arquivo parcial removido)."))
                    break
                except Exception as exc:  # noqa: BLE001
                    q.put(("log", f"  ERRO: {exc}"))
                    resultados.append((tarefa["base"], "ERRO", str(exc)))
            q.put(("done", resultados))

        def ao_terminar(resultados) -> None:
            n_ok = sum(1 for _, status, _ in resultados if status == "OK")
            n_erro = sum(1 for _, status, _ in resultados if status == "ERRO")
            cancelado = self.token is not None and self.token.is_cancelled()
            self._progresso(0 if cancelado else 100)
            self.log.add(f"=== CONCLUIDO | OK: {n_ok} | ERRO: {n_erro}"
                         + (" | CANCELADO" if cancelado else "") + " ===")
            self.v_status.set("Merge cancelado." if cancelado
                              else f"Merge concluido: {n_ok} arquivo(s) gerado(s).")
            if cancelado:
                messagebox.showinfo("Cancelado", "Merge interrompido pelo usuario.")
            elif n_erro:
                detalhes = "\n".join(f"{base}: {info}"
                                     for base, status, info in resultados if status == "ERRO")
                messagebox.showwarning("Concluido com erros",
                                       f"OK: {n_ok} | Erros: {n_erro}\n\n{detalhes}")
            else:
                messagebox.showinfo("Concluido", f"Merge concluido - {n_ok} arquivo(s).")

        def ao_falhar(mensagem) -> None:
            self._progresso(0)
            self.log.add(f"ERRO FATAL: {mensagem}")
            self.v_status.set("Falha no merge.")
            messagebox.showerror("Erro", mensagem)

        self._rodar(worker, ao_terminar, ao_falhar,
                    [self.btn_executar, self.btn_reescanear, self.btn_manual])

    def _rodar(self, worker, ao_terminar, ao_falhar, botoes) -> None:
        """Mesmo esquema de thread + fila das outras abas, com a mensagem de
        etapa ("status") que a barra de progresso usa."""
        fila: queue.Queue = queue.Queue()
        for botao in botoes:
            botao.config(state="disabled")
        self.btn_parar.config(state="normal")

        def runner() -> None:
            try:
                worker(fila)
            except Exception as exc:  # noqa: BLE001
                fila.put(("fatal", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

        def liberar() -> None:
            for botao in botoes:
                botao.config(state="normal")
            self.btn_parar.config(state="disabled")

        def poll() -> None:
            try:
                while True:
                    tipo, carga = fila.get_nowait()
                    if tipo == "log":
                        self.log.add(carga)
                    elif tipo == "progress":
                        self._progresso(carga)
                    elif tipo == "status":
                        self.v_status.set(str(carga))
                    elif tipo == "done":
                        ao_terminar(carga)
                        liberar()
                        return
                    elif tipo == "fatal":
                        ao_falhar(carga)
                        liberar()
                        return
            except queue.Empty:
                pass
            self.root.after(100, poll)

        self.root.after(100, poll)


def _base_comum(nomes: List[str]) -> str:
    """Nome de conjunto para arquivos sem padrao de bloco: o maior prefixo
    comum, sem separadores soltos no fim. Com um arquivo so, o proprio nome."""
    if not nomes:
        return "merge"
    comum = os.path.commonprefix(nomes).rstrip("-_ .")
    return comum or "merge"


def build_tab(app, frame: ttk.Frame) -> AbaMergeRaster:
    """Ponto de entrada usado por gui.py."""
    return AbaMergeRaster(app, frame)
