# Tutorial — GeoRaster Tools V3

Guia prático de uso da aplicação, aba por aba. Para a descrição técnica do
projeto (estrutura do código, testes, decisões de implementação) veja o
[README.md](README.md).

---

## Sumário

1. [Instalar e abrir](#1-instalar-e-abrir)
2. [A janela](#2-a-janela)
3. [Fluxo recomendado](#3-fluxo-recomendado)
4. [Aba 1 — Recortar Rasters](#aba-1--recortar-rasters)
5. [Aba 2 — Reclassificar](#aba-2--reclassificar)
6. [Aba 3 — Separar Bandas](#aba-3--separar-bandas)
7. [Aba 4 — Reamostrar](#aba-4--reamostrar)
8. [Aba 5 — Merge de Rasters](#aba-5--merge-de-rasters)
9. [Aba 6 — Verificar Alinhamento](#aba-6--verificar-alinhamento)
10. [Aba 7 — Amostragem](#aba-7--amostragem)
11. [Tipos de dado de saída](#11-tipos-de-dado-de-saída)
12. [Problemas comuns](#12-problemas-comuns)

---

## 1. Instalar e abrir

### Opção A — executável (recomendada para quem só vai usar)

1. Baixe o `GeoRasterToolsV3.zip` em **Releases** deste repositório.
2. Descompacte a pasta inteira em algum lugar com permissão de escrita
   (`C:\Ferramentas\GeoRasterToolsV3`, por exemplo). **Evite** "Arquivos de
   Programas" e pastas sincronizadas do OneDrive.
3. Duplo clique em `GeoRasterToolsV3.exe`.

Não precisa instalar Python, GDAL, QGIS nem R. Não precisa de internet.
Copiar só o `.exe` **não funciona**: ele depende da pasta `_internal\` que
fica ao lado, com as bibliotecas e as tabelas de projeção do GDAL/PROJ.

A primeira abertura demora alguns segundos (o Windows verifica os arquivos);
da segunda em diante abre rápido.

### Opção B — a partir do código-fonte

Pré-requisito: **Python 3.11 (64 bits)** com "Add python.exe to PATH" e
Tcl/Tk (vem marcado por padrão no instalador oficial).

```bat
git clone https://github.com/rnvuefsppgm/GeoRasterToolsV3.git
cd GeoRasterToolsV3
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

### Conferir se está tudo certo

```bat
GeoRasterToolsV3.exe --autoteste
```

(ou `venv\Scripts\python.exe main.py --autoteste` no código-fonte). São 11
verificações, das bibliotecas ao ciclo completo de processamento raster. O
relatório vai para `autoteste.txt`, ao lado do executável — é esse arquivo
que você envia se algo der errado.

---

## 2. A janela

Sete abas, na ordem do fluxo de trabalho:

| # | Aba | Para quê |
|---|---|---|
| 1 | Recortar Rasters | cortar vários `.tif` pelo contorno de um shapefile |
| 2 | Reclassificar | trocar valores de pixel por regras (ex.: MapBiomas → classes do seu modelo) |
| 3 | Separar Bandas | quebrar um raster multibanda em vários arquivos de uma banda |
| 4 | Reamostrar | colocar todos os rasters na mesma grade de um raster modelo |
| 5 | Merge de Rasters | remontar os blocos que o Earth Engine gera em exportações grandes |
| 6 | Verificar Alinhamento | conferir se a pilha de preditores está realmente alinhada |
| 7 | Amostragem | sortear presenças e background a partir de um raster de contagem |

**Em todas as abas**, no rodapé: barra de progresso, porcentagem e a linha de
etapa (`[7/213] bio_12.tif`). O painel preto é o log — ele guarda o histórico
da execução e é por onde você descobre o que aconteceu com cada arquivo.

**Processamento pesado nunca trava a janela**: cada execução roda em uma
thread separada. Nas abas 5 e 7 há ainda o botão `PARAR PROCESSAMENTO`.

---

## 3. Fluxo recomendado

O caminho típico, do download até o modelo:

```
   (exportou do Earth Engine em blocos?)
                 │
                 ▼
      Aba 5  Merge de Rasters        remonta cada camada partida
                 │
                 ▼
      Aba 1  Recortar Rasters        corta tudo pela área de estudo
                 │
                 ▼
      Aba 2  Reclassificar           agrupa as classes de uso do solo
                 │
                 ▼
      Aba 3  Separar Bandas          (só se houver raster multibanda)
                 │
                 ▼
      Aba 4  Reamostrar              leva tudo para a grade comum
                 │
                 ▼
      Aba 6  Verificar Alinhamento   confirma que a pilha está coerente
                 │
                 ▼
      Aba 7  Amostragem              gera presenças e background para o SDM
```

Você não precisa usar todas: cada aba funciona sozinha.

---

## Aba 1 — Recortar Rasters

Corta **todos** os `.tif` de uma pasta pelo contorno de um shapefile
(`crop` + `mask`).

**Campos**

| Campo | O que informar |
|---|---|
| Pasta dos rasters (.tif) | a pasta de entrada; os arquivos aparecem na tabela "Arquivos de Entrada" |
| Shapefile de recorte (.shp) | o polígono da área de estudo |
| Pasta de saída | onde gravar |
| Prefixo dos arquivos de saída | `MAT_` por padrão → `MAT_bio_1.tif` |
| Sobrescrever arquivos existentes | ligado por padrão |

**Como usar:** selecione os três caminhos → `Executar Recorte`. A tabela
"Resultado do Processamento" mostra OK/ERRO por arquivo.

**Dica:** o shapefile pode estar em outro CRS — as geometrias são
reprojetadas para o CRS de cada raster automaticamente (é preciso que o
`.prj` esteja presente ao lado do `.shp`).

---

## Aba 2 — Reclassificar

Troca valores de pixel seguindo regras `Val_Min, Val_Max, Novo_Val`. É o
equivalente ao `classify()` do `terra`.

**Como usar**

1. Escolha a pasta de entrada e a de saída.
2. `Escanear Arquivos` — todos entram com a regra `Padrao` (as classes do
   MapBiomas já vêm preenchidas).
3. Edite as regras no **Editor de Regras**: duplo clique em qualquer célula
   para alterar, `+ Linha` para incluir, `- Linha Selecionada` para remover,
   `Salvar Regra` para guardar as alterações do conjunto.
4. Para tratar arquivos diferentes com regras diferentes: crie um conjunto
   novo (nome + `Criar`), ajuste as regras e use `Atribuir regra ativa aos
   selecionados` na tabela de arquivos.
5. `Executar Reclassificacao`. A saída sai como `Reclass_<nome>.tif`.

**Parâmetros de saída**

| Campo | O que faz |
|---|---|
| Tipo de dado de saída | `INT1U - 8 bits - uint8` por padrão; veja a [seção 11](#11-tipos-de-dado-de-saída) |
| Pixels não mapeados | `NA` (sem valor) ou `Zero (0)` — o que fazer com valores fora das regras |
| Intervalo fechado em | `Ambos os lados`, `Direita` ou `Esquerda`, como o argumento `right` do `terra::classify` |
| Incluir valor mínimo | equivale a `include.lowest` |

**Importar/exportar:** `Carregar .txt` e `Salvar .txt` guardam um conjunto de
regras em arquivo de texto, para reaproveitar entre projetos.

---

## Aba 3 — Separar Bandas

Quebra rasters multibanda em um arquivo por banda — típico de séries
bioclimáticas futuras, que vêm com 19 bandas em um `.tif` só.

| Campo | O que faz |
|---|---|
| Pasta dos rasters (.tif) | entrada |
| Pasta de saída | destino |
| Padrão de nomenclatura | como nomear cada banda (abaixo) |

**Padrões de nomenclatura**

- **Bioclim Futuro** — lê o nome no formato `bioc_Modelo_sspXXX_AAAA-AAAA` e
  gera `Modelo_sspXXX_AAAA-AAAA_bio_1.tif`, `..._bio_2.tif`, …
- **Prefixo Personalizado** — mesma ideia, para nomes `Modelo_sspXXX_AAAA-AAAA`.
- **Genérico** — `NomeArquivo_Band1.tif`, `NomeArquivo_Band2.tif`, …

O tipo de dado e o CRS do arquivo de origem são preservados.

---

## Aba 4 — Reamostrar

Leva todos os rasters para a **grade de um raster modelo**: mesma resolução,
mesma extensão, mesmo alinhamento de pixel. É o passo que torna a pilha de
preditores utilizável por qualquer modelo.

**Como usar**

1. **Raster Modelo (template)** — escolha o raster que define a grade
   (normalmente um bioclimático). Os metadados dele aparecem à direita.
2. **Pasta dos rasters alvo** e **Pasta de saída**.
3. `Escanear Arquivos` — cada arquivo entra na tabela com um **método**
   sugerido automaticamente: `near` para raster que parece categórico,
   `bilinear` para contínuo.
4. Ajuste o que quiser:
   - duplo clique na coluna **Metodo** ou **Dtype** para editar uma linha;
   - ou selecione várias linhas (clique/Shift/Ctrl) e use `Aplicar a
     selecionados` / `Aplicar a todos`.
5. `Executar Reamostramento`. A saída sai como `res_<nome>.tif`.

**A coluna Tipo × a coluna Dtype:** "Tipo" é o tipo de dado do arquivo de
**entrada** (`int16 (16 bits)`); "Dtype" é o tipo que será **gravado**
(`FLT4S - 32 bits - float32`).

**Converter zeros (0) para NA** — vem **desmarcada**, e é importante que
continue assim na maioria dos casos. Marque apenas quando o zero significar
"sem dado" no raster de entrada (máscaras do MapBiomas, por exemplo). Em
raster de **distância** o zero é o dado mais informativo ("dentro da
classe"); marcar a opção apaga justamente as áreas de interesse.

**Avisos:** se um raster sair com pouquíssima área válida, a aba informa na
caixa de diálogo ao final. Vale conferir antes de seguir: uma grade com pouca
área válida só costuma dar sinal lá na frente, quando a amostragem devolve
poucos pontos.

---

## Aba 5 — Merge de Rasters

Remonta os blocos que o **Earth Engine** gera quando uma exportação passa do
limite de tamanho. O GEE acrescenta ao nome o deslocamento do bloco em
pixels:

```
reclass_ndvi_stdDev_tau-0000000000-0000000000.tif
reclass_ndvi_stdDev_tau-0000046592-0000000000.tif
reclass_ndvi_stdDev_tau_p-0000000000-0000000000.tif
reclass_ndvi_stdDev_tau_p-0000046592-0000000000.tif
```

Atenção: esses quatro arquivos são **dois** mosaicos, não um. O que vem antes
do sufixo numérico é o nome real da camada, e `_p` faz parte dele. A aba
agrupa por esse nome, não por "prefixo comum".

**Como usar**

1. **Pasta dos rasters (.tif)** — ao escolher a pasta, a aba lista sozinha
   **apenas** os conjuntos que precisam de merge (dois ou mais blocos com a
   mesma base). Arquivos avulsos nem aparecem.
2. Confira a tabela **Conjuntos detectados**:
   - coluna **Usar** — um clique liga/desliga o conjunto;
   - **Blocos** — `2/2` = dois de dois blocos marcados;
   - **Deteccao** — como o conjunto foi identificado;
   - **Arquivo de saida** — duplo clique edita o nome.
3. Clique num conjunto para ver seus blocos na tabela de baixo (dimensões,
   bandas, tipo, NoData). A coluna **Usar** também liga/desliga bloco a bloco.
4. **Pasta de saída** — opcional; o padrão é a mesma pasta dos blocos.
5. **Prefixo** — `Merge_` por padrão → `Merge_reclass_ndvi_stdDev_tau.tif`.
6. `EXECUTAR MERGE`.

**Selecionar arquivos manualmente...** aceita qualquer combinação de
arquivos, inclusive nomes sem padrão de bloco — útil quando os arquivos foram
renomeados.

**Memória por faixa (MB)** — o mosaico é montado em faixas de linhas, então
arquivos de vários GB são unidos sem estourar a memória. 64 MB é um bom
valor; só aumente se a máquina tiver folga.

**O que a aba garante**

- saída idêntica à entrada em resolução, CRS, tipo de dado e número de
  bandas; GeoTIFF LZW em *tiles*, BigTIFF quando necessário;
- onde os blocos se sobrepõem, vale o primeiro — e NoData de um bloco nunca
  apaga o dado do vizinho;
- lacunas entre blocos ficam como NoData;
- o merge é **recusado**, com explicação do que destoa, se os blocos tiverem
  CRS, resolução, tipo de dado ou número de bandas diferentes, ou se a grade
  estiver deslocada (nesse caso, reamostre antes na aba 4);
- se você cancelar, o arquivo parcial é apagado — um GeoTIFF truncado deixado
  para trás seria usado depois como se estivesse bom.

---

## Aba 6 — Verificar Alinhamento

Compara cada raster de uma pasta com um raster modelo e diz se estão
alinhados: resolução, dimensões, extensão e **origem da grade**.

Escolha o **Raster Modelo**, a **Pasta dos rasters reamostrados** e clique em
`Verificar Alinhamento`. A tabela mostra OK (verde) ou ERRO (vermelho) com o
detalhe da divergência — inclusive o deslocamento em **pixel**, que é a
informação útil para decidir se vale reamostrar de novo.

Rode esta aba sempre antes da amostragem e antes de levar a pilha para o
modelo. Preditores desalinhados produzem modelos silenciosamente errados.

---

## Aba 7 — Amostragem

Analisa um raster de **contagem** (frequência de fogo acumulada, por exemplo)
e sorteia pontos de presença e de background no formato que o SDM_APP e o
MaxEnt consomem.

**Passo a passo**

1. **1. Arquivos** — o raster de frequência e a pasta de saída.
2. **2. Análise da frequência** — banda, anos observados e valores a excluir
   (`0; 99`, por exemplo). Clique em `ANALISAR FREQUENCIA`: a tabela de
   limiares aparece à direita, com pixels e ocorrências mantidas, retorno
   médio em anos e uma interpretação automática. Duplo clique numa linha
   preenche as faixas da seção 3.
3. **3. Classes para amostragem** — as faixas de valor que contam como
   presença e como ausência.
4. **4. Amostragem** — quantos pontos de cada, distância mínima em **metros
   reais** (medida em coordenadas geocêntricas, sem reprojetar o raster) e a
   semente aleatória, para o sorteio ser reproduzível.
5. **5. Preparo para o SDM** — escolha **um** raster preditor como referência
   da grade e marque:
   - *Descartar pontos em NoData da referência*;
   - *No máximo um ponto por célula da referência*.

   É isso que faz a quantidade pedida chegar inteira ao modelo. Sem o filtro,
   parte dos pontos cai em NoData de preditor (o SDMtune descarta em
   silêncio) ou repete a mesma célula ambiental.

   > **Use um raster, não a pasta inteira.** Exigir validade simultânea em
   > todos os preditores pode zerar a amostra: os vazios de NaN dos rasters
   > de distância são complementares entre si. Prefira um bioclimático ou o
   > raster de uso do solo. O botão `Conferir referência` sonda o arquivo
   > escolhido antes de processar.

6. **6. Formato das amostras** — `x,y` em WGS84 (o que o SDM_APP espera) ou
   no CRS original; valores de `Value` para presença e ausência; separador
   decimal das tabelas auxiliares.
7. **7. Arquivos de saída** — 11 caixas, em duas dimensões: quais classes
   (presenças, ausências, arquivo único) e em que formato (`x,y,Value`,
   tabela detalhada, GeoJSON), mais as tabelas da análise.
8. **8. Executar** — `GERAR E SALVAR AMOSTRAS`.

**O que sai**

```
x,y,Value
-46.88625,-16.44875,1
```

CSV com vírgula, ponto decimal, sem BOM, colunas `x` e `y` — exatamente o
contrato do SDM_APP. No SDM_APP, `amostras_presencas.csv` vai em "Pontos de
presença" e `amostras_ausencias.csv` em "Pontos de background", ambos com
**Coluna X = 1** e **Coluna Y = 2**.

O relatório de execução registra quantos candidatos foram recusados e por
quê.

---

## 11. Tipos de dado de saída

Nas abas 2 e 4 o tipo aparece como `INT2U - 16 bits - uint16`: o código, os
**bits** e o nome do tipo em numpy. O código vem do `terra`/R, onde o número
conta **bytes** (`INT2U` = 2 bytes = 16 bits) — por isso a interface mostra
os bits por extenso.

| Código | Bits | numpy | Faixa representável | NoData gravado | Quando usar |
|---|---|---|---|---|---|
| `FLT4S` | 32 | `float32` | ±3,4×10³⁸ (~7 dígitos) | `NaN` | contínuos: bioclim, distância, NDVI |
| `FLT8S` | 64 | `float64` | ±1,8×10³⁰⁸ (~16 dígitos) | `NaN` | precisão extra; dobra o tamanho |
| `INT2S` | 16 | `int16` | −32.768 a 32.767 | `-9999` | inteiros que podem ser negativos |
| `INT2U` | 16 | `uint16` | 0 a 65.535 | `0` | contagens, anos, códigos |
| `INT4S` | 32 | `int32` | ±2,1×10⁹ | `-9999` | inteiros grandes com sinal |
| `INT4U` | 32 | `uint32` | 0 a 4,29×10⁹ | `0` | identificadores muito grandes |
| `INT1U` / `BYTE` | 8 | `uint8` | 0 a 255 | `255` | classes e máscaras — menor arquivo |

`INT1U` aparece na aba Reclassificar; `INT4U`, na Reamostrar. Em disco, antes
da compressão LZW: 1 byte/pixel no `BYTE`, 2 nos `INT2*`, 4 nos
`INT4*`/`FLT4S`, 8 no `FLT8S`.

**Três armadilhas**

1. **Os tipos sem sinal gravam NoData = 0.** Se o raster tem zeros válidos —
   um raster de distância, onde 0 é "dentro da classe" —, eles passam a ser
   lidos como "sem dado". Use `FLT4S` ou `INT2S` nesses casos.
2. **Converter para inteiro trunca, não arredonda.** Com `bilinear`, `2,9`
   vira `2`. Para raster de classes, use `near` + `BYTE`.
3. **Não há recorte de faixa.** Valor fora do intervalo do tipo escolhido
   estoura em silêncio (70.000 em `INT2U`, por exemplo).

---

## 12. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| A janela não abre | empacotamento ou antivírus | rode `GeoRasterToolsV3.exe --autoteste` e leia `autoteste.txt`; se a pasta encolheu após a cópia, veja a quarentena do antivírus |
| "Aba de Amostragem indisponível" | `pyproj` ausente (só no modo código-fonte) | `pip install -r requirements.txt` |
| Merge recusa: "CRS / Resolucao / Tipo de dado diferente" | os blocos não vieram da mesma exportação | reprojete ou reamostre antes (abas 4 e 6) |
| Merge recusa: "nao esta na mesma grade" | origens deslocadas por fração de pixel | reamostre os blocos para um raster modelo (aba 4) |
| Um conjunto esperado não aparece na aba 5 | o nome não tem sufixo numérico de bloco | use `Selecionar arquivos manualmente...` |
| "Origem divergente" na aba 6 | a grade está deslocada em relação ao modelo | a mensagem traz o deslocamento em pixel; se for relevante, reamostre pela aba 4 |
| Preditor reamostrado quase todo vazio | "Converter zeros (0) para NA" foi marcada | refaça sem marcar |
| A amostragem gera 0 pontos | raster de referência inadequado | use `Conferir referência`; prefira um bioclimático a um raster de distância |
| O SDM recebe menos pontos que o pedido | filtro da seção 5 desligado | selecione o raster de referência e marque as duas opções |
| Distância mínima recusada | raster sem CRS definido | defina o CRS do arquivo ou use distância 0 |
| Erro de projeção só no `.exe` | dados do PROJ ausentes | confirme a pasta `_internal\pyproj` ao lado do executável |

---

## Onde ficam os arquivos

- **Log de processamento:** `processing.log`, na pasta de onde a aplicação
  foi executada.
- **Relatório do autoteste:** `autoteste.txt`, ao lado do executável.
- **Falha na inicialização:** `erro_inicializacao.txt`, na mesma pasta.

Ao relatar um problema, envie o `autoteste.txt` junto — ele diz qual parte do
ambiente falhou.
