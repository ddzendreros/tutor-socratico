# Teoremas de Circuitos, Divisores, Thévenin y Norton

## Divisor de voltaje
id: divisor_voltaje
claves: divisor de voltaje, divisor de tension, serie, v_rx = vt * rx / rt, caida de voltaje
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 2
El divisor de voltaje permite calcular directamente la caída de tensión en cualquier resistencia que forme parte de una conexión en serie a partir del voltaje total aplicado. Su fórmula general es V_R1 = V_T · (R1 / R_T), donde V_T es el voltaje total suministrado a la serie y R_T es la suma de las resistencias de dicha serie. Esta relación demuestra que el voltaje total se reparte de forma directamente proporcional al valor de cada resistencia.

## Divisor de corriente
id: divisor_corriente
claves: divisor de corriente, formula del divisor de corriente, paralelo, i_r1 = it * r2 / (r1 + r2), division de corriente
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 4
El divisor de corriente permite determinar la corriente que fluye por una rama determinada en una conexión en paralelo conociendo la corriente total que ingresa al nodo. Para dos resistencias en paralelo la expresión es I_R1 = I_t · (R2 / (R1 + R2)), multiplicando por la resistencia opuesta dividida entre la suma de ambas. Para más de dos ramas se aplica I_Rx = I_t · (Req / Rx), dividiéndose la corriente de forma inversamente proporcional a la resistencia de cada rama.

## Teorema de superposición
id: superposicion
claves: teorema de superposicion, superposicion de efectos, fuentes independientes, suma algebraica
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 6
El teorema de superposición estipula que en cualquier red eléctrica lineal con dos o más fuentes independientes, la corriente o el voltaje en cualquier elemento es igual a la suma algebraica de las respuestas producidas por cada fuente actuando de forma aislada. Para aplicarlo se analiza el circuito activando una sola fuente independiente a la vez mientras se desactivan todas las demás fuentes independientes. Finalmente, los efectos parciales obtenidos para cada caso se suman algebraicamente tomando en cuenta sus sentidos y polaridades.

## Desactivar fuentes de voltaje en superposición
id: desactivar_fuentes_voltaje
claves: desactivar fuentes de voltaje, apagar fuentes de voltaje, cortocircuito, v = 0v, reemplazar por corto, apagar fuente de voltaje, quitar fuente de voltaje
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 8
Para desactivar o apagar una fuente de voltaje independiente durante la aplicación del teorema de superposición, se ajusta su voltaje a cero volts (0 V). Físicamente esto equivale a sustituir la fuente de voltaje por un cortocircuito, es decir, un conductor ideal de resistencia nula que conecta directamente sus dos terminales.

## Desactivar fuentes de corriente en superposición
id: desactivar_fuentes_corriente
claves: desactivar fuentes de corriente, apagar fuentes de corriente, circuito abierto, i = 0a, terminales abiertas, apagar fuente de corriente, apago la fuente, quitar fuente de corriente
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 8
Para desactivar o apagar una fuente de corriente independiente en el análisis por superposición, se fija su corriente en cero amperes (0 A). Físicamente esto equivale a reemplazar la fuente por un circuito abierto, dejando las dos terminales completamente desconectadas e interrumpiendo el flujo de corriente por esa rama.

## Teorema de Thévenin
id: teorema_thevenin
claves: teorema de thevenin, circuito equivalente thevenin, vth en serie con rth, c.e. thevenin
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 17
El teorema de Thévenin establece que cualquier circuito lineal y activo provisto de un par de terminales abiertas (A y B) puede sustituirse por un circuito equivalente Thévenin formado por una sola fuente de voltaje (Vth) conectada en serie con una resistencia (Rth). El voltaje Vth corresponde al voltaje de circuito abierto medido entre las terminales A y B (Vth = V_AB). La resistencia Rth es la resistencia equivalente vista desde las terminales A y B cuando todas las fuentes independientes están apagadas (Rth = R_AB).

## Voltaje de Thévenin
id: voltaje_thevenin
claves: voltaje de thevenin, vth, vab, voltaje en circuito abierto, terminales a y b abiertas
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 17
El voltaje de Thévenin (Vth) es la diferencia de potencial que aparece entre las terminales abiertas A y B del circuito original antes de conectar cualquier carga externa (Vth = V_AB). Se calcula resolviendo el circuito completo mediante cualquier método analítico (como mallas, nodos, divisor de voltaje o LVK) para determinar el voltaje presente entre dichas terminales en circuito abierto.

## Resistencia de Thévenin
id: resistencia_thevenin
claves: resistencia de thevenin, rth, rab, resistencia equivalente thevenin, apagar fuentes
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 17
La resistencia de Thévenin (Rth) es la resistencia equivalente que presenta la red observada desde el par de terminales abiertas A y B con todas las fuentes independientes apagadas. Para calcularla se reemplazan las fuentes de voltaje independientes por cortocircuitos y las fuentes de corriente independientes por circuitos abiertos, y posteriormente se reduce la red resistiva resultante en combinaciones serie y paralelo hasta encontrar R_AB = Rth.

## Teorema de Norton
id: teorema_norton
claves: teorema de norton, circuito equivalente norton, in en paralelo con rn, c.e. norton
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 18
El teorema de Norton establece que cualquier circuito lineal y activo con un par de terminales (A y B) puede sustituirse por un circuito equivalente Norton compuesto por una sola fuente de corriente (In) conectada en paralelo con una resistencia (Rn). La corriente In es la corriente que fluye entre las terminales A y B cuando se colocan en cortocircuito (In = I_AB). La resistencia Norton es idéntica a la resistencia de Thévenin: Rn = Rth = R_AB.

## Corriente de Norton
id: corriente_norton
claves: corriente de norton, in, iab, corriente de cortocircuito, relacion thevenin norton
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 18
La corriente de Norton (In) es la corriente que circula a través de un cortocircuito ideal colocado entre las terminales A y B del circuito original (In = I_AB). Se calcula aplicando análisis de mallas, nodos o ley de Ohm con un conductor de resistencia nula entre A y B. Se relaciona directamente con el equivalente Thévenin mediante la ley de Ohm: In = Vth / Rth (o Vth = In · Rn).

## Resistencia R_AB
id: resistencia_rab
claves: resistencia rab, terminales ab, resistencia equivalente entre terminales, rth = rn = rab, resistencia de salida
fuente: 6 MATERIAL DE CLASE Divisor de VyI Superposición Thevenin y Norton.pdf, p. 20
La resistencia R_AB representa la resistencia equivalente observada hacia el interior del circuito desde el par de terminales A y B cuando se han apagado todas las fuentes independientes. Este valor es exactamente el mismo para la resistencia de Thévenin y la resistencia de Norton (R_AB = Rth = Rn). Su obtención se realiza reduciendo las combinaciones en serie y en paralelo vistas desde las terminales A y B hacia la red pasivada.
