"""Testes automatizados da aba Amostragem de Fogo (nucleo V5).

Executar com:

    .\\.venv\\Scripts\\python.exe testes_v5.py

Cria rasters sinteticos numa pasta temporaria, roda o mesmo caminho que a
interface grafica usa e confere numericamente cada resultado.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Geod, Transformer
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amostragem_fogo.amostragem import verificar_distancia_minima
from amostragem_fogo.analise import OperacaoCancelada
from amostragem_fogo.config import (
    Faixa,
    ParametrosAmostragem,
    ParametrosAnalise,
    texto_para_conjunto,
    texto_para_float,
    texto_para_int,
)
from amostragem_fogo.exportacao import formatar
from amostragem_fogo.geo import GradeEspacial, centros_de_pixel
from amostragem_fogo.pipeline import executar_completo

GEOD = Geod(ellps="WGS84")
_falhas: list[str] = []
_ok = 0


def checar(condicao: bool, descricao: str) -> None:
    global _ok
    if condicao:
        _ok += 1
        print(f"  [ok]    {descricao}")
    else:
        _falhas.append(descricao)
        print(f"  [FALHA] {descricao}")


def proximo(a: float, b: float, tolerancia: float, descricao: str) -> None:
    checar(abs(a - b) <= tolerancia, f"{descricao} (obtido {a!r}, esperado {b!r})")


# ---------------------------------------------------------------------------
# Rasters de teste
# ---------------------------------------------------------------------------


def criar_rasters(pasta: Path) -> dict[str, Path]:
    rng = np.random.default_rng(7)
    largura = altura = 700
    dados = rng.integers(0, 8, size=(altura, largura)).astype("int16")
    dados[rng.random((altura, largura)) < 0.25] = 0
    dados[:30, :30] = -9999  # NoData

    caminhos: dict[str, Path] = {}

    perfis = {
        "geografico": dict(crs="EPSG:4326", transform=from_origin(-47.5, -15.0, 0.0025, 0.0025)),
        "utm": dict(crs="EPSG:31983", transform=from_origin(200000.0, 8350000.0, 30.0, 30.0)),
        "sem_crs": dict(crs=None, transform=from_origin(200000.0, 8350000.0, 30.0, 30.0)),
    }
    for nome, perfil in perfis.items():
        caminho = pasta / f"{nome}.tif"
        with rasterio.open(
            caminho, "w", driver="GTiff", height=altura, width=largura, count=1,
            dtype="int16", nodata=-9999, tiled=True, blockxsize=256, blockysize=256,
            **perfil,
        ) as dst:
            dst.write(dados, 1)
        caminhos[nome] = caminho

    # GeoTIFF em faixas de 1 linha: o caso que travava a V3 com milhares de janelas
    caminho = pasta / "faixas.tif"
    with rasterio.open(
        caminho, "w", driver="GTiff", height=altura, width=largura, count=1,
        dtype="int16", nodata=-9999, crs="EPSG:4326",
        transform=from_origin(-47.5, -15.0, 0.0025, 0.0025), blockysize=1,
    ) as dst:
        dst.write(dados, 1)
    caminhos["faixas"] = caminho
    return caminhos


def parametros(raster: Path, saida: Path, **extras) -> ParametrosAmostragem:
    base = dict(
        analise=ParametrosAnalise(
            raster=raster, banda=1, anos_observados=41, valores_excluidos=frozenset({-9999.0})
        ),
        saida=saida,
        presenca=Faixa(4, 7),
        ausencia=Faixa(0, 3),
        n_presenca=60,
        n_ausencia=60,
        distancia_minima_m=1500.0,
        distancia_entre_classes=True,
        semente=42,
    )
    base.update(extras)
    return ParametrosAmostragem(**base)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


def teste_conversoes() -> None:
    print("\n1. Conversao de texto para numero")
    proximo(texto_para_float("1,5", "x"), 1.5, 0, "virgula decimal aceita")
    proximo(texto_para_float("1.5", "x"), 1.5, 0, "ponto decimal aceito")
    checar(texto_para_float("", "x", vazio_permitido=True) is None, "campo vazio permitido")
    checar(texto_para_int("1.000", "n") == 1000, "separador de milhar removido")
    checar(texto_para_conjunto("0; 99, 255") == frozenset({0.0, 99.0, 255.0}), "lista de exclusoes")
    for texto in ("abc", "1,2,3"):
        try:
            texto_para_float(texto, "x")
            checar(False, f"texto invalido {texto!r} deveria falhar")
        except ValueError:
            checar(True, f"texto invalido {texto!r} rejeitado")


def teste_faixas() -> None:
    print("\n2. Validacao das faixas")
    checar(Faixa(4, 7).sobrepoe(Faixa(0, 3)) is False, "faixas disjuntas nao se sobrepoem")
    checar(Faixa(3, 7).sobrepoe(Faixa(0, 3)) is True, "faixas com valor comum se sobrepoem")
    try:
        Faixa(7, 4)
        checar(False, "faixa invertida deveria falhar")
    except ValueError:
        checar(True, "faixa invertida rejeitada")


def teste_centro_do_pixel() -> None:
    print("\n3. Centro do pixel")
    from rasterio.transform import from_origin

    t = from_origin(-47.5, -15.0, 0.0025, 0.0025)
    x, y = centros_de_pixel(t, np.array([0, 1]), np.array([0, 1]))
    proximo(float(x[0]), -47.5 + 0.00125, 1e-12, "x do primeiro pixel no centro")
    proximo(float(y[0]), -15.0 - 0.00125, 1e-12, "y do primeiro pixel no centro")
    proximo(float(x[1]), -47.5 + 0.00375, 1e-12, "x do segundo pixel no centro")


def teste_coordenadas_contra_rasterio(caminhos: dict[str, Path]) -> None:
    print("\n4. Coordenadas conferidas contra o rasterio")
    for nome in ("geografico", "utm"):
        with rasterio.open(caminhos[nome]) as src:
            linhas = np.array([0, 37, 350, 699])
            colunas = np.array([0, 120, 400, 699])
            x, y = centros_de_pixel(src.transform, linhas, colunas)
            for i in range(len(linhas)):
                rx, ry = src.xy(int(linhas[i]), int(colunas[i]))  # centro do pixel
                proximo(float(x[i]), rx, 1e-9, f"{nome}: x do pixel ({linhas[i]},{colunas[i]})")
                proximo(float(y[i]), ry, 1e-9, f"{nome}: y do pixel ({linhas[i]},{colunas[i]})")


def teste_distancia_ecef(caminhos: dict[str, Path]) -> None:
    print("\n5. Distancia geocentrica contra a geodesica exata")
    with rasterio.open(caminhos["geografico"]) as src:
        from amostragem_fogo.geo import abrir_sistema

        sistema = abrir_sistema(src)
        checar(sistema.modo_metrico.exato, "modo geocentrico selecionado no raster geografico")
        lon0, lat0 = -47.2, -15.4
        for alvo in (100.0, 1000.0, 10000.0, 50000.0):
            lon1, lat1, _ = GEOD.fwd(lon0, lat0, 37.0, alvo)
            mx, my, mz = sistema.para_metros(
                np.array([lon0, lon1]), np.array([lat0, lat1])
            )
            corda = float(
                np.sqrt((mx[0] - mx[1]) ** 2 + (my[0] - my[1]) ** 2 + (mz[0] - mz[1]) ** 2)
            )
            erro = abs(corda - alvo) / alvo
            checar(erro < 1e-5, f"corda de {alvo:.0f} m com erro relativo {erro:.2e} < 1e-5")

    with rasterio.open(caminhos["utm"]) as src:
        from amostragem_fogo.geo import abrir_sistema

        sistema = abrir_sistema(src)
        checar(sistema.modo_metrico.exato, "modo geocentrico selecionado tambem no raster UTM")


def teste_grade_espacial() -> None:
    print("\n6. Grade espacial de rejeicao")
    grade = GradeEspacial(1000.0)
    grade.inserir(0.0, 0.0, 0.0)
    checar(grade.aceita(999.0, 0.0, 0.0) is False, "ponto a 999 m e rejeitado")
    checar(grade.aceita(1001.0, 0.0, 0.0) is True, "ponto a 1001 m e aceito")
    checar(grade.aceita(0.0, 0.0, 1500.0) is True, "vizinhanca em z respeitada")
    livre = GradeEspacial(0.0)
    livre.inserir(0.0, 0.0, 0.0)
    checar(livre.aceita(0.0, 0.0, 0.0) is True, "distancia zero aceita tudo")


def teste_execucao(caminhos: dict[str, Path], base: Path) -> None:
    print("\n7. Execucao completa no raster geografico")
    saida = base / "saida_geografico"
    par = parametros(caminhos["geografico"], saida)
    resultado = executar_completo(par)

    checar(len(resultado.presenca) == 60, "60 presencas selecionadas")
    checar(len(resultado.ausencia) == 60, "60 ausencias selecionadas")

    freq_p = resultado.presenca.frequencias
    checar(bool(np.all((freq_p >= 4) & (freq_p <= 7))), "presencas dentro da faixa 4-7")
    freq_a = resultado.ausencia.frequencias
    checar(bool(np.all((freq_a >= 0) & (freq_a <= 3))), "ausencias dentro da faixa 0-3")

    # Conferir valor do pixel direto no raster, pela linha/coluna gravada.
    with rasterio.open(caminhos["geografico"]) as src:
        banda = src.read(1)
        conferidos = 0
        for i in range(len(resultado.presenca)):
            lin = int(resultado.presenca.linhas[i])
            col = int(resultado.presenca.colunas[i])
            if banda[lin, col] != resultado.presenca.frequencias[i]:
                break
            conferidos += 1
        checar(conferidos == len(resultado.presenca), "linha/coluna batem com o valor do raster")

        # As coordenadas gravadas devem voltar ao mesmo pixel.
        erros = 0
        for i in range(len(resultado.presenca)):
            lin, col = src.index(float(resultado.presenca.x[i]), float(resultado.presenca.y[i]))
            if (lin, col) != (int(resultado.presenca.linhas[i]), int(resultado.presenca.colunas[i])):
                erros += 1
        checar(erros == 0, "coordenadas de saida retornam ao pixel de origem")

        from amostragem_fogo.geo import abrir_sistema

        sistema = abrir_sistema(src)

    menor = verificar_distancia_minima([resultado.presenca, resultado.ausencia], sistema)
    checar(menor >= 1500.0, f"distancia minima respeitada entre todas as classes ({menor:.1f} m)")

    # Conferencia independente: distancia geodesica real entre lon/lat gravados.
    lons = np.concatenate([resultado.presenca.longitude, resultado.ausencia.longitude])
    lats = np.concatenate([resultado.presenca.latitude, resultado.ausencia.latitude])
    menor_geo = float("inf")
    for i in range(len(lons)):
        for j in range(i + 1, len(lons)):
            _, _, d = GEOD.inv(lons[i], lats[i], lons[j], lats[j])
            menor_geo = min(menor_geo, d)
    checar(
        menor_geo >= 1500.0 * 0.9999,
        f"distancia geodesica real tambem respeitada ({menor_geo:.1f} m)",
    )


def teste_saida_utm(caminhos: dict[str, Path], base: Path) -> None:
    print("\n8. Coordenadas de saida no raster UTM")
    saida = base / "saida_utm"
    par = parametros(caminhos["utm"], saida)
    resultado = executar_completo(par)

    para_wgs = Transformer.from_crs(CRS.from_epsg(31983), CRS.from_epsg(4326), always_xy=True)
    erros = 0
    for i in range(len(resultado.presenca)):
        lon, lat = para_wgs.transform(
            float(resultado.presenca.x[i]), float(resultado.presenca.y[i])
        )
        if abs(lon - float(resultado.presenca.longitude[i])) > 1e-9:
            erros += 1
        if abs(lat - float(resultado.presenca.latitude[i])) > 1e-9:
            erros += 1
    checar(erros == 0, "longitude/latitude coerentes com x/y no CRS original")

    xs = resultado.presenca.x
    ys = resultado.presenca.y
    checar(bool(np.all((xs > 190000) & (xs < 225000))), "x dentro da extensao do raster")
    checar(bool(np.all((ys > 8328000) & (ys < 8351000))), "y dentro da extensao do raster")

    # Prova final: pegar as coordenadas do GeoJSON, voltar ao CRS do raster e
    # amostrar o pixel. Se a frequencia bater em todos os pontos, a coordenada
    # gravada aponta exatamente para o pixel que foi sorteado.
    feicoes = json.loads((saida / "amostras_presencas.geojson").read_text(encoding="utf-8"))
    de_wgs = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(31983), always_xy=True)
    with rasterio.open(caminhos["utm"]) as src:
        pontos = [
            de_wgs.transform(*feicao["geometry"]["coordinates"])
            for feicao in feicoes["features"]
        ]
        amostrados = [valor[0] for valor in src.sample(pontos)]
    esperados = [feicao["properties"]["frequencia"] for feicao in feicoes["features"]]
    iguais = sum(1 for a, e in zip(amostrados, esperados) if float(a) == float(e))
    checar(
        iguais == len(esperados),
        f"o raster amostrado nas coordenadas do GeoJSON devolve a frequencia gravada "
        f"({iguais}/{len(esperados)} pontos)",
    )


def teste_arquivos_de_saida(base: Path) -> None:
    print("\n9. Arquivos gerados e leitura de volta")
    saida = base / "saida_geografico"
    esperados = [
        "amostras_presencas.csv",
        "amostras_ausencias.csv",
        "amostras_todas.csv",
        "amostras_presencas_detalhado.csv",
        "amostras_presencas_detalhado.csvt",
        "amostras_presencas_detalhado.prj",
        "amostras_ausencias_detalhado.csv",
        "amostras_todas_detalhado.csv",
        "amostras_presencas.geojson",
        "amostras_todas.geojson",
        "tabela_limiares_frequencia.csv",
        "distribuicao_frequencias.csv",
        "parametros_amostragem.csv",
        "relatorio_amostragem.txt",
        "LEIA-ME_SAIDA.txt",
    ]
    for nome in esperados:
        checar((saida / nome).is_file(), f"arquivo {nome} criado")

    with (saida / "amostras_presencas_detalhado.csv").open(encoding="utf-8-sig") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    checar(len(linhas) == 60, "tabela detalhada de presencas com 60 linhas")
    primeira = linhas[0]
    checar("," not in primeira["longitude_wgs84"], "separador ponto respeitado no CSV")
    lon = float(primeira["longitude_wgs84"])
    lat = float(primeira["latitude_wgs84"])
    checar(-47.51 < lon < -45.74, f"longitude dentro da area de estudo ({lon})")
    checar(-16.76 < lat < -14.99, f"latitude dentro da area de estudo ({lat})")
    wkt = primeira["geometria_wkt"]
    checar(wkt.startswith("POINT(") and wkt.count(".") >= 2, "coluna WKT com ponto decimal")
    dentro = wkt[6:-1].split()
    proximo(float(dentro[0]), lon, 1e-6, "WKT usa a mesma longitude")
    proximo(float(dentro[1]), lat, 1e-6, "WKT usa a mesma latitude")

    dados = json.loads((saida / "amostras_presencas.geojson").read_text(encoding="utf-8"))
    checar(dados["type"] == "FeatureCollection", "GeoJSON valido")
    checar(len(dados["features"]) == 60, "GeoJSON com 60 feicoes")
    coord = dados["features"][0]["geometry"]["coordinates"]
    proximo(coord[0], lon, 1e-6, "GeoJSON usa a mesma longitude do CSV")
    proximo(coord[1], lat, 1e-6, "GeoJSON usa a mesma latitude do CSV")

    todas = json.loads((saida / "amostras_todas.geojson").read_text(encoding="utf-8"))
    checar(len(todas["features"]) == 120, "GeoJSON combinado com 120 feicoes")
    classes = {f["properties"]["classe"] for f in todas["features"]}
    checar(classes == {"presenca", "ausencia"}, "GeoJSON combinado traz as duas classes")


def teste_separador_virgula(caminhos: dict[str, Path], base: Path) -> None:
    print("\n10. Separador decimal virgula para Excel pt-BR")
    saida = base / "saida_virgula"
    par = parametros(caminhos["geografico"], saida, separador_decimal=",")
    executar_completo(par)
    with (saida / "amostras_presencas_detalhado.csv").open(encoding="utf-8-sig") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    bruto = linhas[0]["longitude_wgs84"]
    checar("," in bruto and "." not in bruto, f"longitude com virgula decimal ({bruto})")
    valor = float(bruto.replace(",", "."))
    checar(-47.51 < valor < -45.74, "longitude continua correta apos a troca de separador")
    wkt = linhas[0]["geometria_wkt"]
    checar("." in wkt and "," not in wkt, "coluna WKT permanece com ponto, como manda a norma")
    dados = json.loads((saida / "amostras_presencas.geojson").read_text(encoding="utf-8"))
    checar(len(dados["features"]) == 60, "GeoJSON continua valido no modo virgula")

    proximo(float(formatar(-46.826250, 6, ",").replace(",", ".")), -46.82625, 1e-9,
            "formatar com virgula preserva o valor")


def teste_tabela_limiares(caminhos: dict[str, Path], base: Path) -> None:
    print("\n11. Coerencia da tabela de limiares")
    from amostragem_fogo.pipeline import executar_analise

    analise = executar_analise(
        ParametrosAnalise(
            raster=caminhos["geografico"], banda=1, anos_observados=41,
            valores_excluidos=frozenset({-9999.0}),
        )
    )
    contagens = analise.contagens
    checar(-9999.0 not in contagens, "NoData excluido da contagem")
    positivos = analise.valores_positivos
    checar(positivos == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], "valores positivos observados")

    primeira = analise.linhas[0]
    proximo(primeira.limiar, 1.0, 0, "primeira linha e o menor limiar positivo")
    proximo(primeira.pixels_mantidos_pct, 100.0, 1e-9, "menor limiar mantem 100% dos pixels")
    proximo(primeira.ocorrencias_mantidas_pct, 100.0, 1e-9, "menor limiar mantem 100% das ocorrencias")
    ultima = analise.linhas[-1]
    proximo(float(ultima.pixels_mantidos), float(contagens[7.0]), 0, "maior limiar mantem so o valor 7")
    proximo(ultima.intervalo_retorno_anos or 0.0, 41 / 7, 1e-9, "intervalo de retorno do limiar 7")

    soma = sum(v * n for v, n in contagens.items() if v > 0)
    proximo(analise.ocorrencias_totais, soma, 1e-6, "total de ocorrencias confere")
    proximo(
        float(analise.pixels_queimados),
        float(sum(n for v, n in contagens.items() if v > 0)),
        0,
        "total de pixels queimados confere",
    )


def teste_faixas_em_geotiff_striped(caminhos: dict[str, Path], base: Path) -> None:
    print("\n12. GeoTIFF em faixas de uma linha")
    from amostragem_fogo.blocos import montar_plano

    with rasterio.open(caminhos["faixas"]) as src:
        plano = montar_plano(src, 1)
        checar(src.block_shapes[0][0] == 1, "arquivo realmente esta em faixas de 1 linha")
        checar(len(plano) < 20, f"janelas agregadas em {len(plano)} leituras, nao {src.height}")
        cobertos = sum(int(j.width) * int(j.height) for j in plano.janelas)
        checar(cobertos == src.width * src.height, "as janelas cobrem o raster inteiro sem sobra")

    saida = base / "saida_faixas"
    resultado = executar_completo(parametros(caminhos["faixas"], saida, n_presenca=30, n_ausencia=30))
    checar(len(resultado.presenca) == 30, "amostragem funciona em arquivo em faixas")


def teste_sem_crs(caminhos: dict[str, Path], base: Path) -> None:
    print("\n13. Raster sem CRS")
    try:
        executar_completo(parametros(caminhos["sem_crs"], base / "saida_sem_crs"))
        checar(False, "distancia em metros sem CRS deveria ser recusada")
    except ValueError as exc:
        checar("CRS" in str(exc), f"mensagem clara ao usuario: {exc}")

    saida = base / "saida_sem_crs_zero"
    resultado = executar_completo(
        parametros(caminhos["sem_crs"], saida, distancia_minima_m=0.0)
    )
    checar(len(resultado.presenca) == 60, "amostragem sem CRS funciona com distancia zero")
    with (saida / "amostras_presencas_detalhado.csv").open(encoding="utf-8-sig") as arquivo:
        linha = next(csv.DictReader(arquivo, delimiter=";"))
    checar(linha["longitude_wgs84"] == "", "longitude fica vazia, nao zero, quando nao ha CRS")
    checar(linha["geometria_wkt"] == "", "WKT fica vazio quando nao ha CRS")
    checar(not (saida / "amostras_presencas.geojson").exists(), "GeoJSON nao e criado sem CRS")


def teste_casos_limite(caminhos: dict[str, Path], base: Path) -> None:
    print("\n14. Casos limite")
    saida = base / "saida_so_presenca"
    resultado = executar_completo(
        parametros(caminhos["geografico"], saida, n_ausencia=0)
    )
    checar(len(resultado.ausencia) == 0, "zero ausencias solicitadas devolve zero")
    checar(len(resultado.presenca) == 60, "presencas continuam sendo selecionadas")
    checar(not (saida / "amostras_ausencias.csv").exists(), "CSV vazio de ausencias nao e criado")
    checar(
        not (saida / "amostras_ausencias_detalhado.csv").exists(),
        "tabela detalhada vazia de ausencias nao e criada",
    )

    try:
        parametros(caminhos["geografico"], base / "x", presenca=Faixa(3, 7))
        checar(False, "faixas sobrepostas deveriam ser recusadas")
    except ValueError:
        checar(True, "faixas sobrepostas recusadas")

    try:
        parametros(caminhos["geografico"], base / "x", n_presenca=0, n_ausencia=0)
        checar(False, "zero amostras nas duas classes deveria ser recusado")
    except ValueError:
        checar(True, "zero amostras nas duas classes recusado")

    try:
        parametros(caminhos["geografico"], base / "x", distancia_minima_m=-5)
        checar(False, "distancia negativa deveria ser recusada")
    except ValueError:
        checar(True, "distancia negativa recusada")

    try:
        ParametrosAnalise(raster=base / "nao_existe.tif")
        checar(False, "raster inexistente deveria ser recusado")
    except ValueError:
        checar(True, "raster inexistente recusado")

    # Pedir mais do que cabe com a distancia exigida
    saida = base / "saida_impossivel"
    resultado = executar_completo(
        parametros(caminhos["geografico"], saida, n_presenca=5000, distancia_minima_m=8000.0)
    )
    checar(len(resultado.presenca) < 5000, "quantidade inatingivel devolve menos amostras")
    checar(bool(resultado.avisos), "aviso registrado quando a quantidade nao e atingida")


def teste_reprodutibilidade(caminhos: dict[str, Path], base: Path) -> None:
    print("\n15. Reprodutibilidade pela semente")
    a = executar_completo(parametros(caminhos["geografico"], base / "rep_a", semente=123))
    b = executar_completo(parametros(caminhos["geografico"], base / "rep_b", semente=123))
    c = executar_completo(parametros(caminhos["geografico"], base / "rep_c", semente=999))
    checar(
        np.array_equal(a.presenca.linhas, b.presenca.linhas)
        and np.array_equal(a.presenca.colunas, b.presenca.colunas),
        "mesma semente produz exatamente as mesmas amostras",
    )
    checar(
        not np.array_equal(a.presenca.linhas, c.presenca.linhas),
        "sementes diferentes produzem amostras diferentes",
    )


def teste_cancelamento(caminhos: dict[str, Path], base: Path) -> None:
    print("\n16. Cancelamento")
    try:
        executar_completo(
            parametros(caminhos["geografico"], base / "cancelado"), cancelado=lambda: True
        )
        checar(False, "cancelamento deveria interromper")
    except OperacaoCancelada:
        checar(True, "cancelamento interrompe com excecao propria")


def teste_interface(caminhos: dict[str, Path], base: Path) -> None:
    print("\n17. Interface grafica")
    import tkinter as tk

    try:
        from amostragem_fogo.gui import Aplicacao

        app = Aplicacao()
    except tk.TclError:
        print("  [pulado] sem ambiente grafico disponivel")
        return

    try:
        app.withdraw()
        app.update_idletasks()

        try:
            app._ler_parametros_analise()
            checar(False, "raster vazio deveria ser recusado")
        except ValueError:
            checar(True, "raster vazio recusado antes de iniciar a thread")

        app.var_raster.set(str(caminhos["geografico"]))
        app.var_excluir.set("-9999")

        try:
            app._ler_parametros_amostragem()
            checar(False, "amostrar sem analise deveria ser recusado")
        except ValueError:
            checar(True, "amostrar sem analise recusado")

        from amostragem_fogo.pipeline import executar_analise

        app.analise = executar_analise(app._ler_parametros_analise())
        app._concluir_analise(app.analise)
        checar(len(app.tabela.get_children()) == 7, "tabela preenchida com os 7 limiares")
        checar("EPSG:4326" in app.var_resumo.get(), "resumo mostra o CRS")

        app.tabela.selection_set("5")
        app._usar_limiar()
        checar(
            (app.var_p_min.get(), app.var_a_max.get()) == ("5", "4"),
            "limiar selecionado preenche presenca e ausencia",
        )

        app.var_saida.set(str(base / "saida_gui"))
        app.var_n_p.set("40")
        app.var_n_a.set("40")
        parametros_gui = app._ler_parametros_amostragem()
        checar(parametros_gui.n_presenca == 40, "quantidades lidas da tela")
        checar(parametros_gui.separador_decimal == ".", "separador decimal padrao e o ponto")

        app.var_banda.set("2")
        checar(app.analise is None, "trocar a banda invalida a analise anterior")

        caminho_log = app._gravar_log("traceback de teste")
        checar(caminho_log is not None and caminho_log.is_file(), "erro gravado em arquivo de log")

        # -- caixas de selecao dos arquivos de saida --
        checar(len(app.vars_saidas) == 11, "onze caixas de selecao na tela")
        checar(
            all(bool(var.get()) for var in app.vars_saidas.values()),
            "todas as caixas comecam marcadas",
        )

        app._marcar_saidas(False)
        checar(
            not any(bool(var.get()) for var in app.vars_saidas.values()),
            "botao Desmarcar todas desliga tudo",
        )
        try:
            app._ler_selecao_de_saidas()
            checar(False, "tela sem nenhuma caixa marcada deveria ser recusada")
        except ValueError as exc:
            checar("marcad" in str(exc).lower(), f"recusa orienta o usuario: {str(exc).splitlines()[0]}")

        app._somente_presencas()
        selecao = app._ler_selecao_de_saidas()
        checar(
            selecao.ligadas() == ["presencas", "formato_xyv"],
            f"botao So presencas deixa so presencas em x,y,Value: {selecao.ligadas()}",
        )

        app._somente_ausencias()
        selecao = app._ler_selecao_de_saidas()
        checar(
            selecao.ligadas() == ["ausencias", "formato_xyv"],
            f"botao So ausencias deixa so ausencias em x,y,Value: {selecao.ligadas()}",
        )

        app._marcar_saidas(True)
        checar(len(app._ler_selecao_de_saidas().ligadas()) == 11, "botao Marcar todas religa tudo")

        # -- formato do x,y,Value --
        checar(app.var_coordenadas.get() == "wgs84", "coordenadas do x,y,Value comecam em WGS84")
        app.var_valor_presenca.set("3")
        app.var_valor_ausencia.set("7")
        app.var_banda.set("1")  # o teste de invalidacao acima deixou a banda em 2
        app.analise = executar_analise(app._ler_parametros_analise())
        app.var_saida.set(str(base / "saida_gui_v5"))
        par = app._ler_parametros_amostragem()
        checar(par.valor_presenca == 3 and par.valor_ausencia == 7, "codigos Value lidos da tela")

        app.var_valor_ausencia.set("3")
        try:
            app._ler_parametros_amostragem()
            checar(False, "codigos iguais na tela deveriam ser recusados")
        except ValueError:
            checar(True, "codigos iguais na tela sao recusados antes de processar")
    finally:
        app.destroy()


def teste_formato_xyv(caminhos: dict[str, Path], base: Path) -> None:
    print("\n18. Formato x,y,Value")
    from amostragem_fogo.exportacao import formatar_xy

    # O formatador tem de reproduzir o estilo do arquivo de referencia.
    casos = [
        (-42.61, "-42.61"),
        (-10.864167, "-10.864167"),
        (-39.014331, "-39.014331"),
        (-42.0, "-42"),
        (0.0, "0"),
        (-0.0000001, "0"),
        (-12.5, "-12.5"),
        (-40.6828401234, "-40.68284"),
    ]
    for valor, esperado in casos:
        obtido = formatar_xy(valor)
        checar(obtido == esperado, f"formatar_xy({valor!r}) = {obtido!r}, esperado {esperado!r}")

    saida = base / "saida_xyv"
    resultado = executar_completo(parametros(caminhos["geografico"], saida))

    bruto = (saida / "amostras_presencas.csv").read_text(encoding="utf-8")
    linhas = bruto.strip().splitlines()
    checar(linhas[0] == "x,y,Value", f"cabecalho e exatamente 'x,y,Value' (obtido {linhas[0]!r})")
    checar(len(linhas) == 61, "61 linhas: cabecalho mais 60 presencas")
    checar(";" not in bruto, "sem ponto e virgula no arquivo x,y,Value")
    checar(
        bruto.count(",") == 2 * 60 + 2,
        f"duas virgulas por linha, 122 no total (contadas {bruto.count(chr(44))})",
    )

    for linha in linhas[1:]:
        partes = linha.split(",")
        if len(partes) != 3 or partes[2] != "1":
            checar(False, f"linha fora do formato: {linha!r}")
            break
    else:
        checar(True, "todas as linhas tem 3 campos e Value = 1")

    # Sem zeros a direita, como no arquivo de referencia.
    checar(
        not any(linha.split(",")[0].rstrip("0") != linha.split(",")[0] for linha in linhas[1:]
                if "." in linha.split(",")[0]),
        "nenhuma coordenada termina em zero desnecessario",
    )

    # As ausencias recebem Value = 0.
    ausencias = (saida / "amostras_ausencias.csv").read_text(encoding="utf-8").strip().splitlines()
    checar(all(linha.endswith(",0") for linha in ausencias[1:]), "ausencias gravadas com Value = 0")

    # O arquivo unico junta as duas classes.
    todas = (saida / "amostras_todas.csv").read_text(encoding="utf-8").strip().splitlines()
    checar(len(todas) == 121, "arquivo unico com 120 pontos mais cabecalho")
    valores = [linha.rsplit(",", 1)[1] for linha in todas[1:]]
    checar(valores.count("1") == 60 and valores.count("0") == 60, "60 presencas e 60 ausencias")

    # As coordenadas do x,y,Value tem de bater com as do GeoJSON.
    geojson = json.loads((saida / "amostras_presencas.geojson").read_text(encoding="utf-8"))
    do_csv = [(float(l.split(",")[0]), float(l.split(",")[1])) for l in linhas[1:]]
    do_geojson = [tuple(f["geometry"]["coordinates"]) for f in geojson["features"]]
    iguais = sum(
        1 for (cx, cy), (gx, gy) in zip(do_csv, do_geojson)
        if abs(cx - gx) < 1e-6 and abs(cy - gy) < 1e-6
    )
    checar(iguais == 60, f"x,y,Value e GeoJSON concordam nos 60 pontos ({iguais}/60)")

    # pandas e R leem esse arquivo sem nenhuma opcao especial.
    with (saida / "amostras_presencas.csv").open(encoding="utf-8") as arquivo:
        lidas = list(csv.DictReader(arquivo))
    checar(
        set(lidas[0].keys()) == {"x", "y", "Value"},
        "leitura padrao devolve exatamente as colunas x, y e Value",
    )
    checar(-47.51 < float(lidas[0]["x"]) < -45.74, "x lido cai dentro da area de estudo")
    checar(-16.76 < float(lidas[0]["y"]) < -14.99, "y lido cai dentro da area de estudo")

    # O separador decimal da tela nao pode contaminar o x,y,Value.
    saida_v = base / "saida_xyv_virgula"
    executar_completo(parametros(caminhos["geografico"], saida_v, separador_decimal=","))
    texto = (saida_v / "amostras_presencas.csv").read_text(encoding="utf-8")
    checar(texto.splitlines()[0] == "x,y,Value", "cabecalho intacto no modo virgula")
    checar(
        all(len(linha.split(",")) == 3 for linha in texto.strip().splitlines()[1:]),
        "x,y,Value continua com 3 campos quando a tela pede virgula decimal",
    )


def teste_coordenadas_xyv(caminhos: dict[str, Path], base: Path) -> None:
    print("\n19. Escolha das coordenadas do x,y,Value")
    from amostragem_fogo.config import COORDENADAS_ORIGINAL

    saida = base / "saida_xyv_utm_original"
    executar_completo(
        parametros(caminhos["utm"], saida, coordenadas_xy=COORDENADAS_ORIGINAL)
    )
    linhas = (saida / "amostras_presencas.csv").read_text(encoding="utf-8").strip().splitlines()
    xs = [float(linha.split(",")[0]) for linha in linhas[1:]]
    checar(all(190000 < x < 225000 for x in xs), "x em metros do CRS original, nao em graus")

    saida = base / "saida_xyv_utm_wgs84"
    executar_completo(parametros(caminhos["utm"], saida))
    linhas = (saida / "amostras_presencas.csv").read_text(encoding="utf-8").strip().splitlines()
    xs = [float(linha.split(",")[0]) for linha in linhas[1:]]
    checar(all(-60 < x < -30 for x in xs), "x em grau decimal quando a opcao e WGS84")

    # Sem CRS nao ha WGS84: o arquivo nao pode sair com coordenada inventada.
    saida = base / "saida_xyv_sem_crs"
    executar_completo(
        parametros(caminhos["sem_crs"], saida, distancia_minima_m=0.0)
    )
    checar(
        not (saida / "amostras_presencas.csv").exists(),
        "x,y,Value nao e criado quando nao ha como obter WGS84",
    )

    saida = base / "saida_xyv_sem_crs_original"
    executar_completo(
        parametros(
            caminhos["sem_crs"], saida, distancia_minima_m=0.0,
            coordenadas_xy=COORDENADAS_ORIGINAL,
        )
    )
    checar(
        (saida / "amostras_presencas.csv").is_file(),
        "com coordenadas do CRS original o arquivo sai mesmo sem CRS definido",
    )


def teste_selecao_de_saidas(caminhos: dict[str, Path], base: Path) -> None:
    print("\n20. Selecao dos arquivos de saida")
    from amostragem_fogo.config import SAIDAS_DISPONIVEIS, SelecaoDeSaidas

    checar(len(SAIDAS_DISPONIVEIS) == 11, "onze saidas disponiveis")
    checar(bool(SelecaoDeSaidas()), "selecao padrao liga tudo")
    checar(not bool(SelecaoDeSaidas.nenhuma()), "selecao vazia e falsa")
    parcial = SelecaoDeSaidas.de_nomes(["presencas", "formato_xyv"])
    checar(
        parcial.presencas and parcial.formato_xyv and not parcial.relatorio,
        "de_nomes liga so o que foi pedido",
    )
    try:
        SelecaoDeSaidas.de_nomes(["nao_existe"])
        checar(False, "nome invalido deveria ser recusado")
    except ValueError as exc:
        checar("nao_existe" in str(exc), "nome invalido apontado na mensagem")

    try:
        parametros(caminhos["geografico"], base / "x", saidas=SelecaoDeSaidas.nenhuma())
        checar(False, "nenhuma saida marcada deveria ser recusado")
    except ValueError as exc:
        checar(
            "nenhum arquivo" in str(exc).lower(),
            f"recusa explica que nada foi marcado: {exc}",
        )

    # So o x,y,Value das classes separadas.
    saida = base / "saida_so_xyv"
    resultado = executar_completo(
        parametros(
            caminhos["geografico"], saida,
            saidas=SelecaoDeSaidas.de_nomes(["presencas", "ausencias", "formato_xyv"]),
        )
    )
    gravados = sorted(caminho.name for caminho in resultado.arquivos)
    checar(
        gravados == ["amostras_ausencias.csv", "amostras_presencas.csv"],
        f"apenas os dois arquivos x,y,Value foram gravados: {gravados}",
    )
    presentes = sorted(item.name for item in saida.iterdir())
    checar(presentes == gravados, f"nada mais sobrou na pasta: {presentes}")

    # Cada caixa e independente das outras.
    saida = base / "saida_so_detalhado"
    resultado = executar_completo(
        parametros(
            caminhos["geografico"], saida,
            saidas=SelecaoDeSaidas.de_nomes(["presencas", "ausencias", "combinado", "formato_detalhado"]),
        )
    )
    nomes = {caminho.name for caminho in resultado.arquivos}
    checar(
        "amostras_presencas_detalhado.csv" in nomes and "amostras_presencas.csv" not in nomes,
        "tabela detalhada sai sem depender da caixa do x,y,Value",
    )
    checar(
        not any(nome.endswith(".geojson") for nome in nomes),
        "GeoJSON nao sai quando a caixa dele esta desmarcada",
    )

    # Somente as tabelas da analise, sem nenhuma amostra.
    saida = base / "saida_so_analise"
    resultado = executar_completo(
        parametros(
            caminhos["geografico"], saida,
            saidas=SelecaoDeSaidas.de_nomes(["tabela_limiares", "distribuicao"]),
        )
    )
    nomes = sorted(caminho.name for caminho in resultado.arquivos)
    checar(
        nomes == ["distribuicao_frequencias.csv", "tabela_limiares_frequencia.csv"],
        f"apenas as tabelas da analise: {nomes}",
    )
    checar(
        not any(nome.startswith("amostras") for nome in nomes),
        "nenhum arquivo de amostra quando as caixas estao desmarcadas",
    )

    # O relatorio registra o que foi marcado.
    saida = base / "saida_relatorio"
    executar_completo(
        parametros(
            caminhos["geografico"], saida,
            saidas=SelecaoDeSaidas.de_nomes(["relatorio", "presencas", "formato_xyv"]),
        )
    )
    texto = (saida / "relatorio_amostragem.txt").read_text(encoding="utf-8")
    checar("ARQUIVOS SELECIONADOS" in texto, "relatorio traz a lista de arquivos selecionados")
    checar("[x] Presencas" in texto or "[x] Presen" in texto, "marca o que foi ligado")
    checar("[ ] GeoJSON" in texto or "[ ] Ausencias" in texto or "[ ] Aus" in texto,
           "marca o que foi desligado")
    checar("Value na presenca ...... 1" in texto, "relatorio registra o codigo da presenca")


def teste_codigos_value(caminhos: dict[str, Path], base: Path) -> None:
    print("\n21. Codigos da coluna Value")
    saida = base / "saida_codigos"
    executar_completo(
        parametros(caminhos["geografico"], saida, valor_presenca=2, valor_ausencia=9)
    )
    presencas = (saida / "amostras_presencas.csv").read_text(encoding="utf-8").strip().splitlines()
    ausencias = (saida / "amostras_ausencias.csv").read_text(encoding="utf-8").strip().splitlines()
    checar(all(linha.endswith(",2") for linha in presencas[1:]), "codigo 2 aplicado as presencas")
    checar(all(linha.endswith(",9") for linha in ausencias[1:]), "codigo 9 aplicado as ausencias")

    try:
        parametros(caminhos["geografico"], base / "x", valor_presenca=1, valor_ausencia=1)
        checar(False, "codigos iguais deveriam ser recusados")
    except ValueError:
        checar(True, "codigos iguais para as duas classes sao recusados")


def teste_linha_de_comando(caminhos: dict[str, Path], base: Path) -> None:
    print("\n22. Linha de comando")
    from amostragem_fogo.cli import construir_parser
    from amostragem_fogo.cli import main as cli_main

    checar(construir_parser() is not None, "parser da linha de comando e construido")
    checar(cli_main(["--listar-saidas"]) == 0, "--listar-saidas encerra sem erro")

    saida = base / "saida_cli"
    codigo = cli_main(
        [
            "--cli",
            "--raster", str(caminhos["geografico"]),
            "--saida", str(saida),
            "--presenca", "4",
            "--ausencia", "0", "3",
            "--n-presenca", "40",
            "--n-ausencia", "40",
            "--distancia", "1500",
            "--excluir", "-9999",
            "--saidas", "presencas", "ausencias", "formato_xyv", "parametros",
            "--valor-presenca", "1",
            "--valor-ausencia", "0",
        ]
    )
    checar(codigo == 0, "execucao pela linha de comando termina com codigo 0")
    presentes = sorted(item.name for item in saida.iterdir())
    checar(
        presentes == ["amostras_ausencias.csv", "amostras_presencas.csv", "parametros_amostragem.csv"],
        f"--saidas respeitado na linha de comando: {presentes}",
    )
    linhas = (saida / "amostras_presencas.csv").read_text(encoding="utf-8").strip().splitlines()
    checar(linhas[0] == "x,y,Value" and len(linhas) == 41, "arquivo x,y,Value com 40 presencas")

    checar(
        cli_main(
            ["--cli", "--raster", str(base / "inexistente.tif"), "--saida", str(base / "x"),
             "--presenca", "4", "--ausencia", "0", "3"]
        ) == 1,
        "raster inexistente devolve codigo de erro 1",
    )


def teste_barra_de_progresso(caminhos: dict[str, Path], base: Path) -> None:
    print("\n23. Barra de progresso visivel")
    import tkinter as tk

    from amostragem_fogo.pipeline import executar_analise

    try:
        from amostragem_fogo.gui import Aplicacao

        app = Aplicacao()
    except tk.TclError:
        print("  [pulado] sem ambiente grafico disponivel")
        return

    try:
        # A barra tem de ficar visivel em qualquer tamanho de janela, inclusive
        # nos menores. Antes desta correcao ela nem era mapeada pelo pack.
        for largura, altura in ((1380, 940), (1120, 780), (1366, 700)):
            app.geometry(f"{largura}x{altura}")
            app.update_idletasks()
            app.update()
            barra = app.barra_progresso
            base_da_barra = (barra.winfo_rooty() - app.winfo_rooty()) + barra.winfo_height()
            visivel = bool(barra.winfo_ismapped()) and base_da_barra <= app.winfo_height()
            checar(
                visivel,
                f"barra visivel em {largura}x{altura} "
                f"(base {base_da_barra}px, janela {app.winfo_height()}px)",
            )

        # O rotulo de status e o percentual acompanham a barra.
        app.var_status.set("mensagem de teste")
        app.var_percentual.set("42%")
        app.update_idletasks()
        checar(app.var_status.get() == "mensagem de teste", "rotulo de status acompanha a barra")

        # O progresso avanca de verdade durante a analise.
        app.var_raster.set(str(caminhos["geografico"]))
        app.var_excluir.set("-9999")
        valores: list[float] = []
        executar_analise(
            app._ler_parametros_analise(),
            progresso=lambda valor, _msg: valores.append(valor),
        )
        checar(bool(valores), "a analise emite eventos de progresso")
        checar(valores == sorted(valores), "o progresso so avanca, nunca retrocede")
        checar(abs(valores[-1] - 100.0) < 1e-9, f"a analise termina em 100% ({valores[-1]})")

        # Os eventos chegam na tela pela fila, como no uso real.
        app._enviar_progresso(37.0, "lendo o raster")
        app._processar_eventos()
        app.update_idletasks()
        checar(abs(app.var_progresso.get() - 37.0) < 1e-9, "a barra recebe o valor pela fila")
        checar(app.var_percentual.get() == "37%", "o percentual acompanha a barra")
        checar(app.var_status.get() == "lendo o raster", "a mensagem da etapa chega a tela")
    finally:
        app.destroy()


def teste_salvar_so_uma_classe(caminhos: dict[str, Path], base: Path) -> None:
    print("\n24. Salvar apenas presencas ou apenas ausencias")
    from amostragem_fogo.config import SelecaoDeSaidas

    saida = base / "saida_so_presencas"
    resultado = executar_completo(
        parametros(caminhos["geografico"], saida, saidas=SelecaoDeSaidas.somente_presencas())
    )
    presentes = sorted(item.name for item in saida.iterdir())
    checar(
        presentes == ["amostras_presencas.csv"],
        f"so o arquivo de presencas foi gravado: {presentes}",
    )
    linhas = (saida / "amostras_presencas.csv").read_text(encoding="utf-8").strip().splitlines()
    checar(linhas[0] == "x,y,Value", "formato x,y,Value preservado")
    checar(all(linha.endswith(",1") for linha in linhas[1:]), "so pontos de presenca no arquivo")
    checar(len(resultado.ausencia) == 60, "as ausencias continuam sendo sorteadas, so nao gravadas")

    saida = base / "saida_so_ausencias"
    executar_completo(
        parametros(caminhos["geografico"], saida, saidas=SelecaoDeSaidas.somente_ausencias())
    )
    presentes = sorted(item.name for item in saida.iterdir())
    checar(
        presentes == ["amostras_ausencias.csv"],
        f"so o arquivo de ausencias foi gravado: {presentes}",
    )
    linhas = (saida / "amostras_ausencias.csv").read_text(encoding="utf-8").strip().splitlines()
    checar(all(linha.endswith(",0") for linha in linhas[1:]), "so pontos de ausencia no arquivo")

    # Uma classe, varios formatos.
    saida = base / "saida_presenca_completa"
    executar_completo(
        parametros(
            caminhos["geografico"], saida,
            saidas=SelecaoDeSaidas.de_nomes(
                ["presencas", "formato_xyv", "formato_detalhado", "formato_geojson"]
            ),
        )
    )
    presentes = sorted(item.name for item in saida.iterdir())
    checar(
        presentes == [
            "amostras_presencas.csv",
            "amostras_presencas.geojson",
            "amostras_presencas_detalhado.csv",
            "amostras_presencas_detalhado.csvt",
            "amostras_presencas_detalhado.prj",
        ],
        f"presencas nos tres formatos, nada de ausencia: {presentes}",
    )

    # Classe sem formato e formato sem classe sao recusados com mensagem util.
    try:
        parametros(
            caminhos["geografico"], base / "x",
            saidas=SelecaoDeSaidas.de_nomes(["presencas"]),
        )
        checar(False, "classe sem formato deveria ser recusada")
    except ValueError as exc:
        checar("formato" in str(exc).lower(), f"recusa pede um formato: {exc}")

    try:
        parametros(
            caminhos["geografico"], base / "x",
            saidas=SelecaoDeSaidas.de_nomes(["formato_xyv"]),
        )
        checar(False, "formato sem classe deveria ser recusado")
    except ValueError as exc:
        checar("classe" in str(exc).lower(), f"recusa pede uma classe: {exc}")

    # As tabelas da analise nao dependem das classes.
    saida = base / "saida_presenca_e_limiares"
    executar_completo(
        parametros(
            caminhos["geografico"], saida,
            saidas=SelecaoDeSaidas.de_nomes(["presencas", "formato_xyv", "tabela_limiares"]),
        )
    )
    presentes = sorted(item.name for item in saida.iterdir())
    checar(
        presentes == ["amostras_presencas.csv", "tabela_limiares_frequencia.csv"],
        f"presencas mais a tabela de limiares: {presentes}",
    )


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="teste_amostragem_v5_"))
    print(f"Pasta de teste: {base}")
    try:
        caminhos = criar_rasters(base)
        teste_conversoes()
        teste_faixas()
        teste_centro_do_pixel()
        teste_coordenadas_contra_rasterio(caminhos)
        teste_distancia_ecef(caminhos)
        teste_grade_espacial()
        teste_execucao(caminhos, base)
        teste_saida_utm(caminhos, base)
        teste_arquivos_de_saida(base)
        teste_separador_virgula(caminhos, base)
        teste_tabela_limiares(caminhos, base)
        teste_faixas_em_geotiff_striped(caminhos, base)
        teste_sem_crs(caminhos, base)
        teste_casos_limite(caminhos, base)
        teste_reprodutibilidade(caminhos, base)
        teste_cancelamento(caminhos, base)
        teste_interface(caminhos, base)
        teste_formato_xyv(caminhos, base)
        teste_coordenadas_xyv(caminhos, base)
        teste_selecao_de_saidas(caminhos, base)
        teste_codigos_value(caminhos, base)
        teste_linha_de_comando(caminhos, base)
        teste_barra_de_progresso(caminhos, base)
        teste_salvar_so_uma_classe(caminhos, base)
    except Exception:
        traceback.print_exc()
        _falhas.append("excecao nao tratada durante os testes")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\n" + "=" * 70)
    if _falhas:
        print(f"{_ok} verificacoes passaram, {len(_falhas)} FALHARAM:")
        for item in _falhas:
            print(f"  - {item}")
        return 1
    print(f"Todas as {_ok} verificacoes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
