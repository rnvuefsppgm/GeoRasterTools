# Correções da V2.1 / V2.2 — amostragem do Acre e alinhamento

Diagnóstico feito sobre os dados reais:
`D:\Disciplina_MOD\Fogo\MapBiomas_Fogo_Frequencia_Acre_1985_2025.tif` e os
preditores de `D:\Disciplina_MOD\CLIP_BIOCLIM\Acre\Presente`.

---

## O que **não** era o problema

Antes de mudar qualquer coisa, descartei as duas hipóteses levantadas:

**Sistema de coordenadas — está correto.** Raster de fogo e preditores estão
ambos em `EPSG:4326`, com extensões praticamente idênticas:

| | CRS | extensão |
|---|---|---|
| fogo | EPSG:4326 | −73,991 a −66,623 / −11,146 a −7,112 |
| bio_1 | EPSG:4326 | −73,992 a −66,617 / −11,150 a −7,108 |

Sondando 2000 pontos do raster de fogo contra o `bio_1`: **2000 caem dentro,
2000 em célula válida**. Quando os CRS diferem, a conversão em memória é
aplicada e testada (`test_sondagem_detecta_referencia_de_outra_area`).

**Distância em dado geográfico — está correta.** Testei as quatro combinações
sobre o reservatório real de 200 000 candidatos do Acre:

| configuração | amostras obtidas |
|---|---|
| sem distância, sem filtro | 1000 |
| distância 1000 m, sem filtro | 1000 (74 recusas) |
| sem distância, com filtro | 1000 (20 recusas) |
| distância 1000 m, com filtro | 1000 (74 recusas) |

A distância é medida em coordenadas geocêntricas (ECEF, EPSG:4978), que não
dependem de projeção nenhuma.

---

## Erro 1 — exigir validade em **todos** os preditores (aba 6)

O passo 5 recebia a *pasta* de preditores e exigia que o ponto tivesse valor
válido em **todos** eles. Com os 25 rasters de `Presente`:

| preditor | aprovação isolada | interseção acumulada |
|---|---|---|
| 19 bioclim | 100 % | 2000 |
| + `Dist_AGRO` | 41,9 % | 838 |
| + `Dist_NVEGET` | 99,4 % | 826 |
| + `Dist_URBAN` | 100 % | 826 |
| + `Dist_VEGET` | 64,3 % | 113 |
| + `Dist_WATER` | 94,3 % | **0** |

Os vazios de NaN dos rasters de distância são complementares: cada um sozinho
aprova a maioria, mas a interseção dos cinco não sobra nada. Daí o **0 de 1000**.

**Correção:** o passo 5 passa a receber **um** raster de referência (botão de
arquivo, não de pasta). É ele que define a grade válida e a deduplicação por
célula. Prefira um bioclimático ou o de uso do solo.

## Erro 2 — a aba **Reamostrar** destruía os rasters de distância

Esta é a causa raiz dos vazios do erro 1. A opção **"Converter zeros (0) para
NA"** vinha **marcada** por padrão, herdada do app em R, onde `0` significa
ausência de dado nos rasters do MapBiomas.

Em raster de **distância**, `0` é o valor mais informativo: significa "dentro da
classe". Convertê-lo em NA apaga justamente as áreas de interesse — e, com
reamostragem bilinear de 30 m para 1 km, cada NA contamina as células vizinhas.

Medido nos arquivos reais:

| raster | zeros na entrada | válido na entrada | válido na saída | esperado |
|---|---|---|---|---|
| `Dist_AGRO` | 6,9 % | 100 % | 38,5 % | 45,2 % |
| `Dist_NVEGET` | 0,1 % | 100 % | 45,2 % | 45,2 % |
| `Dist_URBAN` | 0,1 % | 100 % | 45,2 % | 45,2 % |
| **`Dist_VEGET`** | **38,2 %** | 100 % | **7,2 %** | 45,2 % |
| `Dist_WATER` | 0,2 % | 100 % | 45,1 % | 45,2 % |

Os dois rasters com muitos zeros são exatamente os dois que perderam dados. E
`Dist_VEGET` caiu para 7,2 % — a vegetação cobre quase todo o Acre, então
quase todo o estado tinha distância 0.

**Correções:**

1. a opção passa a vir **DESMARCADA por padrão**, tanto na interface quanto na
   assinatura de `resample_raster`. Marque-a só quando o zero realmente
   significar ausência de dado (máscaras do MapBiomas);
2. `resample_raster` passa a **devolver estatísticas** (zeros convertidos, % da
   grade válida na saída);
3. a aba registra no log quanto da grade sobrou em cada arquivo e **avisa**
   quando ≥ 5 % dos pixels de entrada eram zero ou quando a saída ficou com
   < 10 % de grade válida;
4. o aviso sobe até a caixa de diálogo final — antes a perda passava em silêncio;
5. o texto de ajuda ao lado da opção explica quando marcá-la.

**Para refazer os seus arquivos:** basta reprocessar os `Dist_*` na aba
Reamostrar — a opção já vem desmarcada.

## Erro 3 — "Origem divergente" em rasters perfeitamente alinhados (aba 5)

