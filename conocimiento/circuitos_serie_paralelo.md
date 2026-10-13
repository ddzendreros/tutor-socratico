# Circuitos en Serie, Paralelo y Reducción

## Resistencias en serie
id: resistencias_serie
claves: resistencias en serie, circuito serie, req serie, suma de resistencias, r_eq = r1 + r2
fuente: 1 MATERIAL DE CLASE Tema 1y2.pdf, p. 5
Dos o más resistencias están conectadas en serie cuando están unidas una tras otra compartiendo un solo nodo exclusivo por el que no entra ni sale otra corriente. Por lo tanto, la corriente eléctrica que circula a través de todos los elementos en serie es exactamente la misma. La resistencia equivalente se calcula sumando algebraicamente los valores individuales: Req = R1 + R2 + ... + Rn.

## Resistencias en paralelo
id: resistencias_paralelo
claves: resistencias en paralelo, circuito paralelo, req paralelo, inversos, 1/req = 1/r1 + 1/r2
fuente: 1 MATERIAL DE CLASE Tema 1y2.pdf, p. 6
Dos o más resistencias se encuentran conectadas en paralelo cuando sus terminales están unidas entre el mismo par de nodos comunes. Todos los elementos en paralelo experimentan exactamente la misma caída de tensión o voltaje. La resistencia equivalente se calcula sumando los inversos de las resistencias: 1 / Req = 1 / R1 + 1 / R2 + ... + 1 / Rn, resultando un valor siempre menor que la menor de las resistencias conectadas.

## Reducción de resistencias
id: reduccion_resistencias
claves: reduccion de resistencias, simplificacion de resistencias, r_t1, r_t2, resistencia equivalente, simplificar circuito, rt1, rt2, rt3
fuente: 3 MATERIAL DE CLASE Tema 3.pdf, p. 1
La reducción de resistencias consiste en simplificar un circuito combinando asociaciones consecutivas de resistores en serie o en paralelo hasta obtener una resistencia equivalente total. En el método del profesor se designan las combinaciones intermedias con nombres ordenados como R_T1, R_T2, R_T3... a medida que se calculan. Esto permite redibujar el circuito paso a paso reduciendo su complejidad de manera ordenada.

## Resistencia en corto
id: resistencia_en_corto
claves: resistencia en corto, cortocircuito, cable en paralelo, eliminar resistencia, r = 0, elemento en corto
fuente: 3 MATERIAL DE CLASE Tema 3.pdf, p. 18
Un elemento en corto-circuito ocurre cuando un conductor ideal de resistencia cero (R = 0) une directamente los dos extremos de una resistencia o conjunto de resistencias. Toda la corriente eléctrica circula preferentemente a través del conductor sin resistencia, por lo que la corriente que pasa por la resistencia es cero y no disipa potencia. En consecuencia, la resistencia queda anulada y se elimina del circuito para el cálculo de la resistencia equivalente.

## Conversión delta-estrella
id: conversion_delta_estrella
claves: conversion delta estrella, delta estrella, estrella delta, transformacion delta estrella, triangulo a estrella, delta-y
fuente: 3 MATERIAL DE CLASE Tema 3.pdf, p. 25
La conversión delta-estrella (Δ-Y) o estrella-delta (Y-Δ) se utiliza cuando un conjunto de tres resistencias se encuentra conectado de tal manera que no están ni en serie ni en paralelo. Permite transformar matemáticamente una red de tres terminales en forma de triángulo (delta) en una red equivalente con un nodo central común (estrella), o viceversa. Al realizar esta conversión se destraba el circuito y se puede continuar con la reducción serie-paralelo convencional.

## Verificación con simulador
id: verificacion_simulador
claves: verificacion con simulador, simulador, multisim, phet, instrumentos virtuales, comprobacion de circuitos
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 35
La verificación con un simulador de circuitos (como Multisim o PhET) consiste en construir el esquema eléctrico en un entorno virtual interactivo para medir sus voltajes y corrientes con instrumentos como voltímetros y amperímetros. Permite comparar los resultados numéricos obtenidos mediante cálculos analíticos con las lecturas arrojadas por la simulación computacional. Esta comprobación práctica ayuda a detectar inconsistencias algebraicas y a consolidar la comprensión física del circuito.
