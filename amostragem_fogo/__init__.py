"""Amostragem da frequência de fogo — versão 5.

Pacote reestruturado em módulos independentes:

``config``      parâmetros validados da execução;
``blocos``      leitura do GeoTIFF em blocos agregados;
``analise``     histograma de frequências e tabela de limiares;
``geo``         coordenadas de pixel, CRS, WGS84 e distância métrica real;
``amostragem``  reservatório aleatório e seleção com distância mínima;
``exportacao``  CSV, GeoJSON, CSVT, PRJ e relatório;
``gui``         interface gráfica Tkinter;
``cli``         linha de comando.

Novidades da V5 em relação à V4:

* as amostras saem no formato ``x,y,Value`` (vírgula separando colunas, ponto
  decimal), compatível com MaxEnt, dismo e biomod2;
* cada arquivo de saída tem sua própria caixa de seleção.
"""

from __future__ import annotations

__version__ = "5.0.0"
APP_TITLE = "Amostragem de frequência de fogo — V5"

__all__ = ["__version__", "APP_TITLE"]
