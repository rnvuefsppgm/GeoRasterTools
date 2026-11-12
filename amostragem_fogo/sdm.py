"""Preparacao das amostras para modelagem de distribuicao (SDM).

Por que este modulo existe
--------------------------

A V5 sorteia os pontos olhando apenas para o raster de frequencia de fogo.
O SDM, porem, le esses pontos assim (``app_sdm_multi_v6.R``)::

    p_coords  <- utils::read.csv(cfg$pres_csv)[, c("x", "y")]
    bg_coords <- utils::read.csv(cfg$bg_csv)[, c("x", "y")]

e em seguida extrai os valores da pilha de rasters preditores. Dois pontos
perfeitamente validos no raster de fogo podem ser inuteis para o modelo:

1. **Caem em NoData de algum preditor.** O SDMtune descarta esses registros em
   silencio. Numa medicao com preditor de ~1 km e 18 % de NoData, 21,4 % das
   presencas sorteadas pela V5 sumiam sem aviso.
2. **Caem na mesma celula do preditor.** O raster de fogo costuma ser bem mais
   fino que o preditor bioclimatico; varios pontos distintos no fogo viram o
   mesmo registro ambiental. Na mesma medicao, 13,6 % eram pseudo-replicas.

Somando os dois, de 500 presencas pedidas restavam 343 (68,6 %) para o modelo,
sem que a aplicacao avisasse nada.

Este modulo aplica os dois filtros **durante** o sorteio, de modo que a
quantidade pedida seja de fato atingida, e registra quantos candidatos foram
recusados por cada motivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError

#: Valores abaixo deste limite sao tratados como NoData mesmo quando o raster
#: nao declara ``nodata``. Cobre o -3.4e38 gravado por varios softwares.
LIMITE_SENTINELA = -1e30


@dataclass(frozen=True)
class FiltroSDM:
    """Configuracao do preparo para SDM. Sem rasters, nada e filtrado.

    Use **um** raster de referencia, nao a pasta inteira de preditores.

    Exigir validade simultanea em todos os preditores parece mais seguro, mas
    na pratica zera a amostra. Medido no Acre com os 25 preditores da pasta
    ``CLIP_BIOCLIM/Acre/Presente``: cada raster aprovava entre 42 % e 100 % dos
    candidatos isoladamente, mas os buracos de NaN dos rasters de distancia
    (``Dist_AGRO``, ``Dist_VEGET``, ``Dist_WATER``...) sao complementares e a
    intersecao dos 25 aprovava **zero**. O sorteio devolvia 0 de 1000 sem
    explicar por que.

    O papel deste filtro e garantir que o ponto caia numa celula util da grade
    do modelo -- e para isso um raster de referencia basta. Rasters preditores
    com muito NaN sao um problema do conjunto de dados, nao da amostragem.
    """

    preditores: tuple[Path, ...] = ()
    exigir_preditor_valido: bool = True
    uma_amostra_por_celula: bool = True

    @property
    def ativo(self) -> bool:
        return bool(self.preditores) and (
            self.exigir_preditor_valido or self.uma_amostra_por_celula
        )

    @property
    def referencia(self) -> Path | None:
        """Raster que define a grade. E o primeiro da lista."""
        return Path(self.preditores[0]) if self.preditores else None

    def __post_init__(self) -> None:
        for caminho in self.preditores:
            if not Path(caminho).is_file():
                raise ValueError(f"Raster de referencia nao encontrado:\n{caminho}")


@dataclass
class EstatisticasFiltro:
    """Quantos candidatos foram recusados, e por que."""

    avaliados: int = 0
    fora_da_extensao: int = 0
    nodata_no_preditor: int = 0
    celula_repetida: int = 0
    distancia_minima: int = 0
    aceitos: int = 0

    #: Motivos de recusa, na ordem em que sao avaliados.
    _MOTIVOS = (
        ("fora_da_extensao", "fora da extensao do raster de referencia"),
        ("nodata_no_preditor", "em NoData do raster de referencia"),
        ("celula_repetida", "em celula de referencia ja usada"),
        ("distancia_minima", "abaixo da distancia minima"),
    )

    def resumo(self) -> list[str]:
        linhas = [f"Candidatos avaliados: {self.avaliados}", f"Aceitos: {self.aceitos}"]
        for campo, rotulo in self._MOTIVOS:
            valor = getattr(self, campo)
            if valor:
                linhas.append(f"Recusados por estarem {rotulo}: {valor}")
        if self.avaliados and self.aceitos == 0:
            linhas.append(f"NENHUMA amostra aceita. {self.motivo_dominante()}")
        return linhas

    def motivo_dominante(self) -> str:
        """Frase acionavel apontando o filtro que recusou mais candidatos."""
        contagens = [(getattr(self, campo), rotulo) for campo, rotulo in self._MOTIVOS]
        maior, rotulo = max(contagens, key=lambda item: item[0])
        if maior == 0:
            return "Nao havia candidatos no reservatorio."
        conselhos = {
            "fora da extensao do raster de referencia":
                "O raster de referencia cobre outra area. Use o do mesmo recorte.",
            "em NoData do raster de referencia":
                "O raster de referencia tem NoData onde o fogo ocorre. Escolha "
                "outro: rasters de distancia costumam ter grandes vazios de NaN.",
            "em celula de referencia ja usada":
                "A grade de referencia e grossa demais para essa quantidade. "
                "Reduza o numero de amostras ou desmarque 'um ponto por celula'.",
            "abaixo da distancia minima":
                "Reduza a distancia minima ou o numero de amostras.",
        }
        return f"Motivo principal: {maior} recusados por estarem {rotulo}. {conselhos[rotulo]}"


class MascaraPreditores:
    """Avalia pontos contra a pilha de preditores, em lote.

    O primeiro raster da lista serve de grade de referencia para a
    deduplicacao por celula; e a mesma grade que a aba "Verificar Alinhamento"
    confere. Cada preditor pode estar em CRS diferente do raster de fogo: as
    coordenadas sao transformadas em memoria, o preditor nunca e reescrito.
    """

    def __init__(self, caminhos, crs_origem: CRS | None) -> None:
        self._fontes: list[rasterio.io.DatasetReader] = []
        self._transformadores: list[Transformer | None] = []
        for caminho in caminhos:
            fonte = rasterio.open(caminho)
            self._fontes.append(fonte)
            self._transformadores.append(self._montar_transformador(crs_origem, fonte))
        if not self._fontes:
            raise ValueError("Informe pelo menos um raster preditor.")
        self.referencia = self._fontes[0]

    @staticmethod
    def _montar_transformador(crs_origem, fonte) -> Transformer | None:
        if crs_origem is None or fonte.crs is None:
            return None
        try:
            destino = CRS.from_user_input(fonte.crs)
        except (ProjError, ValueError):
            return None
        if destino == crs_origem:
            return None
        return Transformer.from_crs(crs_origem, destino, always_xy=True)

    def __enter__(self) -> "MascaraPreditores":
        return self

    def __exit__(self, *_exc) -> None:
        self.fechar()

    def fechar(self) -> None:
        for fonte in self._fontes:
            try:
                fonte.close()
            except Exception:  # noqa: BLE001 - fechar nunca deve derrubar a aplicacao
                pass
        self._fontes = []

    @property
    def descricao(self) -> str:
        nomes = [Path(f.name).name for f in self._fontes]
        return f"{len(nomes)} preditor(es): " + ", ".join(nomes)

    # -- avaliacao ----------------------------------------------------------

    def avaliar(
        self, x: np.ndarray, y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Avalia um lote de pontos no CRS do raster de fogo.

        Devolve ``(dentro, valido, celula)``:

        ``dentro``  o ponto cai dentro da extensao de todos os preditores;
        ``valido``  ``dentro`` e nenhum preditor tem NoData ali;
        ``celula``  identificador da celula na grade de referencia, -1 se fora.
        """
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        total = x.size
        dentro = np.ones(total, dtype=bool)
        valido = np.ones(total, dtype=bool)
        celula = np.full(total, -1, dtype=np.int64)

        for indice, (fonte, transformador) in enumerate(
            zip(self._fontes, self._transformadores)
        ):
            if transformador is None:
                px, py = x, y
            else:
                px, py = transformador.transform(x, y)
                px = np.asarray(px, dtype=np.float64)
                py = np.asarray(py, dtype=np.float64)

            # Inversa da afim aplicada diretamente em numpy. rasterio.transform
            # .rowcol percorre ponto a ponto e ainda emite uma advertencia de
            # depreciacao por ponto -- dezenas de milhares delas num lote.
            inversa = ~fonte.transform
            colunas = np.floor(inversa.a * px + inversa.b * py + inversa.c).astype(np.int64)
            linhas = np.floor(inversa.d * px + inversa.e * py + inversa.f).astype(np.int64)

            neste = (
                (linhas >= 0)
                & (linhas < fonte.height)
                & (colunas >= 0)
                & (colunas < fonte.width)
                & np.isfinite(px)
                & np.isfinite(py)
            )
            dentro &= neste

            if indice == 0:
                celula = np.where(neste, linhas * fonte.width + colunas, -1)

            # Amostrar so os pontos que caem dentro deste preditor.
            posicoes = np.flatnonzero(neste)
            if posicoes.size:
                pares = list(zip(px[posicoes].tolist(), py[posicoes].tolist()))
                valores = np.array(
                    [v[0] for v in fonte.sample(pares)], dtype=np.float64
                )
                nodata = fonte.nodata
                ruim = ~np.isfinite(valores) | (valores <= LIMITE_SENTINELA)
                if nodata is not None and np.isfinite(nodata):
                    ruim |= np.isclose(valores, float(nodata), rtol=0.0, atol=1e-6)
                bom = np.ones(total, dtype=bool)
                bom[posicoes] = ~ruim
                valido &= bom

        valido &= dentro
        return dentro, valido, celula


