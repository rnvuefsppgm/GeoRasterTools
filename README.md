# GeoRaster Tools V3 (v3.0)

Aplicativo desktop para Windows 10/11, interface em português (Tkinter/ttk),
que reúne numa única janela as ferramentas de **preparo de rasters** e a
**amostragem de pontos** para modelos de distribuição de espécies, com os
arquivos já gravados no formato que o **SDM_APP** consome.

## Abas

| # | Aba | O que faz |
|---|---|---|
| 1 | **Recortar Rasters** | recorta todos os `.tif` de uma pasta por um shapefile (crop + mask) |
| 2 | **Reclassificar** | reclassifica por regras `Val_Min, Val_Max, Novo_Val`, com editor, conjuntos nomeados e import/export `.txt` |
| 3 | **Separar Bandas** | separa bandas de rasters multibanda, com 3 padrões de nomenclatura |
| 4 | **Reamostrar** | reamostra para a grade de um raster modelo, com `near`/`bilinear` detectado por arquivo |
| 5 | **Merge de Rasters** | remonta os blocos que o Earth Engine gera quando uma exportação passa do limite de tamanho |
| 6 | **Verificar Alinhamento** | confere resolução, dimensões, extensão e origem contra um raster modelo |
| 7 | **Amostragem** | analisa a frequência acumulada e sorteia presenças e background prontos para o SDM |

Em todas as abas o rodapé traz **barra de progresso, porcentagem e a linha de
etapa** (`[7/213] bio_12.tif`), e o processamento roda em uma thread separada,
sem travar a janela. O painel de log guarda o histórico da execução, arquivo
por arquivo.

O guia de uso passo a passo está em **[TUTORIAL.md](TUTORIAL.md)**.

### Tipos de dado de saída

Nas abas 2 e 4 o tipo aparece como `INT2U - 16 bits - uint16`: o código, os
bits e o nome em numpy. O código vem do `terra`/R, onde o número conta
**bytes** (`INT2U` = 2 bytes = 16 bits) — por isso a interface mostra os bits
por extenso. Nas tabelas, o tipo do arquivo de **entrada** também traz os bits
(`int16 (16 bits)`), para a comparação com o tipo escolhido ser imediata.

| Opção | Bits | numpy | Faixa | NoData gravado |
|---|---|---|---|---|
| `FLT4S` | 32 | `float32` | ±3,4×10³⁸ (~7 dígitos) | `NaN` |
| `FLT8S` | 64 | `float64` | ±1,8×10³⁰⁸ (~16 dígitos) | `NaN` |
| `INT2S` | 16 | `int16` | −32.768 a 32.767 | `-9999` |
| `INT2U` | 16 | `uint16` | 0 a 65.535 | `0` |
| `INT4S` | 32 | `int32` | ±2,1×10⁹ | `-9999` |
| `INT4U` | 32 | `uint32` | 0 a 4,29×10⁹ | `0` |
| `INT1U` / `BYTE` | 8 | `uint8` | 0 a 255 | `255` |

`INT1U` aparece na aba Reclassificar e `INT4U` na aba Reamostrar; o resto é
comum às duas. Três cuidados ao escolher: os tipos sem sinal gravam
**NoData = 0**, o que transforma zeros válidos em "sem dado"; converter para
inteiro **trunca** (com `bilinear`, `2,9` vira `2`); e não há recorte de faixa
— valor fora do intervalo do tipo escolhido estoura em silêncio.

---

## A aba 5 — Merge de Rasters

### O cenário

Quando uma exportação do Earth Engine passa do limite de tamanho, o GEE parte
o resultado em blocos e acrescenta ao nome o deslocamento do bloco em pixels:

```
reclass_ndvi_stdDev_tau-0000000000-0000000000.tif
reclass_ndvi_stdDev_tau-0000046592-0000000000.tif
reclass_ndvi_stdDev_tau_p-0000000000-0000000000.tif
reclass_ndvi_stdDev_tau_p-0000046592-0000000000.tif
```

Esses quatro arquivos são **dois** mosaicos, não um. É o sufixo numérico que
marca o bloco; o que vem antes dele é o nome real da camada. Agrupar por
"prefixo comum" juntaria os quatro e misturaria duas camadas diferentes — por
isso a aba recorta o sufixo de bloco e compara o que sobra por **igualdade
exata**: `reclass_ndvi_stdDev_tau` e `reclass_ndvi_stdDev_tau_p` são conjuntos
distintos.

### Como usar

