"""Linha de comando, para rodar sem abrir a janela.

Exemplo:

    python amostragem_fogo_v5.py --cli ^
        --raster fogo.tif --saida resultados ^
        --presenca 4 --ausencia 0 3 --n-presenca 1000 --n-ausencia 1000 ^
        --distancia 1000 --excluir 0 --decimal ,
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analise import OperacaoCancelada
from .config import (
    COORDENADAS_ORIGINAL,
    COORDENADAS_WGS84,
    SAIDAS_DISPONIVEIS,
    Faixa,
    ParametrosAmostragem,
    ParametrosAnalise,
    SelecaoDeSaidas,
    texto_para_conjunto,
)
from .pipeline import executar_completo


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amostragem_fogo",
        description="Amostragem de presencas e ausencias a partir de um raster de frequencia de fogo.",
    )
    parser.add_argument("--cli", action="store_true", help="roda sem interface grafica")
    parser.add_argument("--raster", required=True, type=Path, help="GeoTIFF de frequencia")
    parser.add_argument("--saida", required=True, type=Path, help="pasta de saida")
    parser.add_argument("--banda", type=int, default=1)
    parser.add_argument("--anos", type=int, default=41, help="anos observados na serie")
    parser.add_argument("--excluir", default="", help='valores a excluir, ex.: "0; 99"')
    parser.add_argument(
        "--presenca", nargs="+", type=float, required=True,
        help="minimo [maximo] da presenca; sem maximo usa o maior valor observado",
    )
    parser.add_argument("--ausencia", nargs=2, type=float, required=True, metavar=("MIN", "MAX"))
    parser.add_argument("--n-presenca", type=int, default=1000)
    parser.add_argument("--n-ausencia", type=int, default=1000)
    parser.add_argument("--distancia", type=float, default=0.0, help="distancia minima em metros")
    parser.add_argument(
        "--sem-distancia-entre-classes", action="store_true",
        help="permite presenca e ausencia proximas entre si",
    )
    parser.add_argument("--semente", type=int, default=42)
    parser.add_argument(
        "--decimal", choices=[".", ","], default=".",
        help="separador decimal dos CSV: ponto para QGIS/R, virgula para Excel pt-BR",
    )
    parser.add_argument(
        "--xy", choices=[COORDENADAS_WGS84, COORDENADAS_ORIGINAL], default=COORDENADAS_WGS84,
        help="coordenadas gravadas nas colunas x e y do formato x,y,Value",
    )
    parser.add_argument("--valor-presenca", type=int, default=1, help="codigo da presenca em Value")
    parser.add_argument("--valor-ausencia", type=int, default=0, help="codigo da ausencia em Value")
    parser.add_argument(
        "--saidas", nargs="+", metavar="NOME",
        choices=[nome for nome, _ in SAIDAS_DISPONIVEIS],
        help=(
            "arquivos a gravar; sem esta opcao grava todos. Combine classes "
            "(presencas, ausencias, combinado) com formatos (formato_xyv, "
            "formato_detalhado, formato_geojson). Valores aceitos: "
            + ", ".join(nome for nome, _ in SAIDAS_DISPONIVEIS)
        ),
    )
    parser.add_argument(
        "--listar-saidas", action="store_true",
        help="mostra os arquivos que podem ser gravados e encerra",
    )
    return parser


def main(argumentos: list[str] | None = None) -> int:
    if argumentos is not None and "--listar-saidas" in argumentos:
        print("Arquivos que podem ser gravados (use com --saidas):\n")
        for nome, rotulo in SAIDAS_DISPONIVEIS:
            print(f"  {nome:22} {rotulo}")
        return 0

    args = construir_parser().parse_args(argumentos)

    def progresso(valor: float, mensagem: str) -> None:
        print(f"\r[{valor:5.1f}%] {mensagem:<60}", end="", flush=True)

    try:
        analise = ParametrosAnalise(
            raster=args.raster,
            banda=args.banda,
            anos_observados=args.anos,
            valores_excluidos=texto_para_conjunto(args.excluir),
        )
        if len(args.presenca) == 1:
            from .pipeline import executar_analise

            previa = executar_analise(analise)
            presenca = Faixa(args.presenca[0], previa.valor_maximo)
        elif len(args.presenca) == 2:
            presenca = Faixa(args.presenca[0], args.presenca[1])
        else:
            raise ValueError("--presenca aceita um ou dois valores.")

        parametros = ParametrosAmostragem(
            analise=analise,
            saida=args.saida,
            presenca=presenca,
            ausencia=Faixa(args.ausencia[0], args.ausencia[1]),
            n_presenca=args.n_presenca,
            n_ausencia=args.n_ausencia,
            distancia_minima_m=args.distancia,
            distancia_entre_classes=not args.sem_distancia_entre_classes,
            semente=args.semente,
            separador_decimal=args.decimal,
            saidas=SelecaoDeSaidas.de_nomes(args.saidas) if args.saidas else SelecaoDeSaidas(),
            coordenadas_xy=args.xy,
            valor_presenca=args.valor_presenca,
            valor_ausencia=args.valor_ausencia,
        )
        resultado = executar_completo(parametros, progresso)
    except OperacaoCancelada as exc:
        print(f"\n{exc}")
        return 130
    except (ValueError, OSError) as exc:
        print(f"\nErro: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Presencas: {len(resultado.presenca)} de {resultado.presenca.solicitado}")
    print(f"Ausencias: {len(resultado.ausencia)} de {resultado.ausencia.solicitado}")
    for aviso in resultado.avisos:
        print(f"Aviso: {aviso}")
    print(f"\n{len(resultado.arquivos)} arquivos gravados em {parametros.saida}:")
    for caminho in resultado.arquivos:
        print(f"  {caminho.name}")
    return 0
