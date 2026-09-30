# Datos del Problema 2.1 (Knockeos genéticos)

Los problemas `biomasa` y `knock` leen los tres archivos de material docente
desde esta carpeta. Cópialos aquí **antes del evento**, con estos nombres
exactos:

```
problems/_datos_knockeos/
    S_matriz.npy
    L.npy
    U.npy
```

Se montan read-only dentro del contenedor como `/data/knockeos/` (y la ruta
queda también en la variable de entorno `DATASET_KNOCKEOS`).

Si falta la carpeta, el panel de administración marca el problema con
«datasets no encontrados» y no deja ejecutar. Si falta alguno de los tres
archivos, el evaluador aborta con un mensaje claro apuntando al organizador.

La carpeta empieza por `_` para que el cargador de problemas no la interprete
como un problema.

## Índices que hay que confirmar

El enunciado indica que en el vector de flujos la reacción de **biomasa** es la
`1520` y la de **alcohol** la `472`, y que `eta = 0.288 * 0.4`. Esos valores
están en `[params]` de `problems/biomasa/manifest.toml` y
`problems/knock/manifest.toml`; verifícalos contra los datos reales y corrígelos
ahí si hace falta (también aparecen escritos en el enunciado que ven los
equipos, así que actualiza el `statement` si cambian).
