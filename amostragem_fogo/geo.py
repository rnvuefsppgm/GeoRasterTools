"""Coordenadas, CRS e distancia metrica.

Correcoes em relacao a V3
-------------------------

1. Distancia em metros. A V3 projetava os candidatos numa azimutal
   equidistante centrada no raster. Essa projecao so preserva distancias
   a partir do centro; entre dois pontos afastados do centro o erro chega a
   1,3 % num raster do tamanho do Brasil. Aqui a distancia e calculada sobre
   coordenadas geocentricas (ECEF, EPSG:4978). A corda 3D difere da distancia
   geodesica em menos de 3e-6 % ate 50 km, nao depende de centro nenhum e
   funciona no antimeridiano e nos polos.

2. CRS projetado. A V3 tratava a distancia da grade projetada como distancia
   no terreno. Em UTM isso erra ate 0,5 % na borda do fuso. Aqui o caminho
   padrao tambem e o ECEF; a grade projetada so e usada quando o CRS nao tem
   datum conhecido.

3. Coordenadas de saida. O centro do pixel e calculado uma unica vez e as
   mesmas coordenadas alimentam o filtro de distancia, o CSV e o GeoJSON, sem
   caminhos paralelos que possam divergir.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from rasterio.transform import Affine

WGS84 = CRS.from_epsg(4326)
ECEF = CRS.from_epsg(4978)  # geocentrico cartesiano, em metros


def centros_de_pixel(
    transform: Affine, linhas: np.ndarray, colunas: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Coordenadas do centro do pixel no CRS do raster.

    linha/coluna sao indices base zero; o centro do pixel fica em +0,5.
    """
    col = np.asarray(colunas, dtype=np.float64) + 0.5
    lin = np.asarray(linhas, dtype=np.float64) + 0.5
    x = transform.a * col + transform.b * lin + transform.c
    y = transform.d * col + transform.e * lin + transform.f
    return x, y


def _fator_para_metros(crs: CRS) -> float | None:
    """Fator de conversao do eixo linear do CRS para metros."""
    if not crs.axis_info:
        return None
    for eixo in crs.axis_info:
        unidade = (eixo.unit_name or "").lower()
        if unidade in ("degree", "radian", "grad", "unknown"):
            return None
        fator = eixo.unit_conversion_factor
        if fator:
            return float(fator)
    return None


@dataclass(frozen=True)
class ModoMetrico:
    """Como a distancia minima esta sendo medida, para registro no relatorio."""

    nome: str
    descricao: str
    exato: bool


MODO_ECEF = ModoMetrico(
    "geocentrico",
    "distancia real no elipsoide via coordenadas geocentricas (EPSG:4978)",
    True,
)
MODO_GRADE = ModoMetrico(
    "grade_projetada",
    "distancia na grade do CRS projetado, convertida para metros pela unidade do eixo",
    False,
)
MODO_INDISPONIVEL = ModoMetrico(
    "indisponivel", "o raster nao permite medir distancia em metros", False
)


