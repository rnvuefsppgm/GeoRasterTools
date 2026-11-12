"""Histograma de frequencias e tabela de limiares.

Para cada valor positivo de frequencia t a tabela informa:

    pixels_mantidos(t)      = soma de N(f) para f >= t
    ocorrencias_mantidas(t) = soma de f * N(f) para f >= t

Os percentuais usam como denominador, respectivamente, todos os pixels com
frequencia maior que zero que nao foram excluidos e a soma total f * N(f).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from .blocos import ler_janela, montar_plano
from .config import ParametrosAnalise, Progresso


class OperacaoCancelada(RuntimeError):
    """Levantada quando o usuario interrompe o processamento."""


@dataclass(frozen=True)
class LinhaLimiar:
    limiar: float
    pixels_mantidos: int
    pixels_mantidos_pct: float
    ocorrencias_mantidas: float
    ocorrencias_mantidas_pct: float
    intervalo_retorno_anos: float | None
    interpretacao: str


@dataclass
class ResultadoAnalise:
    raster: Path
    banda: int
    anos_observados: int
    valores_excluidos: frozenset[float]
    crs_texto: str
    unidade_crs: str
    largura: int
    altura: int
    resolucao: tuple[float, float]
    nodata: float | None
    dtype: str
    contagens: dict[float, int]
    pixels_validos: int
    pixels_queimados: int
    ocorrencias_totais: float
    valor_minimo: float
    valor_maximo: float
    linhas: list[LinhaLimiar] = field(default_factory=list)

    @property
    def valores_observados(self) -> list[float]:
        return sorted(self.contagens)

    @property
    def valores_positivos(self) -> list[float]:
        return [v for v in sorted(self.contagens) if v > 0]

    def pixels_na_faixa(self, minimo: float, maximo: float) -> int:
        return sum(n for v, n in self.contagens.items() if minimo <= v <= maximo)

    def assinatura(self) -> tuple[str, int, int, frozenset[float]]:
        """Identifica a origem da analise, para detectar troca de parametros."""
        return (str(self.raster), self.banda, self.anos_observados, self.valores_excluidos)


def _interpretar(
    limiar: float,
    menor_positivo: float,
    pct_pixels: float,
    pct_ocorrencias: float,
    anos: int,
) -> tuple[float | None, str]:
    if math.isclose(limiar, menor_positivo) and pct_ocorrencias >= 99.999:
        rotulo = "Qualquer ocorrencia historica incluida"
    elif pct_ocorrencias >= 90:
        rotulo = "Cobertura muito ampla do fogo observado"
    elif pct_ocorrencias >= 75:
        rotulo = "Fogo recorrente, com boa cobertura"
    elif pct_ocorrencias >= 60:
        rotulo = "Recorrencia mais intensa"
    elif pct_ocorrencias >= 40:
        rotulo = "Hotspots historicos"
    elif pct_ocorrencias >= 20:
        rotulo = "Hotspots persistentes"
    else:
        rotulo = "Extremo superior da recorrencia"

    intervalo: float | None = None
    if limiar > 0 and anos > 0:
        intervalo = anos / limiar
        rotulo = (
            f"{rotulo}; intervalo medio de retorno <= {intervalo:.1f} anos "
            f"({pct_pixels:.1f}% dos pixels queimados)"
        )
    return intervalo, rotulo


def contar_frequencias(
    parametros: ParametrosAnalise,
    progresso: Progresso,
    cancelado,
) -> tuple[dict[float, int], dict[str, object]]:
    """Percorre o raster em blocos e acumula a contagem de cada valor."""
    contagens: dict[float, int] = {}
    with rasterio.open(parametros.raster) as src:
        if parametros.banda > src.count:
            raise ValueError(
                f"O raster possui {src.count} banda(s); a banda {parametros.banda} nao existe."
            )
        plano = montar_plano(src, parametros.banda)
        total = len(plano)
        for indice, janela in plano:
            if cancelado():
                raise OperacaoCancelada("Analise interrompida pelo usuario.")
            dados, validos = ler_janela(src, parametros.banda, janela, parametros.valores_excluidos)
            if validos.any():
                valores = dados[validos]
                unicos, quantidades = np.unique(valores, return_counts=True)
                for valor, quantidade in zip(unicos.tolist(), quantidades.tolist()):
                    contagens[float(valor)] = contagens.get(float(valor), 0) + int(quantidade)
            progresso(
                100.0 * (indice + 1) / max(total, 1),
                f"Lendo o raster: janela {indice + 1} de {total}",
                forcar=(indice + 1 == total),
            )

        metadados = {
            "crs_texto": src.crs.to_string() if src.crs else "Sem CRS definido",
            "largura": src.width,
            "altura": src.height,
            "nodata": src.nodata,
            "dtype": str(src.dtypes[parametros.banda - 1]),
            "resolucao": (abs(src.transform.a), abs(src.transform.e)),
        }
        from .geo import abrir_sistema  # import tardio: evita ciclo na importacao

        metadados["unidade_crs"] = abrir_sistema(src).unidade_do_crs
    return contagens, metadados


def analisar(
    parametros: ParametrosAnalise,
    progresso: Progresso,
    cancelado,
) -> ResultadoAnalise:
    contagens, meta = contar_frequencias(parametros, progresso, cancelado)

    if not contagens:
        raise ValueError(
            "Nenhum pixel valido restou depois de aplicar NoData e a lista de "
            "valores a excluir. Revise o campo de exclusoes."
        )

    valores = sorted(contagens)
    positivos = [v for v in valores if v > 0]
    if not positivos:
        raise ValueError(
            "Nao existe nenhuma frequencia maior que zero apos as exclusoes. "
            "Sem pixels queimados nao ha o que amostrar como presenca."
        )

    pixels_validos = sum(contagens.values())
    pixels_queimados = sum(contagens[v] for v in positivos)
    ocorrencias = sum(v * contagens[v] for v in positivos)
    if ocorrencias <= 0:
        raise ValueError("A soma das ocorrencias e igual a zero.")

    acumulado_pixels = 0
    acumulado_ocorrencias = 0.0
    linhas_desc: list[LinhaLimiar] = []
    menor_positivo = positivos[0]
    for limiar in reversed(positivos):
        quantidade = contagens[limiar]
        acumulado_pixels += quantidade
        acumulado_ocorrencias += limiar * quantidade
        pct_pixels = 100.0 * acumulado_pixels / pixels_queimados
        pct_ocorrencias = 100.0 * acumulado_ocorrencias / ocorrencias
        intervalo, texto = _interpretar(
            limiar, menor_positivo, pct_pixels, pct_ocorrencias, parametros.anos_observados
        )
        linhas_desc.append(
            LinhaLimiar(
                limiar=limiar,
                pixels_mantidos=acumulado_pixels,
                pixels_mantidos_pct=pct_pixels,
                ocorrencias_mantidas=acumulado_ocorrencias,
                ocorrencias_mantidas_pct=pct_ocorrencias,
                intervalo_retorno_anos=intervalo,
                interpretacao=texto,
            )
        )

    return ResultadoAnalise(
        raster=parametros.raster,
        banda=parametros.banda,
        anos_observados=parametros.anos_observados,
        valores_excluidos=parametros.valores_excluidos,
        crs_texto=str(meta["crs_texto"]),
        unidade_crs=str(meta["unidade_crs"]),
        largura=int(meta["largura"]),
        altura=int(meta["altura"]),
        resolucao=tuple(meta["resolucao"]),  # type: ignore[arg-type]
        nodata=meta["nodata"],  # type: ignore[arg-type]
        dtype=str(meta["dtype"]),
        contagens=contagens,
        pixels_validos=pixels_validos,
        pixels_queimados=pixels_queimados,
        ocorrencias_totais=ocorrencias,
        valor_minimo=valores[0],
        valor_maximo=valores[-1],
        linhas=list(reversed(linhas_desc)),
    )
