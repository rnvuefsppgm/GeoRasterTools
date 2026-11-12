"""Leitura do GeoTIFF em blocos, sem carregar o arquivo inteiro na memória.

A V3 percorria os blocos nativos do arquivo. Em GeoTIFFs em faixas (``striped``)
o bloco nativo costuma ter uma única linha, o que gerava dezenas de milhares de
janelas, uma chamada de progresso para cada uma e travamento da interface.
Aqui os blocos nativos são agregados até um alvo de pixels por janela.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import rasterio
from rasterio.windows import Window

#: Alvo de pixels lidos por janela. 8 Mpx de float64 ≈ 64 MB no pior caso.
PIXELS_POR_JANELA = 8_000_000


@dataclass(frozen=True)
class PlanoDeLeitura:
    """Janelas de leitura já agregadas, com o total conhecido de antemão."""

    janelas: tuple[Window, ...]
    largura: int
    altura: int

    def __len__(self) -> int:
        return len(self.janelas)

    def __iter__(self) -> Iterator[tuple[int, Window]]:
        return iter(enumerate(self.janelas))


def _forma_do_bloco(src: rasterio.io.DatasetReader, banda: int) -> tuple[int, int]:
    try:
        altura, largura = src.block_shapes[banda - 1]
    except (IndexError, TypeError):
        altura, largura = 512, 512
    altura = max(1, int(altura))
    largura = max(1, int(largura))
    return altura, largura


def montar_plano(
    src: rasterio.io.DatasetReader,
    banda: int,
    pixels_por_janela: int = PIXELS_POR_JANELA,
) -> PlanoDeLeitura:
    """Agrega blocos nativos em janelas alinhadas de tamanho aproximado."""
    bloco_h, bloco_l = _forma_do_bloco(src, banda)
    bloco_h = min(bloco_h, src.height)
    bloco_l = min(bloco_l, src.width)

    # Quantos blocos nativos cabem no alvo, mantendo o alinhamento do arquivo.
    blocos_alvo = max(1, pixels_por_janela // max(bloco_h * bloco_l, 1))

    # Prioriza crescer na horizontal até cobrir a largura, depois na vertical.
    blocos_por_linha = max(1, math.ceil(src.width / bloco_l))
    blocos_na_largura = min(blocos_por_linha, blocos_alvo)
    blocos_na_altura = max(1, blocos_alvo // blocos_na_largura)

    passo_l = min(src.width, bloco_l * blocos_na_largura)
    passo_h = min(src.height, bloco_h * blocos_na_altura)

    janelas: list[Window] = []
    for linha in range(0, src.height, passo_h):
        altura = min(passo_h, src.height - linha)
        for coluna in range(0, src.width, passo_l):
            largura = min(passo_l, src.width - coluna)
            janelas.append(Window(coluna, linha, largura, altura))

    return PlanoDeLeitura(tuple(janelas), src.width, src.height)


def ler_janela(
    src: rasterio.io.DatasetReader,
    banda: int,
    janela: Window,
    excluidos: frozenset[float],
) -> tuple[np.ndarray, np.ndarray]:
    """Devolve ``(dados, validos)`` para a janela.

    ``dados`` é float64 e ``validos`` é a máscara booleana dos pixels que
    passaram por NoData, valores não finitos e lista de exclusão.
    """
    bruto = src.read(banda, window=janela, masked=True)
    dados = np.asarray(np.ma.getdata(bruto), dtype=np.float64)
    validos = ~np.ma.getmaskarray(bruto)
    if validos.shape != dados.shape:  # máscara escalar quando não há NoData
        validos = np.ones(dados.shape, dtype=bool)
    else:
        validos = validos.copy()
    np.logical_and(validos, np.isfinite(dados), out=validos)
    if excluidos:
        alvo = np.fromiter(sorted(excluidos), dtype=np.float64, count=len(excluidos))
        np.logical_and(validos, ~np.isin(dados, alvo), out=validos)
    return dados, validos
