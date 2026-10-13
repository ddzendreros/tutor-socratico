# Método de Voltajes de Nodos (Nodos)

## Nodo
id: nodo
claves: nodo, definicion de nodo, punto de union, terminales, conexion comun
fuente: 5 MATERIAL DE CLASE Tema 5 NODOS.pdf, p. 2
Un nodo es el punto o región de un circuito eléctrico donde convergen y se unen dos o más terminales de elementos conductores o componentes. Todas las partes conductoras que están unidas por alambres ideales sin resistencia forman parte del mismo nodo y se encuentran al mismo potencial eléctrico. Es la entidad sobre la cual se aplica de manera fundamental la ley de corrientes de Kirchhoff (LCK).

## Nodo de referencia
id: nodo_referencia
claves: nodo de referencia, tierra, masa, vref = 0, potencial cero, eleccion de referencia
fuente: 5 MATERIAL DE CLASE Tema 5 NODOS.pdf, p. 3
El nodo de referencia o tierra es el nodo del circuito que se elige convencionalmente como origen del potencial eléctrico, asignándole un voltaje de cero volts: V_REF = 0 V. Todos los voltajes nodales (V1, V2...) se definen y miden con respecto a este punto. Conviene seleccionar como nodo de referencia aquel que tenga conectadas fuentes de voltaje o el mayor número de ramas, para simplificar el sistema de ecuaciones.

## Nodo real
id: nodo_real
claves: nodo real, caso 1 nodos, sin fuente de voltaje, alimentado por fuente de corriente, ecuacion de nodo real, lck en nodo
fuente: 5 MATERIAL DE CLASE Tema 5 NODOS.pdf, p. 3
Un nodo real es el que no tiene ninguna fuente de voltaje conectada: está alimentado por una fuente de corriente o no tiene fuente (si tuviera una fuente de voltaje hacia la referencia sería fantasma, y hacia otro nodo, supernodo). Para cada nodo real se plantea una ecuación de formato aplicando LCK: el voltaje del nodo multiplica a la suma de los inversos de las resistencias conectadas a él, menos los voltajes de los nodos vecinos multiplicados por los inversos de las resistencias compartidas, igual a la suma algebraica de corrientes de las fuentes que entran menos las que salen.

## Nodo fantasma
id: nodo_fantasma
claves: nodo fantasma, caso 2 nodos, fuente de voltaje con referencia, voltaje conocido, v_nodo = v_fuente
fuente: 5 MATERIAL DE CLASE Tema 5 NODOS.pdf, p. 11
Un nodo fantasma se presenta cuando existe una fuente de voltaje conectada directamente entre dicho nodo y el nodo de referencia. Debido a esta conexión directa, el voltaje de ese nodo queda fijado inmediatamente por el valor y la polaridad de la fuente: V_nodo = V_fuente (o -V_fuente según la orientación). Al ser un voltaje ya conocido desde el inicio, no se formula una ecuación nodal con LCK para ese nodo.

## Supernodo
id: supernodo
claves: supernodo, caso 3 nodos, fuente de voltaje entre dos nodos, fuente sin nodo de referencia
fuente: 5 MATERIAL DE CLASE Tema 5 NODOS.pdf, p. 15
Un supernodo se forma cuando una fuente de voltaje está conectada entre dos nodos y ninguno de ellos es el nodo de referencia. Puesto que no se conoce la corriente que circula a través de la fuente de voltaje, no es factible aplicar la LCK a esos nodos de manera individual. Para resolver el circuito se encierra la fuente de voltaje y ambos nodos en una superficie cerrada combinada (supernodo), requiriendo una ecuación de restricción y una ecuación nodal del supernodo.

## Ecuación de supernodo
id: supernodo_ecuacion
claves: ecuacion de supernodo, ecuacion auxiliar supernodo, pasos supernodo
fuente: 5 MATERIAL DE CLASE Tema 5 NODOS.pdf, p. 15
Para resolver un supernodo se formulan dos ecuaciones complementarias. Primero se escribe la ecuación de restricción o auxiliar, que relaciona los voltajes de los dos nodos con el valor de la fuente de voltaje que los une: V_positivo - V_negativo = V_fuente. Segundo, se plantea la ecuación de LCK para el supernodo, sumando todas las corrientes que salen de ambos nodos a través de las resistencias hacia los nodos vecinos e igualando a las corrientes de fuentes que entran a dicha frontera cerrada.
