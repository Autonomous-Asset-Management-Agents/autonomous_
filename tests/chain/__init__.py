"""#3384 (ARC-E2.1) — Vorrichtungen fuer die Kettenabnahme des Epics #3367.

Hier liegt **kein Test**, sondern Messgeraet: eine Chaos-Vorrichtung, die einen
Unterprozess an drei benannten Punkten hart beendet, und eine Zwei-Instanzen-
Vorrichtung, die zwei Engines auf dasselbe Konto legt.

Das Verzeichnis steht bewusst **nicht** in ``testpaths`` (``pyproject.toml:10-15``)
und enthaelt darum keine ``test_*.py``. Die eigenen Pruefungen der Vorrichtungen
liegen in ``tests/unit/`` — dort faehrt sie der bestehende Job „Backend Iron Dome
(Fast Run)", ohne dass eine Job-Zuordnung neu erfunden werden muss. Die roten
Abnahmeszenarien liegen in ``tests/features/`` und laufen im Job „Backend BDD
Tests".
"""