| Campo | O que faz |
|---|---|
| **Pasta dos rasters (.tif)** | ao escolher a pasta, a aba já lista **só** os conjuntos que precisam de merge (dois ou mais blocos com a mesma base). Arquivos avulsos não aparecem |
| **Reescanear pasta** | relê a pasta depois de novos downloads |
| **Selecionar arquivos manualmente...** | aceita qualquer combinação de arquivos, inclusive nomes sem padrão de bloco (viram um conjunto único) |
| **Pasta de saída** | opcional; o padrão é a mesma pasta dos blocos |
| **Prefixo do arquivo de saída** | `Merge_` por padrão → `Merge_reclass_ndvi_stdDev_tau.tif` |
| **Coluna "Usar"** | um clique liga/desliga o conjunto inteiro ou um bloco específico |
| **"Arquivo de saída"** | duplo clique edita o nome daquele conjunto |
| **Memória por faixa (MB)** | tamanho do pedaço lido/gravado por vez; 64 MB por padrão |

### Como o mosaico é montado

A saída é percorrida em **faixas de linhas**: para cada faixa, só os pedaços
das entradas que a cruzam são lidos, combinados e gravados de uma vez. O pico
de memória é o tamanho da faixa, não o do mosaico — por isso blocos que somam
vários GB são unidos sem pressão sobre a máquina.

Outras garantias:

- **saída idêntica à entrada** — mesma resolução, CRS, tipo de dado e número
  de bandas; GeoTIFF LZW em tiles de 256×256, BigTIFF quando necessário;
- **sobreposição**: vale o primeiro bloco da lista (mesma regra do
  `rasterio.merge`), aplicada pixel a pixel sobre a máscara de dado válido —
  NoData de um bloco nunca apaga o dado do vizinho;
- **lacunas** entre blocos ficam como NoData;
- **NoData nunca é inventado**: se nenhum bloco declara NoData e o tipo é
  inteiro, o mosaico sai sem NoData declarado e o log avisa. Declarar `0`
  nesse caso marcaria a classe 0 como ausência de dado em todo o resto da
  cadeia;
- **recusa explícita** quando os blocos não formam mosaico — CRS, resolução,
  tipo de dado ou número de bandas diferentes, ou grade deslocada —, dizendo
  qual arquivo destoa e em quê;
- **cancelamento** remove o arquivo parcial, para que nenhum GeoTIFF truncado
  fique para trás.

---

## A aba 7 — Amostragem

### O contrato do SDM_APP

O SDM_APP lê os pontos assim (`app_sdm_multi_v6.R`):

```r
p_coords  <- utils::read.csv(cfg$pres_csv)[, c("x", "y")]
bg_coords <- utils::read.csv(cfg$bg_csv)[, c("x", "y")]
```

Ou seja: CSV com **vírgula**, **ponto decimal**, sem BOM, colunas chamadas
exatamente `x` e `y`, e **arquivos separados** para presença e background. A
aba grava exatamente isso:

```
x,y,Value
-46.88625,-16.44875,1
```

`Value` é 1 para presença e 0 para ausência (ambos configuráveis). O R ignora a
terceira coluna; ela existe para quem for usar os arquivos no MaxEnt ou no QGIS.

### Por que existe o filtro pelo raster de referência

Um ponto sorteado apenas sobre o raster de frequência pode ser inútil para o
modelo por duas razões:

1. **cai em NoData de algum preditor** — o SDMtune o descarta em silêncio;
2. **cai na mesma célula do preditor** que outro ponto — o raster de
   frequência costuma ser bem mais fino que o bioclimático, então vários
   pontos viram o mesmo registro ambiental (pseudo-replicação).

Na seção **5. Preparo para o SDM** você aponta **um** raster de referência — de
preferência um bioclimático ou o de uso do solo — e marca:

- **Descartar pontos em NoData da referência**;
- **No máximo um ponto por célula da referência** — é ela que define a grade
  (a mesma que a aba 6 confere).

O filtro é aplicado **durante** o sorteio, e não depois: é isso que faz a
quantidade pedida chegar inteira ao modelo. Numa medição com preditor de ~1 km
e 18 % de NoData, pedindo 500 presenças, o filtro ligado entrega as 500
utilizáveis.

> **Use um raster, não a pasta inteira.** Exigir validade simultânea em todos
> os preditores pode zerar a amostra: em uma pilha de 25 rasters do Acre, cada
> um aprovava de 42 % a 100 % dos candidatos isoladamente, mas os vazios de
> NaN dos rasters de distância são complementares entre si e a interseção
> aprovava zero.

O botão **Conferir referência** sonda o raster escolhido contra o de frequência
antes de processar, e informa CRS, quantos pontos caem dentro, quantos em
célula válida e quantas células distintas existem.

Sem raster de referência selecionado o filtro fica desligado. O relatório
registra em qualquer caso quantos candidatos foram recusados e por quê:

