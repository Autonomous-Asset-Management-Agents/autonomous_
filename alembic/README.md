# Alembic — GCP Cloud SQL Schema Migrations

This directory contains all database schema migrations for the **GCP Cloud SQL PostgreSQL** instance.

## Struktur

```
alembic/
├── env.py                     # Async asyncpg setup, reads DATABASE_URL from env
├── script.py.mako              # Migration template
└── versions/
    └── 0001_initial_cloud_sql_schema.py  # Initial schema (alle 6 Tabellen)
```

## Migration ausführen

**Voraussetzung:** `DATABASE_URL` muss gesetzt sein (Secret Manager in Prod, `.env` lokal).

```bash
# Schema auf neuestem Stand bringen
alembic upgrade head

# Neue Migration erzeugen (nach ORM-Änderungen)
alembic revision --autogenerate -m "Beschreibung der Änderung"

# Status prüfen
alembic current
alembic history
```

## Tabellen

| Tabelle | Zweck | Regulierung |
|---|---|---|
| `decisions` | Trading-Entscheidungen | MiFID II Art. 25 |
| `trades` | Ausgeführte Trades (FK → decisions) | MiFID II Art. 16 |
| `ai_thoughts` | AI Reasoning Traces (JSONB + GIN Index) | EU AI Act |
| `risk_events` | Risiko-Events + Performance-Metriken | Intern |
| `mifid_decision_log` | Compliance Append-Only Audit | MiFID II Art. 16 |
| `portfolio_snapshots` | Portfolio-State Snapshots | Intern |

## Hinweis: Append-Only für MiFID II

Die Tabelle `mifid_decision_log` ist logisch append-only. Da Cloud SQL keine Row-Level-Security
wie Cloud SQL kennt, wird dies durch **IAM-Berechtigungen** auf Service-Account-Ebene gesichert:
Der `trading-bot-sa` Service Account erhält nur `INSERT` und `SELECT` Rechte auf diese Tabelle.
`UPDATE` und `DELETE` werden ihm nicht gewährt.
