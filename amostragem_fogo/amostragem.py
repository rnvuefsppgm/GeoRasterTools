"""Reservatorio aleatorio de candidatos e selecao com distancia minima.

Estrategia
----------

1. Uma unica varredura do raster alimenta dois reservatorios independentes
   (presenca e ausencia). O reservatorio guarda uma amostra aleatoria simples
   sem reposicao de tamanho fixo, sorteada entre todos os pixels elegiveis, sem
   precisar da lista completa na memoria.
2. Os candidatos sao embaralhados e aceitos um a um enquanto respeitarem a
   distancia minima, medida em metros reais (ver ``geo``).
3. A presenca e resolvida antes da ausencia, porque costuma ser a classe rara.
   Com ``distancia_entre_classes`` ligado, as ausencias tambem sao afastadas
   das presencas ja escolhidas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import rasterio

from .analise import OperacaoCancelada
from .blocos import ler_janela, montar_plano
from .config import ParametrosAmostragem, Progresso
from .geo import GradeEspacial, SistemaDeCoordenadas, abrir_sistema
from .sdm import (EstatisticasFiltro, FiltroSDM, MascaraPreditores,
                  sondar_referencia)


@dataclass
class Reservatorio:
    """Amostra aleatoria de tamanho fixo sobre um fluxo de pixels elegiveis."""

    capacidade: int
    chaves: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    linhas: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    colunas: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    valores: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    elegiveis: int = 0

    def __len__(self) -> int:
        return int(self.linhas.size)

    def adicionar(
        self,
        linhas: np.ndarray,
        colunas: np.ndarray,
        valores: np.ndarray,
        rng: np.random.Generator,
    ) -> None:
        tamanho = int(linhas.size)
        if tamanho == 0:
            return
        self.elegiveis += tamanho
        if self.capacidade <= 0:
            return

        chaves = rng.random(tamanho)
        if tamanho > self.capacidade:
            escolhidos = np.argpartition(chaves, self.capacidade - 1)[: self.capacidade]
            chaves = chaves[escolhidos]
            linhas = linhas[escolhidos]
            colunas = colunas[escolhidos]
            valores = valores[escolhidos]

        self.chaves = np.concatenate((self.chaves, chaves))
        self.linhas = np.concatenate((self.linhas, linhas.astype(np.int64, copy=False)))
        self.colunas = np.concatenate((self.colunas, colunas.astype(np.int64, copy=False)))
        self.valores = np.concatenate((self.valores, valores.astype(np.float64, copy=False)))

        if self.chaves.size > self.capacidade:
            escolhidos = np.argpartition(self.chaves, self.capacidade - 1)[: self.capacidade]
            self.chaves = self.chaves[escolhidos]
            self.linhas = self.linhas[escolhidos]
            self.colunas = self.colunas[escolhidos]
            self.valores = self.valores[escolhidos]


@dataclass
class Amostra:
    """Resultado de uma classe: arrays paralelos, todos do mesmo tamanho."""

    classe: str
    linhas: np.ndarray
    colunas: np.ndarray
    frequencias: np.ndarray
    x: np.ndarray
    y: np.ndarray
    longitude: np.ndarray
    latitude: np.ndarray
    solicitado: int
    elegiveis: int
    candidatos: int
    estatisticas: EstatisticasFiltro = field(default_factory=EstatisticasFiltro)

    def __len__(self) -> int:
        return int(self.linhas.size)

    @property
    def completa(self) -> bool:
        return len(self) >= self.solicitado

    @property
    def fracao_nominal(self) -> float | None:
        if self.elegiveis <= 0 or len(self) == 0:
            return None
        return len(self) / self.elegiveis

    @property
    def peso_nominal(self) -> float | None:
        fracao = self.fracao_nominal
        return None if fracao is None else 1.0 / fracao


def coletar_candidatos(
    parametros: ParametrosAmostragem,
    progresso: Progresso,
    cancelado,
) -> tuple[Reservatorio, Reservatorio, SistemaDeCoordenadas, dict[str, object]]:
    """Varre o raster uma unica vez e preenche os dois reservatorios."""
    analise = parametros.analise
    rng = np.random.default_rng(parametros.semente)
    presenca = Reservatorio(parametros.capacidade(parametros.n_presenca))
    ausencia = Reservatorio(parametros.capacidade(parametros.n_ausencia))

    with rasterio.open(analise.raster) as src:
        if analise.banda > src.count:
            raise ValueError(
                f"O raster possui {src.count} banda(s); a banda {analise.banda} nao existe."
            )
        sistema = abrir_sistema(src)
        if parametros.distancia_minima_m > 0 and not sistema.suporta_distancia:
            raise ValueError(
                "A distancia minima so pode ser aplicada em rasters com CRS valido. "
                f"O CRS lido foi {sistema.crs_texto!r}. Defina o CRS do arquivo ou "
                "use distancia minima igual a zero."
            )

        plano = montar_plano(src, analise.banda)
        total = len(plano)
        for indice, janela in plano:
            if cancelado():
                raise OperacaoCancelada("Amostragem interrompida pelo usuario.")
            dados, validos = ler_janela(src, analise.banda, janela, analise.valores_excluidos)
            linha_off = int(janela.row_off)
            coluna_off = int(janela.col_off)

            for faixa, reservatorio in (
                (parametros.presenca, presenca),
                (parametros.ausencia, ausencia),
            ):
                if reservatorio.capacidade <= 0:
                    continue
                mascara = validos & (dados >= faixa.minimo) & (dados <= faixa.maximo)
                if not mascara.any():
                    continue
                linhas_locais, colunas_locais = np.nonzero(mascara)
                reservatorio.adicionar(
                    linhas_locais + linha_off,
                    colunas_locais + coluna_off,
                    dados[linhas_locais, colunas_locais],
                    rng,
                )

            progresso(
                75.0 * (indice + 1) / max(total, 1),
                f"Procurando candidatos: janela {indice + 1} de {total}",
                forcar=(indice + 1 == total),
            )

        metadados: dict[str, object] = {
            "crs_texto": sistema.crs_texto,
            "crs_objeto": sistema.crs,
            "unidade_crs": sistema.unidade_do_crs,
            "modo_metrico": sistema.modo_metrico,
            "largura": src.width,
            "altura": src.height,
            "resolucao": (abs(src.transform.a), abs(src.transform.e)),
            "janelas": total,
        }
    return presenca, ausencia, sistema, metadados


#: Candidatos avaliados por vez. O lote existe por causa da mascara de
#: preditores: amostrar o raster ponto a ponto seria lento demais.
LOTE_DE_CANDIDATOS = 4096


def selecionar(
    classe: str,
    reservatorio: Reservatorio,
    solicitado: int,
    sistema: SistemaDeCoordenadas,
    distancia: float,
    rng: np.random.Generator,
    grade: GradeEspacial,
    cancelado,
    mascara: "MascaraPreditores | None" = None,
    filtro: "FiltroSDM | None" = None,
    celulas_usadas: set[int] | None = None,
) -> Amostra:
    """Aceita candidatos em ordem aleatoria enquanto passarem por todos os filtros.

    Alem da distancia minima, quando ``mascara`` e informada o candidato
    tambem precisa cair dentro dos preditores, fora do NoData deles e numa
    celula ainda nao usada. Aplicar isso **durante** o sorteio, e nao depois,
    e o que faz a quantidade pedida ser de fato atingida.
    """
    vazio_i = np.empty(0, dtype=np.int64)
    vazio_f = np.empty(0, dtype=np.float64)
    estatisticas = EstatisticasFiltro()

    if solicitado <= 0 or len(reservatorio) == 0:
        return Amostra(
            classe=classe,
            linhas=vazio_i,
            colunas=vazio_i.copy(),
            frequencias=vazio_f,
            x=vazio_f.copy(),
            y=vazio_f.copy(),
            longitude=vazio_f.copy(),
            latitude=vazio_f.copy(),
            solicitado=solicitado,
            elegiveis=reservatorio.elegiveis,
            candidatos=len(reservatorio),
            estatisticas=estatisticas,
        )

    x, y = sistema.xy(reservatorio.linhas, reservatorio.colunas)
    usa_distancia = distancia > 0
    usa_mascara = mascara is not None and filtro is not None and filtro.ativo

    if not usa_distancia and not usa_mascara:
        quantidade = min(solicitado, len(reservatorio))
        indices = rng.permutation(len(reservatorio))[:quantidade].astype(np.int64)
        estatisticas.avaliados = int(indices.size)
        estatisticas.aceitos = int(indices.size)
    else:
        if celulas_usadas is None:
            celulas_usadas = set()
        mx = my = mz = None
        if usa_distancia:
            mx, my, mz = sistema.para_metros(x, y)

        ordem = rng.permutation(len(reservatorio))
        escolhidos: list[int] = []
        for inicio in range(0, ordem.size, LOTE_DE_CANDIDATOS):
            if cancelado():
                raise OperacaoCancelada("Amostragem interrompida pelo usuario.")
            bloco = ordem[inicio : inicio + LOTE_DE_CANDIDATOS]

            if usa_mascara:
                dentro, valido, celula = mascara.avaliar(x[bloco], y[bloco])
            else:
                dentro = valido = celula = None

            for posicao, indice in enumerate(bloco):
                estatisticas.avaliados += 1
                chave = -1

                if usa_mascara:
                    if not dentro[posicao]:
                        estatisticas.fora_da_extensao += 1
                        continue
                    if filtro.exigir_preditor_valido and not valido[posicao]:
                        estatisticas.nodata_no_preditor += 1
                        continue
                    chave = int(celula[posicao])
                    if filtro.uma_amostra_por_celula and chave in celulas_usadas:
                        estatisticas.celula_repetida += 1
                        continue

                if usa_distancia:
                    px = float(mx[indice])
                    py = float(my[indice])
                    pz = float(mz[indice])
                    if not grade.aceita(px, py, pz):
                        estatisticas.distancia_minima += 1
                        continue
                    grade.inserir(px, py, pz)

                if usa_mascara and filtro.uma_amostra_por_celula and chave >= 0:
                    celulas_usadas.add(chave)

                escolhidos.append(int(indice))
                estatisticas.aceitos += 1
                if len(escolhidos) >= solicitado:
                    break

            if len(escolhidos) >= solicitado:
                break

        indices = np.asarray(escolhidos, dtype=np.int64)

    lon, lat = sistema.para_wgs84(x[indices], y[indices])
    return Amostra(
        classe=classe,
        linhas=reservatorio.linhas[indices],
        colunas=reservatorio.colunas[indices],
        frequencias=reservatorio.valores[indices],
        x=x[indices],
        y=y[indices],
        longitude=lon,
        latitude=lat,
        solicitado=solicitado,
        elegiveis=reservatorio.elegiveis,
        candidatos=len(reservatorio),
        estatisticas=estatisticas,
    )


def amostrar(
    parametros: ParametrosAmostragem,
    progresso: Progresso,
    cancelado,
) -> tuple[Amostra, Amostra, dict[str, object]]:
    presenca_res, ausencia_res, sistema, metadados = coletar_candidatos(
        parametros, progresso, cancelado
    )
    if cancelado():
        raise OperacaoCancelada("Amostragem interrompida pelo usuario.")

    rng = np.random.default_rng(parametros.semente + 1)
    distancia = parametros.distancia_minima_m
    filtro = parametros.filtro_sdm

    mascara: MascaraPreditores | None = None
    if filtro.ativo:
        progresso(77, "Conferindo o raster de referencia", forcar=True)
        # Sondagem antes do sorteio: um raster de referencia inadequado tem de
        # ser apontado agora, e nao depois de devolver 0 amostras em silencio.
        amostra_x, amostra_y = sistema.xy(presenca_res.linhas, presenca_res.colunas)
        sondagem = sondar_referencia(filtro, amostra_x, amostra_y, sistema.crs)
        metadados["sondagem"] = sondagem
        metadados["avisos_referencia"] = sondagem.diagnostico(parametros.n_presenca)

        # Com referencia inviavel, varrer os 200 mil candidatos leva minutos
        # para terminar em zero. Melhor parar agora, com a explicacao.
        if sondagem.avaliados and sondagem.validos == 0:
            raise ValueError(
                "O raster de referencia nao aproveita nenhum candidato.\n\n"
                + "\n".join(sondagem.diagnostico(parametros.n_presenca))
                + f"\n\nReferencia: {filtro.referencia}"
                + f"\nCRS do fogo: {sistema.crs_texto}"
                + f"\nCRS da referencia: {sondagem.crs_referencia}"
                + "\n\nUse o botao 'Conferir referencia' para testar outro raster, "
                "ou desmarque as opcoes da secao 5 para amostrar sem filtro."
            )

        progresso(78, "Abrindo o raster de referencia", forcar=True)
        mascara = MascaraPreditores(filtro.preditores, sistema.crs)
        metadados["preditores"] = mascara.descricao

    # Uma celula de preditor nao pode ser presenca e background ao mesmo tempo
    # quando o usuario pediu separacao entre as classes.
    celulas = set()
    try:
        etapa = "Filtrando candidatos" if filtro.ativo else "Aplicando a distancia minima"
        progresso(80, f"{etapa} nas presencas", forcar=True)
        grade = GradeEspacial(distancia)
        presenca = selecionar(
            "presenca", presenca_res, parametros.n_presenca, sistema, distancia, rng,
            grade, cancelado, mascara, filtro, celulas,
        )

        progresso(92, f"{etapa} nas ausencias", forcar=True)
        grade_ausencia = grade if parametros.distancia_entre_classes else GradeEspacial(distancia)
        celulas_ausencia = celulas if parametros.distancia_entre_classes else set()
        ausencia = selecionar(
            "ausencia", ausencia_res, parametros.n_ausencia, sistema, distancia, rng,
            grade_ausencia, cancelado, mascara, filtro, celulas_ausencia,
        )
    finally:
        if mascara is not None:
            mascara.fechar()

    metadados.update(
        {
            "presenca_elegiveis": presenca.elegiveis,
            "ausencia_elegiveis": ausencia.elegiveis,
            "presenca_candidatos": presenca.candidatos,
            "ausencia_candidatos": ausencia.candidatos,
            "presenca_selecionadas": len(presenca),
            "ausencia_selecionadas": len(ausencia),
            "filtro_sdm": filtro,
            "estatisticas_filtro": {
                "Presenca": presenca.estatisticas,
                "Ausencia": ausencia.estatisticas,
            },
        }
    )
    progresso(100, "Amostras selecionadas", forcar=True)
    return presenca, ausencia, metadados


def verificar_distancia_minima(amostras: list[Amostra], sistema: SistemaDeCoordenadas) -> float:
    """Menor distancia em metros entre os pontos informados.

    Usado nos testes e no relatorio para conferir o filtro espacial.
    """
    xs = np.concatenate([a.x for a in amostras]) if amostras else np.empty(0)
    ys = np.concatenate([a.y for a in amostras]) if amostras else np.empty(0)
    if xs.size < 2:
        return float("inf")
    mx, my, mz = sistema.para_metros(xs, ys)
    pontos = np.column_stack((mx, my, mz))
    menor = float("inf")
    for inicio in range(0, len(pontos), 512):
        bloco = pontos[inicio : inicio + 512]
        difs = bloco[:, None, :] - pontos[None, :, :]
        distancias = np.sqrt((difs**2).sum(axis=2))
        indices = np.arange(len(bloco)) + inicio
        distancias[np.arange(len(bloco)), indices] = np.inf
        menor = min(menor, float(distancias.min()))
    return menor
