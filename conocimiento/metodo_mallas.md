# Método de Corrientes de Malla (Mallas)

## Malla
id: malla
claves: malla, definicion de malla, ventana, camino cerrado, lazo simple
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 2
Una malla es una trayectoria o lazo cerrado en un circuito eléctrico que no encierra ningún otro lazo o camino cerrado en su interior. En una representación gráfica de un circuito plano equivale a una 'ventana' o espacio cerrado indivisible. Es el concepto central para definir las corrientes ficticias de circulación en el método de mallas.

## Lazo vs malla
id: lazo_vs_malla
claves: lazo vs malla, diferencia lazo y malla, lazo cerrado, lazo que encierra otros lazos, lazo y malla
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 2
Un lazo es cualquier trayectoria cerrada que parte de un nodo y regresa al mismo nodo pasando por cada elemento a lo más una vez, pudiendo contener otros lazos o mallas dentro de él. En cambio, una malla es un lazo elemental que no contiene ningún otro camino cerrado en su interior. Por esta razón, toda malla es necesariamente un lazo, pero no todo lazo constituye una malla.

## Sentido de corriente FMR y CMR
id: sentido_corriente_fmr_cmr
claves: fmr, cmr, sentido de corriente de malla, manecillas del reloj, sentido horario, sentido antihorario
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 2
En el método de mallas de formato se asigna un sentido uniforme de circulación a todas las corrientes de malla del circuito. La convención indicada por el profesor es asignar todas las corrientes a FMR (a Favor de las Manecillas del Reloj, sentido horario) o bien todas en CMR (en Contra de las Manecillas del Reloj). Adoptar el mismo sentido en todas las mallas garantiza que las resistencias compartidas aparezcan sistemáticamente con signo negativo en los términos de acoplamiento.

## Malla real
id: malla_real
claves: malla real, caso 1 mallas, sin fuente de corriente, ecuacion de malla real, lvk en malla
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 4
Una malla real es aquella que no contiene ninguna fuente de corriente en ninguna de sus ramas, ya sean propias o compartidas. Para una malla real se plantea una ecuación general aplicando LVK: la corriente propia multiplica a la suma de todas las resistencias de la malla, menos las corrientes vecinas multiplicadas por las resistencias compartidas, igual a la suma algebraica de las fuentes de voltaje de la malla.

## Malla fantasma
id: malla_fantasma
claves: malla fantasma, caso 2 mallas, fuente de corriente en rama exterior, fuente de corriente directa, im = if
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 6
Una malla fantasma se presenta cuando una fuente de corriente alimenta directamente a una malla a través de una rama perimetral o no compartida. En esta situación, la corriente de dicha malla toma de manera inmediata el valor de la fuente de corriente: I_M = I_fuente (con signo positivo si coincide con el sentido asignado o negativo si es opuesto). Al ser una corriente ya conocida, no se necesita formular una ecuación de voltajes con LVK para esa malla.

## Supermalla
id: supermalla
claves: supermalla, caso 3 mallas, fuente de corriente compartida, fuente entre dos mallas
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 7
Una supermalla se forma cuando una fuente de corriente se encuentra compartida entre dos mallas adyacentes. Como el voltaje en los extremos de una fuente de corriente es una incógnita a priori, no es posible aplicar la LVK de forma independiente en cada una de esas dos mallas. Para solucionar el circuito se crea una supermalla que engloba a ambas mallas, requiriendo el planteamiento de una ecuación auxiliar y una ecuación real.

## Ecuación de supermalla
id: supermalla_ecuacion
claves: ecuacion de supermalla, pasos supermalla
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 7
Para plantear la solución de una supermalla se obtienen dos ecuaciones. La primera es la ecuación auxiliar, que relaciona las dos corrientes de malla con el valor de la fuente de corriente compartida mediante una resta algebraica: I_b - I_a = I_fuente. La segunda es la ecuación real, que se formula aplicando la ley de voltajes de Kirchhoff (LVK) a lo largo del contorno exterior que rodea a ambas mallas, omitiendo temporalmente la rama compartida que aloja a la fuente de corriente.

## Ecuación auxiliar
id: ecuacion_auxiliar
claves: ecuacion auxiliar, ecuacion auxiliar de supermalla, relacion de corrientes, ib - ia = ifuente
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 7
La ecuación auxiliar es la expresión algebraica que vincula a las corrientes de dos mallas contiguas que comparten una fuente de corriente. Se obtiene observando el sentido de la fuente de corriente compartida: la corriente de malla que fluye en el mismo sentido que la fuente menos la que circula en sentido contrario es igual al valor de la fuente: I_favor - I_contra = I_fuente. Esta ecuación proporciona la relación complementaria necesaria para igualar el número de ecuaciones con el de incógnitas.

## Ecuación real
id: ecuacion_real
claves: ecuacion real, ecuacion real supermalla, lvk exterior, lazo exterior supermalla
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 7
La ecuación real de una supermalla es la ecuación de voltajes que se plantea aplicando LVK alrededor del lazo combinado exterior formado por las dos mallas involucradas, ignorando la rama compartida que contiene la fuente de corriente. En ella se incluyen todas las caídas de tensión en las resistencias perimetrales y las fuentes de voltaje presentes a lo largo de esa trayectoria cerrada. Posteriormente se sustituye la ecuación auxiliar en la ecuación real para resolver el sistema de corrientes.

## Rama compartida y corriente neta
id: rama_compartida_corriente_neta
claves: rama compartida, corriente neta, diferencia de corrientes de malla, i1 - i2, i2 - i1, sentido opuesto
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 5
Una rama compartida es aquella que divide a dos mallas adyacentes y por cuya resistencia circulan simultáneamente dos corrientes de malla en sentidos contrarios. La corriente neta que realmente atraviesa dicha resistencia se calcula como la diferencia algebraica entre ambas corrientes de malla: I_neta = I1 - I2 o I_neta = I2 - I1 según el sentido que se tome como referencia. Con esa corriente neta se determina la caída de voltaje en la resistencia (V = I_neta · R) y su potencia disipada.

## Tabla V-I-P
id: tabla_vip
claves: tabla v-i-p, tabla vip, tabla v i p, resumen de circuito, v = i*r, p = v*i, calculo de potencia y voltaje
fuente: 4 MATERIAL DE CLASE Tema 4 Mallas.pdf, p. 5
La tabla V-I-P es el formato organizado que utiliza el profesor para registrar la solución completa de un circuito eléctrico. En ella se tabulan todos los resistores y fuentes, indicando para cada elemento su valor de resistencia (R en ohms), corriente (I en amperes), voltaje (V = I·R en volts) y potencia (P = V·I en watts). Además, en la tabla se calculan la potencia total entregada (PE) y la potencia total consumida (PC) para comprobar que PE = PC.