@dataclass
class Sondagem:
    """Resultado da verificacao previa do raster de referencia."""

    avaliados: int
    dentro: int
    validos: int
    celulas_distintas: int
    crs_referencia: str
    crs_iguais: bool

    @property
    def taxa_dentro(self) -> float:
        return self.dentro / self.avaliados if self.avaliados else 0.0

    @property
    def taxa_valida(self) -> float:
        return self.validos / self.avaliados if self.avaliados else 0.0

    @property
    def teto_de_amostras(self) -> int:
        """Maximo de amostras possiveis por classe com 1 ponto por celula."""
        return self.celulas_distintas

    def diagnostico(self, solicitado: int) -> list[str]:
        """Mensagens de alerta. Lista vazia significa que esta tudo bem."""
        avisos: list[str] = []
        if self.avaliados == 0:
            return ["Nao ha candidatos para sondar."]
        if self.dentro == 0:
            avisos.append(
                "NENHUM candidato cai dentro do raster de referencia. "
                f"O raster esta em {self.crs_referencia} e provavelmente cobre "
                "outra area. Confira se e o raster do mesmo recorte."
            )
            return avisos
        if self.taxa_dentro < 0.5:
            avisos.append(
                f"So {self.taxa_dentro:.0%} dos candidatos caem dentro do raster de "
                "referencia; ele parece cobrir area menor que o raster de fogo."
            )
        if self.validos == 0:
            avisos.append(
                "Todos os candidatos caem em NoData do raster de referencia. "
                "Escolha outro raster: os de distancia costumam ter grandes "
                "vazios de NaN. Um bioclimatico ou o de uso do solo funciona melhor."
            )
        elif self.taxa_valida < 0.25:
            avisos.append(
                f"So {self.taxa_valida:.0%} dos candidatos caem em celula valida "
                "do raster de referencia. Considere outro raster."
            )
        return avisos


