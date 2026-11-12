"""Escrita dos arquivos de saida.

Formato x,y,Value (V5)
----------------------

As tabelas de amostras saem no mesmo formato dos arquivos de ocorrencia usados
em modelagem de distribuicao (MaxEnt, dismo, biomod2):

    x,y,Value
    -42.61,-10.864167,1
    -39.014331,-7.712431,1

Virgula separando as colunas, ponto decimal, ate 6 casas sem zeros a direita, e
uma coluna ``Value`` com o codigo da classe (1 para presenca, 0 para ausencia,
configuravel). Esse arquivo tem separador fixo de proposito: a virgula ja separa
as colunas, entao usar virgula decimal aqui quebraria o arquivo.

As demais tabelas seguem usando ponto e virgula, com o separador decimal que o
usuario escolher.

Por que a V3 entregava coordenadas erradas ao abrir os arquivos
---------------------------------------------------------------

A V3 gravava um CSV com ponto e virgula como separador de campos e **ponto**
como separador decimal, e a documentacao dizia que o arquivo abria direto no
Excel em configuracao brasileira. Nao abre. No Excel pt-BR o ponto e separador
de milhar: ``-46.82625000`` vira ``-4682625000`` e ``208085.00000000`` vira um
inteiro gigante. As coordenadas chegam corrompidas na planilha e, coladas em
qualquer outro programa a partir dela, caem no lugar errado do mapa.

O que a V4 e a V5 fazem
-----------------------

* O separador decimal das tabelas detalhadas e escolhido pelo usuario.
* Junto de cada tabela detalhada saem um ``.csvt`` (tipos das colunas para o
  QGIS) e um ``.prj`` (CRS das colunas ``x_crs_original``/``y_crs_original``).
* Sai tambem um **GeoJSON em EPSG:4326**, que nao tem ambiguidade de separador
  nem de CRS: basta arrastar para o QGIS.
* A tabela detalhada ganha a coluna ``geometria_wkt`` com ``POINT(lon lat)`` em
  WGS84, sempre com ponto decimal.
* Um ``LEIA-ME_SAIDA.txt`` explica qual coluna usar com qual CRS.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path

from .analise import ResultadoAnalise
from .amostragem import Amostra
from .config import SAIDAS_DISPONIVEIS, ParametrosAmostragem
from .sdm import relatorio_preparo

CODIFICACAO = "utf-8-sig"
DELIMITADOR = ";"

CABECALHO_AMOSTRAS = [
    "id",
    "classe",
    "frequencia",
    "linha_raster",
    "coluna_raster",
    "x_crs_original",
    "y_crs_original",
    "longitude_wgs84",
    "latitude_wgs84",
    "geometria_wkt",
    "crs_original",
    "pixels_elegiveis_classe",
    "fracao_amostral_nominal",
    "peso_expansao_nominal",
]

TIPOS_CSVT = [
    "Integer",
    "String(16)",
    "Real(18.6)",
    "Integer64",
    "Integer64",
    "Real(24.9)",
    "Real(24.9)",
    "Real(24.9)",
    "Real(24.9)",
    "String(64)",
    "String(254)",
    "Integer64",
    "Real(24.15)",
    "Real(24.6)",
]


# ---------------------------------------------------------------------------
# Formatacao numerica
# ---------------------------------------------------------------------------


def formatar(valor: float | None, casas: int, separador: str) -> str:
    """Numero com casas fixas e o separador decimal pedido; vazio se ausente."""
    if valor is None:
        return ""
    if isinstance(valor, float) and not math.isfinite(valor):
        return ""
    texto = f"{valor:.{casas}f}"
    return texto.replace(".", separador) if separador != "." else texto


def formatar_generico(valor: float | None, separador: str) -> str:
    """Numero com precisao suficiente, sem notacao cientifica."""
    if valor is None:
        return ""
    if isinstance(valor, float) and not math.isfinite(valor):
        return ""
    texto = f"{valor:.15g}"
    if "e" in texto or "E" in texto:
        texto = f"{valor:.15f}".rstrip("0").rstrip(".")
    return texto.replace(".", separador) if separador != "." else texto


def formatar_valor_inteiro_quando_possivel(valor: float, separador: str) -> str:
    if math.isclose(valor, round(valor), rel_tol=0.0, abs_tol=1e-9):
        return str(int(round(valor)))
    return formatar_generico(valor, separador)


def ptbr(valor: float | None, casas: int = 0) -> str:
    """Numero no padrao brasileiro: ponto no milhar e virgula no decimal.

    Usado apenas no relatorio em texto. Aplicar ``.replace(",", ".")`` sobre o
    resultado de ``f"{v:,.2f}"`` produziria ``1.166.53``, que nao e nem um
    formato nem o outro.
    """
    if valor is None or (isinstance(valor, float) and not math.isfinite(valor)):
        return "-"
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _casas_para_crs(unidade_crs: str) -> int:
    """Casas decimais das coordenadas no CRS original."""
    if unidade_crs.lower().startswith("grau") or unidade_crs.lower() == "degree":
        return 9  # ~0,1 mm no equador
    return 4  # 0,1 mm em CRS metrico


# ---------------------------------------------------------------------------
# Formato x,y,Value
# ---------------------------------------------------------------------------

#: Cabecalho e delimitador do formato usado por MaxEnt, dismo, biomod2 e afins.
CABECALHO_XYV = ["x", "y", "Value"]
DELIMITADOR_XYV = ","
CASAS_XYV = 6  # ~0,11 m em grau decimal; mais que suficiente para qualquer pixel


def formatar_xy(valor: float, casas: int = CASAS_XYV) -> str:
    """Coordenada no estilo do arquivo de referencia.

    Ponto decimal, no maximo ``casas`` casas, sem zeros a direita e sem ponto
    quando o valor e inteiro: ``-42.61``, ``-10.864167``, ``-42``, ``0``.
    O separador decimal aqui e sempre o ponto, porque a virgula ja separa as
    colunas; trocar um pelo outro quebraria o arquivo.
    """
    if valor is None or not math.isfinite(valor):
        return ""
    texto = f"{valor:.{casas}f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    if texto in ("-0", "", "-"):
        return "0"
    return texto


def escrever_csv_xyv(
    caminho: Path,
    amostras: list[Amostra],
    coordenadas: str,
    codigos: dict[str, int],
) -> int:
    """Grava ``x,y,Value``, um ponto por linha.

    ``coordenadas`` escolhe entre longitude/latitude WGS84 e as coordenadas no
    CRS original do raster. Pontos sem coordenada valida sao omitidos: uma
    linha com x ou y vazio entraria como NA no R e como ponto na origem em
    varios programas.
    """
    total = 0
    with caminho.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo, delimiter=DELIMITADOR_XYV)
        escritor.writerow(CABECALHO_XYV)
        for amostra in amostras:
            codigo = codigos[amostra.classe]
            if coordenadas == "wgs84":
                eixo_x, eixo_y = amostra.longitude, amostra.latitude
            else:
                eixo_x, eixo_y = amostra.x, amostra.y
            for i in range(len(amostra)):
                vx = float(eixo_x[i])
                vy = float(eixo_y[i])
                if not (math.isfinite(vx) and math.isfinite(vy)):
                    continue
                escritor.writerow([formatar_xy(vx), formatar_xy(vy), codigo])
                total += 1
    if total == 0:
        caminho.unlink(missing_ok=True)
    return total


# ---------------------------------------------------------------------------
# CSV detalhado de amostras
# ---------------------------------------------------------------------------


def _linhas_amostra(
    amostra: Amostra,
    crs_texto: str,
    unidade_crs: str,
    separador: str,
    id_inicial: int,
):
    casas_crs = _casas_para_crs(unidade_crs)
    fracao = amostra.fracao_nominal
    peso = amostra.peso_nominal
    for i in range(len(amostra)):
        lon = float(amostra.longitude[i])
        lat = float(amostra.latitude[i])
        wkt = ""
        if math.isfinite(lon) and math.isfinite(lat):
            wkt = f"POINT({lon:.9f} {lat:.9f})"
        yield [
            id_inicial + i,
            amostra.classe,
            formatar_valor_inteiro_quando_possivel(float(amostra.frequencias[i]), separador),
            int(amostra.linhas[i]),
            int(amostra.colunas[i]),
            formatar(float(amostra.x[i]), casas_crs, separador),
            formatar(float(amostra.y[i]), casas_crs, separador),
            formatar(lon, 9, separador),
            formatar(lat, 9, separador),
            wkt,
            crs_texto,
            amostra.elegiveis if amostra.elegiveis else "",
            formatar_generico(fracao, separador),
            formatar(peso, 6, separador) if peso is not None else "",
        ]


def escrever_csv_amostras(
    caminho: Path,
    amostras: list[Amostra],
    crs_texto: str,
    unidade_crs: str,
    separador: str,
) -> int:
    total = 0
    with caminho.open("w", newline="", encoding=CODIFICACAO) as arquivo:
        escritor = csv.writer(arquivo, delimiter=DELIMITADOR)
        escritor.writerow(CABECALHO_AMOSTRAS)
        for amostra in amostras:
            for linha in _linhas_amostra(amostra, crs_texto, unidade_crs, separador, total + 1):
                escritor.writerow(linha)
                total += 1
    caminho.with_suffix(".csvt").write_text(
        ",".join(f'"{t}"' for t in TIPOS_CSVT), encoding="utf-8"
    )
    return total


def escrever_prj(caminho: Path, crs) -> None:
    """WKT do CRS original, para o QGIS ler as colunas x/y sem perguntar."""
    if crs is None:
        return
    try:
        caminho.write_text(crs.to_wkt(), encoding="utf-8")
    except Exception:  # CRS exotico: o GeoJSON continua sendo o caminho seguro
        pass


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------


def escrever_geojson(caminho: Path, amostras: list[Amostra], crs_texto: str) -> int:
    """GeoJSON RFC 7946 em EPSG:4326. Pontos sem coordenada valida sao omitidos."""
    total = 0
    omitidos = 0
    with caminho.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write('{"type":"FeatureCollection",')
        arquivo.write('"name":"amostras_frequencia_fogo",')
        arquivo.write(
            '"crs":{"type":"name","properties":'
            '{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}},\n"features":[\n'
        )
        primeiro = True
        for amostra in amostras:
            for i in range(len(amostra)):
                lon = float(amostra.longitude[i])
                lat = float(amostra.latitude[i])
                if not (math.isfinite(lon) and math.isfinite(lat)):
                    omitidos += 1
                    continue
                total += 1
                propriedades = {
                    "id": total,
                    "classe": amostra.classe,
                    "frequencia": float(amostra.frequencias[i]),
                    "linha_raster": int(amostra.linhas[i]),
                    "coluna_raster": int(amostra.colunas[i]),
                    "x_crs_original": float(amostra.x[i]),
                    "y_crs_original": float(amostra.y[i]),
                    "crs_original": crs_texto,
                    "pixels_elegiveis_classe": int(amostra.elegiveis),
                    "fracao_amostral_nominal": amostra.fracao_nominal,
                    "peso_expansao_nominal": amostra.peso_nominal,
                }
                feicao = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(lon, 9), round(lat, 9)],
                    },
                    "properties": propriedades,
                }
                if not primeiro:
                    arquivo.write(",\n")
                arquivo.write(json.dumps(feicao, ensure_ascii=False))
                primeiro = False
        arquivo.write("\n]}\n")
    if total == 0:
        # Sem nenhum ponto com coordenada geografica valida o arquivo nao serve
        # para nada e so confundiria quem abrisse a pasta.
        caminho.unlink(missing_ok=True)
    return total


# ---------------------------------------------------------------------------
# Tabelas auxiliares
# ---------------------------------------------------------------------------


def escrever_csv_limiares(caminho: Path, analise: ResultadoAnalise, separador: str) -> None:
    with caminho.open("w", newline="", encoding=CODIFICACAO) as arquivo:
        escritor = csv.writer(arquivo, delimiter=DELIMITADOR)
        escritor.writerow(
            [
                "limiar",
                "pixels_queimados_mantidos",
                "pixels_queimados_mantidos_pct",
                "ocorrencias_mantidas",
                "ocorrencias_mantidas_pct",
                "intervalo_medio_retorno_anos",
                "interpretacao",
            ]
        )
        for linha in analise.linhas:
            escritor.writerow(
                [
                    ">=" + formatar_valor_inteiro_quando_possivel(linha.limiar, separador),
                    linha.pixels_mantidos,
                    formatar(linha.pixels_mantidos_pct, 6, separador),
                    formatar(linha.ocorrencias_mantidas, 6, separador),
                    formatar(linha.ocorrencias_mantidas_pct, 6, separador),
                    formatar(linha.intervalo_retorno_anos, 3, separador),
                    linha.interpretacao,
                ]
            )


def escrever_csv_distribuicao(caminho: Path, analise: ResultadoAnalise, separador: str) -> None:
    """Histograma bruto: uma linha por valor de frequencia observado."""
    with caminho.open("w", newline="", encoding=CODIFICACAO) as arquivo:
        escritor = csv.writer(arquivo, delimiter=DELIMITADOR)
        escritor.writerow(["frequencia", "pixels", "pixels_pct_validos", "ocorrencias"])
        for valor in analise.valores_observados:
            quantidade = analise.contagens[valor]
            escritor.writerow(
                [
                    formatar_valor_inteiro_quando_possivel(valor, separador),
                    quantidade,
                    formatar(100.0 * quantidade / analise.pixels_validos, 6, separador),
                    formatar(valor * quantidade, 6, separador),
                ]
            )


def escrever_csv_parametros(caminho: Path, parametros: dict[str, object]) -> None:
    with caminho.open("w", newline="", encoding=CODIFICACAO) as arquivo:
        escritor = csv.writer(arquivo, delimiter=DELIMITADOR)
        escritor.writerow(["parametro", "valor"])
        for chave, valor in parametros.items():
            escritor.writerow([chave, "" if valor is None else valor])


# ---------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------

LEIA_ME = """COMO ABRIR CORRETAMENTE OS ARQUIVOS DESTA PASTA

