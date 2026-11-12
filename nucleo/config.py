"""
config.py
=========

Constantes e parametros padrao do GeoRaster Tools (traducao Python do
app Shiny original ``app_v4.R``).

Contem as regras default de reclassificacao (nomenclatura MapBiomas) e
os mapeamentos de tipos de dado (dtype) usados nas abas de
Reclassificacao e Reamostragem.
"""

from __future__ import annotations

APP_NAME = "GeoRaster Tools"
APP_VERSION = "3.0.0"

# Nucleo de amostragem embutido na ultima aba (aplicacao V5).
# V3: o rotulo perdeu o "de Fogo" -- a aba sorteia amostras a partir de
# qualquer raster de contagem, nao so de frequencia de fogo.
SAMPLING_TAB_LABEL = "Amostragem"

# Aba de merge (mosaico de blocos), nova na V3.
MERGE_TAB_LABEL = "Merge de Rasters"

# -----------------------------------------------------------------------
# Regras default de reclassificacao (equivalente a REGRAS_DEFAULT em R)
# Cada tupla: (Val_Min, Val_Max, Novo_Val)
# -----------------------------------------------------------------------

REGRAS_DEFAULT = [
    (1, 1, 1), (3, 3, 1), (4, 4, 1), (5, 5, 1),
    (6, 6, 1), (49, 49, 1), (10, 10, 1), (11, 11, 1),
    (12, 12, 1), (32, 32, 1), (29, 29, 1), (50, 50, 1),
    (14, 14, 14), (15, 15, 14), (18, 18, 14), (19, 19, 14),
    (39, 39, 14), (20, 20, 14), (40, 40, 14), (62, 62, 14),
    (41, 41, 14), (36, 36, 14), (46, 46, 14), (47, 47, 14),
    (35, 35, 14), (48, 48, 14), (9, 9, 14), (21, 21, 14),
    (22, 22, 22), (23, 23, 22), (75, 75, 22), (25, 25, 22),
    (24, 24, 24),
    (30, 30, 22),
    (26, 26, 26), (33, 33, 26), (31, 31, 26),
]

# Colunas padrao usadas nas tabelas de regras (equivalente a mat_to_df)
RULE_COLUMNS = ("Val_Min", "Val_Max", "Novo_Val")

# Tipos de dado aceitos na aba de Reclassificacao
RECLASSIFY_DTYPES = ("INT1U", "INT2U", "INT2S", "INT4S", "FLT4S", "FLT8S", "BYTE")

# Tipos de dado aceitos na aba de Reamostragem
RESAMPLE_DTYPES = ("FLT4S", "FLT8S", "INT2S", "INT2U", "INT4S", "INT4U", "BYTE")

# Mapeamento dtype "estilo terra/GDAL" -> dtype numpy
DTYPE_MAP = {
    "INT1U": "uint8",
    "BYTE": "uint8",
    "INT2U": "uint16",
    "INT2S": "int16",
    "INT4S": "int32",
    "INT4U": "uint32",
    "FLT4S": "float32",
    "FLT8S": "float64",
}

# Quantos BITS cada codigo ocupa. O nome vem do terra/R, onde o numero e o
# tamanho em BYTES (INT2U = 2 bytes = 16 bits), o que confunde na hora de
# escolher -- a interface mostra "INT2U - 16 bits - uint16" para nao restar
# duvida sobre o que esta sendo gravado.
DTYPE_BITS = {
    "INT1U": 8,
    "BYTE": 8,
    "INT2U": 16,
    "INT2S": 16,
    "INT4S": 32,
    "INT4U": 32,
    "FLT4S": 32,
    "FLT8S": 64,
}


def rotulo_de_dtype(codigo: str) -> str:
    """'INT2U' -> 'INT2U - 16 bits - uint16'."""
    codigo = (codigo or "").strip().upper()
    if codigo not in DTYPE_MAP:
        return codigo
    return f"{codigo} - {DTYPE_BITS[codigo]} bits - {DTYPE_MAP[codigo]}"


def rotulos_de_dtype(codigos) -> list:
    """A lista pronta para um Combobox, na ordem recebida."""
    return [rotulo_de_dtype(c) for c in codigos]


def dtype_do_rotulo(rotulo: str) -> str:
    """Caminho inverso: devolve so o codigo. Aceita o rotulo inteiro ou o
    codigo puro, para que uma edicao digitada na mao ('FLT4S') continue
    valendo na tabela da aba Reamostrar."""
    texto = (rotulo or "").strip()
    if not texto:
        return ""
    return texto.split("-")[0].strip().upper()


# Padroes de nomenclatura para a aba "Separar Bandas"
BAND_NAMING_PATTERNS = {
    "bioclim": "Bioclim Futuro (bioc_Modelo_sspXXX_AAAA-AAAA)",
    "mat": "Prefixo Personalizado (Modelo_sspXXX_AAAA-AAAA)",
    "generico": "Generico (NomeArquivo_BandN)",
}

# Metodos de reamostragem
RESAMPLE_METHODS = ("near", "bilinear")

# Opcoes de snap no crop (reamostragem)
SNAP_OPTIONS = ("out", "in", "near")
