"""Orquestracao sem interface grafica.

A GUI, a linha de comando e os testes usam exatamente este mesmo caminho, de
modo que o que e testado e o que o usuario executa.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .amostragem import Amostra, amostrar
from .analise import ResultadoAnalise, analisar
from .config import ParametrosAmostragem, ParametrosAnalise, Progresso
from .exportacao import exportar_tudo


def _sem_cancelamento() -> bool:
    return False


@dataclass
class ResultadoExecucao:
    analise: ResultadoAnalise
    presenca: Amostra
    ausencia: Amostra
    metadados: dict[str, object]
    arquivos: list[Path]

    @property
    def avisos(self) -> list[str]:
        avisos: list[str] = []
        for amostra in (self.presenca, self.ausencia):
            if amostra.solicitado > 0 and not amostra.completa:
                avisos.append(
                    f"Foram selecionadas {len(amostra)} amostras de "
                    f"{amostra.solicitado} solicitadas na classe {amostra.classe}."
                )
        modo = self.metadados.get("modo_metrico")
        if modo is not None and not getattr(modo, "exato", True):
            avisos.append(
                "A distancia foi medida na grade projetada do raster, nao no "
                "elipsoide. O erro pode chegar a alguns decimos de por cento."
            )
        # Avisos da sondagem do raster de referencia (preparo para SDM).
        avisos.extend(self.metadados.get("avisos_referencia", []))
        # Quando uma classe nao produziu nada, dizer o motivo dominante.
        for amostra in (self.presenca, self.ausencia):
            if amostra.solicitado > 0 and len(amostra) == 0:
                estatisticas = amostra.estatisticas
                if estatisticas.avaliados:
                    avisos.append(
                        f"Classe {amostra.classe}: {estatisticas.motivo_dominante()}"
                    )
        return avisos


def executar_analise(
    parametros: ParametrosAnalise,
    progresso=None,
    cancelado=None,
) -> ResultadoAnalise:
    return analisar(parametros, Progresso(progresso), cancelado or _sem_cancelamento)


def executar_amostragem(
    parametros: ParametrosAmostragem,
    analise: ResultadoAnalise,
    progresso=None,
    cancelado=None,
) -> ResultadoExecucao:
    cancelado = cancelado or _sem_cancelamento
    presenca, ausencia, metadados = amostrar(parametros, Progresso(progresso), cancelado)
    arquivos = exportar_tudo(
        parametros,
        analise,
        presenca,
        ausencia,
        metadados,
        crs_objeto=metadados.get("crs_objeto"),
    )
    return ResultadoExecucao(analise, presenca, ausencia, metadados, arquivos)


def executar_completo(
    parametros: ParametrosAmostragem,
    progresso=None,
    cancelado=None,
) -> ResultadoExecucao:
    """Analisa e amostra numa chamada so. Usado pela CLI e pelos testes."""
    analise = executar_analise(parametros.analise, progresso, cancelado)
    return executar_amostragem(parametros, analise, progresso, cancelado)