```
PREPARO PARA SDM
  Preditores ............. 1 preditor(es): AC_wc2.1_30s_bio_1.tif
  Presenca:
    Candidatos avaliados: 788
    Aceitos: 500
    Recusados por estarem em NoData do raster de referencia: 154
    Recusados por estarem em celula de referencia ja usada: 132
```

### Fluxo recomendado

1. Aba 5 remonta os blocos que vieram partidos do Earth Engine;
2. Abas 1 a 4 preparam a pilha de preditores (recortar, reclassificar,
   reamostrar para a grade comum);
3. Aba 6 confere que estão todos alinhados;
4. Aba 7 usa **um** raster dessa pasta como referência e gera presenças e background;
5. No SDM_APP, `amostras_presencas.csv` vai em "Pontos de presença" e
   `amostras_ausencias.csv` em "Pontos de background", ambos com **Coluna X = 1**
   e **Coluna Y = 2**.

### Demais recursos da aba

- tabela de limiares de frequência com pixels e ocorrências mantidas, retorno
  médio em anos e interpretação automática; duplo clique preenche as faixas;
- distância mínima em **metros reais**, medida em coordenadas geocêntricas
  (ECEF, EPSG:4978) — o raster nunca é reprojetado;
- 11 caixas de seleção de saída, em duas dimensões: quais classes (presenças,
  ausências, arquivo único) e em que formato (`x,y,Value`, tabela detalhada,
  GeoJSON), mais as tabelas da análise;
- leitura do GeoTIFF em janelas agregadas de ~8 Mpixels, sem carregar o arquivo
  inteiro na memória;
- botão `PARAR PROCESSAMENTO` com cancelamento cooperativo.

---

## Estrutura do projeto

O código está separado em três pacotes: **`nucleo/`** faz as contas e não
importa nada de interface (dá para usar num script ou notebook), **`interface/`**
é a camada Tkinter e **`amostragem_fogo/`** é o núcleo de amostragem, com CLI
própria.

```
GeoRasterToolsV3/
├── main.py                    Ponto de entrada (e `--autoteste`)
│
├── nucleo/                    Processamento, sem interface gráfica
│   ├── config.py              regras default, dtypes, constantes
│   ├── utils.py               logging, cancelamento, listagem de arquivos
│   ├── raster_ops.py          recorte, reclassificação, bandas, reamostragem, alinhamento
│   └── merge_ops.py           agrupamento de blocos e montagem de mosaicos
│
├── interface/                 Camada Tkinter
│   ├── gui.py                 janela principal, widgets compartilhados, abas 1-4 e 6
│   ├── tab_merge.py           aba 5
│   └── tab_amostragem.py      aba 7
│
├── amostragem_fogo/           Núcleo de amostragem + preparo para SDM
│   ├── config.py              parâmetros validados, seleção de saídas
│   ├── blocos.py              leitura em janelas agregadas
│   ├── analise.py             histograma e tabela de limiares
│   ├── geo.py                 coordenadas, CRS, distância geocêntrica
│   ├── amostragem.py          reservatório aleatório e filtros
│   ├── sdm.py                 máscara pelo raster de referência
│   ├── exportacao.py          x,y,Value, CSV detalhado, GeoJSON, relatório
│   ├── pipeline.py            orquestração sem interface
│   ├── gui.py                 janela autônoma do núcleo (não usada pela aplicação)
│   └── cli.py                 linha de comando
│
├── tests/
│   ├── test_raster_ops.py          abas 1 a 4 e 6
│   ├── test_reamostragem_zeros.py  tratamento de zeros na aba 4
│   ├── test_alinhamento_fase.py    fase da grade na aba 6
│   ├── test_merge_ops.py           núcleo da aba 5 (agrupamento e mosaico)
│   ├── test_tab_merge.py           interface da aba 5
│   ├── test_rodape_e_dtypes.py     barra de progresso e rótulos de dtype
│   ├── test_tab_amostragem.py      aba 7 e contrato do SDM_APP
│   └── testes_amostragem_fogo.py   206 verificações numéricas do núcleo
│
├── Executavel/                Pacote compilado para Windows + LEIA-ME
├── TUTORIAL.md                Guia de uso passo a passo
├── requirements.txt
├── pytest.ini
├── build_windows.bat
└── GeoRasterToolsV3.spec
```

As abas 5 e 7 são montadas com `import` tardio: se uma delas não puder ser
carregada (por exemplo, `pyproj` ausente numa instalação a partir do código),
ela mostra o motivo e **as demais abas continuam funcionando**.

---

## Instalação