class SistemaDeCoordenadas:
    """Converte pixels em coordenadas de saida e em coordenadas metricas."""

    def __init__(self, transform: Affine, crs: CRS | None) -> None:
        self.transform = transform
        self.crs = crs
        self.crs_texto = crs.to_string() if crs is not None else "Sem CRS definido"

        self._para_wgs84: Transformer | None = None
        self._para_ecef: Transformer | None = None
        self._fator_metros: float | None = None
        self.modo_metrico: ModoMetrico = MODO_INDISPONIVEL

        if crs is not None:
            try:
                self._para_wgs84 = Transformer.from_crs(crs, WGS84, always_xy=True)
                self._para_ecef = Transformer.from_crs(crs, ECEF, always_xy=True)
                self.modo_metrico = MODO_ECEF
            except (ProjError, RuntimeError):
                self._para_wgs84 = None
                self._para_ecef = None
            if self._para_ecef is None and crs.is_projected:
                self._fator_metros = _fator_para_metros(crs)
                if self._fator_metros is not None:
                    self.modo_metrico = MODO_GRADE

    # -- propriedades de conveniencia ---------------------------------------

    @property
    def tem_wgs84(self) -> bool:
        return self._para_wgs84 is not None

    @property
    def suporta_distancia(self) -> bool:
        return self.modo_metrico is not MODO_INDISPONIVEL

    @property
    def unidade_do_crs(self) -> str:
        if self.crs is None:
            return "desconhecida"
        if self.crs.is_geographic:
            return "graus"
        if self.crs.axis_info:
            return self.crs.axis_info[0].unit_name or "desconhecida"
        return "desconhecida"

    # -- conversoes ---------------------------------------------------------

    def xy(self, linhas: np.ndarray, colunas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return centros_de_pixel(self.transform, linhas, colunas)

    def para_wgs84(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Longitude e latitude em graus decimais; NaN quando indisponivel."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if self._para_wgs84 is None or x.size == 0:
            vazio = np.full(x.shape, np.nan, dtype=np.float64)
            return vazio, vazio.copy()
        lon, lat = self._para_wgs84.transform(x, y)
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        invalidos = ~(np.isfinite(lon) & np.isfinite(lat))
        if invalidos.any():
            lon = np.where(invalidos, np.nan, lon)
            lat = np.where(invalidos, np.nan, lat)
        return lon, lat

    def para_metros(
        self, x: np.ndarray, y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Coordenadas cartesianas em metros usadas no filtro de distancia."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if self._para_ecef is not None:
            zeros = np.zeros(x.shape, dtype=np.float64)
            mx, my, mz = self._para_ecef.transform(x, y, zeros)
            return (
                np.asarray(mx, dtype=np.float64),
                np.asarray(my, dtype=np.float64),
                np.asarray(mz, dtype=np.float64),
            )
        if self._fator_metros is not None:
            fator = self._fator_metros
            return x * fator, y * fator, np.zeros(x.shape, dtype=np.float64)
        raise ValueError(
            "Nao e possivel medir distancia em metros neste raster porque o CRS "
            f"e {self.crs_texto!r}. Defina um CRS valido no arquivo ou use "
            "distancia minima igual a zero."
        )


def abrir_sistema(src) -> SistemaDeCoordenadas:
    """Constroi o sistema de coordenadas a partir de um dataset aberto."""
    crs = None
    if src.crs is not None:
        try:
            crs = CRS.from_user_input(src.crs)
        except (ProjError, RuntimeError, ValueError):
            crs = None
    return SistemaDeCoordenadas(src.transform, crs)


class GradeEspacial:
    """Indice 3D em celulas de lado ``distancia`` para rejeicao por vizinhanca.

    Qualquer ponto a menos de ``distancia`` esta na propria celula ou numa das
    26 vizinhas, entao 27 celulas bastam para decidir.
    """

    __slots__ = ("distancia", "_limite2", "_celulas", "_total")

    def __init__(self, distancia: float) -> None:
        self.distancia = float(distancia)
        self._limite2 = self.distancia * self.distancia
        self._celulas: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
        self._total = 0

    def __len__(self) -> int:
        return self._total

    def _chave(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        d = self.distancia
        return (int(x // d), int(y // d), int(z // d))

    def aceita(self, x: float, y: float, z: float) -> bool:
        if self.distancia <= 0:
            return True
        cx, cy, cz = self._chave(x, y, z)
        obter = self._celulas.get
        limite2 = self._limite2
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    vizinhos = obter((cx + dx, cy + dy, cz + dz))
                    if not vizinhos:
                        continue
                    for ox, oy, oz in vizinhos:
                        if (x - ox) ** 2 + (y - oy) ** 2 + (z - oz) ** 2 < limite2:
                            return False
        return True

    def inserir(self, x: float, y: float, z: float) -> None:
        self._total += 1
        if self.distancia <= 0:
            return
        self._celulas.setdefault(self._chave(x, y, z), []).append((x, y, z))
