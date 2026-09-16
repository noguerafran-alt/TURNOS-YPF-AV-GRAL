# Maestro matrículas (versión final)

Archivo: `data/maestro-matriculas-combustible.xlsx` (sheet `BASE`)  
También: `data/maestro-aviones-version-final.xlsx`

**2105** matrículas únicas · JET A-1 **1699** · AVGAS 100LL **406** · 0 duplicados.

```bash
python -m scripts.import_maestro_matriculas --replace
```

Full replace del maestro anterior. Upserts en runtime (ABASTECIDO) siguen aparte.
