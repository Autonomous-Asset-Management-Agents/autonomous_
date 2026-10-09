# core/engine/ausfuehrung.py
# ARC-E6 H-1d (#4233) — Sammelpunkt der komponierten Ausfuehrungs-Mixins.
"""Die komponierten Module des Executors, zu einer Basis von ``BotEngine`` gebuendelt.

Jedes komponierte Modul (Entscheidung #4183 §3) importiert den Kern
``order_executor.py`` auf Modulebene; der Kern importiert keines davon. Zusammengesetzt
werden sie deshalb nicht im Kern, sondern hier — und ``BotEngine`` erbt nur diese eine
Klasse. So waechst ``base.py`` mit den Umzuegen H-1d bis H-1k nicht: Jeder Umzug traegt
seine Klasse hier ein, nicht in der Basisliste von ``BotEngine``.

Die Reihenfolge ist die MRO: ``SignalUebergabeMixin`` und ``AbsendungNachlaufMixin``
stehen vorn wie zuvor in ``BotEngine``; die Methodennamen aller Mixins sind disjunkt
(``tests/unit/test_h1d_mandanten_zugang.py``).

Dieses Modul enthaelt nur Importe und die Klasse (Entscheidung §3, „Zirkelimport“).
"""

from .absendung_abgang import AbsendungAbgangMixin
from .absendung_nachlauf import AbsendungNachlaufMixin
from .absendung_vorlauf import AbsendungVorlaufMixin
from .hitl_freigabe import HitlFreigabeMixin
from .mandanten_zugang import MandantenZugangMixin
from .signal_desktop_absendung import SignalDesktopAbsendungMixin
from .signal_desktop_entscheid import SignalDesktopEntscheidMixin
from .signal_uebergabe import SignalUebergabeMixin
from .verdraengung import VerdraengungMixin


class AusfuehrungMixin(
    SignalUebergabeMixin,  # #3819: die Schritte von _process_signal_event
    AbsendungNachlaufMixin,  # #3821: Nachlauf-Schritte von _execute_tenant_order
    MandantenZugangMixin,  # #4233: Mandanten-Zugang (H-1d)
    HitlFreigabeMixin,  # #4234: HITL-Freigabe und Markt-Tor (H-1e)
    AbsendungVorlaufMixin,  # #4235: Vorlauf-Schritte von _execute_tenant_order (H-1f)
    AbsendungAbgangMixin,  # #4236: Abgang-Schritte von _execute_tenant_order (H-1g)
    SignalDesktopEntscheidMixin,  # #4238: Entscheid-Schritte von _process_signal_event (H-1i)
    SignalDesktopAbsendungMixin,  # #4239: Absendungs-Schritte von _process_signal_event (H-1j)
    VerdraengungMixin,  # #4241: Rueckkauf nach gescheiterter Verdraengung (H-1l)
):
    """Komponierte Ausfuehrungs-Mixins; in ``BotEngine`` nach ``OrderExecutorMixin``."""
