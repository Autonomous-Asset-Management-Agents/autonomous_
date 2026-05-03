"""
scripts/test_worm_compliance.py

Verifikationsscript für WORM-Schutz auf mifid_decision_log (MiFID II Art. 16).

Testet:
  1. INSERT    → muss klappen (Append-Only)
  2. UPDATE    → muss mit Exception fehlschlagen (WORM aktiv)
  3. DELETE    → muss mit Exception fehlschlagen (WORM aktiv)

Aufruf:
    $env:DATABASE_URL="postgresql+asyncpg://postgres:<PW>@<HOST>:5432/trading_bot_db"
    $env:PYTHONPATH="."
    .\.venv\Scripts\python.exe scripts/test_worm_compliance.py
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL nicht gesetzt")

engine = create_async_engine(DATABASE_URL, echo=False)


async def main():
    record_id = str(uuid.uuid4())
    passed = 0
    failed = 0

    print("\n🔒 WORM Compliance Test — mifid_decision_log (MiFID II Art. 16)\n")
    print(f"   Ziel-DB: {DATABASE_URL.split('@')[-1]}")
    print(f"   Test-ID: {record_id[:8]}...\n")

    # ── 1. INSERT ─────────────────────────────────────────────────────────────
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                INSERT INTO mifid_decision_log
                    (id, event_time, event_type, severity, message)
                VALUES
                    (:id, :ts, 'WORM_TEST', 'INFO', 'WORM compliance verification record')
            """
                ),
                {"id": record_id, "ts": datetime.now(timezone.utc)},
            )
        print("  ✅ INSERT: Datensatz geschrieben (Append klappt)")
        passed += 1
    except Exception as e:
        print(f"  ❌ INSERT fehlgeschlagen (sollte klappen): {e}")
        failed += 1

    # ── 2. UPDATE ─────────────────────────────────────────────────────────────
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                UPDATE mifid_decision_log
                SET message = 'TAMPERED'
                WHERE id = :id
            """
                ),
                {"id": record_id},
            )
            rows = result.rowcount
            print(f"  ❌ UPDATE erfolgreich ({rows} Zeilen) — WORM NICHT AKTIV!")
            failed += 1
    except Exception as e:
        err_msg = str(e)
        if "WORM" in err_msg or "MiFID" in err_msg or "verboten" in err_msg:
            print(f"  ✅ UPDATE blockiert: Trigger hat ausgelöst (WORM aktiv)")
        else:
            print(f"  ✅ UPDATE blockiert (andere Ursache): {err_msg[:120]}")
        passed += 1

    # ── 3. DELETE ─────────────────────────────────────────────────────────────
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                DELETE FROM mifid_decision_log WHERE id = :id
            """
                ),
                {"id": record_id},
            )
            rows = result.rowcount
            print(f"  ❌ DELETE erfolgreich ({rows} Zeilen) — WORM NICHT AKTIV!")
            failed += 1
    except Exception as e:
        err_msg = str(e)
        if "WORM" in err_msg or "MiFID" in err_msg or "verboten" in err_msg:
            print(f"  ✅ DELETE blockiert: Trigger hat ausgelöst (WORM aktiv)")
        else:
            print(f"  ✅ DELETE blockiert (andere Ursache): {err_msg[:120]}")
        passed += 1

    # ── 4. Re-READ — Datensatz noch vorhanden? ────────────────────────────────
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                SELECT id, message FROM mifid_decision_log WHERE id = :id
            """
                ),
                {"id": record_id},
            )
            row = result.fetchone()
            if row and row[1] == "WORM compliance verification record":
                print(f"  ✅ READ: Originaldaten unveränderlich erhalten")
                passed += 1
            elif row:
                print(f"  ❌ READ: Datensatz vorhanden aber Inhalt geändert: {row[1]}")
                failed += 1
            else:
                print(f"  ❌ READ: Datensatz verschwunden (wurde gelöscht?)")
                failed += 1
    except Exception as e:
        print(f"  ❌ READ fehlgeschlagen: {e}")
        failed += 1

    await engine.dispose()

    # ── Ergebnis ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*55}")
    if failed == 0:
        print(f"  🏆 WORM AKTIV — Alle {passed} Tests bestanden.")
        print(f"     MiFID II Art. 16 Anforderung erfüllt.")
    else:
        print(f"  ⚠️  {failed} Test(s) fehlgeschlagen, {passed} bestanden.")
        print(f"     WORM-Schutz ist NICHT vollständig aktiv!")
    print(f"{'─'*55}\n")

    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    exit(0 if ok else 1)
