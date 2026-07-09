import time
import json
import threading
from pynput import mouse, keyboard


class Recorder:
    """
    Записывает все события мыши и клавиатуры с точными временными метками.

    Использование:
        rec = Recorder()
        rec.start()
        # ... делай что хочешь ...
        rec.stop()
        rec.save("session.rec")
    """

    def __init__(self, mouse_hz: float = 60.0):
        """
        mouse_hz — максимальная частота записи движений мыши (событий в секунду).
        При 60 Hz одно событие каждые ~16 мс. Для 1-часовой сессии это ~216k событий.
        Повышай до 120-1000 если нужна аптечная точность движений.
        """
        self.events: list = []
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._running = False
        self._initial_pos: tuple = (0, 0)  # позиция курсора в момент старта записи

        self._mouse_listener = None
        self._kb_listener = None

        # Throttle для движения мыши
        self._mouse_min_dt = 1.0 / mouse_hz
        self._last_move_t = 0.0
        self._last_move_x = 0
        self._last_move_y = 0

    # ── Внутренние обработчики ─────────────────────────────────────────────

    def _ts(self) -> float:
        return time.perf_counter() - self._start_time

    def _push(self, event: dict):
        with self._lock:
            self.events.append(event)

    def _on_move(self, x: int, y: int):
        t = self._ts()
        dt = t - self._last_move_t
        if dt < self._mouse_min_dt:
            return

        vx = (x - self._last_move_x) / dt if dt > 0 else 0.0
        vy = (y - self._last_move_y) / dt if dt > 0 else 0.0

        self._push({
            't':  round(t, 6),
            'e':  'move',
            'x':  x,
            'y':  y,
            'vx': round(vx, 2),
            'vy': round(vy, 2),
        })

        self._last_move_t = t
        self._last_move_x = x
        self._last_move_y = y

    def _on_click(self, x: int, y: int, button, pressed: bool):
        self._push({
            't':      round(self._ts(), 6),
            'e':      'click',
            'x':      x,
            'y':      y,
            'btn':    button.name,
            'down':   pressed,
        })

    def _on_scroll(self, x: int, y: int, dx: int, dy: int):
        self._push({
            't':  round(self._ts(), 6),
            'e':  'scroll',
            'x':  x,
            'y':  y,
            'dx': dx,
            'dy': dy,
        })

    @staticmethod
    def _key_name(key) -> str:
        """Сериализует pynput-клавишу в строку."""
        try:
            ch = key.char
            return ch if ch is not None else str(key)
        except AttributeError:
            return str(key)  # 'Key.space', 'Key.ctrl_l', etc.

    def _on_key_press(self, key):
        if key is None:
            return
        vk = getattr(key, 'vk', None)
        self._push({
            't':    round(self._ts(), 6),
            'e':    'key',
            'key':  self._key_name(key),
            'vk':   vk,
            'down': True,
        })

    def _on_key_release(self, key):
        if key is None:
            return
        vk = getattr(key, 'vk', None)
        self._push({
            't':    round(self._ts(), 6),
            'e':    'key',
            'key':  self._key_name(key),
            'vk':   vk,
            'down': False,
        })

    # ── Публичный интерфейс ────────────────────────────────────────────────

    def start(self):
        """Начать запись. Слушатели запускаются в фоновых потоках."""
        # Запоминаем позицию курсора до старта записи
        self._initial_pos = mouse.Controller().position

        self.events.clear()
        self._start_time = time.perf_counter()
        self._last_move_t = 0.0
        self._running = True

        self._mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._kb_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )

        self._mouse_listener.start()
        self._kb_listener.start()
        return self

    def stop(self):
        """Остановить запись."""
        self._running = False
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        return self

    @property
    def duration(self) -> float:
        with self._lock:
            return self.events[-1]['t'] if self.events else 0.0

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.events)

    def save(self, path: str) -> str:
        """Сохранить запись в файл .rec (JSON Lines)."""
        with self._lock:
            events_snapshot = list(self.events)

        header = {
            'version':     '1.0',
            'event_count': len(events_snapshot),
            'duration':    round(events_snapshot[-1]['t'], 3) if events_snapshot else 0,
            'initial_pos': list(self._initial_pos),  # [x, y] — точка старта курсора
        }

        with open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(header, ensure_ascii=False) + '\n')
            for ev in events_snapshot:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')

        return path

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