1. amostras_presencas.csv / amostras_ausencias.csv / amostras_todas.csv
   Formato x,y,Value, o mesmo dos arquivos de ocorrencia usados em modelagem
   de distribuicao de especies:

       x,y,Value
       -42.61,-10.864167,1
       -39.014331,-7.712431,1

   Virgula separa as colunas e o ponto e o separador decimal. A coluna Value
   traz o codigo da classe; o arquivo parametros_amostragem.csv registra qual
   codigo foi usado para presenca e qual para ausencia.

   R:      read.csv("amostras_presencas.csv")
   Python: pd.read_csv("amostras_presencas.csv")
   MaxEnt: aponte direto para o arquivo de presencas.

   ATENCAO no Excel em portugues: abrir este arquivo com duplo clique pode
   jogar tudo numa coluna so. Use Dados > Obter dados > De arquivo de texto,
   escolha virgula como delimitador e ponto como separador decimal. Ou use as
   tabelas *_detalhado.csv, que respeitam a configuracao escolhida na tela.

2. QGIS (forma que nunca erra a posicao)
   Arraste o arquivo .geojson para a janela do QGIS.
   Ele ja esta em EPSG:4326 (WGS84) e nao depende de configuracao regional.

3. QGIS a partir do CSV x,y,Value
   Camada > Adicionar camada > Adicionar camada de texto delimitado.
   Separador: virgula.
   Definicao da geometria: coordenadas de ponto, campo X = x, campo Y = y.
   SRC da geometria: o mesmo registrado em coordenadas_xy no arquivo
   parametros_amostragem.csv (EPSG:4326 quando a opcao foi WGS84).