def sondar_referencia(
    filtro: FiltroSDM,
    x: np.ndarray,
    y: np.ndarray,
    crs_origem,
    limite: int = 3000,
) -> Sondagem:
    """Testa uma amostra de candidatos antes de rodar o sorteio inteiro.

    Existe para que um raster de referencia inadequado seja apontado na hora,
    e nao depois de varrer o raster inteiro para devolver 0 amostras.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size > limite:
        passo = np.linspace(0, x.size - 1, limite).astype(np.int64)
        x, y = x[passo], y[passo]

    with MascaraPreditores(filtro.preditores, crs_origem) as mascara:
        dentro, valido, celula = mascara.avaliar(x, y)
        crs_ref = mascara.referencia.crs
        try:
            iguais = crs_origem is not None and CRS.from_user_input(crs_ref) == crs_origem
        except Exception:  # noqa: BLE001
            iguais = False
        return Sondagem(
            avaliados=int(x.size),
            dentro=int(dentro.sum()),
            validos=int(valido.sum()),
            celulas_distintas=len(set(celula[valido].tolist())),
            crs_referencia=str(crs_ref) if crs_ref else "sem CRS",
            crs_iguais=bool(iguais),
        )


def relatorio_preparo(
    filtro: FiltroSDM,
    estatisticas: dict[str, EstatisticasFiltro],
    descricao_preditores: str,
) -> list[str]:
    """Bloco de texto para o relatorio da execucao."""
    linhas = ["PREPARO PARA SDM"]
    if not filtro.ativo:
        linhas.append("  Nenhum filtro de preditores foi aplicado.")
        linhas.append("  As amostras podem cair em NoData dos preditores ou repetir")
        linhas.append("  celula, e o SDMtune as descartaria em silencio.")
        return linhas

    linhas.append(f"  Preditores ............. {descricao_preditores}")
    linhas.append(
        "  Exigir preditor valido . "
        + ("sim" if filtro.exigir_preditor_valido else "nao")
    )
    linhas.append(
        "  Uma amostra por celula . "
        + ("sim" if filtro.uma_amostra_por_celula else "nao")
    )
    for classe, dados in estatisticas.items():
        linhas.append(f"  {classe}:")
        for texto in dados.resumo():
            linhas.append(f"    {texto}")
    return linhas