Pré-requisito: **Python 3.11 (64 bits)** com "Add python.exe to PATH" e Tcl/Tk
(marcado por padrão no instalador oficial).

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

Para quem só vai usar a aplicação, o executável em `Executavel\` dispensa
qualquer instalação — veja o [TUTORIAL.md](TUTORIAL.md).

## Testes

```bat
venv\Scripts\python.exe -m pytest tests -q
```

São 115 testes. E as 206 verificações numéricas do núcleo de amostragem, que
criam rasters sintéticos e conferem cada resultado:

```bat
venv\Scripts\python.exe tests\testes_amostragem_fogo.py
```

## Gerar o executável

```bat
build_windows.bat
```

O resultado fica em `Executavel\GeoRasterToolsV3\GeoRasterToolsV3.exe`.
Distribua a pasta inteira: o executável depende dos dados do GDAL e do PROJ
que ficam ao lado dele.

O `.spec` empacota explicitamente `pyproj` e a base PROJ, além dos pacotes
locais `nucleo`, `interface` e `amostragem_fogo` — as abas são importadas de
forma tardia e não apareceriam na análise estática do PyInstaller.

Para conferir o pacote gerado (ou a instalação a partir do código):

```bat
venv\Scripts\python.exe main.py --autoteste
```

São 11 verificações, das bibliotecas ao ciclo completo de processamento
raster, incluindo um merge de dois blocos conferido pixel a pixel e a
conferência de que as sete abas montam **com barra de progresso**. O
resultado vai para `autoteste.txt`.

---

## Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| A janela não abre (executável) | empacotamento incompleto ou antivírus | rode `GeoRasterToolsV3.exe --autoteste` e leia `autoteste.txt`; se a pasta encolheu após a cópia, verifique a quarentena do antivírus |
| Aba 7 mostra "indisponível" | `pyproj` ausente (instalação a partir do código) | `pip install -r requirements.txt` |
| Aba 5 mostra "indisponível" | pacote `nucleo` incompleto | reinstale as dependências e rode `main.py --autoteste` |
| Merge recusa: "CRS / Resolucao / Tipo de dado diferente" | os blocos não vieram da mesma exportação | reprojete ou reamostre antes (abas 4 e 6) |
| Merge recusa: "nao esta na mesma grade" | origens deslocadas por fração de pixel | reamostre os blocos para um raster modelo na aba 4 |
| Conjunto esperado não aparece na aba 5 | o nome não tem sufixo numérico de bloco | use **Selecionar arquivos manualmente...** |
| Mosaico com faixas de NoData entre os blocos | a exportação não cobre a área inteira | confira a extensão de cada bloco na tabela de blocos |
| "Origem divergente" na aba 6 | a grade do raster está deslocada em relação ao modelo | a mensagem informa o deslocamento em pixel; se for relevante, reamostre pela aba 4 |
| Preditor reamostrado quase todo vazio | "Converter zeros (0) para NA" marcada num raster cujo zero é dado válido | refaça sem marcar a opção |
| A amostragem gera 0 pontos | raster de referência inadequado | use **Conferir referência**; prefira um bioclimático a um raster de distância |
| O SDM recebe menos pontos que o pedido | filtro da seção 5 desligado | selecione o raster de referência e marque as duas opções |
| Distância mínima recusada | raster sem CRS definido | defina o CRS do arquivo ou use distância 0 |
| `ImportError: DLL load failed` no rasterio | wheel incompatível | reinstale com Python 3.11 de 64 bits |
| `ModuleNotFoundError: tkinter` | Python sem Tcl/Tk | reinstale marcando "tcl/tk and IDLE" |
| Erro de projeção só no `.exe` | dados do PROJ ausentes no pacote | confirme a pasta `Executavel\GeoRasterToolsV3\_internal\pyproj` e reconstrua com o `.spec` |
| Pasta de saída sem permissão de escrita | executando de "Arquivos de Programas" ou de pasta do OneDrive | execute de uma pasta comum, como `C:\Ferramentas` |

Ao relatar um problema, envie o `autoteste.txt` junto — ele diz qual parte do
ambiente falhou.

## Observações estatísticas

A fração e o peso gravados na tabela detalhada são nominais (`n/N` e `N/n`).
Com distância mínima maior que zero, ou com o filtro de preditores ligado, a
rejeição faz com que não sejam probabilidades de inclusão exatas; para
inferência de desenho estrita, use uma implementação GRTS com probabilidades
conhecidas.

## Licença

Este programa é software livre, distribuído sob a **GNU General Public License
v3.0 (GPL-3.0)**. Você pode redistribuí-lo e/ou modificá-lo nos termos dessa
licença. O texto completo está no arquivo [`LICENSE`](LICENSE).
