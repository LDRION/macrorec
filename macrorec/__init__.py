"""
MacroRec — запись и воспроизведение мыши + клавиатуры.

Быстрый старт:
    from macrorec import Recorder, Player

    # Запись
    rec = Recorder(mouse_hz=60)
    rec.start()
    input("Enter — стоп...")
    rec.stop()
    rec.save("session.rec")

    # Воспроизведение
    Player("session.rec").play(speed=1.0)
"""

from .recorder import Recorder
from .replayer import Player

__all__ = ['Recorder', 'Player']
__version__ = '1.0.0'