4. Tabelas *_detalhado.csv
   Trazem todas as colunas: linha e coluna do raster, coordenadas nos dois
   sistemas, WKT, fracao amostral e peso de expansao.
   Separador de campos: ponto e virgula.
   Separador decimal: o que foi escolhido na tela (virgula para Excel pt-BR,
   ponto para QGIS, R e Python).
   Carregue no QGIS por WKT, usando o campo geometria_wkt com SRC EPSG:4326.

QUAL COLUNA USAR COM QUAL SISTEMA DE COORDENADAS

   Nos arquivos x,y,Value  -> x e y seguem a opcao coordenadas_xy registrada
                              em parametros_amostragem.csv
   longitude_wgs84 / latitude_wgs84 -> sempre EPSG:4326 (WGS84)
   x_crs_original  / y_crs_original  -> o CRS do raster de entrada, gravado na
                                        coluna crs_original e no arquivo .prj
   geometria_wkt                     -> POINT(longitude latitude), EPSG:4326

Nunca misture: usar x_crs_original declarando EPSG:4326 (ou o contrario) joga
os pontos para fora da area de estudo. Na duvida, use o GeoJSON.

O arquivo .csvt ao lado de cada tabela detalhada informa os tipos das colunas
ao QGIS. Nao apague, nao renomeie e nao edite esses arquivos auxiliares.
"""


def escrever_leia_me(caminho: Path) -> None:
    caminho.write_text(LEIA_ME, encoding="utf-8")


def escrever_relatorio(
    caminho: Path,
    parametros: ParametrosAmostragem,
    analise: ResultadoAnalise,
    presenca: Amostra,
    ausencia: Amostra,
    metadados: dict[str, object],
) -> None:
    modo = metadados.get("modo_metrico")
    linhas: list[str] = []
    a = linhas.append

    a("RELATORIO DA AMOSTRAGEM DA FREQUENCIA DE FOGO")
    a(f"Gerado em {datetime.now():%d/%m/%Y %H:%M:%S}")
    a("")
    a("ENTRADA")
    a(f"  Raster ................. {analise.raster}")
    a(f"  Banda .................. {analise.banda}")
    a(f"  Dimensoes .............. {analise.largura} x {analise.altura} pixels")
    a(f"  Tipo de dado ........... {analise.dtype}")
    a(f"  NoData ................. {analise.nodata}")
    a(f"  CRS .................... {analise.crs_texto}")
    a(f"  Unidade do CRS ......... {analise.unidade_crs}")
    a(f"  Resolucao .............. {analise.resolucao[0]:g} x {analise.resolucao[1]:g} ({analise.unidade_crs})")
    a(f"  Anos observados ........ {analise.anos_observados}")
    excluidos = ", ".join(f"{v:g}" for v in sorted(analise.valores_excluidos)) or "nenhum"
    a(f"  Valores excluidos ...... {excluidos}")
    a("")
    a("DISTRIBUICAO")
    a(f"  Pixels validos ......... {ptbr(analise.pixels_validos)}")
    a(f"  Pixels queimados ....... {ptbr(analise.pixels_queimados)}")
    a(f"  Ocorrencias acumuladas . {ptbr(analise.ocorrencias_totais)}")
    a(f"  Faixa observada ........ {analise.valor_minimo:g} a {analise.valor_maximo:g}")
    a("")
    a("AMOSTRAGEM")
    a(f"  Faixa de presenca ...... {parametros.presenca}")
    a(f"  Faixa de ausencia ...... {parametros.ausencia}")
    a(f"  Semente aleatoria ...... {parametros.semente}")
    a(f"  Distancia minima ....... {parametros.distancia_minima_m:g} m")
    a(f"  Distancia entre classes  {'sim' if parametros.distancia_entre_classes else 'nao'}")
    if modo is not None:
        a(f"  Medida da distancia .... {modo.descricao}")
        a(f"  Distancia exata ........ {'sim' if modo.exato else 'aproximada'}")
    a("")
    for rotulo, amostra in (("PRESENCA", presenca), ("AUSENCIA", ausencia)):
        a(rotulo)
        a(f"  Solicitadas ............ {ptbr(amostra.solicitado)}")
        a(f"  Selecionadas ........... {ptbr(len(amostra))}")
        a(f"  Pixels elegiveis ....... {ptbr(amostra.elegiveis)}")
        a(f"  Candidatos sorteados ... {ptbr(amostra.candidatos)}")
        fracao = amostra.fracao_nominal
        a(f"  Fracao amostral nominal  {ptbr(fracao, 10) if fracao else '-'}")
        a(f"  Peso de expansao ....... {ptbr(amostra.peso_nominal, 2)}")
        if not amostra.completa:
            a("  ATENCAO: a quantidade solicitada nao foi alcancada.")
            a("           Reduza a distancia minima, amplie a faixa de valores")
            a("           ou reduza o numero de amostras.")
        a("")
    for linha in relatorio_preparo(
        parametros.filtro_sdm,
        metadados.get("estatisticas_filtro", {}),  # type: ignore[arg-type]
        str(metadados.get("preditores", "-")),
    ):
        a(linha)
    a("")

    escolhas = parametros.saidas
    a("FORMATO DAS AMOSTRAS")
    a("  Arquivo x,y,Value ...... virgula separando colunas, ponto decimal,")
    a("                           ate 6 casas, sem zeros a direita")
    coord = (
        "longitude e latitude WGS84"
        if parametros.coordenadas_xy == "wgs84"
        else f"CRS original do raster ({analise.crs_texto})"
    )
    a(f"  Colunas x e y .......... {coord}")
    a(f"  Value na presenca ...... {parametros.valor_presenca}")
    a(f"  Value na ausencia ...... {parametros.valor_ausencia}")
    decimal = "virgula" if parametros.separador_decimal == "," else "ponto"
    a(f"  Demais tabelas ......... ponto e virgula, decimal com {decimal}")
    a("")
    a("ARQUIVOS SELECIONADOS")
    for nome, rotulo in SAIDAS_DISPONIVEIS:
        marca = "[x]" if getattr(escolhas, nome) else "[ ]"
        a(f"  {marca} {rotulo}")
    a("")
    a("OBSERVACOES ESTATISTICAS")
    a("  A fracao e o peso gravados sao nominais (n/N e N/n). Com distancia")
    a("  minima maior que zero, a rejeicao espacial faz com que eles nao sejam")
    a("  probabilidades de inclusao exatas. Para inferencia de desenho estrita,")
    a("  use uma implementacao GRTS com probabilidades conhecidas.")
    a("  Se as classes forem amostradas em proporcoes diferentes das existentes")
    a("  na paisagem, registre e use pesos amostrais na calibracao do modelo.")

    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


def exportar_tudo(
    parametros: ParametrosAmostragem,
    analise: ResultadoAnalise,
    presenca: Amostra,
    ausencia: Amostra,
    metadados: dict[str, object],
    crs_objeto=None,
) -> list[Path]:
    """Grava os arquivos marcados na selecao e devolve a lista do que foi criado."""
    saida = parametros.saida
    saida.mkdir(parents=True, exist_ok=True)
    escolhas = parametros.saidas
    separador = parametros.separador_decimal
    crs_texto = str(metadados.get("crs_texto", analise.crs_texto))
    unidade = str(metadados.get("unidade_crs", analise.unidade_crs))
    codigos = {"presenca": parametros.valor_presenca, "ausencia": parametros.valor_ausencia}

    criados: list[Path] = []

    def registrar(caminho: Path) -> None:
        if caminho.exists():
            criados.append(caminho)

    # -- amostras -----------------------------------------------------------
    # Classe e formato sao escolhas independentes: a classe diz QUAIS pontos
    # exportar, o formato diz EM QUE ARQUIVO. Marcar so "presencas" com so
    # "x,y,Value" grava apenas amostras_presencas.csv.
    conjuntos: list[tuple[str, list[Amostra], bool]] = [
        ("amostras_presencas", [presenca], escolhas.presencas),
        ("amostras_ausencias", [ausencia], escolhas.ausencias),
        ("amostras_todas", [presenca, ausencia], escolhas.combinado),
    ]
    for nome, grupo, exportar_classe in conjuntos:
        if not exportar_classe or sum(len(a) for a in grupo) == 0:
            continue

        if escolhas.formato_xyv:
            caminho = saida / f"{nome}.csv"
            escrever_csv_xyv(caminho, grupo, parametros.coordenadas_xy, codigos)
            registrar(caminho)

        if escolhas.formato_detalhado:
            detalhado = saida / f"{nome}_detalhado.csv"
            escrever_csv_amostras(detalhado, grupo, crs_texto, unidade, separador)
            registrar(detalhado)
            registrar(detalhado.with_suffix(".csvt"))
            if crs_objeto is not None:
                escrever_prj(detalhado.with_suffix(".prj"), crs_objeto)
                registrar(detalhado.with_suffix(".prj"))

        if escolhas.formato_geojson:
            geojson = saida / f"{nome}.geojson"
            if escrever_geojson(geojson, grupo, crs_texto) > 0:
                registrar(geojson)

    # -- tabelas da analise -------------------------------------------------
    if escolhas.tabela_limiares:
        caminho = saida / "tabela_limiares_frequencia.csv"
        escrever_csv_limiares(caminho, analise, separador)
        registrar(caminho)

    if escolhas.distribuicao:
        caminho = saida / "distribuicao_frequencias.csv"
        escrever_csv_distribuicao(caminho, analise, separador)
        registrar(caminho)

    # -- registro da execucao -----------------------------------------------
    modo = metadados.get("modo_metrico")
    if escolhas.parametros:
        caminho = saida / "parametros_amostragem.csv"
        escrever_csv_parametros(
            caminho,
            {
                "raster": str(analise.raster),
                "banda": analise.banda,
                "anos_observados": analise.anos_observados,
                "valores_excluidos": ", ".join(f"{v:g}" for v in sorted(analise.valores_excluidos)),
                "presenca_min": f"{parametros.presenca.minimo:g}",
                "presenca_max": f"{parametros.presenca.maximo:g}",
                "ausencia_min": f"{parametros.ausencia.minimo:g}",
                "ausencia_max": f"{parametros.ausencia.maximo:g}",
                "presencas_solicitadas": parametros.n_presenca,
                "presencas_selecionadas": len(presenca),
                "ausencias_solicitadas": parametros.n_ausencia,
                "ausencias_selecionadas": len(ausencia),
                "pixels_elegiveis_presenca": presenca.elegiveis,
                "pixels_elegiveis_ausencia": ausencia.elegiveis,
                "candidatos_presenca": presenca.candidatos,
                "candidatos_ausencia": ausencia.candidatos,
                "distancia_minima_m": f"{parametros.distancia_minima_m:g}",
                "distancia_entre_classes": "sim" if parametros.distancia_entre_classes else "nao",
                "metodo_distancia": modo.nome if modo is not None else "",
                "distancia_exata": ("sim" if modo.exato else "nao") if modo is not None else "",
                "semente": parametros.semente,
                "separador_decimal": "virgula" if separador == "," else "ponto",
                "coordenadas_xy": parametros.coordenadas_xy,
                "codigo_presenca": parametros.valor_presenca,
                "codigo_ausencia": parametros.valor_ausencia,
                "saidas_selecionadas": ", ".join(escolhas.ligadas()),
                "preditores_sdm": str(metadados.get("preditores", "")),
                "exigir_preditor_valido": (
                    "sim" if parametros.filtro_sdm.exigir_preditor_valido else "nao"
                ) if parametros.filtro_sdm.ativo else "",
                "uma_amostra_por_celula": (
                    "sim" if parametros.filtro_sdm.uma_amostra_por_celula else "nao"
                ) if parametros.filtro_sdm.ativo else "",
                "crs_original": crs_texto,
                "unidade_crs": unidade,
                "gerado_em": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            },
        )
        registrar(caminho)

    if escolhas.relatorio:
        caminho = saida / "relatorio_amostragem.txt"
        escrever_relatorio(caminho, parametros, analise, presenca, ausencia, metadados)
        registrar(caminho)

    if escolhas.leia_me:
        caminho = saida / "LEIA-ME_SAIDA.txt"
        escrever_leia_me(caminho)
        registrar(caminho)

    return criados
