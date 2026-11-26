# 5. Alucinación de catálogo

```bash
task scenario:catalog-hallucination
```

**Qué enseña.** Un fallo de calidad de datos del RAG no produce errores. La respuesta parece buena y es falsa: hace falta ver los documentos recuperados y una eval de fidelidad.

## Cómo se provoca

Dos fases: el RAG devuelve **vacío** (`rag_mode: empty`) y después **productos al azar** (`rag_mode: irrelevant`). Las peticiones piden productos concretos con precio.

Definición: [`scenarios/src/wonder_scenarios/definitions/catalog-hallucination.yaml`](../../scenarios/src/wonder_scenarios/definitions/catalog-hallucination.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Span **RETRIEVER `catalog.vector_search`** (servicio `wonder-toys-mcp`): la lista de documentos recuperados está vacía, o contiene productos que no tienen que ver con la consulta. Atributo `wonder.rag.mode`.
2. Eval **catalog_faithfulness**: `hallucinated` si la respuesta presenta productos o precios que no están en los resultados de las herramientas, o presenta los irrelevantes como si encajasen.
3. Eval **correctness** baja en la fase `rag-irrelevant`.
4. Filtra por metadato `scenario_phase` para comparar las dos fases.

## Qué mirar en Dynatrace

1. **Nada anómalo**: ChromaDB y Postgres responden, sin errores ni latencia.
2. El contenido de los documentos no llega a Dynatrace (está redactado en el colector); solo se ve que la búsqueda se hizo.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-mcp" and span.name == "catalog.vector_search"
| fields start_time, duration, wonder.rag.mode, wonder.rag.documents
```

## Reversión

El runner borra el caos entre fases y al terminar; además caduca solo.
