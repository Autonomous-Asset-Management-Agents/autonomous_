"""Agenten der Round Table, je Agent ein Modul (#3831, ARC-E6 G-6a).

Die Agenten wandern in Teilen (G-6b … G-6d) aus ``core/round_table/agents.py`` in Module
dieses Pakets; der gemeinsame Unterbau (Ausnahmetypen, Enable-Gate, Abstain-Record,
Gewichts-Naht) liegt in ``_basis.py``. ``agents.py`` importiert jeden verschobenen Namen
zurueck — alle Importeure und Tests mit ``from core.round_table.agents import X`` bleiben
gueltig (bewacht von ``tests/unit/test_agenten_reexport.py``).

Zugriffsregel (bewacht von ``tests/unit/test_agenten_zugriffsregel.py``):
    Geteilte Abhaengigkeiten (``get_global_registry``, ``_specialist_registry_instance``,
    ``_warmup_warned_symbols``, ``_MOMENTUM_ABSTAIN_WARNED``, LLM-Provider-Getter, ``datetime``) bleiben in ``agents`` und werden **zur Laufzeit ueber
    das Modulobjekt** gelesen::

        from core.round_table import agents as _ag

        async def vote(self, state):
            registry = _ag.get_global_registry()

    Nie ``from core.round_table.agents import get_global_registry`` und nie ein Import
    dieser Namen auf Modulebene: Diese Bindung sieht einen Patch auf
    ``core.round_table.agents.<name>`` nicht mehr, der Test wird still falsch. Der
    Import-Kreis ist harmlos, weil das Modulobjekt erst im Aufruf gelesen wird.
"""
