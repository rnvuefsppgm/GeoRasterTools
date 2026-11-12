"""
merge_ops.py
============

Nucleo da aba "Merge de Rasters" (sem nenhuma dependencia de interface).

Para que serve
--------------
Quando uma exportacao do Google Earth Engine passa do limite de tamanho, o
GEE parte o resultado em blocos e acrescenta ao nome o deslocamento do bloco
em pixels::

    reclass_ndvi_stdDev_tau-0000000000-0000000000.tif
    reclass_ndvi_stdDev_tau-0000046592-0000000000.tif
    reclass_ndvi_stdDev_tau_p-0000000000-0000000000.tif
    reclass_ndvi_stdDev_tau_p-0000046592-0000000000.tif

Os quatro arquivos acima sao DOIS mosaicos, nao um: o sufixo numerico e que
marca o bloco, e o que vem antes dele (``reclass_ndvi_stdDev_tau`` e
``reclass_ndvi_stdDev_tau_p``) e o nome real da camada. Agrupar por "prefixo
comum" juntaria os quatro no mesmo mosaico e misturaria duas camadas
diferentes -- por isso o agrupamento aqui recorta o sufixo de bloco e compara
o que sobra por igualdade exata.

Como o mosaico e montado
------------------------
Nao se carrega tudo na memoria. Os blocos do GEE costumam ter varios GB
somados, e ``rasterio.merge.merge()`` monta o resultado inteiro em um array
antes de gravar. Aqui a saida e percorrida em FAIXAS de linhas: para cada
faixa, so os pedacos das entradas que a cruzam sao lidos, combinados e
gravados de uma vez. O pico de memoria passa a ser o tamanho da faixa
(~64 MB por padrao), independente do tamanho do mosaico.

Sobreposicao entre blocos resolve-se pela regra "o primeiro que chegar vence"
(mesma regra default do ``rasterio.merge``), aplicada pixel a pixel sobre a
mascara de dado valido.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from rasterio.windows import Window

from utils import CancelledError

# ---------------------------------------------------------------------------
# Padroes de nome
# ---------------------------------------------------------------------------

#: Sufixo de bloco do Earth Engine: "-0000046592-0000000000" (dois numeros
#: longos no fim do nome). Aceita tambem "_" como separador.
PADRAO_BLOCO_GEE = re.compile(r"^(?P<base>.+?)[-_](?P<linha>\d{4,})[-_](?P<coluna>\d{4,})$")

#: Sequencia simples no fim do nome: "mosaico-1", "mosaico_02", "tile-10".
PADRAO_SEQUENCIA = re.compile(r"^(?P<base>.+?)[-_](?P<indice>\d{1,4})$")

MOTIVO_GEE = "blocos do Earth Engine"
MOTIVO_SEQUENCIA = "sequencia numerica"

#: Prefixo default do arquivo de saida (Merge_ + nome da camada).
PREFIXO_PADRAO = "Merge_"

#: Memoria alvo de cada faixa lida/gravada, em MB.
MEMORIA_POR_FAIXA_MB = 64.0


class MergeIncompativel(Exception):
    """Os rasters escolhidos nao formam um mosaico (CRS, resolucao, numero de
    bandas, tipo de dado ou grade diferentes)."""


# ---------------------------------------------------------------------------
# Agrupamento por nome
# ---------------------------------------------------------------------------


def base_do_arquivo(nome: str) -> Tuple[str, Optional[str]]:
    """Devolve (base, motivo) para um nome de arquivo.

    ``motivo`` e ``None`` quando o nome nao tem cara de bloco -- nesse caso a
    base e o proprio nome sem extensao, e o arquivo so entra em um grupo se
    outro arquivo chegar a mesma base por outro caminho (o que nao acontece,
    na pratica, para nomes sem sufixo)."""
    radical = os.path.splitext(os.path.basename(nome))[0]

    m = PADRAO_BLOCO_GEE.match(radical)
    if m:
        return m.group("base"), MOTIVO_GEE

    m = PADRAO_SEQUENCIA.match(radical)
    if m:
        return m.group("base"), MOTIVO_SEQUENCIA

    return radical, None


@dataclass(frozen=True)
class GrupoDeMerge:
    """Um conjunto de arquivos que, juntos, formam um unico raster."""

    base: str
    arquivos: Tuple[str, ...]
    motivo: str

    @property
    def quantidade(self) -> int:
        return len(self.arquivos)

    def nome_de_saida(self, prefixo: str = PREFIXO_PADRAO) -> str:
        return nome_de_saida(self.base, prefixo)


def nome_de_saida(base: str, prefixo: str = PREFIXO_PADRAO) -> str:
    """``Merge_reclass_ndvi_stdDev_tau.tif``. O prefixo e opcional e pode vir
    com ou sem o separador."""
    prefixo = (prefixo or "").strip()
    return f"{prefixo}{base}.tif"


def agrupar_para_merge(caminhos: Sequence[str], minimo: int = 2) -> List[GrupoDeMerge]:
    """Agrupa caminhos por base e devolve so os grupos com pelo menos
    ``minimo`` arquivos -- ou seja, so o que de fato precisa de merge.

    Arquivos avulsos (um unico bloco, ou nome sem sufixo de bloco) ficam de
    fora: sao exatamente os que a aba nao deve carregar."""
    por_base: Dict[str, List[str]] = {}
    motivos: Dict[str, str] = {}

    for caminho in caminhos:
        base, motivo = base_do_arquivo(caminho)
        if motivo is None:
            continue
        por_base.setdefault(base, []).append(caminho)
        # Um grupo com um bloco GEE e um sequencial e, para o usuario, um
        # grupo do GEE: o motivo mais especifico ganha.
        if motivo == MOTIVO_GEE or base not in motivos:
            motivos[base] = motivo

    grupos = [
        GrupoDeMerge(base=base, arquivos=tuple(sorted(arquivos)), motivo=motivos[base])
        for base, arquivos in por_base.items()
        if len(arquivos) >= minimo
    ]
    grupos.sort(key=lambda g: g.base.lower())
    return grupos


def parece_mosaico(grupo: GrupoDeMerge) -> Tuple[bool, str]:
    """Confere pela geometria se o grupo e mesmo um mosaico.

    So o sufixo do Earth Engine e inequivoco. A regra da sequencia numerica
    pega tambem ``bio_1.tif ... bio_19.tif``: 19 variaveis bioclimaticas que
    cobrem EXATAMENTE a mesma area e nao sao blocos de nada. Um mosaico tem
    pedacos em posicoes diferentes -- e e isso que se verifica aqui, lendo so
    os cabecalhos."""
    if grupo.motivo == MOTIVO_GEE:
        return True, ""

    try:
        infos = [ler_info(c) for c in grupo.arquivos]
    except Exception as exc:  # noqa: BLE001 - arquivo ilegivel nao vira grupo
        return False, f"nao foi possivel ler os arquivos ({exc})"

    extensoes = {tuple(round(v, 9) for v in info.bounds) for info in infos}
    if len(extensoes) == 1:
        return False, ("todos os arquivos cobrem exatamente a mesma area; "
                       "parecem variaveis/bandas, nao blocos de um mosaico")
    return True, ""


def filtrar_mosaicos(
    grupos: Sequence[GrupoDeMerge],
) -> Tuple[List[GrupoDeMerge], List[Tuple[GrupoDeMerge, str]]]:
    """Separa os grupos em (mosaicos de verdade, descartados com o motivo)."""
    mantidos: List[GrupoDeMerge] = []
    descartados: List[Tuple[GrupoDeMerge, str]] = []
    for grupo in grupos:
        ok, motivo = parece_mosaico(grupo)
        (mantidos if ok else descartados).append(grupo if ok else (grupo, motivo))
    return mantidos, descartados


def grupos_da_pasta(pasta: str, minimo: int = 2) -> List[GrupoDeMerge]:
    """Atalho: lista os .tif da pasta e devolve os grupos que precisam merge."""
    from utils import list_tif_files

    return agrupar_para_merge(list_tif_files(pasta), minimo=minimo)


# ---------------------------------------------------------------------------
# Conferencia das entradas
# ---------------------------------------------------------------------------


@dataclass
class InfoDoBloco:
    """O cabecalho de um bloco, do jeito que a tabela da aba mostra."""

    caminho: str
    largura: int
    altura: int
    bandas: int
    dtype: str
    crs: Optional[str]
    nodata: Optional[float]
    res: Tuple[float, float]
    bounds: Tuple[float, float, float, float]

    @property
    def nome(self) -> str:
        return os.path.basename(self.caminho)

    @property
    def dimensoes(self) -> str:
        return f"{self.largura} x {self.altura}"


def ler_info(caminho: str) -> InfoDoBloco:
    with rasterio.open(caminho) as src:
        return InfoDoBloco(
            caminho=caminho,
            largura=src.width,
            altura=src.height,
            bandas=src.count,
            dtype=src.dtypes[0],
            crs=(src.crs.to_string() if src.crs else None),
            nodata=src.nodata,
            res=(abs(src.transform.a), abs(src.transform.e)),
            bounds=tuple(src.bounds),
        )


@dataclass
class PlanoDeMerge:
    """Geometria da saida e a posicao de cada entrada dentro dela."""

    largura: int
    altura: int
    bandas: int
    dtype: str
    crs: Optional[str]
    nodata: Optional[float]
    transform: "rasterio.Affine"
    # (caminho, col_off, row_off, largura, altura, nodata da entrada)
    posicoes: List[Tuple[str, int, int, int, int, Optional[float]]] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    @property
    def pixels(self) -> int:
        return self.largura * self.altura


def _nodata_de_saida(dtype: str, declarados: List[Optional[float]],
                     avisos: List[str]) -> Optional[float]:
    """Escolhe o NoData do mosaico. Nunca inventa um NoData inteiro quando as
    entradas nao declaram nenhum: marcar 0 como "sem dado" num raster de
    classes apagaria a classe 0 em todo o resto da cadeia."""
    presentes = [v for v in declarados if v is not None]
    if presentes:
        primeiro = presentes[0]
        diferentes = {
            v for v in presentes
            if not (v == primeiro or (np.isnan(v) and np.isnan(primeiro)))
        }
        if diferentes:
            avisos.append(
                f"Os blocos declaram NoData diferentes ({primeiro} e "
                f"{', '.join(str(v) for v in sorted(diferentes))}); o mosaico "
                f"usa {primeiro}."
            )
        return primeiro

    if np.issubdtype(np.dtype(dtype), np.floating):
        return float("nan")

    avisos.append(
        "Nenhum bloco declara NoData e o tipo de dado e inteiro: as lacunas "
        "entre blocos (se houver) ficam com 0 e o mosaico sai sem NoData "
        "declarado."
    )
    return None


def planejar_merge(caminhos: Sequence[str]) -> PlanoDeMerge:
    """Le os cabecalhos, confere a compatibilidade e calcula a grade da saida.

    Levanta ``MergeIncompativel`` com uma mensagem que diz qual arquivo
    destoa e em que -- diagnosticar isso olhando o GeoTIFF no QGIS e bem mais
    trabalhoso do que ler a frase pronta."""
    if len(caminhos) < 2:
        raise MergeIncompativel("Selecione pelo menos dois arquivos para o merge.")

    infos = [ler_info(c) for c in caminhos]
    primeira = infos[0]
    avisos: List[str] = []

    rx, ry = primeira.res
    if rx <= 0 or ry <= 0:
        raise MergeIncompativel(f"Resolucao invalida em {primeira.nome}.")
    tol_res = max(rx, ry) * 1e-6

    for info in infos[1:]:
        if (info.crs or "") != (primeira.crs or ""):
            raise MergeIncompativel(
                f"CRS diferente: {primeira.nome} usa {primeira.crs or 'sem CRS'} e "
                f"{info.nome} usa {info.crs or 'sem CRS'}. Reprojete antes do merge."
            )
        if abs(info.res[0] - rx) > tol_res or abs(info.res[1] - ry) > tol_res:
            raise MergeIncompativel(
                f"Resolucao diferente: {primeira.nome} tem {rx:.10g} x {ry:.10g} e "
                f"{info.nome} tem {info.res[0]:.10g} x {info.res[1]:.10g}. Use a aba "
                f"Reamostrar antes do merge."
            )
        if info.bandas != primeira.bandas:
            raise MergeIncompativel(
                f"Numero de bandas diferente: {primeira.nome} tem {primeira.bandas} e "
                f"{info.nome} tem {info.bandas}."
            )
        if info.dtype != primeira.dtype:
            raise MergeIncompativel(
                f"Tipo de dado diferente: {primeira.nome} e {primeira.dtype} e "
                f"{info.nome} e {info.dtype}."
            )

    # Grade da saida: uniao das extensoes, ancorada no canto do primeiro bloco.
    esquerda = min(i.bounds[0] for i in infos)
    baixo = min(i.bounds[1] for i in infos)
    direita = max(i.bounds[2] for i in infos)
    topo = max(i.bounds[3] for i in infos)

    largura = int(round((direita - esquerda) / rx))
    altura = int(round((topo - baixo) / ry))
    if largura <= 0 or altura <= 0:
        raise MergeIncompativel("A uniao das extensoes resultou em um raster vazio.")

    transform = rasterio.Affine(rx, 0.0, esquerda, 0.0, -ry, topo)

    posicoes = []
    for info in infos:
        col = (info.bounds[0] - esquerda) / rx
        linha = (topo - info.bounds[3]) / ry
        # Blocos da mesma exportacao caem na mesma grade; uma fracao de pixel
        # aqui significa origens diferentes, e colar assim desloca o bloco.
        if abs(col - round(col)) > 0.01 or abs(linha - round(linha)) > 0.01:
            raise MergeIncompativel(
                f"{info.nome} nao esta na mesma grade dos demais (deslocamento de "
                f"{abs(col - round(col)):.3f} x {abs(linha - round(linha)):.3f} pixel). "
                f"Reamostre para um raster modelo antes do merge."
            )
        posicoes.append((info.caminho, int(round(col)), int(round(linha)),
                         info.largura, info.altura, info.nodata))

    nodata = _nodata_de_saida(primeira.dtype, [i.nodata for i in infos], avisos)

    soma_blocos = sum(i.largura * i.altura for i in infos)
    if soma_blocos > largura * altura:
        avisos.append(
            "Os blocos se sobrepoem; onde houver sobreposicao vale o primeiro "
            "arquivo da lista."
        )

    return PlanoDeMerge(
        largura=largura, altura=altura, bandas=primeira.bandas, dtype=primeira.dtype,
        crs=primeira.crs, nodata=nodata, transform=transform, posicoes=posicoes,
        avisos=avisos,
    )


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParametrosMerge:
    arquivos: Tuple[str, ...]
    saida: str
    sobrescrever: bool = True
    memoria_por_faixa_mb: float = MEMORIA_POR_FAIXA_MB


@dataclass
class ResultadoMerge:
    caminho: str
    largura: int
    altura: int
    bandas: int
    dtype: str
    nodata: Optional[float]
    pixels_com_dado: int
    segundos: float
    avisos: List[str] = field(default_factory=list)

    @property
    def pixels(self) -> int:
        return self.largura * self.altura

    @property
    def pct_com_dado(self) -> float:
        return 100.0 * self.pixels_com_dado / self.pixels if self.pixels else 0.0

    def resumo(self) -> str:
        return (
            f"{self.largura} x {self.altura} pixels, {self.bandas} banda(s), "
            f"{self.dtype}, {self.pct_com_dado:.1f}% com dado"
        )


def _mascara_de_dado(dados: np.ndarray, nodata: Optional[float]) -> np.ndarray:
    """Pixel com dado = ao menos uma banda com valor diferente de NoData."""
    if nodata is None:
        return np.ones(dados.shape[1:], dtype=bool)
    if isinstance(nodata, float) and np.isnan(nodata):
        return np.isfinite(dados.astype(np.float64)).any(axis=0)
    return (dados != nodata).any(axis=0)


def altura_da_faixa(plano: PlanoDeMerge, memoria_mb: float = MEMORIA_POR_FAIXA_MB) -> int:
    """Quantas linhas cabem no orcamento de memoria de uma faixa."""
    por_linha = max(1, plano.largura * plano.bandas * np.dtype(plano.dtype).itemsize)
    linhas = int(memoria_mb * 1024 * 1024 / por_linha)
    return max(1, min(plano.altura, linhas))


def executar_merge(
    parametros: ParametrosMerge,
    progresso: Optional[Callable[[float, str], None]] = None,
    cancelado: Optional[Callable[[], bool]] = None,
) -> ResultadoMerge:
    """Monta o mosaico faixa por faixa e grava um GeoTIFF LZW em tiles."""
    inicio = time.time()

    if not parametros.sobrescrever and os.path.exists(parametros.saida):
        raise FileExistsError(f"Arquivo ja existe: {parametros.saida}")

    plano = planejar_merge(parametros.arquivos)
    linhas_por_faixa = altura_da_faixa(plano, parametros.memoria_por_faixa_mb)
    total_faixas = max(1, -(-plano.altura // linhas_por_faixa))

    np_dtype = np.dtype(plano.dtype)
    preenchimento = plano.nodata if plano.nodata is not None else 0
    com_dado = 0

    perfil = {
        "driver": "GTiff",
        "height": plano.altura,
        "width": plano.largura,
        "count": plano.bandas,
        "dtype": plano.dtype,
        "crs": plano.crs,
        "transform": plano.transform,
        "compress": "LZW",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "bigtiff": "IF_SAFER",
    }
    if plano.nodata is not None:
        perfil["nodata"] = plano.nodata

    os.makedirs(os.path.dirname(parametros.saida) or ".", exist_ok=True)

    abertos: Dict[str, "rasterio.DatasetReader"] = {}
    try:
        with rasterio.open(parametros.saida, "w", **perfil) as dst:
            for indice in range(total_faixas):
                if cancelado is not None and cancelado():
                    raise CancelledError("Merge interrompido pelo usuario.")

                linha0 = indice * linhas_por_faixa
                altura = min(linhas_por_faixa, plano.altura - linha0)
                faixa = np.full((plano.bandas, altura, plano.largura),
                                preenchimento, dtype=np_dtype)
                preenchido = np.zeros((altura, plano.largura), dtype=bool)

                for caminho, col_off, row_off, larg_i, alt_i, nodata_i in plano.posicoes:
                    r0 = max(linha0, row_off)
                    r1 = min(linha0 + altura, row_off + alt_i)
                    if r1 <= r0:
                        continue
                    c0 = max(0, col_off)
                    c1 = min(plano.largura, col_off + larg_i)
                    if c1 <= c0:
                        continue

                    src = abertos.get(caminho)
                    if src is None:
                        src = abertos[caminho] = rasterio.open(caminho)
                    dados = src.read(window=Window(c0 - col_off, r0 - row_off,
                                                   c1 - c0, r1 - r0))

                    valido = _mascara_de_dado(dados, nodata_i)
                    alvo = (slice(None), slice(r0 - linha0, r1 - linha0), slice(c0, c1))
                    ja = preenchido[r0 - linha0:r1 - linha0, c0:c1]
                    aplicar = valido & ~ja
                    if aplicar.any():
                        recorte = faixa[alvo]
                        recorte[:, aplicar] = dados[:, aplicar]
                        ja |= aplicar

                com_dado += int(preenchido.sum())
                dst.write(faixa, window=Window(0, linha0, plano.largura, altura))

                if progresso is not None:
                    progresso(100.0 * (indice + 1) / total_faixas,
                              f"faixa {indice + 1}/{total_faixas}")
    except BaseException:
        for src in abertos.values():
            src.close()
        abertos.clear()
        # Um GeoTIFF truncado e pior do que nenhum: na proxima execucao ele
        # passaria por "arquivo ja existe" e seria usado como se estivesse bom.
        if os.path.exists(parametros.saida):
            try:
                os.remove(parametros.saida)
            except OSError:
                pass
        raise
    finally:
        for src in abertos.values():
            src.close()

    return ResultadoMerge(
        caminho=parametros.saida, largura=plano.largura, altura=plano.altura,
        bandas=plano.bandas, dtype=plano.dtype, nodata=plano.nodata,
        pixels_com_dado=com_dado, segundos=time.time() - inicio,
        avisos=list(plano.avisos),
    )