Reamostrar contra `Presente/AC_wc2.1_30s_bio_1.tif` e conferir contra
`HadGEM/126/Hadgem3_126_21_40_Bio1.tif` acusava **ERRO — Origem divergente**
nos seis arquivos, embora as grades sejam idênticas.

| | canto X |
|---|---|
| modelo da reamostragem (`Presente/...`) | `-73.99166666666667` |
| modelo da verificação (`HadGEM/126/...`) | `-73.99166666666666` |

A diferença é de **1,4·10⁻¹⁴ grau** — cerca de 1,5 nanômetro no terreno. São
dois arquivos que descrevem a mesma grade, produzidos por ferramentas
diferentes. Confirmei que o gravador da aba Reamostrar preserva a transform do
modelo **bit a bit**; o defeito estava só na comparação.

`RasterMeta.origin` reproduz o `terra::origin()`: a **fase** da grade, isto é,
o resto da divisão do canto pela resolução. Fase é grandeza **circular** — fase
≈ 0 e fase ≈ resolução descrevem o mesmo alinhamento, são os dois extremos do
mesmo intervalo. A verificação comparava com `abs(a - b)`:

```
fase do modelo   = 5,6e-15        (logo acima de 0)
fase do arquivo  = 0,0083333333   (logo abaixo de res)
diferença medida = 0,0083333333   → uma célula inteira → "Origem divergente"
```

Bastava um *ulp* de ruído no canto para a verificação acusar erro.

**Correções:**

1. nova função `diferenca_de_fase(a, b, res)`, que mede a distância
   **circular** entre duas fases;
2. as tolerâncias passam a ser **relativas à resolução** (`tol_pixel`, padrão um
   milionésimo de pixel). Uma tolerância absoluta não serve: `1e-6` vale 11 cm
   num raster em graus e 1 µm num raster em metros;
3. nova propriedade `RasterMeta.canto`, com a coordenada real do canto superior
   esquerdo — `origin` continua sendo a fase, agora documentada como circular;
4. a mensagem de erro passa a dizer **quanto** e **onde**: `Origem divergente
   (deslocamento de 0.5 x 0 pixel; canto -73.98704343,-7.11304008 !=
   -73.99166667,-7.10833333)`.

Resultado nos seus arquivos: os seis passaram a dar **OK**, e um raster
realmente desalinhado (`Dist_AGRO.tif` a 30 m) continua sendo reprovado.

## Erro 4 — a máscara do raster modelo não agia com NoData NaN

Em `resample_raster`, a máscara final era `model_data != model_nodata`. Em
Python, `x != nan` é **sempre verdadeiro** — então, quando o raster modelo tem
NoData igual a NaN, a máscara não mascarava nada. Não afetou o seu caso (o
bio_1 usa −3,4e38), mas afetaria quem usasse um modelo com NaN.

**Correção:** detecção explícita de NaN com `np.isfinite`.
Teste: `test_modelo_com_nodata_nan_agora_mascara`.

## Erro 5 — "0 de 1000" com mensagem de sucesso

A caixa final dizia *"Os pontos foram filtrados contra os preditores: caem em
célula válida e não repetem célula"* mesmo com 0 amostras — afirmando sucesso
justamente quando nada saiu.

**Correções:**

1. com 0 amostras a caixa passa a ser de **aviso**, não de informação, e traz o
   **motivo dominante** de recusa mais sugestões concretas;
2. novo botão **"Conferir referência"** no passo 5: sonda o raster escolhido
   contra o de fogo **antes** de processar e informa CRS, taxa de pontos dentro,
   taxa em célula válida e número de células distintas;
3. a sondagem roda também no início da amostragem e seus avisos entram no log,
   no relatório e na caixa final.

---

## Verificação

```
pytest tests -q                        45 passam
tests/testes_amostragem_fogo.py       206 verificações passam
```

Validação com os dados reais do Acre:

| cenário | presenças | ausências |
|---|---|---|
| 25 preditores da pasta (como estava) | 0 de 1000 | 0 de 1000 |
| um raster de referência (`bio_1`) | **1000 de 1000** | **1000 de 1000** |

Saída conferida contra o contrato do SDM_APP: sem BOM, colunas `x,y,Value`,
nenhum ponto em NoData, 1000 células distintas em cada classe.

Reamostragem do `Dist_VEGET`: com a opção marcada, 7,2 % da grade útil
(156 213 306 zeros convertidos); desmarcada, 45,3 % — que é exatamente a
máscara do estado.

Testes novos desta correção:

- `test_reamostragem_zeros.py` — reproduz a destruição do raster de distância,
  confirma que desmarcar a opção o preserva, que o aviso é emitido e que o
  padrão (função e caixa da tela) é **desmarcado**;
- `test_sondagem_detecta_referencia_de_outra_area` — raster de outra área é
  apontado de imediato;
- `test_zero_amostras_explica_o_motivo` — 0 amostras vem com diagnóstico;
- `test_referencia_unica_e_nao_a_pasta` — o passo 5 recebe um arquivo;
- `test_alinhamento_fase.py` — reproduz o "Origem divergente" com cantos
  separados por um ulp, confere que meio pixel de deslocamento continua sendo
  erro, e que a tolerância acompanha a unidade do CRS.
