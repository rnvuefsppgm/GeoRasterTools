"""
raster_ops.py
=============

Traducao para Python (rasterio + numpy) de todas as funcoes de
processamento geoespacial do app Shiny original ``app_v4.R``
(GeoRaster Tools):

    - Recorte de rasters por shapefile (crop + mask)
    - Reclassificacao por regras (Val_Min, Val_Max, Novo_Val)
    - Separacao de bandas de rasters multibanda (com nomenclatura
      "bioclim"/"mat"/"generico")
    - Reamostragem para um raster modelo (near / bilinear)
    - Verificacao de alinhamento entre rasters e um raster modelo
    - Parse/gravacao de conjuntos de regras em arquivo .txt

Nenhum codigo de interface (Tkinter) fica neste modulo - apenas logica
pura, testavel via pytest sem necessidade de GUI.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.mask import mask as rio_mask
from rasterio.warp import reproject, transform_geom

import config

Rule = Tuple[float, float, float]
RuleSet = List[Rule]


# =============================================================================
# Regras de reclassificacao: parse / gravacao / conversoes
# =============================================================================


def default_ruleset() -> RuleSet:
    """Copia das regras default (equivalente a mat_to_df(REGRAS_DEFAULT))."""
    return [tuple(map(float, r)) for r in config.REGRAS_DEFAULT]


_SPLIT_RE = re.compile(r"[,;\t ]+")
_COMMENT_OR_BLANK_RE = re.compile(r"^\s*#|^\s*$")


def parse_rules_txt(filepath: str) -> Optional[RuleSet]:
    """Equivalente a parse_rules_txt() em R: le um arquivo .txt com linhas
    'Val_Min,Val_Max,Novo_Val' (aceita virgula, ponto-e-virgula, tab ou
    espaco como separador), ignorando comentarios (#) e linhas em branco.

    Retorna None se nenhuma regra valida for encontrada.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    rules: RuleSet = []
    for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")
        if _COMMENT_OR_BLANK_RE.match(line):
            continue
        parts = _SPLIT_RE.split(line.strip())
        nums: List[float] = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                continue
        if len(nums) >= 3:
            rules.append((nums[0], nums[1], nums[2]))

    return rules if rules else None


def save_rules_txt(rules: RuleSet, filepath: str, regra_nome: str = "Regra") -> None:
    """Equivalente a save_rules_txt() em R."""
    header = [
        "# GeoRaster Tools - Regras de Reclassificacao",
        f"# Regra: {regra_nome}",
        "# Formato: Val_Min,Val_Max,Novo_Val",
        "# ----------------------------------------",
    ]
    rows = [f"{r[0]:g},{r[1]:g},{r[2]:g}" for r in rules]
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header + rows) + "\n")


# =============================================================================
# Metadados de raster
# =============================================================================


@dataclass
class RasterMeta:
    path: str
    crs: Optional[str]
    transform: "rasterio.Affine"
    width: int
    height: int
    count: int
    dtype: str
    nodata: Optional[float]
    res: Tuple[float, float]
    bounds: Tuple[float, float, float, float]

    @property
    def canto(self) -> Tuple[float, float]:
        """Coordenada do canto superior esquerdo, no CRS do raster."""
        return (self.transform.c, self.transform.f)

    @property
    def origin(self) -> Tuple[float, float]:
        # Equivalente a terra::origin(): posicao (x,y) do canto,
        # normalizada ao intervalo [0, res).
        #
        # ATENCAO: esta e uma grandeza CIRCULAR. Fase ~0 e fase ~res descrevem
        # o mesmo alinhamento; sao os dois extremos do mesmo intervalo. Nunca
        # compare duas fases com `abs(a - b)` -- use `diferenca_de_fase()`.
        rx, ry = self.res
        ox = self.transform.c % rx if rx else 0.0
        oy = self.transform.f % ry if ry else 0.0
        return (ox, oy)


def read_meta(path: str) -> RasterMeta:
    with rasterio.open(path) as src:
        return RasterMeta(
            path=path,
            crs=(src.crs.to_string() if src.crs else None),
            transform=src.transform,
            width=src.width,
            height=src.height,
            count=src.count,
            dtype=src.dtypes[0],
            nodata=src.nodata,
            res=(abs(src.transform.a), abs(src.transform.e)),
            bounds=tuple(src.bounds),
        )


