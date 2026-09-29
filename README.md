# Tutor Socrático · Circuitos Eléctricos

Tutor que guía al estudiante con preguntas y pistas graduadas (escalera de andamiaje 0–5) para
resolver problemas de circuitos de CD: Ley de Ohm y potencia, serie/paralelo, mallas (con mallas
fantasma y supermallas) y nodos (con nodos fantasma y supernodos). Sigue el **método de formato**
que se usa en clase y funciona con costo **$0**: corre en el navegador de cada alumno.

## Idea central

El tutor conoce la solución exacta de cada circuito, pero no la entrega: revisa cada paso del
alumno contra esa solución, identifica el error concreto y decide la mínima ayuda necesaria.

| Capa | Qué hace |
|---|---|
| **Motor de circuitos** (`solver.py`, `analysis.py`) | Resuelve de forma exacta cualquier circuito DC resistivo (análisis nodal modificado con SymPy) y construye las ecuaciones de referencia de mallas y nodos, con los nombres de clase: ecuación directa, ecuación auxiliar, ecuación real, supernodo. |
| **Diagnóstico** (`diagnose.py`, `reduction.py`) | Compara cada ecuación o valor del alumno con la referencia y con las ecuaciones que producirían los errores típicos: sumar corrientes en la rama compartida, invertir la polaridad de una fuente, usar R en vez de 1/R, sumar resistencias en paralelo, combinar resistencias que no están ni en serie ni en paralelo… |
| **Política pedagógica** (`engine.py`, `data/banco_errores.csv`) | Plan de pasos, escalera de ayuda, modos (Aprender, Practicar, Verificar, Simulador), solicitud de justificación, verificación con balance de potencias o simulador, reflexión final e indicadores de la interacción. |

Las decisiones pedagógicas son deterministas: todos los alumnos reciben la misma intervención ante
el mismo error, lo que permite comparar resultados en la investigación. Un modelo de lenguaje es
opcional y sólo se usa en la versión con servidor (ver *Modelo de lenguaje*).

## Cómo sigue el método de clase

- **Mallas:** el alumno cuenta y describe las mallas, las clasifica (real, fantasma o supermalla),
  identifica la rama compartida y su corriente neta, plantea las ecuaciones en el orden de clase
  (directa o auxiliar primero, después la real) y puede escribirlas en forma simbólica,
  `I1(R1 + R2) − I2R2 = VX − VY`, o con valores, `I1(10 + 20) − I2(20) = 100 − 100`.
  Acepta sentido horario (FMR) o antihorario (CMR) mientras todas giren igual, y la notación
  `ia, ib, ic`.
- **Nodos:** clasificación (real, fantasma, supernodo), ecuación directa, ecuación de la fuente del
  supernodo y ecuación complementaria, en el formato `V1(1/4 + 1/2) − V2(1/2) = 5`.
- **Serie/paralelo:** reducción paso a paso con equivalentes intermedios R_T1, R_T2…; cada paso
  correcto se acepta aunque no sea el resultado final. Reconoce resistencias en corto.
- **Tabla de resultados:** ecuación de rama, I, V y P de cada resistencia.
- **Verificación:** balance de potencias PE = PC, sustitución o comparación con PhET/Multisim.

## Banco de problemas

Cada problema es un archivo de texto en `problemas/` que se puede editar sin programar:

```
TITULO: Mallas · Caso 1: malla real
TEMA: mallas
METODO: mallas
NIVEL: básico
FUENTE: Material de clase, Tema 4
ENUNCIADO: ...
PIDE: VIP
RESPUESTA: I1=1.82 I2=2.73

VX a 0 100          fuente de voltaje: el primer nodo es la terminal +
R1 a b 10           resistencia en Ω
R2 b m 20
VY m 0 100
R3 b 0 30
MALLA 1: VX R1 R2 VY    mallas como están dibujadas, en sentido horario
MALLA 2: VY R2 R3
```

- `PIDE`: `VIP` (tabla de V, I y P en cada resistencia), `Req VT` (la que ve la fuente),
  `Req A-0`, `I R2`, `V R1`, `P R3`, separados por `;`.
