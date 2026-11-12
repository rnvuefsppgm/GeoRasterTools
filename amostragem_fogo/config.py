"""Parâmetros de execução e conversões de texto para número."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .sdm import FiltroSDM

# ---------------------------------------------------------------------------
# Conversões de texto
# ---------------------------------------------------------------------------


def texto_para_float(texto: str, campo: str, *, vazio_permitido: bool = False) -> float | None:
    """Converte texto em float aceitando vírgula ou ponto como separador."""
    bruto = (texto or "").strip().replace(" ", "")
    if not bruto:
        if vazio_permitido:
            return None
        raise ValueError(f"{campo} não pode ficar vazio.")
    normalizado = bruto.replace(",", ".")
    try:
        valor = float(normalizado)
    except ValueError as exc:
        raise ValueError(f"{campo} deve ser um número. Valor recebido: {bruto!r}.") from exc
    if not math.isfinite(valor):
        raise ValueError(f"{campo} deve ser um número finito.")
    return valor


def texto_para_int(texto: str, campo: str, *, minimo: int = 0) -> int:
    bruto = (texto or "").strip().replace(".", "").replace(" ", "")
    if not bruto:
        raise ValueError(f"{campo} não pode ficar vazio.")
    try:
        valor = int(bruto)
    except ValueError as exc:
        raise ValueError(f"{campo} deve ser um número inteiro. Valor recebido: {bruto!r}.") from exc
    if valor < minimo:
        raise ValueError(f"{campo} deve ser maior ou igual a {minimo}.")
    return valor


def texto_para_conjunto(texto: str, campo: str = "Valores a excluir") -> frozenset[float]:
    """Lê uma lista de valores separados por vírgula, ponto e vírgula ou espaço.

    Aceita ``0; 99`` e também ``0 99``. Como a vírgula é usada como separador de
    itens, o separador decimal aceito nesse campo é o ponto.
    """
    bruto = (texto or "").strip()
    if not bruto:
        return frozenset()
    for simbolo in (";", "\t", "\n"):
        bruto = bruto.replace(simbolo, ",")
    bruto = bruto.replace(" ", ",")
    valores: set[float] = set()
    for item in bruto.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            valores.add(float(item))
        except ValueError as exc:
            raise ValueError(f"{campo}: {item!r} não é um número válido.") from exc
    return frozenset(valores)


# ---------------------------------------------------------------------------
# Estruturas de parâmetros
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Faixa:
    """Faixa fechada de valores de frequência ``[minimo, maximo]``."""

    minimo: float
    maximo: float

    def __post_init__(self) -> None:
        if self.minimo > self.maximo:
            raise ValueError(
                f"Faixa inválida: mínimo ({self.minimo:g}) é maior que o máximo ({self.maximo:g})."
            )

    def contem(self, valor: float) -> bool:
        return self.minimo <= valor <= self.maximo

    def sobrepoe(self, outra: "Faixa") -> bool:
        return max(self.minimo, outra.minimo) <= min(self.maximo, outra.maximo)

    def __str__(self) -> str:
        return f"[{self.minimo:g}, {self.maximo:g}]"


@dataclass(frozen=True)
class ParametrosAnalise:
    raster: Path
    banda: int = 1
    anos_observados: int = 41
    valores_excluidos: frozenset[float] = frozenset()

    def __post_init__(self) -> None:
        if not self.raster.is_file():
            raise ValueError(f"Arquivo raster não encontrado:\n{self.raster}")
        if self.banda < 1:
            raise ValueError("A banda deve ser maior ou igual a 1.")
        if self.anos_observados < 1:
            raise ValueError("O número de anos observados deve ser maior ou igual a 1.")

    def assinatura_esperada(self) -> tuple[str, int, int, frozenset[float]]:
        """Identidade dos parâmetros que determinam o resultado da análise.

        A interface compara esta assinatura com a da análise já calculada para
        impedir que o usuário troque o raster e gere amostras incoerentes com a
        tabela de limiares que está vendo na tela.
        """
        return (str(self.raster), self.banda, self.anos_observados, self.valores_excluidos)


#: Saídas agrupadas como aparecem na tela. A escolha é feita em duas dimensões
#: independentes — *quais classes* exportar e *em quais formatos* —, de modo que
#: salvar apenas as presenças é marcar uma caixa, não desmarcar seis.
GRUPOS_DE_SAIDA: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Classes a exportar",
        (
            ("presencas", "Presenças"),
            ("ausencias", "Ausências"),
            ("combinado", "Arquivo único com as duas classes"),
        ),
    ),
    (
        "Formato das amostras",
        (
            ("formato_xyv", "x,y,Value  (MaxEnt, R, Python)"),
            ("formato_detalhado", "Tabela detalhada (todas as colunas)"),
            ("formato_geojson", "GeoJSON (arrastar no QGIS)"),
        ),
    ),
    (
        "Tabelas e documentos",
        (
            ("tabela_limiares", "Tabela de limiares de frequência"),
            ("distribuicao", "Distribuição das frequências"),
            ("parametros", "Parâmetros da execução"),
            ("relatorio", "Relatório em texto"),
            ("leia_me", "Instruções de leitura (LEIA-ME_SAIDA.txt)"),
        ),
    ),
)

#: Lista achatada, na ordem da tela. Usada na linha de comando e no relatório.
SAIDAS_DISPONIVEIS: tuple[tuple[str, str], ...] = tuple(
    item for _, itens in GRUPOS_DE_SAIDA for item in itens
)

CLASSES_DE_SAIDA: tuple[str, ...] = ("presencas", "ausencias", "combinado")
FORMATOS_DE_SAIDA: tuple[str, ...] = ("formato_xyv", "formato_detalhado", "formato_geojson")

#: Coordenadas gravadas nas colunas ``x`` e ``y`` do formato ``x,y,Value``.
COORDENADAS_WGS84 = "wgs84"
COORDENADAS_ORIGINAL = "original"


@dataclass(frozen=True)
class SelecaoDeSaidas:
    """Quais arquivos serão gravados. Todos ligados por padrão.

    As classes e os formatos se combinam: marcar apenas ``presencas`` com
    ``formato_xyv`` grava só ``amostras_presencas.csv``.
    """

    # classes
    presencas: bool = True
    ausencias: bool = True
    combinado: bool = True
    # formatos das amostras
    formato_xyv: bool = True
    formato_detalhado: bool = True
    formato_geojson: bool = True
    # tabelas e documentos
    tabela_limiares: bool = True
    distribuicao: bool = True
    parametros: bool = True
    relatorio: bool = True
    leia_me: bool = True

    @classmethod
    def de_nomes(cls, nomes) -> "SelecaoDeSaidas":
        """Liga apenas as saídas cujos nomes foram informados."""
        pedidos = set(nomes)
        validos = {nome for nome, _ in SAIDAS_DISPONIVEIS}
        desconhecidos = pedidos - validos
        if desconhecidos:
            raise ValueError(
                "Saída desconhecida: " + ", ".join(sorted(desconhecidos)) + ". "
                "Valores aceitos: " + ", ".join(nome for nome, _ in SAIDAS_DISPONIVEIS) + "."
            )
        return cls(**{nome: (nome in pedidos) for nome in validos})

    @classmethod
    def nenhuma(cls) -> "SelecaoDeSaidas":
        return cls(**{nome: False for nome, _ in SAIDAS_DISPONIVEIS})

    @classmethod
    def somente_presencas(cls) -> "SelecaoDeSaidas":
        return cls.de_nomes(["presencas", "formato_xyv"])

    @classmethod
    def somente_ausencias(cls) -> "SelecaoDeSaidas":
        return cls.de_nomes(["ausencias", "formato_xyv"])

    def ligadas(self) -> list[str]:
        return [nome for nome, _ in SAIDAS_DISPONIVEIS if getattr(self, nome)]

    def classes_ligadas(self) -> list[str]:
        return [nome for nome in CLASSES_DE_SAIDA if getattr(self, nome)]

    def formatos_ligados(self) -> list[str]:
        return [nome for nome in FORMATOS_DE_SAIDA if getattr(self, nome)]

    def alguma_amostra(self) -> bool:
        return bool(self.classes_ligadas()) and bool(self.formatos_ligados())

    def __bool__(self) -> bool:
        return bool(self.ligadas())


@dataclass(frozen=True)
class ParametrosAmostragem:
    analise: ParametrosAnalise
    saida: Path
    presenca: Faixa
    ausencia: Faixa
    n_presenca: int
    n_ausencia: int
    distancia_minima_m: float = 0.0
    distancia_entre_classes: bool = True
    semente: int = 42
    separador_decimal: str = "."
    fator_reservatorio: int = 200
    reservatorio_minimo: int = 100_000
    reservatorio_maximo: int = 4_000_000
    saidas: SelecaoDeSaidas = SelecaoDeSaidas()
    coordenadas_xy: str = COORDENADAS_WGS84
    valor_presenca: int = 1
    valor_ausencia: int = 0
    filtro_sdm: FiltroSDM = FiltroSDM()

    def __post_init__(self) -> None:
        if self.presenca.sobrepoe(self.ausencia) and self.n_presenca > 0 and self.n_ausencia > 0:
            raise ValueError(
                "As faixas de presença e ausência se sobrepõem "
                f"(presença {self.presenca}, ausência {self.ausencia}). "
                "Ajuste os limites para que não compartilhem nenhum valor."
            )
        if self.n_presenca <= 0 and self.n_ausencia <= 0:
            raise ValueError("Informe pelo menos uma amostra de presença ou de ausência.")
        if self.distancia_minima_m < 0:
            raise ValueError("A distância mínima não pode ser negativa.")
        if self.separador_decimal not in (".", ","):
            raise ValueError("O separador decimal deve ser ponto ou vírgula.")
        if self.coordenadas_xy not in (COORDENADAS_WGS84, COORDENADAS_ORIGINAL):
            raise ValueError(
                "As coordenadas do formato x,y,Value devem ser "
                f"{COORDENADAS_WGS84!r} ou {COORDENADAS_ORIGINAL!r}."
            )
        if self.valor_presenca == self.valor_ausencia:
            raise ValueError(
                "O código da presença e o da ausência precisam ser diferentes na "
                "coluna Value; do contrário as duas classes ficam indistinguíveis."
            )
        if not self.saidas:
            raise ValueError(
                "Nenhum arquivo de saída foi selecionado. Marque pelo menos uma "
                "caixa na seção de arquivos de saída."
            )
        classes = self.saidas.classes_ligadas()
        formatos = self.saidas.formatos_ligados()
        if classes and not formatos:
            raise ValueError(
                "Você marcou classes para exportar mas nenhum formato. "
                "Marque pelo menos um formato das amostras (x,y,Value, tabela "
                "detalhada ou GeoJSON)."
            )
        if formatos and not classes:
            raise ValueError(
                "Você marcou formatos de amostra mas nenhuma classe. "
                "Marque presenças, ausências ou o arquivo único."
            )

    def capacidade(self, solicitado: int) -> int:
        """Tamanho do reservatório de candidatos para a quantidade pedida."""
        if solicitado <= 0:
            return 0
        alvo = max(solicitado * self.fator_reservatorio, self.reservatorio_minimo)
        return int(min(alvo, self.reservatorio_maximo))


@dataclass
class Progresso:
    """Encaminha percentual e mensagem, limitando a frequência de avisos."""

    callback: object = None
    _ultimo: float = field(default=-1.0, repr=False)

    def __call__(self, percentual: float, mensagem: str, *, forcar: bool = False) -> None:
        percentual = max(0.0, min(100.0, float(percentual)))
        if not forcar and percentual - self._ultimo < 0.5:
            return
        self._ultimo = percentual
        if callable(self.callback):
            self.callback(percentual, mensagem)