def describe_meta(meta: RasterMeta) -> str:
    xmin, ymin, xmax, ymax = meta.bounds
    return (
        f"Arquivo   : {os.path.basename(meta.path)}\n"
        f"Resolucao : {meta.res[0]:.8f} x {meta.res[1]:.8f}\n"
        f"Dimensoes : {meta.height} linhas  x  {meta.width} colunas\n"
        f"Extensao  : xmin={xmin:.6f}  xmax={xmax:.6f}  ymin={ymin:.6f}  ymax={ymax:.6f}\n"
        f"CRS       : {meta.crs or 'N/A'}\n"
        f"Tipo dado : {meta.dtype}\n"
        f"Bandas    : {meta.count}"
    )


# =============================================================================
# ABA 1 - Recorte de rasters (equivalente ao bloco btn_clip_run)
# =============================================================================


def _read_shapefile_geoms(shp_path: str, dst_crs) -> List[dict]:
    """Le geometrias de um shapefile (.shp) usando pyshp (sem depender de
    fiona/GDAL vetorial) e retorna uma lista de geometrias GeoJSON-like,
    reprojetadas para dst_crs se um arquivo .prj estiver presente e o CRS
    for diferente."""
    import shapefile as pyshp  # import tardio (dependencia leve)

    with pyshp.Reader(shp_path) as sf:
        shapes = sf.shapes()
        geoms = [s.__geo_interface__ for s in shapes if s.points]

    src_crs = None
    prj_path = os.path.splitext(shp_path)[0] + ".prj"
    if os.path.exists(prj_path):
        with open(prj_path, "r", encoding="utf-8", errors="replace") as fh:
            wkt = fh.read()
        try:
            src_crs = rasterio.crs.CRS.from_wkt(wkt)
        except Exception:
            src_crs = None

    if src_crs is not None and dst_crs is not None and src_crs != dst_crs:
        geoms = [transform_geom(src_crs, dst_crs, g) for g in geoms]

    return geoms


def clip_raster(raster_path: str, shp_path: str, output_path: str, overwrite: bool = True) -> None:
    """Equivalente ao trecho: r <- rast(arq); mask_r <- mask(crop(r, limite),
    limite); writeRaster(mask_r, ...)."""
    if not overwrite and os.path.exists(output_path):
        raise FileExistsError(f"Arquivo ja existe: {output_path}")

    with rasterio.open(raster_path) as src:
        geoms = _read_shapefile_geoms(shp_path, src.crs)
        if not geoms:
            raise ValueError("Nenhuma geometria valida encontrada no shapefile.")
        out_image, out_transform = rio_mask(src, geoms, crop=True)
        out_meta = src.meta.copy()
        out_meta.update({
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
        })

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with rasterio.open(output_path, "w", **out_meta) as dst:
        dst.write(out_image)


# =============================================================================
# ABA 2 - Reclassificacao (equivalente a classify() + writeRaster)
# =============================================================================


def _rule_mask(values: np.ndarray, vmin: float, vmax: float,
                right: Optional[bool], is_global_lowest: bool,
                include_lowest: bool) -> np.ndarray:
    """Constroi a mascara booleana de um intervalo (vmin, vmax] segundo a
    convencao 'right' (True/False/None) usada pelo terra::classify:

        right = True  -> intervalo (vmin, vmax]  ("Direita")
        right = False -> intervalo [vmin, vmax)  ("Esquerda")
        right = None  -> intervalo [vmin, vmax]  ("Ambos os lados")

    Quando right=True e include_lowest=True, o valor minimo global (entre
    todas as regras) tambem passa a ser incluido na regra correspondente.
    """
    if right is True:
        m = (values > vmin) & (values <= vmax)
        if include_lowest and is_global_lowest:
            m = m | (values == vmin)
        return m
    if right is False:
        m = (values >= vmin) & (values < vmax)
        return m
    # right is None -> ambos os lados fechados
    return (values >= vmin) & (values <= vmax)


