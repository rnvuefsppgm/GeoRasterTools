@echo off
REM ============================================================
REM build_windows.bat
REM Cria o ambiente virtual, instala dependencias e gera o
REM executavel GeoRasterToolsV3.exe usando PyInstaller (Windows
REM 10/11, 64 bits). Nao depende de Conda nem de internet apos a
REM instalacao inicial.
REM ============================================================

setlocal

echo ============================================================
echo  GeoRaster Tools V3
echo  Build para Windows
echo ============================================================

echo.
echo [1/5] Criando ambiente virtual em .\venv com Python 3.11 ...

if exist venv (
    echo Removendo ambiente virtual existente...
    rmdir /s /q venv
)

REM Tentamos primeiro o Python Launcher pedindo explicitamente a versao
REM 3.11, pois em maquinas com varias versoes instaladas (3.12, 3.13, 3.14...)
REM o comando "python" isolado pode apontar para uma versao mais nova sem
REM wheels pre-compiladas para numpy/rasterio, forcando compilacao do zero.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 -m venv venv 2>nul
)

if not exist venv\Scripts\python.exe (
    echo [AVISO] Nao foi possivel criar o venv com "py -3.11". Tentando com
    echo         o Python padrao do PATH ^(comando "python"^)...
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERRO] Nenhum Python encontrado ^(nem "py -3.11" nem "python"^).
        echo        Instale o Python 3.11 ^(64 bits^) em
        echo        https://www.python.org/downloads/ marcando "Add python.exe to PATH"
        echo        e marcando tambem a opcao de instalar o Tcl/Tk ^(tkinter^),
        echo        que vem habilitada por padrao no instalador oficial.
        exit /b 1
    )
    python -m venv venv
)

if not exist venv\Scripts\python.exe (
    echo [ERRO] Falha ao criar o ambiente virtual.
    exit /b 1
)

echo.
echo Verificando a versao do Python usada no venv...
venv\Scripts\python.exe --version
venv\Scripts\python.exe -c "import sys; exit(0 if sys.version_info[:2]==(3,11) else 1)"
if errorlevel 1 (
    echo.
    echo [AVISO] O venv NAO foi criado com Python 3.11. Isso pode causar
    echo         falhas ao instalar numpy/rasterio ^(compilacao do zero em
    echo         vez de usar wheels prontas^). Instale o Python 3.11 ^(64
    echo         bits^) de https://www.python.org/downloads/, apague a
    echo         pasta venv e rode este script novamente.
    echo         Continuando mesmo assim...
)

echo.
echo Verificando se o Tkinter esta disponivel no Python escolhido...
venv\Scripts\python.exe -c "import tkinter" 2>nul
if errorlevel 1 (
    echo [ERRO] O Python usado nao possui o modulo tkinter instalado.
    echo        Reinstale o Python marcando a opcao "tcl/tk and IDLE"
    echo        no instalador oficial de https://www.python.org/downloads/
    exit /b 1
)

echo.
echo [2/5] Ativando ambiente virtual...
call venv\Scripts\activate.bat

echo.
echo [3/5] Atualizando pip e instalando dependencias...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias. Veja a secao de solucao de
    echo        problemas no README.md ^(erros comuns de rasterio/GDAL^).
    exit /b 1
)

echo.
echo [4/5] Executando testes unitarios...
pytest tests -q
if errorlevel 1 (
    echo [AVISO] Um ou mais testes falharam. Revise antes de distribuir o
    echo         executavel. O build sera continuado mesmo assim.
)

echo.
echo [5/5] Gerando executavel com PyInstaller (GeoRasterToolsV3.spec)...
pyinstaller GeoRasterToolsV3.spec --noconfirm --distpath Executavel --workpath build
if errorlevel 1 (
    echo [ERRO] Falha ao gerar o executavel.
    exit /b 1
)

echo.
echo ============================================================
echo  Build concluido. Executavel disponivel em:
echo  Executavel\GeoRasterToolsV3\GeoRasterToolsV3.exe
echo ============================================================

endlocal