- `RESPUESTA`: resultados publicados; las pruebas verifican que el motor los reproduce.
- `NOTA`: comentario para el profesor; no se muestra al alumno.
- Las **mallas se declaran** porque una lista de elementos no dice cómo está dibujado el
  circuito: el mismo circuito admite varios dibujos, con distintas ventanas. El tutor valida que
  cada malla sea un lazo cerrado, que sean independientes, que haya B − N + 1 y que todas puedan
  girar en el mismo sentido.

Los alumnos también pueden capturar un problema propio con el mismo formato.

## Versión web (la que se usa en el salón)

El motor corre en el navegador con [Pyodide](https://pyodide.org), así que el sitio es estático:
no necesita servidor, atiende a cualquier número de alumnos a la vez y funciona en celular o en las
computadoras del laboratorio. La primera carga descarga unos 14 MB (Python, SymPy y NumPy); después
queda en la caché del navegador. El motor arranca en un hilo aparte (`web/worker.js`) para que la
página no se congele mientras tanto, y guarda en el dispositivo las bibliotecas ya compiladas, así
que a partir de la segunda visita arranca más rápido.

```bash
python tools/build_web.py                              # arma site/
python -m http.server 8765 --directory site            # http://localhost:8765
```

**Publicar en GitHub Pages ($0):** en el repositorio, *Settings > Pages > Source: GitHub Actions*.
Cada `push` a `main` corre las pruebas, arma el sitio y lo publica
(`.github/workflows/pages.yml`).

**Registro para la investigación:** ver [docs/REGISTRO.md](docs/REGISTRO.md). Cada sesión se
guarda en una hoja de cálculo de Google del profesor mediante Apps Script. La boleta no sale del
dispositivo: se envía su huella y el script la convierte en un código con una clave secreta.

## Versión con servidor (Gradio)

Útil para el profesor o para usar el extractor de enunciados con un modelo de lenguaje.

```bash
python -m venv .venv && .venv\Scripts\activate          # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                  # llenar PSEUDONYM_SECRET y TEACHER_PASSWORD
python app.py                                           # http://localhost:7860
```

El panel del profesor descarga `indicadores.csv` y `transcripciones.json`.

### Modelo de lenguaje

Opcional (`LLM_CHAIN` en `.env`): interpreta un enunciado en texto y lo convierte en la tabla del
circuito, que el alumno confirma. Con `LLM_REPHRASE=1` además reescribe los mensajes del tutor,
pasando por una guardia que bloquea resultados y ecuaciones pendientes; viene apagado para que la
intervención sea la misma para todos. Con Ollama el contenido no sale de la computadora.

## Pruebas

```bash
python -m pytest -q
```

- `tests/test_material.py`: cada problema del banco reproduce los resultados publicados en el
  material de clase.
- `tests/test_flow.py`: diálogos completos con la notación de clase.
- `tests/test_core.py`: solver, mallas y nodos contra MNA en 40 circuitos aleatorios, diagnóstico.

## Estructura

```
tutor/circuit.py     netlist, mallas declaradas, validación
tutor/solver.py      MNA exacto, resistencia equivalente, magnitudes pedidas
tutor/analysis.py    ecuaciones de mallas y nodos; tipos de malla y de nodo
tutor/mathparse.py   lectura de ecuaciones y valores: unidades, prefijos, ia/ib, forma simbólica
tutor/diagnose.py    ecuación o valor del alumno -> código de error
tutor/reduction.py   reducción serie/paralelo paso a paso
tutor/engine.py      plan de pasos, escalera 0–5, modos, indicadores
tutor/problems.py    banco de problemas
tutor/llm.py         cliente de modelos de lenguaje (opcional)
tutor/store.py       SQLite y seudonimización (versión con servidor)
web/                 página, hilo del motor, puente con el motor y configuración del sitio
problemas/           banco de problemas
data/                banco de errores (editable en Excel)
docs/                registro en Google Sheets
```

## Límites conocidos

- Sólo CD resistivo. Thévenin/Norton completos, superposición, fuentes dependientes y CA quedan
  para la etapa 2 (la resistencia vista desde dos terminales sí está disponible).
- La conversión delta-estrella se acepta por su resultado final, sin revisar los pasos intermedios.
- Las respuestas conceptuales abiertas se evalúan con palabras clave y son permisivas a propósito:
  en la duda, el tutor acepta y pide justificación.
