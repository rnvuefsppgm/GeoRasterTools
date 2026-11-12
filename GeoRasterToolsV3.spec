# -*- mode: python ; coding: utf-8 -*-
"""
GeoRasterToolsV3.spec
=====================

Especificacao do PyInstaller para o GeoRaster Tools V3 (Windows).

Uso:

    pyinstaller GeoRasterToolsV3.spec --noconfirm

Alem do que a V1 ja tratava (dados GDAL do rasterio), esta versao empacota:

* ``pyproj`` e a base PROJ, exigidos pela aba 6 para a distancia geocentrica
  (EPSG:4978) e para a conversao das coordenadas em WGS84. Sem os arquivos de
  ``proj_data`` o executavel abre mas falha ao transformar coordenadas;
* o pacote local ``amostragem_fogo`` e os modulos ``tab_amostragem``,
  ``tab_merge`` e ``merge_ops``, que sao importados de forma tardia dentro de
  ``gui.py`` e por isso nao aparecem na analise estatica do PyInstaller.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ---------------------------------------------------------------------------
# Dados de GDAL (rasterio) e PROJ (pyproj) exigidos em tempo de execucao
# ---------------------------------------------------------------------------

datas = []
datas += collect_data_files("rasterio", include_py_files=False)
datas += collect_data_files("pyproj", include_py_files=False)

try:
    import rasterio

    for subpasta in ("gdal_data", "proj_data"):
        caminho = os.path.join(os.path.dirname(rasterio.__file__), subpasta)
        if os.path.isdir(caminho):
            datas.append((caminho, f"rasterio/{subpasta}"))
except Exception:
    pass

try:
    import pyproj

    proj_dir = pyproj.datadir.get_data_dir()
    if proj_dir and os.path.isdir(proj_dir):
        datas.append((proj_dir, "pyproj/proj_dir/share/proj"))
except Exception:
    pass

hidden_imports = []
hidden_imports += collect_submodules("rasterio")
hidden_imports += collect_submodules("pyproj")
hidden_imports += collect_submodules("amostragem_fogo")
hidden_imports += [
    "rasterio.sample",
    "rasterio._io",
    "rasterio.vrt",
    "rasterio._features",
    "shapefile",
    "tab_amostragem",
    "tab_merge",
    "merge_ops",
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook_geo.py"],
    excludes=[
        "tests", "pytest", "_pytest", "py",
        # Nao entram na aplicacao e engordam o pacote em centenas de MB.
        "matplotlib", "scipy", "pandas", "IPython", "notebook",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
        "setuptools", "pip", "pkg_resources",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeoRasterToolsV3",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX desligado de proposito: ele nao esta instalado nesta maquina (seria
    # ignorado em silencio) e executavel comprimido com UPX e um gatilho
    # classico de falso positivo de antivirus.
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GeoRasterToolsV3",
)
