"""
gui.py
======

Interface grafica (Tkinter + ttk) do GeoRaster Tools, traduzida do app
Shiny original (``app_v4.R``). Na V3 sao 7 abas:

    1. Recortar Rasters
    2. Reclassificar
    3. Separar Bandas
    4. Reamostrar
    5. Merge de Rasters          <- novo na V3
    6. Verificar Alinhamento
    7. Amostragem

A aba 5 e construida em ``tab_merge.py`` (conta em ``merge_ops.py``) e une
os blocos que o Earth Engine gera quando uma exportacao passa do limite de
tamanho. A aba 7 e construida em ``tab_amostragem.py`` e usa o pacote
``amostragem_fogo`` (nucleo da aplicacao V5), preparando os pontos no
formato que o SDM_APP le com ``read.csv(...)[, c("x", "y")]``.

Todo processamento pesado roda em uma thread separada por operacao para
nao congelar a interface; o progresso e as mensagens de log sao
repassados a thread principal via ``queue.Queue`` e consumidos com
``root.after``.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional

from nucleo import config
from nucleo import raster_ops
from nucleo.utils import ensure_dir, get_logger, list_tif_files, setup_logging, ts_msg

logger = get_logger()


# =============================================================================
# Helpers genericos de UI
# =============================================================================


class LogPanel:
    """Painel de log com tema escuro, equivalente a log_div()/push_log() em
    R. Deve ser atualizado sempre a partir da thread principal (usar
    root.after ao chamar de uma worker thread)."""

    def __init__(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(
            frame, height=10, state="disabled", bg="#0d1117", fg="#58a6ff",
            font=("Consolas", 9), wrap="word",
        )
        self.text.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scroll.set)
        # A quebra de linha no fim importa: sem ela a primeira mensagem sai
        # colada no texto inicial ("Aguardando execucao...[19:48:23] === SCAN").
        self._set_text("Aguardando execucao...\n")

    def _set_text(self, content: str):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", content)
        self.text.see("end")
        self.text.configure(state="disabled")

    def add(self, message: str):
        self.text.configure(state="normal")
        self.text.insert("end", ts_msg(message) + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self._set_text("Log limpo.\n")


def make_result_tree(parent, columns: Dict[str, str], height: int = 8) -> ttk.Treeview:
    """Cria uma Treeview de resultados com colunas (id -> titulo) e tags de
    cor para Status OK/ERRO."""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=list(columns.keys()), show="headings", height=height)
    for col_id, title in columns.items():
        tree.heading(col_id, text=title)
        tree.column(col_id, width=140, anchor="w")
    tree.pack(fill="both", expand=True, side="left")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    scroll.pack(side="right", fill="y")
    tree.configure(yscrollcommand=scroll.set)
    tree.tag_configure("ok", background="#d4edda", foreground="#155724")
    tree.tag_configure("erro", background="#f8d7da", foreground="#721c24")
    return tree


def populate_status_tree(tree: ttk.Treeview, rows: List[Dict[str, str]], columns: List[str]):
    tree.delete(*tree.get_children())
    for row in rows:
        values = [row.get(c, "") for c in columns]
        tag = "ok" if row.get("Status") == "OK" else "erro"
        tree.insert("", "end", values=values, tags=(tag,))


def edit_treeview_cell(
    tree: ttk.Treeview,
    event,
    editable_columns: set,
    combo_values: Optional[Dict[str, List[str]]] = None,
    on_commit: Optional[Callable[[str, str, str], None]] = None,
):
    """Habilita edicao de celula por duplo clique em uma Treeview, com
    suporte a edicao livre (Entry) ou restrita a uma lista de valores
    (Combobox), equivalente ao editable=list(target='cell') do DT no R."""
    region = tree.identify("region", event.x, event.y)
    if region != "cell":
        return
    col = tree.identify_column(event.x)
    row = tree.identify_row(event.y)
    if not row:
        return
    col_index = int(col.replace("#", "")) - 1
    columns = tree["columns"]
    if col_index < 0 or col_index >= len(columns):
        return
    col_name = columns[col_index]
    if col_name not in editable_columns:
        return

    bbox = tree.bbox(row, col)
    if not bbox:
        return
    x, y, w, h = bbox
    current_value = tree.set(row, col_name)

    use_combo = combo_values is not None and col_name in combo_values
    if use_combo:
        editor = ttk.Combobox(tree, values=combo_values[col_name], state="readonly")
        editor.set(current_value)
    else:
        editor = ttk.Entry(tree)
        editor.insert(0, current_value)
        editor.select_range(0, "end")

    editor.place(x=x, y=y, width=w, height=h)
    editor.focus_set()

    def commit(_event=None):
        if not editor.winfo_exists():
            return
        new_value = editor.get()
        editor.destroy()
        if on_commit:
            on_commit(row, col_name, new_value)

    def cancel(_event=None):
        if editor.winfo_exists():
            editor.destroy()

    editor.bind("<Return>", commit)
    editor.bind("<FocusOut>", commit)
    editor.bind("<Escape>", cancel)
    if use_combo:
        editor.bind("<<ComboboxSelected>>", commit)


class Rodape:
    """Barra de progresso + porcentagem + linha de etapa.

    A V3 passou a usar o mesmo rodape em TODAS as abas (antes so a de
    Amostragem tinha barra, e nas outras um lote de 200 rasters parecia
    travado). Empacote-o LOGO depois de criar a coluna da direita: ele se
    ancora em ``side="bottom"`` e, no pack do Tk, o espaco e distribuido na
    ordem de empacotamento -- criado depois das tabelas, ele ficaria sem
    espaco nas janelas menores."""

    def __init__(self, parent, status_inicial: str = "Pronto."):
        quadro = ttk.Frame(parent)
        quadro.pack(side="bottom", fill="x", pady=(6, 0))
        linha = ttk.Frame(quadro)
        linha.pack(fill="x")
        self.v_progresso = tk.DoubleVar(value=0.0)
        self.v_pct = tk.StringVar(value="0%")
        self.v_status = tk.StringVar(value=status_inicial)
        self.barra = ttk.Progressbar(linha, variable=self.v_progresso, maximum=100,
                                     mode="determinate")
        self.barra.pack(side="left", fill="x", expand=True)
        ttk.Label(linha, textvariable=self.v_pct, width=6, anchor="e",
                  font=("Segoe UI", 9, "bold")).pack(side="right", padx=(6, 0))
        ttk.Label(quadro, textvariable=self.v_status, anchor="w").pack(fill="x", pady=(2, 0))

    def progresso(self, valor: float):
        valor = max(0.0, min(100.0, float(valor)))
        self.v_progresso.set(valor)
        self.v_pct.set(f"{valor:.0f}%")

    def status(self, texto: str):
        self.v_status.set(str(texto))

    def iniciar(self, texto: str = "Processando..."):
        self.progresso(0)
        self.status(texto)

    def concluir(self, texto: str = "Concluido."):
        self.progresso(100)
        self.status(texto)

    def zerar(self, texto: str = "Pronto."):
        self.progresso(0)
        self.status(texto)


def run_background_task(
    root: tk.Tk,
    worker_fn: Callable[[queue.Queue], None],
    log_cb: Callable[[str], None],
    done_cb: Callable[[object], None],
    error_cb: Callable[[str], None],
    buttons_to_disable: List[tk.Widget],
    progress_cb: Optional[Callable[[float], None]] = None,
    status_cb: Optional[Callable[[str], None]] = None,
):
    """Executa worker_fn em uma thread separada, repassando mensagens de log,
    progresso, etapa e o resultado final de volta a thread principal via fila,
    consumida periodicamente com root.after."""
    q: queue.Queue = queue.Queue()
    for btn in buttons_to_disable:
        btn.config(state="disabled")

    def _runner():
        try:
            worker_fn(q)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro na tarefa em segundo plano")
            q.put(("fatal", str(exc)))

    threading.Thread(target=_runner, daemon=True).start()

    def _poll():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "log":
                    log_cb(payload)
                elif kind == "progress" and progress_cb is not None:
                    progress_cb(payload)
                elif kind == "status" and status_cb is not None:
                    status_cb(payload)
                elif kind == "done":
                    done_cb(payload)
                    for btn in buttons_to_disable:
                        btn.config(state="normal")
                    return
                elif kind == "fatal":
                    error_cb(payload)
                    for btn in buttons_to_disable:
                        btn.config(state="normal")
                    return
        except queue.Empty:
            pass
        root.after(100, _poll)

    root.after(100, _poll)


def _tipo_de_entrada(nome_numpy: str) -> str:
    """'int16' -> 'int16 (16 bits)'. O tipo do arquivo de ENTRADA chega do
    rasterio no nome numpy; mostrar os bits ao lado deixa a comparacao com o
    tipo de saida escolhido imediata."""
    try:
        import numpy as _np

        return f"{nome_numpy} ({_np.dtype(nome_numpy).itemsize * 8} bits)"
    except Exception:  # noqa: BLE001 - '?' quando o cabecalho nao pode ser lido
        return str(nome_numpy)


def quadro_rolavel(parent, largura: int = 430) -> ttk.Frame:
    """Coluna esquerda rolavel, usada pelas abas com muitos controles.

    Sem ela, numa janela de 1150x720 (o tamanho minimo da aplicacao) a coluna
    de parametros simplesmente nao cabe: o Tk corta o que passa da borda e os
    botoes do fim -- inclusive o de executar -- ficam inalcancaveis.

    Devolve o frame INTERNO; empacote os controles nele normalmente."""
    externo = ttk.Frame(parent)
    externo.pack(side="left", fill="y", padx=(8, 4), pady=8)
    canvas = tk.Canvas(externo, width=largura, highlightthickness=0, borderwidth=0)
    barra = ttk.Scrollbar(externo, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=barra.set)
    barra.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    interno = ttk.Frame(canvas)
    janela = canvas.create_window((0, 0), window=interno, anchor="nw")
    interno.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(janela, width=e.width))
    canvas.bind_all(
        "<MouseWheel>",
        lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        if str(canvas) in str(e.widget) or canvas.winfo_containing(e.x_root, e.y_root) in
        (canvas, *interno.winfo_children()) else None,
    )
    return interno


def select_directory(var: tk.StringVar, label: ttk.Label, title: str):
    path = filedialog.askdirectory(title=title)
    if path:
        var.set(path)
        label.config(text=path)


def select_file(var: tk.StringVar, label: ttk.Label, title: str, filetypes):
    path = filedialog.askopenfilename(title=title, filetypes=filetypes)
    if path:
        var.set(path)
        label.config(text=path)


def path_row(parent, title: str, kind: str, on_select) -> tuple:
    """Cria uma linha com botao de selecao + label mostrando o caminho
    escolhido, equivalente a dir_btn()/file_btn() + path_display() em R."""
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    btn = ttk.Button(row, text=title)
    btn.pack(side="left")
    lbl = ttk.Label(row, text="-- nao selecionado --", foreground="gray",
                     font=("Consolas", 8))
    lbl.pack(side="left", padx=6)
    btn.config(command=lambda: on_select(lbl))
    return btn, lbl


# =============================================================================
# Aplicacao principal
# =============================================================================


class GeoRasterToolsApp(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root = root
        self.root.title(f"{config.APP_NAME} v{config.APP_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1150, 720)
        self.pack(fill="both", expand=True)

        log_path = os.path.join(os.getcwd(), "processing.log")
        setup_logging(log_path)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        self.tab_clip = ttk.Frame(self.notebook)
        self.tab_reclassify = ttk.Frame(self.notebook)
        self.tab_bands = ttk.Frame(self.notebook)
        self.tab_resample = ttk.Frame(self.notebook)
        self.tab_merge = ttk.Frame(self.notebook)
        self.tab_verify = ttk.Frame(self.notebook)
        self.tab_sampling = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_clip, text="Recortar Rasters")
        self.notebook.add(self.tab_reclassify, text="Reclassificar")
        self.notebook.add(self.tab_bands, text="Separar Bandas")
        self.notebook.add(self.tab_resample, text="Reamostrar")
        self.notebook.add(self.tab_merge, text=config.MERGE_TAB_LABEL)
        self.notebook.add(self.tab_verify, text="Verificar Alinhamento")
        self.notebook.add(self.tab_sampling, text=config.SAMPLING_TAB_LABEL)

        self._build_tab_clip()
        self._build_tab_reclassify()
        self._build_tab_bands()
        self._build_tab_resample()
        self._build_tab_merge()
        self._build_tab_verify()
        self._build_tab_sampling()

    # =========================================================================
    # ABA 1 - Recortar Rasters
    # =========================================================================

    def _build_tab_clip(self):
        frame = self.tab_clip
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right = ttk.Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.clip_rodape = Rodape(right, "Selecione os caminhos e execute o recorte.")

        paths_box = ttk.LabelFrame(left, text="Caminhos")
        paths_box.pack(fill="x", pady=4)
        self.clip_dir_in = tk.StringVar()
        self.clip_shp = tk.StringVar()
        self.clip_dir_out = tk.StringVar()

        _, lbl_in = path_row(paths_box, "Pasta dos rasters (.tif)", "dir",
                              lambda lbl: (select_directory(self.clip_dir_in, lbl, "Selecionar pasta de entrada"),
                                           self._refresh_clip_input_table()))
        _, lbl_shp = path_row(paths_box, "Shapefile de recorte (.shp)", "file",
                               lambda lbl: select_file(self.clip_shp, lbl, "Selecionar shapefile",
                                                        [("Shapefile", "*.shp"), ("Todos", "*.*")]))
        _, lbl_out = path_row(paths_box, "Pasta de saida", "dir",
                               lambda lbl: select_directory(self.clip_dir_out, lbl, "Selecionar pasta de saida"))

        params_box = ttk.LabelFrame(left, text="Parametros")
        params_box.pack(fill="x", pady=4)
        ttk.Label(params_box, text="Prefixo dos arquivos de saida:").pack(anchor="w")
        self.clip_prefixo = tk.StringVar(value="MAT_")
        ttk.Entry(params_box, textvariable=self.clip_prefixo).pack(fill="x", pady=2)
        self.clip_overwrite = tk.BooleanVar(value=True)
        ttk.Checkbutton(params_box, text="Sobrescrever arquivos existentes",
                         variable=self.clip_overwrite).pack(anchor="w", pady=4)

        self.clip_run_btn = ttk.Button(left, text="Executar Recorte", command=self._on_clip_run)
        self.clip_run_btn.pack(fill="x", pady=(10, 2))
        ttk.Button(left, text="Limpar Log", command=self._on_clip_clear).pack(fill="x")

        tables_row = ttk.Frame(right)
        tables_row.pack(fill="both", expand=True)
        in_box = ttk.LabelFrame(tables_row, text="Arquivos de Entrada")
        in_box.pack(side="left", fill="both", expand=True, padx=(0, 4))
        res_box = ttk.LabelFrame(tables_row, text="Resultado do Processamento")
        res_box.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self.clip_in_tree = make_result_tree(in_box, {"Arquivo": "Arquivo"})
        self.clip_res_tree = make_result_tree(
            res_box, {"Arquivo": "Arquivo", "Status": "Status", "Saida": "Saida"}
        )

        log_box = ttk.LabelFrame(right, text="Log de Execucao")
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.clip_log = LogPanel(log_box)

    def _refresh_clip_input_table(self):
        arqs = list_tif_files(self.clip_dir_in.get())
        rows = [{"Arquivo": os.path.basename(a)} for a in arqs] or [{"Arquivo": "Nenhum .tif encontrado"}]
        self.clip_in_tree.delete(*self.clip_in_tree.get_children())
        for r in rows:
            self.clip_in_tree.insert("", "end", values=(r["Arquivo"],))

    def _on_clip_clear(self):
        self.clip_log.clear()
        self.clip_res_tree.delete(*self.clip_res_tree.get_children())

    def _on_clip_run(self):
        dir_in = self.clip_dir_in.get()
        shp = self.clip_shp.get()
        dir_out = self.clip_dir_out.get()
        if not (dir_in and shp and dir_out):
            messagebox.showwarning("Aviso", "Selecione todos os caminhos antes de executar.")
            return
        arqs = list_tif_files(dir_in)
        if not arqs:
            messagebox.showwarning("Aviso", "Nenhum arquivo .tif encontrado.")
            return
        ensure_dir(dir_out)
        prefixo = self.clip_prefixo.get()
        overwrite = self.clip_overwrite.get()

        self.clip_log.add("=== RECORTE INICIADO ===")
        self.clip_log.add(f"Arquivos: {len(arqs)} | Shapefile: {os.path.basename(shp)}")
        self.clip_rodape.iniciar(f"Recortando {len(arqs)} arquivo(s)...")

        def worker(q: queue.Queue):
            resultados = []
            for i, arq in enumerate(arqs):
                q.put(("status", f"[{i + 1}/{len(arqs)}] {os.path.basename(arq)}"))
                q.put(("log", f"[{i + 1}/{len(arqs)}] {os.path.basename(arq)}"))
                try:
                    nome_out = prefixo + os.path.splitext(os.path.basename(arq))[0] + ".tif"
                    out_path = os.path.join(dir_out, nome_out)
                    raster_ops.clip_raster(arq, shp, out_path, overwrite=overwrite)
                    resultados.append({"Arquivo": os.path.basename(arq), "Status": "OK", "Saida": nome_out})
                    q.put(("log", f"  OK -> {nome_out}"))
                except Exception as exc:  # noqa: BLE001
                    resultados.append({"Arquivo": os.path.basename(arq), "Status": "ERRO", "Saida": str(exc)})
                    q.put(("log", f"  ERRO: {exc}"))
                q.put(("progress", 100.0 * (i + 1) / len(arqs)))
            q.put(("done", resultados))

        def on_done(resultados):
            populate_status_tree(self.clip_res_tree, resultados, ["Arquivo", "Status", "Saida"])
            n_ok = sum(1 for r in resultados if r["Status"] == "OK")
            n_err = len(resultados) - n_ok
            self.clip_log.add(f"=== CONCLUIDO | OK: {n_ok} | ERRO: {n_err} ===")
            self.clip_rodape.concluir(f"Recorte concluido - OK: {n_ok} | Erros: {n_err}")
            messagebox.showinfo("Concluido", f"Recorte - OK: {n_ok} | Erros: {n_err}")

        def on_error(msg):
            self.clip_rodape.zerar("Falha no recorte.")
            self.clip_log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro", msg)

        run_background_task(self.root, worker, self.clip_log.add, on_done, on_error,
                            [self.clip_run_btn], progress_cb=self.clip_rodape.progresso,
                            status_cb=self.clip_rodape.status)

    # =========================================================================
    # ABA 2 - Reclassificar
    # =========================================================================

    def _build_tab_reclassify(self):
        frame = self.tab_reclassify
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right = ttk.Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.rcl_rodape = Rodape(right, "Escaneie a pasta, defina as regras e execute.")

        paths_box = ttk.LabelFrame(left, text="Caminhos")
        paths_box.pack(fill="x", pady=4)
        self.rcl_dir_in = tk.StringVar()
        self.rcl_dir_out = tk.StringVar()
        path_row(paths_box, "Pasta dos rasters de entrada", "dir",
                 lambda lbl: select_directory(self.rcl_dir_in, lbl, "Selecionar pasta de entrada"))
        path_row(paths_box, "Pasta de saida", "dir",
                 lambda lbl: select_directory(self.rcl_dir_out, lbl, "Selecionar pasta de saida"))

        params_box = ttk.LabelFrame(left, text="Parametros de Saida")
        params_box.pack(fill="x", pady=4)
        ttk.Label(params_box, text="Tipo de dado de saida:").pack(anchor="w")
        # O rotulo traz bits e tipo numpy ("INT1U - 8 bits - uint8"): o nome do
        # terra conta BYTES, e escolher 16 bits achando que sao 2 ja custou
        # raster estourado. O valor usado no processamento continua sendo so o
        # codigo (config.dtype_do_rotulo).
        self.rcl_dtype = tk.StringVar(value=config.rotulo_de_dtype("INT1U"))
        ttk.Combobox(params_box, textvariable=self.rcl_dtype,
                     values=config.rotulos_de_dtype(config.RECLASSIFY_DTYPES),
                     state="readonly").pack(fill="x", pady=2)

        ttk.Label(params_box, text="Pixels nao mapeados:").pack(anchor="w")
        self.rcl_others = tk.StringVar(value="NA")
        ttk.Combobox(params_box, textvariable=self.rcl_others,
                     values=["NA (sem valor)", "Zero (0)"], state="readonly").pack(fill="x", pady=2)

        ttk.Label(params_box, text="Intervalo fechado em:").pack(anchor="w")
        self.rcl_right = tk.StringVar(value="Ambos os lados (NA)")
        ttk.Combobox(params_box, textvariable=self.rcl_right,
                     values=["Ambos os lados (NA)", "Direita (TRUE)", "Esquerda (FALSE)"],
                     state="readonly").pack(fill="x", pady=2)

        self.rcl_include_lowest = tk.BooleanVar(value=True)
        ttk.Checkbutton(params_box, text="Incluir valor minimo (include.lowest)",
                         variable=self.rcl_include_lowest).pack(anchor="w", pady=2)
        self.rcl_overwrite = tk.BooleanVar(value=True)
        ttk.Checkbutton(params_box, text="Sobrescrever arquivos existentes",
                         variable=self.rcl_overwrite).pack(anchor="w", pady=2)

        ttk.Button(left, text="Escanear Arquivos", command=self._on_rcl_scan).pack(fill="x", pady=(10, 2))
        self.rcl_run_btn = ttk.Button(left, text="Executar Reclassificacao", command=self._on_rcl_run)
        self.rcl_run_btn.pack(fill="x", pady=2)
        ttk.Button(left, text="Limpar Log", command=lambda: self.rcl_log.clear()).pack(fill="x")

        # -- estado --
        self.rcl_files: List[Dict[str, str]] = []
        self.rcl_rulesets: Dict[str, raster_ops.RuleSet] = {"Padrao": raster_ops.default_ruleset()}
        self.rcl_active = "Padrao"

        top_row = ttk.Frame(right)
        top_row.pack(fill="both", expand=True)

        files_box = ttk.LabelFrame(top_row, text="Arquivos Escaneados")
        files_box.pack(side="left", fill="both", expand=True, padx=(0, 4))
        ttk.Label(files_box, text="Selecione linhas e use os botoes ao lado para atribuir regras.",
                  foreground="#6c757d", font=("Segoe UI", 8)).pack(anchor="w")
        self.rcl_files_tree = ttk.Treeview(files_box, columns=("Arquivo", "Regra"), show="headings", height=10,
                                            selectmode="extended")
        self.rcl_files_tree.heading("Arquivo", text="Arquivo")
        self.rcl_files_tree.heading("Regra", text="Regra")
        self.rcl_files_tree.pack(fill="both", expand=True)
        self.rcl_files_tree.tag_configure("padrao", background="#d4edda", foreground="#155724")

        rules_mgmt_box = ttk.LabelFrame(top_row, text="Gerenciar Conjuntos de Regras")
        rules_mgmt_box.pack(side="left", fill="both", expand=True, padx=(4, 0))

        ttk.Label(rules_mgmt_box, text="Regra ativa no editor:").pack(anchor="w")
        self.rcl_sel_regra = tk.StringVar(value="Padrao")
        self.rcl_sel_regra_combo = ttk.Combobox(rules_mgmt_box, textvariable=self.rcl_sel_regra,
                                                 values=["Padrao"], state="readonly")
        self.rcl_sel_regra_combo.pack(fill="x", pady=2)
        self.rcl_sel_regra_combo.bind("<<ComboboxSelected>>", self._on_rcl_select_active_rule)

        new_rule_row = ttk.Frame(rules_mgmt_box)
        new_rule_row.pack(fill="x", pady=4)
        self.rcl_new_rule_name = tk.StringVar()
        ttk.Entry(new_rule_row, textvariable=self.rcl_new_rule_name).pack(side="left", fill="x", expand=True)
        ttk.Button(new_rule_row, text="Criar", command=self._on_rcl_create_rule).pack(side="left", padx=4)

        ttk.Button(rules_mgmt_box, text="Atribuir regra ativa aos selecionados",
                   command=self._on_rcl_assign_selected).pack(fill="x", pady=2)
        ttk.Button(rules_mgmt_box, text="Atribuir regra ativa a todos os arquivos",
                   command=self._on_rcl_assign_all).pack(fill="x", pady=2)

        ttk.Separator(rules_mgmt_box).pack(fill="x", pady=6)
        ttk.Label(rules_mgmt_box, text="Arquivo de Regras (.txt)", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        txt_btn_row = ttk.Frame(rules_mgmt_box)
        txt_btn_row.pack(fill="x", pady=2)
        ttk.Button(txt_btn_row, text="Carregar .txt", command=self._on_rcl_load_txt).pack(side="left", fill="x",
                                                                                          expand=True, padx=(0, 2))
        ttk.Button(txt_btn_row, text="Salvar .txt", command=self._on_rcl_save_txt).pack(side="left", fill="x",
                                                                                        expand=True, padx=(2, 0))

        editor_box = ttk.LabelFrame(right, text="Editor de Regras")
        editor_box.pack(fill="both", expand=True, pady=(8, 0))

        editor_header = ttk.Frame(editor_box)
        editor_header.pack(fill="x")
        self.rcl_editor_title_lbl = ttk.Label(editor_header, text="EDITOR - Padrao", font=("Segoe UI", 9, "bold"))
        self.rcl_editor_title_lbl.pack(side="left")
        ttk.Button(editor_header, text="+ Linha", command=self._on_rcl_add_row).pack(side="left", padx=4)
        ttk.Button(editor_header, text="- Linha Selecionada", command=self._on_rcl_del_row).pack(side="left", padx=4)
        ttk.Button(editor_header, text="Salvar Regra", command=self._on_rcl_save_rule).pack(side="left", padx=4)

        ttk.Label(editor_box, text=("Clique duas vezes em qualquer celula para editar. Selecione uma linha e clique "
                                     "em '- Linha Selecionada' para remover. 'Salvar Regra' persiste as alteracoes."),
                  foreground="#6c757d", font=("Segoe UI", 8)).pack(anchor="w", pady=2)

        self.rcl_editor_tree = ttk.Treeview(editor_box, columns=config.RULE_COLUMNS, show="headings", height=10)
        for col in config.RULE_COLUMNS:
            self.rcl_editor_tree.heading(col, text=col)
            self.rcl_editor_tree.column(col, width=100, anchor="center")
        self.rcl_editor_tree.pack(fill="both", expand=True)
        self.rcl_editor_tree.bind("<Double-1>", self._on_rcl_editor_double_click)

        log_box = ttk.LabelFrame(right, text="Log de Execucao")
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.rcl_log = LogPanel(log_box)

        self._refresh_rcl_editor_tree()

    def _refresh_rcl_editor_tree(self):
        self.rcl_editor_title_lbl.config(text=f"EDITOR - {self.rcl_active}")
        self.rcl_editor_tree.delete(*self.rcl_editor_tree.get_children())
        for rule in self.rcl_rulesets[self.rcl_active]:
            self.rcl_editor_tree.insert("", "end", values=[f"{v:g}" for v in rule])

    def _refresh_rcl_ruleset_combo(self):
        names = list(self.rcl_rulesets.keys())
        self.rcl_sel_regra_combo.config(values=names)

    def _refresh_rcl_files_tree(self):
        self.rcl_files_tree.delete(*self.rcl_files_tree.get_children())
        for row in self.rcl_files:
            tag = "padrao" if row["Regra"] == "Padrao" else ""
            self.rcl_files_tree.insert("", "end", values=(row["Arquivo"], row["Regra"]),
                                       tags=(tag,) if tag else ())

    def _on_rcl_scan(self):
        dir_in = self.rcl_dir_in.get()
        if not dir_in:
            messagebox.showwarning("Aviso", "Selecione a pasta de entrada.")
            return
        arqs = list_tif_files(dir_in)
        if not arqs:
            messagebox.showwarning("Aviso", "Nenhum .tif encontrado.")
            return
        self.rcl_files = [{"Arquivo": os.path.basename(a), "Caminho": a, "Regra": "Padrao"} for a in arqs]
        self._refresh_rcl_files_tree()
        self.rcl_log.add(f"=== SCAN: {len(arqs)} arquivo(s) ===")
        self.rcl_log.add("Todos os arquivos atribuidos a regra 'Padrao'.")
        self.rcl_log.add("Selecione arquivos e atribua regras especificas se necessario.")

    def _on_rcl_select_active_rule(self, _event=None):
        sel = self.rcl_sel_regra.get()
        if sel in self.rcl_rulesets:
            self.rcl_active = sel
            self._refresh_rcl_editor_tree()

    def _on_rcl_create_rule(self):
        nome = self.rcl_new_rule_name.get().strip()
        if not nome:
            messagebox.showwarning("Aviso", "Digite um nome para a nova regra.")
            return
        if nome not in self.rcl_rulesets:
            self.rcl_rulesets[nome] = raster_ops.default_ruleset()
        self.rcl_active = nome
        self.rcl_sel_regra.set(nome)
        self._refresh_rcl_ruleset_combo()
        self._refresh_rcl_editor_tree()
        self.rcl_new_rule_name.set("")
        messagebox.showinfo("Regra criada", f"Regra criada: {nome}")

    def _on_rcl_assign_selected(self):
        sel_ids = self.rcl_files_tree.selection()
        if not sel_ids:
            messagebox.showwarning("Aviso", "Selecione arquivos na tabela antes de atribuir.")
            return
        indices = [self.rcl_files_tree.index(i) for i in sel_ids]
        for idx in indices:
            self.rcl_files[idx]["Regra"] = self.rcl_active
        self._refresh_rcl_files_tree()
        messagebox.showinfo("Atribuido", f"Regra '{self.rcl_active}' atribuida a {len(indices)} arquivo(s).")

    def _on_rcl_assign_all(self):
        if not self.rcl_files:
            return
        for row in self.rcl_files:
            row["Regra"] = self.rcl_active
        self._refresh_rcl_files_tree()
        messagebox.showinfo("Atribuido", f"Regra '{self.rcl_active}' atribuida a todos os {len(self.rcl_files)} arquivo(s).")

    def _on_rcl_editor_double_click(self, event):
        def on_commit(row_id, col_name, new_value):
            try:
                val = float(new_value)
            except ValueError:
                messagebox.showwarning("Aviso", "Valor invalido. Use apenas numeros.")
                return
            self.rcl_editor_tree.set(row_id, col_name, f"{val:g}")

        edit_treeview_cell(self.rcl_editor_tree, event,
                            editable_columns=set(config.RULE_COLUMNS), on_commit=on_commit)

    def _on_rcl_add_row(self):
        self.rcl_editor_tree.insert("", "end", values=(0, 0, 0))

    def _on_rcl_del_row(self):
        sel = self.rcl_editor_tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione uma linha para remover.")
            return
        if len(self.rcl_editor_tree.get_children()) <= 1:
            messagebox.showwarning("Aviso", "A regra deve ter ao menos uma linha.")
            return
        self.rcl_editor_tree.delete(*sel)

    def _on_rcl_save_rule(self):
        rows = []
        for item in self.rcl_editor_tree.get_children():
            vals = self.rcl_editor_tree.item(item, "values")
            rows.append(tuple(float(v) for v in vals))
        self.rcl_rulesets[self.rcl_active] = rows
        messagebox.showinfo("Salvo", f"Regra '{self.rcl_active}' salva com {len(rows)} linhas.")

    def _on_rcl_load_txt(self):
        path = filedialog.askopenfilename(title="Selecionar arquivo de regras", filetypes=[("Texto", "*.txt")])
        if not path:
            return
        rows = raster_ops.parse_rules_txt(path)
        if not rows:
            messagebox.showwarning("Aviso", "Nenhuma regra valida encontrada no arquivo.")
            return
        nome = os.path.splitext(os.path.basename(path))[0]
        self.rcl_rulesets[nome] = rows
        self.rcl_active = nome
        self.rcl_sel_regra.set(nome)
        self._refresh_rcl_ruleset_combo()
        self._refresh_rcl_editor_tree()
        messagebox.showinfo("Carregado", f"{len(rows)} regras carregadas de '{os.path.basename(path)}' como '{nome}'.")

    def _on_rcl_save_txt(self):
        path = filedialog.asksaveasfilename(title="Salvar regras em arquivo", defaultextension=".txt",
                                             filetypes=[("Texto", "*.txt")])
        if not path:
            return
        rows = []
        for item in self.rcl_editor_tree.get_children():
            vals = self.rcl_editor_tree.item(item, "values")
            rows.append(tuple(float(v) for v in vals))
        try:
            raster_ops.save_rules_txt(rows, path, regra_nome=self.rcl_active)
            messagebox.showinfo("Salvo", f"Regras salvas em: {os.path.basename(path)}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro ao salvar", str(exc))

    def _on_rcl_run(self):
        if not self.rcl_files:
            messagebox.showwarning("Aviso", "Escaneie os arquivos primeiro.")
            return
        dir_out = self.rcl_dir_out.get()
        if not dir_out:
            messagebox.showwarning("Aviso", "Selecione a pasta de saida.")
            return
        ensure_dir(dir_out)

        others_map = {"NA (sem valor)": "NA", "Zero (0)": "0"}
        right_map = {"Ambos os lados (NA)": "NA", "Direita (TRUE)": "TRUE", "Esquerda (FALSE)": "FALSE"}
        others = others_map.get(self.rcl_others.get(), "NA")
        right = right_map.get(self.rcl_right.get(), "NA")
        dtype = config.dtype_do_rotulo(self.rcl_dtype.get())
        include_lowest = self.rcl_include_lowest.get()
        overwrite = self.rcl_overwrite.get()

        files = list(self.rcl_files)
        rulesets = dict(self.rcl_rulesets)

        self.rcl_log.add("=== RECLASSIFICACAO INICIADA ===")
        self.rcl_log.add(f"Arquivos: {len(files)} | Dtype: {config.rotulo_de_dtype(dtype)} "
                         f"| Others: {others} | Right: {right}")
        self.rcl_rodape.iniciar(f"Reclassificando {len(files)} arquivo(s)...")

        def worker(q: queue.Queue):
            n_ok = 0
            n_err = 0
            for i, row in enumerate(files):
                arquivo, nome_arq, regra_nome = row["Caminho"], row["Arquivo"], row["Regra"]
                q.put(("status", f"[{i + 1}/{len(files)}] {nome_arq}"))
                if regra_nome not in rulesets:
                    q.put(("log", f"  ERRO: Regra '{regra_nome}' nao encontrada para {nome_arq}"))
                    n_err += 1
                    q.put(("progress", 100.0 * (i + 1) / len(files)))
                    continue
                rules = rulesets[regra_nome]
                nom_out = os.path.join(dir_out, f"Reclass_{nome_arq}")
                q.put(("log", f"[{i + 1}/{len(files)}] {nome_arq}  (regra: {regra_nome})"))
                try:
                    raster_ops.reclassify_raster(
                        arquivo, rules, nom_out, dtype=dtype, others=others,
                        right=right, include_lowest=include_lowest, overwrite=overwrite,
                    )
                    q.put(("log", f"  OK -> Salvo: {os.path.basename(nom_out)}"))
                    n_ok += 1
                except Exception as exc:  # noqa: BLE001
                    q.put(("log", f"  ERRO: {exc}"))
                    n_err += 1
                q.put(("progress", 100.0 * (i + 1) / len(files)))
            q.put(("done", (n_ok, n_err)))

        def on_done(payload):
            n_ok, n_err = payload
            self.rcl_log.add(f"=== CONCLUIDO | OK: {n_ok} | ERRO: {n_err} ===")
            self.rcl_rodape.concluir(f"Reclassificacao concluida - OK: {n_ok} | Erros: {n_err}")
            messagebox.showinfo("Concluido", f"Reclassificacao - OK: {n_ok} | Erros: {n_err}")

        def on_error(msg):
            self.rcl_rodape.zerar("Falha na reclassificacao.")
            self.rcl_log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro", msg)

        run_background_task(self.root, worker, self.rcl_log.add, on_done, on_error,
                            [self.rcl_run_btn], progress_cb=self.rcl_rodape.progresso,
                            status_cb=self.rcl_rodape.status)

    # =========================================================================
    # ABA 3 - Separar Bandas
    # =========================================================================

    def _build_tab_bands(self):
        frame = self.tab_bands
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right = ttk.Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.bands_rodape = Rodape(right, "Selecione as pastas e separe as bandas.")

        paths_box = ttk.LabelFrame(left, text="Caminhos")
        paths_box.pack(fill="x", pady=4)
        self.bands_dir_in = tk.StringVar()
        self.bands_dir_out = tk.StringVar()
        path_row(paths_box, "Pasta dos rasters (.tif)", "dir",
                 lambda lbl: (select_directory(self.bands_dir_in, lbl, "Selecionar pasta de entrada"),
                              self._refresh_bands_input_table()))
        path_row(paths_box, "Pasta de saida", "dir",
                 lambda lbl: select_directory(self.bands_dir_out, lbl, "Selecionar pasta de saida"))

        params_box = ttk.LabelFrame(left, text="Parametros")
        params_box.pack(fill="x", pady=4)
        ttk.Label(params_box, text="Padrao de nomenclatura:").pack(anchor="w")
        self.bands_padrao_label = tk.StringVar(value=config.BAND_NAMING_PATTERNS["bioclim"])
        self._bands_padrao_reverse = {v: k for k, v in config.BAND_NAMING_PATTERNS.items()}
        ttk.Combobox(params_box, textvariable=self.bands_padrao_label,
                     values=list(config.BAND_NAMING_PATTERNS.values()), state="readonly").pack(fill="x", pady=2)
        self.bands_overwrite = tk.BooleanVar(value=True)
        ttk.Checkbutton(params_box, text="Sobrescrever arquivos existentes",
                         variable=self.bands_overwrite).pack(anchor="w", pady=4)

        self.bands_run_btn = ttk.Button(left, text="Separar Bandas", command=self._on_bands_run)
        self.bands_run_btn.pack(fill="x", pady=(10, 2))
        ttk.Button(left, text="Limpar Log", command=lambda: self.bands_log.clear()).pack(fill="x")

        tables_row = ttk.Frame(right)
        tables_row.pack(fill="both", expand=True)
        in_box = ttk.LabelFrame(tables_row, text="Arquivos de Entrada")
        in_box.pack(side="left", fill="both", expand=True, padx=(0, 4))
        res_box = ttk.LabelFrame(tables_row, text="Resultado do Processamento")
        res_box.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self.bands_in_tree = make_result_tree(in_box, {"Arquivo": "Arquivo"})
        self.bands_res_tree = make_result_tree(
            res_box, {"Arquivo": "Arquivo", "Bandas": "Bandas", "Status": "Status"}
        )

        log_box = ttk.LabelFrame(right, text="Log de Execucao")
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.bands_log = LogPanel(log_box)

    def _refresh_bands_input_table(self):
        arqs = list_tif_files(self.bands_dir_in.get())
        self.bands_in_tree.delete(*self.bands_in_tree.get_children())
        rows = [os.path.basename(a) for a in arqs] or ["Nenhum .tif encontrado"]
        for r in rows:
            self.bands_in_tree.insert("", "end", values=(r,))

    def _on_bands_run(self):
        dir_in = self.bands_dir_in.get()
        dir_out = self.bands_dir_out.get()
        if not (dir_in and dir_out):
            messagebox.showwarning("Aviso", "Selecione os diretorios de entrada e saida.")
            return
        arqs = list_tif_files(dir_in)
        if not arqs:
            messagebox.showwarning("Aviso", "Nenhum arquivo .tif encontrado.")
            return
        ensure_dir(dir_out)
        padrao = self._bands_padrao_reverse.get(self.bands_padrao_label.get(), "bioclim")
        overwrite = self.bands_overwrite.get()

        self.bands_log.add("=== SEPARACAO DE BANDAS INICIADA ===")
        self.bands_log.add(f"Arquivos: {len(arqs)} | Padrao: {padrao}")
        self.bands_rodape.iniciar(f"Separando bandas de {len(arqs)} arquivo(s)...")

        def worker(q: queue.Queue):
            resultados = []
            for i, arq in enumerate(arqs):
                q.put(("status", f"[{i + 1}/{len(arqs)}] {os.path.basename(arq)}"))
                q.put(("log", f"[{i + 1}/{len(arqs)}] {os.path.basename(arq)}"))
                try:
                    n, names = raster_ops.split_bands(arq, dir_out, padrao=padrao, overwrite=overwrite)
                    q.put(("log", f"   Bandas detectadas: {n}"))
                    for j, name in enumerate(names, start=1):
                        q.put(("log", f"  OK -> Banda {j} -> {name}"))
                    resultados.append({"Arquivo": os.path.basename(arq), "Bandas": n, "Status": "OK"})
                except Exception as exc:  # noqa: BLE001
                    q.put(("log", f"  ERRO: {exc}"))
                    resultados.append({"Arquivo": os.path.basename(arq), "Bandas": "-", "Status": "ERRO"})
                q.put(("progress", 100.0 * (i + 1) / len(arqs)))
            q.put(("done", resultados))

        def on_done(resultados):
            populate_status_tree(self.bands_res_tree, resultados, ["Arquivo", "Bandas", "Status"])
            n_ok = sum(1 for r in resultados if r["Status"] == "OK")
            n_err = len(resultados) - n_ok
            self.bands_log.add(f"=== CONCLUIDO | OK: {n_ok} | ERRO: {n_err} ===")
            self.bands_rodape.concluir(f"Separacao concluida - OK: {n_ok} | Erros: {n_err}")
            messagebox.showinfo("Concluido", f"Separacao - OK: {n_ok} | Erros: {n_err}")

        def on_error(msg):
            self.bands_rodape.zerar("Falha na separacao de bandas.")
            self.bands_log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro", msg)

        run_background_task(self.root, worker, self.bands_log.add, on_done, on_error,
                            [self.bands_run_btn], progress_cb=self.bands_rodape.progresso,
                            status_cb=self.bands_rodape.status)

    # =========================================================================
    # ABA 4 - Reamostrar
    # =========================================================================

    def _build_tab_resample(self):
        frame = self.tab_resample
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right = ttk.Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.res_rodape = Rodape(right, "Escaneie os arquivos e execute a reamostragem.")

        paths_box = ttk.LabelFrame(left, text="Caminhos")
        paths_box.pack(fill="x", pady=4)
        self.res_modelo_path = tk.StringVar()
        self.res_dir_in = tk.StringVar()
        self.res_dir_out = tk.StringVar()
        path_row(paths_box, "Raster Modelo (template)", "file",
                 lambda lbl: (select_file(self.res_modelo_path, lbl, "Selecionar raster modelo",
                                           [("GeoTIFF", "*.tif *.tiff"), ("Todos", "*.*")]),
                              self._refresh_res_model_meta()))
        path_row(paths_box, "Pasta dos rasters alvo", "dir",
                 lambda lbl: select_directory(self.res_dir_in, lbl, "Selecionar pasta de entrada"))
        path_row(paths_box, "Pasta de saida", "dir",
                 lambda lbl: select_directory(self.res_dir_out, lbl, "Selecionar pasta de saida"))

        params_box = ttk.LabelFrame(left, text="Parametros Gerais")
        params_box.pack(fill="x", pady=4)
        # Desmarcado por padrao: com a opcao ligada, um Dist_VEGET 100% valido
        # saia da reamostragem com 7% de pixels uteis, porque o zero desses
        # rasters e dado ("dentro da classe") e nao ausencia de dado.
        self.res_zeros_na = tk.BooleanVar(value=False)
        ttk.Checkbutton(params_box, text="Converter zeros (0) para NA",
                         variable=self.res_zeros_na).pack(anchor="w")
        ttk.Label(
            params_box, wraplength=330, justify="left", foreground="#40506a",
            font=("Segoe UI", 8),
            text=("MARQUE apenas quando o zero significar ausencia de dado no "
                  "raster de entrada (ex.: mascaras do MapBiomas). Em rasters de "
                  "DISTANCIA o zero e dado valido ('dentro da classe') e marcar "
                  "esta opcao apaga as areas de interesse."),
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(params_box, text="Snap no crop:").pack(anchor="w")
        self.res_snap = tk.StringVar(value="out")
        ttk.Combobox(params_box, textvariable=self.res_snap, values=config.SNAP_OPTIONS,
                     state="readonly").pack(fill="x", pady=2)
        ttk.Label(params_box, text="Tipo de dado de saida (padrao):").pack(anchor="w")
        # Rotulo com bits e tipo numpy; ver comentario na aba Reclassificar.
        self.res_dtype_default = tk.StringVar(value=config.rotulo_de_dtype("FLT4S"))
        ttk.Combobox(params_box, textvariable=self.res_dtype_default,
                     values=config.rotulos_de_dtype(config.RESAMPLE_DTYPES),
                     state="readonly").pack(fill="x", pady=2)
        self.res_overwrite = tk.BooleanVar(value=True)
        ttk.Checkbutton(params_box, text="Sobrescrever arquivos existentes",
                         variable=self.res_overwrite).pack(anchor="w", pady=4)

        ttk.Button(left, text="Escanear Arquivos", command=self._on_res_scan).pack(fill="x", pady=(10, 2))
        self.res_run_btn = ttk.Button(left, text="Executar Reamostramento", command=self._on_res_run)
        self.res_run_btn.pack(fill="x", pady=2)
        ttk.Button(left, text="Limpar Log", command=lambda: self.res_log.clear()).pack(fill="x")

        self.res_rows: List[Dict[str, str]] = []
        self.res_model_meta: Optional[raster_ops.RasterMeta] = None

        meta_box = ttk.LabelFrame(right, text="Metadados do Raster Modelo")
        meta_box.pack(fill="x")
        self.res_model_meta_text = tk.Text(meta_box, height=6, state="disabled", font=("Consolas", 9))
        self.res_model_meta_text.pack(fill="x")

        camadas_box = ttk.LabelFrame(right, text="Camadas Detectadas")
        camadas_box.pack(fill="both", expand=True, pady=(8, 0))

        bulk_row = ttk.Frame(camadas_box)
        bulk_row.pack(fill="x", pady=4)
        ttk.Label(bulk_row, text="Metodo a aplicar:").pack(side="left")
        self.res_bulk_metodo = tk.StringVar(value="near")
        ttk.Radiobutton(bulk_row, text="near (categorico)", variable=self.res_bulk_metodo,
                         value="near").pack(side="left", padx=4)
        ttk.Radiobutton(bulk_row, text="bilinear (continuo)", variable=self.res_bulk_metodo,
                         value="bilinear").pack(side="left", padx=4)
        ttk.Button(bulk_row, text="Aplicar a selecionados", command=self._on_res_apply_selected).pack(side="left", padx=6)
        ttk.Button(bulk_row, text="Aplicar a todos", command=self._on_res_apply_all).pack(side="left", padx=4)

        ttk.Label(camadas_box, text=("Selecione linhas com clique/Shift/Ctrl. Duplo clique em Metodo ou Dtype para "
                                      "editar diretamente."), foreground="#6c757d", font=("Segoe UI", 8)).pack(anchor="w")

        cols = ("Arquivo", "Tipo", "Bandas", "Metodo", "Dtype", "Origem")
        # "Dtype" agora mostra "FLT4S - 32 bits - float32" e precisa de espaco;
        # "Tipo" (o do arquivo de ENTRADA) ganhou os bits pelo mesmo motivo.
        larguras = {"Arquivo": 230, "Tipo": 120, "Bandas": 65, "Metodo": 90,
                    "Dtype": 190, "Origem": 70}
        self.res_tree = ttk.Treeview(camadas_box, columns=cols, show="headings", height=10, selectmode="extended")
        for c in cols:
            self.res_tree.heading(c, text=c)
            self.res_tree.column(c, width=larguras[c], minwidth=55,
                                 stretch=(c in ("Arquivo", "Dtype")),
                                 anchor="w" if c == "Arquivo" else "center")
        self.res_tree.pack(fill="both", expand=True)
        self.res_tree.bind("<Double-1>", self._on_res_tree_double_click)

        log_box = ttk.LabelFrame(right, text="Log de Execucao")
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.res_log = LogPanel(log_box)

    def _refresh_res_model_meta(self):
        path = self.res_modelo_path.get()
        if not path:
            return
        try:
            meta = raster_ops.read_meta(path)
            self.res_model_meta = meta
            text = raster_ops.describe_meta(meta)
        except Exception as exc:  # noqa: BLE001
            text = f"Erro ao ler raster modelo: {exc}"
        self.res_model_meta_text.configure(state="normal")
        self.res_model_meta_text.delete("1.0", "end")
        self.res_model_meta_text.insert("end", text)
        self.res_model_meta_text.configure(state="disabled")

    def _refresh_res_tree(self):
        self.res_tree.delete(*self.res_tree.get_children())
        for row in self.res_rows:
            tag = "manual" if row["Origem"] == "manual" else "auto"
            self.res_tree.insert("", "end", values=(row["Arquivo"], _tipo_de_entrada(row["Tipo"]),
                                                     row["Bandas"], row["Metodo"],
                                                     config.rotulo_de_dtype(row["Dtype"]),
                                                     row["Origem"]),
                                  tags=(tag,))
        self.res_tree.tag_configure("manual", foreground="#198754")
        self.res_tree.tag_configure("auto", foreground="#6c757d")

    def _on_res_scan(self):
        dir_in = self.res_dir_in.get()
        if not dir_in:
            messagebox.showwarning("Aviso", "Selecione a pasta de entrada.")
            return
        arqs = list_tif_files(dir_in)
        if not arqs:
            messagebox.showwarning("Aviso", "Nenhum arquivo .tif encontrado.")
            return
        self.res_log.add(f"=== SCAN: {len(arqs)} arquivo(s) ===")
        rows = []
        # Guardamos o CODIGO; o rotulo com bits e so o que vai para a tela.
        dtype_default = config.dtype_do_rotulo(self.res_dtype_default.get())
        for arq in arqs:
            metodo = raster_ops.detectar_metodo_resample(arq)
            try:
                meta = raster_ops.read_meta(arq)
                tipo, nbandas = meta.dtype, meta.count
            except Exception:
                tipo, nbandas = "?", "?"
            self.res_log.add(f"{os.path.basename(arq)}  ->  {metodo}  ({tipo} | {nbandas} bandas)")
            rows.append({
                "Arquivo": os.path.basename(arq), "Caminho": arq, "Tipo": tipo, "Bandas": nbandas,
                "Metodo": metodo, "Dtype": dtype_default, "Origem": "auto",
            })
        self.res_rows = rows
        self._refresh_res_tree()
        self.res_log.add(f"Scan concluido: {len(rows)} arquivo(s). Ajuste Metodo/Dtype se necessario.")

    def _on_res_tree_double_click(self, event):
        def on_commit(row_id, col_name, new_value):
            idx = self.res_tree.index(row_id)
            if col_name == "Metodo":
                val = new_value.strip().lower()
                if val not in config.RESAMPLE_METHODS:
                    messagebox.showwarning("Aviso", "Metodo invalido. Use 'near' ou 'bilinear'.")
                    return
                self.res_rows[idx]["Metodo"] = val
            elif col_name == "Dtype":
                # Aceita tanto o rotulo inteiro (escolhido na lista) quanto o
                # codigo digitado na mao.
                val = config.dtype_do_rotulo(new_value)
                if val not in config.RESAMPLE_DTYPES:
                    messagebox.showwarning("Aviso", f"Dtype invalido. Aceitos: {', '.join(config.RESAMPLE_DTYPES)}")
                    return
                self.res_rows[idx]["Dtype"] = val
            else:
                return
            self.res_rows[idx]["Origem"] = "manual"
            self._refresh_res_tree()

        combo_values = {"Metodo": list(config.RESAMPLE_METHODS),
                        "Dtype": config.rotulos_de_dtype(config.RESAMPLE_DTYPES)}
        edit_treeview_cell(self.res_tree, event, editable_columns={"Metodo", "Dtype"},
                            combo_values=combo_values, on_commit=on_commit)

    def _on_res_apply_selected(self):
        sel_ids = self.res_tree.selection()
        if not sel_ids:
            messagebox.showwarning("Aviso", "Selecione ao menos uma linha na tabela antes de aplicar.")
            return
        metodo = self.res_bulk_metodo.get()
        for item in sel_ids:
            idx = self.res_tree.index(item)
            self.res_rows[idx]["Metodo"] = metodo
            self.res_rows[idx]["Origem"] = "manual"
        self._refresh_res_tree()

    def _on_res_apply_all(self):
        if not self.res_rows:
            return
        metodo = self.res_bulk_metodo.get()
        for row in self.res_rows:
            row["Metodo"] = metodo
            row["Origem"] = "manual"
        self._refresh_res_tree()

    def _on_res_run(self):
        if not self.res_rows:
            messagebox.showwarning("Aviso", "Execute o scan primeiro.")
            return
        if not self.res_modelo_path.get():
            messagebox.showwarning("Aviso", "Selecione o raster modelo.")
            return
        dir_out = self.res_dir_out.get()
        if not dir_out:
            messagebox.showwarning("Aviso", "Selecione a pasta de saida.")
            return
        try:
            model_meta = raster_ops.read_meta(self.res_modelo_path.get())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", f"Erro ao carregar modelo: {exc}")
            return
        ensure_dir(dir_out)

        rows = list(self.res_rows)
        zeros_na = self.res_zeros_na.get()
        overwrite = self.res_overwrite.get()

        self.res_log.add("=== REAMOSTRAMENTO INICIADO ===")
        self.res_log.add(f"Modelo: {os.path.basename(self.res_modelo_path.get())} | Arquivos: {len(rows)}")
        self.res_rodape.iniciar(f"Reamostrando {len(rows)} arquivo(s)...")

        avisos_totais: List[str] = []

        def worker(q: queue.Queue):
            n_ok = 0
            n_err = 0
            for i, row in enumerate(rows):
                nome_sem = re.sub(r"^[01]_", "", row["Arquivo"])
                nom_out = os.path.join(dir_out, f"res_{nome_sem}")
                q.put(("status", f"[{i + 1}/{len(rows)}] {row['Arquivo']}"))
                q.put(("log", f"[{i + 1}/{len(rows)}] {row['Arquivo']}  metodo:{row['Metodo']} "
                              f"| dtype: {config.rotulo_de_dtype(row['Dtype'])}"))
                try:
                    stats = raster_ops.resample_raster(
                        row["Caminho"], model_meta, nom_out, method=row["Metodo"], dtype=row["Dtype"],
                        zeros_to_na=zeros_na, overwrite=overwrite,
                    )
                    q.put(("log", f"  OK -> {os.path.basename(nom_out)} "
                                   f"({stats['pct_valido_saida']:.1f}% da grade com valor)"))
                    for aviso in raster_ops.avisos_da_reamostragem(
                        os.path.basename(nom_out), stats
                    ):
                        q.put(("log", f"  {aviso}"))
                        avisos_totais.append(aviso)
                    n_ok += 1
                except Exception as exc:  # noqa: BLE001
                    q.put(("log", f"  ERRO: {exc}"))
                    n_err += 1
                q.put(("progress", 100.0 * (i + 1) / len(rows)))
            q.put(("done", (n_ok, n_err)))

        def on_done(payload):
            n_ok, n_err = payload
            self.res_log.add(f"=== CONCLUIDO | OK: {n_ok} | ERRO: {n_err} ===")
            resumo = f"Reamostramento - OK: {n_ok} | Erros: {n_err}"
            self.res_rodape.concluir(resumo + (f" | {len(avisos_totais)} aviso(s)"
                                               if avisos_totais else ""))
            if avisos_totais:
                # Perder metade da grade em silencio foi o que zerou a amostragem
                # depois; agora o aviso sobe ate a caixa de dialogo.
                self.res_log.add(f"=== {len(avisos_totais)} AVISO(S) - confira acima ===")
                messagebox.showwarning(
                    "Concluido com avisos",
                    resumo + "\n\n" + "\n\n".join(avisos_totais[:5])
                    + ("\n\n(...)" if len(avisos_totais) > 5 else ""),
                )
            else:
                messagebox.showinfo("Concluido", resumo)

        def on_error(msg):
            self.res_rodape.zerar("Falha na reamostragem.")
            self.res_log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro", msg)

        run_background_task(self.root, worker, self.res_log.add, on_done, on_error,
                            [self.res_run_btn], progress_cb=self.res_rodape.progresso,
                            status_cb=self.res_rodape.status)

    # =========================================================================
    # ABA 5 - Merge de Rasters
    # =========================================================================

    def _build_tab_merge(self):
        """Delegada a tab_merge.py. Como na aba de amostragem, o import fica
        aqui dentro para que uma falha nessa aba nao impeca as demais de
        abrir."""
        try:
            from interface import tab_merge
            self.aba_merge = tab_merge.build_tab(self, self.tab_merge)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Falha ao montar a aba de merge")
            aviso = ttk.Frame(self.tab_merge, padding=20)
            aviso.pack(fill="both", expand=True)
            ttk.Label(aviso, text="Aba de Merge indisponivel",
                      font=("Segoe UI", 12, "bold")).pack(anchor="w")
            ttk.Label(
                aviso, wraplength=700, justify="left",
                text=(f"Motivo: {exc}\n\n"
                      "As demais abas continuam funcionando normalmente."),
            ).pack(anchor="w", pady=(8, 0))

    # =========================================================================
    # ABA 6 - Verificar Alinhamento
    # =========================================================================

    def _build_tab_verify(self):
        frame = self.tab_verify
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y", padx=8, pady=8)
        right = ttk.Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.ver_rodape = Rodape(right, "Selecione o modelo e a pasta a conferir.")

        paths_box = ttk.LabelFrame(left, text="Caminhos")
        paths_box.pack(fill="x", pady=4)
        self.ver_modelo_path = tk.StringVar()
        self.ver_dir_in = tk.StringVar()
        path_row(paths_box, "Raster Modelo (template)", "file",
                 lambda lbl: select_file(self.ver_modelo_path, lbl, "Selecionar raster modelo",
                                         [("GeoTIFF", "*.tif *.tiff"), ("Todos", "*.*")]))
        path_row(paths_box, "Pasta dos rasters reamostrados", "dir",
                 lambda lbl: select_directory(self.ver_dir_in, lbl, "Selecionar pasta"))

        self.ver_run_btn = ttk.Button(left, text="Verificar Alinhamento", command=self._on_ver_run)
        self.ver_run_btn.pack(fill="x", pady=(10, 2))
        ttk.Button(left, text="Limpar Log", command=lambda: self.ver_log.clear()).pack(fill="x")

        res_box = ttk.LabelFrame(right, text="Resultado da Verificacao")
        res_box.pack(fill="both", expand=True)
        self.ver_tree = make_result_tree(res_box, {"Arquivo": "Arquivo", "Status": "Status", "Detalhes": "Detalhes"},
                                         height=14)

        log_box = ttk.LabelFrame(right, text="Log")
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.ver_log = LogPanel(log_box)

    def _on_ver_run(self):
        modelo_path = self.ver_modelo_path.get()
        dir_pasta = self.ver_dir_in.get()
        if not (modelo_path and dir_pasta):
            messagebox.showwarning("Aviso", "Selecione o raster modelo e a pasta.")
            return
        arqs = list_tif_files(dir_pasta)
        if not arqs:
            messagebox.showwarning("Aviso", "Nenhum .tif encontrado.")
            return
        try:
            model_meta = raster_ops.read_meta(modelo_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erro", f"Erro ao carregar modelo: {exc}")
            return

        self.ver_log.add("=== VERIFICACAO DE ALINHAMENTO INICIADA ===")
        self.ver_log.add(f"Modelo: {os.path.basename(modelo_path)} | Arquivos: {len(arqs)}")
        self.ver_rodape.iniciar(f"Conferindo {len(arqs)} arquivo(s)...")

        def worker(q: queue.Queue):
            resultados = []
            for i, arq in enumerate(arqs):
                q.put(("status", f"[{i + 1}/{len(arqs)}] {os.path.basename(arq)}"))
                res = raster_ops.verificar_alinhamento(arq, model_meta)
                detalhe = "" if res["Detalhes"] == "Alinhado" else f" -> {res['Detalhes']}"
                q.put(("log", f"{res['Status']} {os.path.basename(arq)}{detalhe}"))
                resultados.append(res)
                q.put(("progress", 100.0 * (i + 1) / len(arqs)))
            q.put(("done", resultados))

        def on_done(resultados):
            populate_status_tree(self.ver_tree, resultados, ["Arquivo", "Status", "Detalhes"])
            n_ok = sum(1 for r in resultados if r["Status"] == "OK")
            n_err = len(resultados) - n_ok
            self.ver_log.add(f"=== CONCLUIDO | OK: {n_ok} | ERRO: {n_err} ===")
            self.ver_rodape.concluir(f"Verificacao concluida - OK: {n_ok} | Erros: {n_err}")
            messagebox.showinfo("Concluido", f"Verificacao - OK: {n_ok} | Erros: {n_err}")

        def on_error(msg):
            self.ver_rodape.zerar("Falha na verificacao.")
            self.ver_log.add(f"ERRO FATAL: {msg}")
            messagebox.showerror("Erro", msg)

        run_background_task(self.root, worker, self.ver_log.add, on_done, on_error,
                            [self.ver_run_btn], progress_cb=self.ver_rodape.progresso,
                            status_cb=self.ver_rodape.status)


    # =========================================================================
    # ABA 7 - Amostragem
    # =========================================================================

    def _build_tab_sampling(self):
        """Delegada a tab_amostragem.py. O import fica aqui dentro para que uma
        falha do pacote amostragem_fogo (ex.: pyproj ausente) nao impeca as
        cinco abas originais de abrir."""
        try:
            from interface import tab_amostragem
            self.aba_amostragem = tab_amostragem.build_tab(self, self.tab_sampling)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Falha ao montar a aba de amostragem")
            aviso = ttk.Frame(self.tab_sampling, padding=20)
            aviso.pack(fill="both", expand=True)
            ttk.Label(aviso, text="Aba de Amostragem indisponivel",
                      font=("Segoe UI", 12, "bold")).pack(anchor="w")
            ttk.Label(
                aviso, wraplength=700, justify="left",
                text=(
                    f"Motivo: {exc}\n\n"
                    "Esta aba precisa da biblioteca pyproj. Instale com:\n"
                    r"    venv\Scripts\python.exe -m pip install -r requirements.txt"
                    "\n\nAs demais abas continuam funcionando normalmente."
                ),
            ).pack(anchor="w", pady=(8, 0))


def launch_app():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    app = GeoRasterToolsApp(root)
    root.mainloop()