def reclassify_array(
    data: np.ndarray,
    rules: RuleSet,
    others: Optional[float],
    right: Optional[bool] = None,
    include_lowest: bool = True,
    valid_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Aplica as regras de reclassificacao a um array numpy (qualquer numero
    de dimensoes). ``others`` e o valor atribuido a pixels que nao casam com
    nenhuma regra (np.nan para 'NA', ou 0.0). Regras sao avaliadas na ordem
    fornecida; a primeira regra que casar define o novo valor do pixel
    (equivalente ao comportamento linha-a-linha do terra::classify)."""
    out = np.full(data.shape, np.nan if others is None else float(others), dtype=np.float64)
    assigned = np.zeros(data.shape, dtype=bool)

    global_lowest = min(r[0] for r in rules) if rules else None

    for vmin, vmax, newval in rules:
        is_global_lowest = (global_lowest is not None and vmin == global_lowest)
        m = _rule_mask(data, vmin, vmax, right, is_global_lowest, include_lowest)
        m = m & ~assigned
        out[m] = newval
        assigned |= m

    if valid_mask is not None:
        out[~valid_mask] = np.nan if others is None else float(others)

    return out


def reclassify_raster(
    raster_path: str,
    rules: RuleSet,
    output_path: str,
    dtype: str = "INT1U",
    others: str = "NA",
    right: Optional[str] = "NA",
    include_lowest: bool = True,
    overwrite: bool = True,
) -> None:
    """Equivalente ao trecho: r_rcl <- classify(r, rcl=..., include.lowest=...,
    right=..., others=...); writeRaster(r_rcl, ..., datatype=..., gdal=...)."""
    if not overwrite and os.path.exists(output_path):
        raise FileExistsError(f"Arquivo ja existe: {output_path}")

    others_val = None if others == "NA" else 0.0
    right_val = {"NA": None, "TRUE": True, "FALSE": False}[right] if right in ("NA", "TRUE", "FALSE") else right

    with rasterio.open(raster_path) as src:
        data = src.read().astype(np.float64)
        nodata = src.nodata
        valid = np.isfinite(data)
        if nodata is not None:
            valid &= (data != nodata)

        result = np.empty_like(data)
        for b in range(data.shape[0]):
            result[b] = reclassify_array(
                data[b], rules, others_val, right_val, include_lowest, valid[b]
            )

        np_dtype = config.DTYPE_MAP.get(dtype, "float32")
        out_nodata = None if others_val is None else 0
        if others_val is None:
            # Sem valor definido -> usar um nodata compativel com o dtype de saida
            out_nodata = {
                "uint8": 255, "int16": -9999, "uint16": 0,
                "int32": -9999, "uint32": 0, "float32": np.nan, "float64": np.nan,
            }.get(np_dtype, 0)
            result = np.where(np.isnan(result), out_nodata, result)

        result = result.astype(np_dtype)

        out_meta = src.meta.copy()
        out_meta.update({"dtype": np_dtype, "nodata": out_nodata})

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with rasterio.open(
        output_path, "w",
        **out_meta,
        compress="LZW", tiled=True, bigtiff="IF_SAFER",
    ) as dst:
        dst.write(result)


# =============================================================================
# ABA 3 - Separacao de bandas (equivalente a gerar_nome_banda + writeRaster)
# =============================================================================


_BIOCLIM_RE = re.compile(
    r"^([A-Za-z0-9\-]+)_ssp(\d+)_([0-9]{4})-([0-9]{4})\.tif$", re.IGNORECASE
)
_META_RE = re.compile(r"_ssp(\d+)_([0-9]{4})-([0-9]{4})", re.IGNORECASE)
_TAIL_TOKEN_RE = re.compile(r"[^_]+$")


def gerar_nome_banda(nome_arquivo: str, num_banda: int, padrao: str = "bioclim") -> str:
    """Traducao literal de gerar_nome_banda() em R."""
    nome_base = os.path.basename(nome_arquivo)

    if padrao == "generico":
        nome_sem_ext = os.path.splitext(nome_base)[0]
        return f"{nome_sem_ext}_Band{num_banda}.tif"

    # Remove tudo ate (e incluindo) o ultimo "bioc_"
    if "bioc_" in nome_base.lower():
        idx = nome_base.lower().rfind("bioc_")
        nome_limpo = nome_base[idx + len("bioc_"):]
    else:
        nome_limpo = nome_base

    m = _BIOCLIM_RE.match(nome_limpo)
    if m:
        modelo_raw, cenario, ano_ini4, ano_fim4 = m.group(1), m.group(2), m.group(3), m.group(4)
    else:
        meta = _META_RE.search(nome_base)
        if meta is None:
            raise ValueError(
                f"Padrao nao reconhecido: '{nome_base}'\n"
                f"Use o padrao 'Generico' ou verifique o nome do arquivo."
            )
        cenario, ano_ini4, ano_fim4 = meta.group(1), meta.group(2), meta.group(3)
        pref = nome_base
        if pref.lower().endswith(".tif"):
            pref = pref[: -len(".tif")]
        pref = re.sub(r"_ssp\d+_[0-9]{4}-[0-9]{4}$", "", pref, flags=re.IGNORECASE)
        if "bioc_" in pref.lower():
            idx = pref.lower().rfind("bioc_")
            modelo_raw = pref[idx + len("bioc_"):]
        else:
            tail = _TAIL_TOKEN_RE.search(pref)
            modelo_raw = tail.group(0) if tail else ""
        if not modelo_raw:
            raise ValueError(f"Nao foi possivel extrair o modelo de: {nome_base}")

    modelo = modelo_raw.split("-")[0].title()
    ano_ini = ano_ini4[2:4]
    ano_fim = ano_fim4[2:4]
    return f"{modelo}_{cenario}_{ano_ini}_{ano_fim}_Bio{num_banda}.tif"


def split_bands(
    raster_path: str, output_dir: str, padrao: str = "bioclim", overwrite: bool = True
) -> Tuple[int, List[str]]:
    """Equivalente ao loop de separacao de bandas. Retorna (n_bandas,
    lista_de_nomes_de_saida)."""
    os.makedirs(output_dir, exist_ok=True)
    nomes_saida: List[str] = []

    with rasterio.open(raster_path) as src:
        n = src.count
        profile = src.profile.copy()
        profile.update(count=1)
        for j in range(1, n + 1):
            banda = src.read(j)
            try:
                nom_banda = gerar_nome_banda(raster_path, j, padrao)
            except ValueError:
                nom_banda = f"{os.path.splitext(os.path.basename(raster_path))[0]}_Band{j}.tif"
            out_path = os.path.join(output_dir, nom_banda)
            if not overwrite and os.path.exists(out_path):
                raise FileExistsError(f"Arquivo ja existe: {out_path}")
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(banda, 1)
            nomes_saida.append(nom_banda)

    return n, nomes_saida


# =============================================================================
# ABA 4 - Reamostragem (equivalente a detectar_metodo_resample + resample)
# =============================================================================

_INT_DTYPE_RE = re.compile(r"^(int|uint|byte)", re.IGNORECASE)


def detectar_metodo_resample(arquivo: str, sample_size: int = 3000) -> str:
    """Equivalente a detectar_metodo_resample() em R: 'near' para tipos
    inteiros/byte ou poucas classes unicas (<=25); 'bilinear' caso
    contrario."""
    try:
        with rasterio.open(arquivo) as src:
            dtype = src.dtypes[0]
            if _INT_DTYPE_RE.match(dtype) or dtype.lower() == "bool":
                return "near"
            band = src.read(1)
            nodata = src.nodata
            flat = band.ravel()
            if nodata is not None:
                flat = flat[flat != nodata]
            flat = flat[np.isfinite(flat)]
            sample = flat[:sample_size]
            n_uniq = len(np.unique(sample))
            return "near" if n_uniq <= 25 else "bilinear"
    except Exception:
        return "bilinear"


_RESAMPLING_MAP = {"near": Resampling.nearest, "bilinear": Resampling.bilinear}


def resample_raster(
    raster_path: str,
    model_meta: RasterMeta,
    output_path: str,
    method: str = "near",
    dtype: str = "FLT4S",
    zeros_to_na: bool = False,
    overwrite: bool = True,
) -> Dict[str, float]:
    """Equivalente ao trecho: if (zeros_na) r[r==0]<-NA; r_res <- resample(
    crop(r_alvo, r_modelo, snap=...), r_modelo, method=metodo); writeRaster(
    mask(r_res, r_modelo), ..., datatype=...).

    A reprojecao/reamostragem e feita diretamente para a grade do raster
    modelo (mesma transformacao, largura e altura), o que produz o mesmo
    resultado pratico do par crop+resample do R, com alinhamento de pixel
    perfeito ao modelo independente da opcao de snap escolhida na UI.

    ``zeros_to_na`` e **False** por padrao. No app em R ele vinha ligado, porque
    la a entrada tipica era mascara do MapBiomas, onde 0 e ausencia de dado. Em
    raster de distancia o 0 e o valor mais informativo -- "dentro da classe" --
    e converte-lo em NA apaga as areas de interesse; com reamostragem bilinear
    para grade mais grossa, cada NA ainda contamina as celulas vizinhas. Ligue a
    opcao apenas quando o zero realmente significar ausencia de dado.

    Devolve estatisticas da operacao (ver ``avisos_da_reamostragem``)."""
    if not overwrite and os.path.exists(output_path):
        raise FileExistsError(f"Arquivo ja existe: {output_path}")
    if method not in _RESAMPLING_MAP:
        raise ValueError(f"Metodo de reamostragem invalido: {method}")

    np_dtype = config.DTYPE_MAP.get(dtype, "float32")
    resampling_enum = _RESAMPLING_MAP[method]

    with rasterio.open(raster_path) as src:
        src_data = src.read(1).astype(np.float64)
        src_nodata = src.nodata

        validos_antes = int(np.isfinite(src_data).sum())
        perdidos_por_zero = 0
        if zeros_to_na:
            eh_zero = src_data == 0
            perdidos_por_zero = int(eh_zero.sum())
            src_data[eh_zero] = np.nan
        if src_nodata is not None and not np.isnan(src_nodata):
            src_data[src_data == src_nodata] = np.nan

        dst_data = np.full((model_meta.height, model_meta.width), np.nan, dtype=np.float64)

        reproject(
            source=src_data,
            destination=dst_data,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=model_meta.transform,
            dst_crs=model_meta.crs,
            src_nodata=np.nan,
            dst_nodata=np.nan,
            resampling=resampling_enum,
        )

    # Mascara final pelo raster modelo (equivalente a mask(r_res, r_modelo))
    with rasterio.open(model_meta.path) as model_src:
        model_data = model_src.read(1)
        model_nodata = model_src.nodata
        if model_nodata is None:
            model_valid = np.ones(model_data.shape, dtype=bool)
        elif isinstance(model_nodata, float) and np.isnan(model_nodata):
            # `dado != nan` e sempre verdadeiro: a comparacao direta nunca
            # mascarava nada em modelos com NoData igual a NaN.
            model_valid = np.isfinite(model_data.astype(np.float64))
        else:
            model_valid = model_data != model_nodata
    dst_data[~model_valid] = np.nan

    validos_depois = int(np.isfinite(dst_data).sum())
    total_saida = int(dst_data.size)

    out_nodata = {
        "uint8": 255, "int16": -9999, "uint16": 0,
        "int32": -9999, "uint32": 0, "float32": np.nan, "float64": np.nan,
    }.get(np_dtype, np.nan)
    out_array = np.where(np.isnan(dst_data), out_nodata, dst_data).astype(np_dtype)

    profile = {
        "driver": "GTiff",
        "height": model_meta.height,
        "width": model_meta.width,
        "count": 1,
        "dtype": np_dtype,
        "crs": model_meta.crs,
        "transform": model_meta.transform,
        "nodata": out_nodata,
        "compress": "LZW",
        "tiled": True,
        "bigtiff": "IF_SAFER",
    }
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(out_array, 1)

    return {
        "validos_antes": validos_antes,
        "zeros_convertidos": perdidos_por_zero,
        "validos_depois": validos_depois,
        "total_saida": total_saida,
        "pct_valido_saida": 100.0 * validos_depois / total_saida if total_saida else 0.0,
        "pct_zeros_entrada": (
            100.0 * perdidos_por_zero / validos_antes if validos_antes else 0.0
        ),
    }


def avisos_da_reamostragem(nome: str, stats: Dict[str, float]) -> List[str]:
    """Traduz as estatisticas em avisos para o log da aba Reamostrar.

    A opcao "Converter zeros (0) para NA" vem marcada por padrao, herdada do
    app em R, onde servia para rasters do MapBiomas em que 0 e ausencia de
    dado. Em raster de DISTANCIA o zero e o valor mais informativo -- significa
    "dentro da classe" -- e converte-lo em NA apaga justamente as areas de
    interesse. Pior: com reamostragem bilinear para uma grade mais grossa, cada
    NA contamina as celulas vizinhas. Foi assim que um Dist_VEGET 100 % valido
    virou um raster com 7 % de pixels uteis, e a intersecao dos preditores
    ficou vazia. O aviso abaixo torna essa perda visivel na hora.
    """
    avisos: List[str] = []
    pct_zeros = stats.get("pct_zeros_entrada", 0.0)
    pct_valido = stats.get("pct_valido_saida", 0.0)

    if pct_zeros >= 5.0:
        avisos.append(
            f"ATENCAO em {nome}: {pct_zeros:.1f}% dos pixels de entrada eram zero "
            "e foram convertidos para NA. Se este raster for de DISTANCIA, o zero "
            "e dado valido ('dentro da classe') e nao deveria virar NA - desmarque "
            "'Converter zeros (0) para NA' e refaca."
        )
    if pct_valido < 10.0:
        avisos.append(
            f"ATENCAO em {nome}: so {pct_valido:.1f}% da grade de saida ficou com "
            "valor valido. Confira o resultado antes de usar como preditor."
        )
    return avisos


# =============================================================================
# ABA 5 - Verificacao de alinhamento
# =============================================================================


def diferenca_de_fase(a: float, b: float, res: float) -> float:
    """Distancia CIRCULAR entre duas fases de grade, ambas em [0, res).

    A fase e o resto da divisao do canto pela resolucao. Como todo resto, ela
    da a volta: um canto em -73.99166666666666 tem fase ~0 e um canto em
    -73.99166666666667 -- 1,4e-14 ao lado, o mesmo pixel -- tem fase ~res.
    Comparadas com `abs(a - b)` elas parecem afastadas de uma celula inteira,
    e a verificacao acusava "Origem divergente" em rasters perfeitamente
    alinhados, gerados por ferramentas diferentes sobre a mesma grade.
    """
    if not res:
        return abs(a - b)
    distancia = abs(a - b) % res
    return min(distancia, res - distancia)


def verificar_alinhamento(
    arquivo: str, meta_modelo: RasterMeta, tol_pixel: float = 1e-6
) -> Dict[str, str]:
    """Equivalente a verificar_alinhamento() em R.

    ``tol_pixel`` e a tolerancia expressa em FRACAO DE PIXEL (padrao: um
    milionesimo). Uma tolerancia absoluta nao serve aqui: 1e-6 vale 11 cm num
    raster em graus e 1 micrometro num raster em metros. Relativa a resolucao,
    o mesmo numero significa a mesma coisa nos dois casos.
    """
    try:
        meta = read_meta(arquivo)
        tol_x = meta_modelo.res[0] * tol_pixel
        tol_y = meta_modelo.res[1] * tol_pixel

        res_ok = (
            abs(meta.res[0] - meta_modelo.res[0]) <= tol_x
            and abs(meta.res[1] - meta_modelo.res[1]) <= tol_y
        )
        nlin_ok = meta.height == meta_modelo.height
        ncol_ok = meta.width == meta_modelo.width
        tolerancias = (tol_x, tol_y, tol_x, tol_y)  # left, bottom, right, top
        ext_ok = all(
            abs(a - b) <= t
            for a, b, t in zip(meta.bounds, meta_modelo.bounds, tolerancias)
        )
        fase_x = diferenca_de_fase(meta.origin[0], meta_modelo.origin[0], meta_modelo.res[0])
        fase_y = diferenca_de_fase(meta.origin[1], meta_modelo.origin[1], meta_modelo.res[1])
        org_ok = fase_x <= tol_x and fase_y <= tol_y
        ok = res_ok and nlin_ok and ncol_ok and ext_ok and org_ok

        erros = []
        if not res_ok:
            erros.append(
                f"Res: {round(meta.res[0], 6)}x{round(meta.res[1], 6)} != "
                f"{round(meta_modelo.res[0], 6)}x{round(meta_modelo.res[1], 6)}"
            )
        if not nlin_ok:
            erros.append(f"Linhas: {meta.height} != {meta_modelo.height}")
        if not ncol_ok:
            erros.append(f"Colunas: {meta.width} != {meta_modelo.width}")
        if not ext_ok:
            piores = max(
                abs(a - b) / r if r else abs(a - b)
                for a, b, r in zip(
                    meta.bounds, meta_modelo.bounds,
                    (meta_modelo.res[0], meta_modelo.res[1]) * 2,
                )
            )
            erros.append(f"Extensao divergente ({piores:.3g} pixel)")
        if not org_ok:
            desloc_x = fase_x / meta_modelo.res[0] if meta_modelo.res[0] else fase_x
            desloc_y = fase_y / meta_modelo.res[1] if meta_modelo.res[1] else fase_y
            erros.append(
                f"Origem divergente (deslocamento de {desloc_x:.4g} x {desloc_y:.4g} pixel; "
                f"canto {meta.canto[0]:.10g},{meta.canto[1]:.10g} != "
                f"{meta_modelo.canto[0]:.10g},{meta_modelo.canto[1]:.10g})"
            )

        return {
            "Arquivo": os.path.basename(arquivo),
            "Status": "OK" if ok else "ERRO",
            "Detalhes": "Alinhado" if not erros else "; ".join(erros),
        }
    except Exception as exc:  # noqa: BLE001
        return {"Arquivo": os.path.basename(arquivo), "Status": "ERRO", "Detalhes": str(exc)}
