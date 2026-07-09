import time
import sys
import json
import math
import random
import ctypes
from typing import Callable, Optional
from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

# ── Windows: системный таймер 1 мс и приоритет потока ────────────────────────
if sys.platform == 'win32':
    _winmm = ctypes.WinDLL('winmm', use_last_error=True)
    _kernel32 = ctypes.windll.kernel32

    def _timer_begin():
        _winmm.timeBeginPeriod(1)

    def _timer_end():
        _winmm.timeEndPeriod(1)

    def _boost_thread():
        handle = _kernel32.GetCurrentThread()
        _kernel32.SetThreadPriority(handle, 2)  # THREAD_PRIORITY_HIGHEST

    def _unboost_thread():
        handle = _kernel32.GetCurrentThread()
        _kernel32.SetThreadPriority(handle, 0)  # THREAD_PRIORITY_NORMAL
else:
    def _timer_begin(): pass
    def _timer_end(): pass
    def _boost_thread(): pass
    def _unboost_thread(): pass

_SPIN_THRESHOLD = 0.005  # спин последние 5 мс


def _precise_sleep(seconds: float):
    if seconds <= 0:
        return
    if seconds > _SPIN_THRESHOLD:
        time.sleep(seconds - _SPIN_THRESHOLD)
    target = time.perf_counter() + max(seconds, 0.0)
    while time.perf_counter() < target:
        pass


def _parse_key(ev: dict):
    """
    Восстанавливает клавишу для воспроизведения из события записи.

    Приоритет — записанный vk (виртуальный код клавиши): аппаратно точная
    эмуляция через нормальный клавиатурный путь ОС. Без неё кириллица слалась
    юникод-инъекцией, которая при быстром наборе ловит гонку и искажает текст
    («БИЗНЕС» → «БИИННС»). char — резерв, если vk не записан.
    """
    key_str = ev.get('key', '') or ''
    # Спец-клавиши (Key.ctrl_l, Key.space, ...) — по имени
    if key_str.startswith('Key.'):
        return getattr(Key, key_str[4:], None)
    # Обычные клавиши — по виртуальному коду (аппаратно, без юникод-гонок)
    vk = ev.get('vk')
    if vk is not None:
        try:
            return KeyCode.from_vk(vk)
        except Exception:
            pass
    # Резерв — по символу
    if key_str:
        try:
            return KeyCode.from_char(key_str[0])
        except Exception:
            return None
    return None


def _parse_button(btn_str: str) -> Button:
    return getattr(Button, btn_str, Button.left)


def _log(msg):
    """Безопасный вывод: в --windowed exe stdout может быть None."""
    try:
        sys.stdout.write(str(msg) + '\n')
        sys.stdout.flush()
    except Exception:
        pass


def _ensure_russian_layout():
    """
    Перед воспроизведением ГАРАНТИРУЕТ русскую раскладку у активного окна.

    Игра идёт по vk (кодам клавиш) — кириллицу они дают только при русской
    раскладке. Логика безопасная: сначала ЧИТАЕМ текущую раскладку активного
    окна; если уже русская — НЕ трогаем (чтобы не сбить); если нет — находим
    установленную русскую и переключаемся, затем ПЕРЕПРОВЕРЯЕМ результат.
    """
    if sys.platform != 'win32':
        return
    try:
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.GetForegroundWindow.restype       = ctypes.c_void_p
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetWindowThreadProcessId.restype  = ctypes.c_uint
        u.GetKeyboardLayout.argtypes        = [ctypes.c_uint]
        u.GetKeyboardLayout.restype         = ctypes.c_void_p
        u.GetKeyboardLayoutList.argtypes    = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        u.GetKeyboardLayoutList.restype     = ctypes.c_int
        u.LoadKeyboardLayoutW.argtypes      = [ctypes.c_wchar_p, ctypes.c_uint]
        u.LoadKeyboardLayoutW.restype       = ctypes.c_void_p
        u.PostMessageW.argtypes             = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

        RU = 0x0419
        WM_INPUTLANGCHANGEREQUEST = 0x0050
        langid = lambda hkl: (hkl or 0) & 0xFFFF

        hwnd = u.GetForegroundWindow()
        tid  = u.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
        cur  = u.GetKeyboardLayout(tid)

        if langid(cur) == RU:
            _log('  🌐 Раскладка уже RU — ок')
            return

        ru_hkl = None
        n = u.GetKeyboardLayoutList(0, None)
        if n > 0:
            arr = (ctypes.c_void_p * n)()
            u.GetKeyboardLayoutList(n, arr)
            for h in arr:
                if langid(h) == RU:
                    ru_hkl = h
                    break
        if ru_hkl is None:
            ru_hkl = u.LoadKeyboardLayoutW('00000419', 0x00000001)  # KLF_ACTIVATE

        if ru_hkl and hwnd:
            u.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, ru_hkl)
            time.sleep(0.4)

        cur2 = u.GetKeyboardLayout(tid)
        if langid(cur2) == RU:
            _log('  🌐 Раскладка переключена на RU ✓')
        else:
            _log(f'  ⚠️ Не удалось переключить на RU (сейчас 0x{langid(cur2):04X})')
    except Exception as e:
        _log(f'  ⚠️ Проверка раскладки: {e}')


class Player:
    """
    Воспроизводит записанный .rec файл с точным соблюдением временны́х меток.

    Использование:
        player = Player("session.rec")
        player.play(speed=1.0)
        player.play(speed=2.0)
    """

    def __init__(self, path: str):
        self.path = path
        self.metadata: dict = {}
        self.events: list = []
        self._mouse = mouse.Controller()
        self._kb    = keyboard.Controller()
        self._load()

    def _load(self):
        with open(self.path, encoding='utf-8') as f:
            lines = f.readlines()
        if not lines:
            raise ValueError(f"Файл пустой: {self.path}")
        self.metadata = json.loads(lines[0])
        self.events = [json.loads(ln) for ln in lines[1:] if ln.strip()]

    @property
    def duration(self) -> float:
        return self.metadata.get('duration', 0.0)

    @property
    def event_count(self) -> int:
        return self.metadata.get('event_count', len(self.events))

    def _dispatch(self, ev: dict):
        e = ev['e']
        if e == 'move':
            self._mouse.position = (ev['x'], ev['y'])
        elif e == 'click':
            btn = _parse_button(ev['btn'])
            if ev['down']:
                self._mouse.press(btn)
            else:
                self._mouse.release(btn)
        elif e == 'scroll':
            self._mouse.scroll(ev['dx'], ev['dy'])
        elif e == 'key':
            key = _parse_key(ev)
            if key is None:
                return
            if ev['down']:
                try: self._kb.press(key)
                except Exception: pass
            else:
                try: self._kb.release(key)
                except Exception: pass

    def _dispatch_overshoot(self, ev: dict, anim_dur: float, target_wall: float):
        """
        Анимация перелёта: курсор проходит чуть дальше цели (overshoot),
        затем плавно возвращается. Конечная точка — точно (x, y) ровно в target_wall.
        """
        x, y = ev['x'], ev['y']
        cfg = ev['overshoot']
        ratio = max(0.01, min(1.0, cfg.get('ratio', 0.10)))
        sharpness = max(0.5, min(20.0, cfg.get('sharpness', 3.0)))

        px, py = self._mouse.position
        dx, dy = x - px, y - py

        # Точка перелёта: чуть дальше цели
        ox = x + int(dx * ratio)
        oy = y + int(dy * ratio)

        anim_start = time.perf_counter()
        P1 = 0.65  # 65% времени идём к перелёту, 35% возвращаемся
        p1_dur = anim_dur * P1
        p2_dur = anim_dur * (1.0 - P1)
        STEPS = 8

        # Фаза 1: ease-out к точке перелёта
        for step in range(1, STEPS + 1):
            t = step / STEPS
            ease = 1.0 - (1.0 - t) ** sharpness
            self._mouse.position = (
                int(px + (ox - px) * ease),
                int(py + (oy - py) * ease),
            )
            _precise_sleep(anim_start + p1_dur * step / STEPS - time.perf_counter())

        # Фаза 2: ease-in обратно к настоящей цели
        p2_start = anim_start + p1_dur
        for step in range(1, STEPS + 1):
            t = step / STEPS
            ease = t ** sharpness
            self._mouse.position = (
                int(ox + (x - ox) * ease),
                int(oy + (y - oy) * ease),
            )
            _precise_sleep(p2_start + p2_dur * step / STEPS - time.perf_counter())

        # Гарантированная конечная точка ровно в нужное время
        _precise_sleep(target_wall - time.perf_counter())
        self._mouse.position = (x, y)

    def _glide_to(self, tx: int, ty: int):
        """
        Плавно перемещает курсор из текущей позиции в (tx, ty).
        Движение с лёгким органическим шумом и плавным ease-in/out.
        Ощущается как обычное движение рукой, не как телепортация.
        """
        px, py = self._mouse.position
        dx, dy = tx - px, ty - py
        dist = math.hypot(dx, dy)

        if dist < 2:
            self._mouse.position = (tx, ty)
            return

        # Длительность: 80–500 мс пропорционально расстоянию
        duration = min(0.50, 0.08 + dist * 0.00028)
        steps    = max(18, int(dist / 9))

        # Перпендикулярный вектор для шума (отклонения вбок)
        nx, ny = -dy / dist, dx / dist

        # Максимальная амплитуда шума ~1.8% от расстояния
        noise_amp = dist * 0.018

        start_t    = time.perf_counter()
        prev_noise = 0.0

        for step in range(1, steps + 1):
            t    = step / steps
            ease = t * t * (3.0 - 2.0 * t)           # cubic smoothstep

            # Огибающая шума: 0 в начале и конце, пик в середине
            envelope = math.sin(math.pi * t) * noise_amp

            # Дрейфующий шум (temporally correlated)
            kick       = random.gauss(0, max(0.1, envelope * 0.35))
            prev_noise = prev_noise * 0.72 + kick * 0.28
            n          = max(-envelope, min(envelope, prev_noise))

            self._mouse.position = (
                int(round(px + dx * ease + nx * n)),
                int(round(py + dy * ease + ny * n)),
            )
            _precise_sleep(start_t + duration * step / steps - time.perf_counter())

        # Точное приземление
        self._mouse.position = (tx, ty)

    def play(
        self,
        speed: float = 1.0,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        """
        Параметры:
            speed       — 1.0 оригинал, 2.0 вдвое быстрее
            on_progress — callback(index, total, timestamp_sec) каждые 100 событий
            on_event    — callback(event_dict) перед каждым событием
        """
        if not self.events:
            return

        # Гарантируем русскую раскладку до первого нажатия (игра идёт по vk)
        _ensure_russian_layout()

        _timer_begin()
        _boost_thread()

        try:
            initial = self.metadata.get('initial_pos')
            if initial and len(initial) == 2:
                self._glide_to(int(initial[0]), int(initial[1]))
                time.sleep(0.04)  # пауза после приземления перед первым событием

            wall_start = time.perf_counter()

            for i, ev in enumerate(self.events):
                target_wall = wall_start + ev['t'] / speed

                if ev['e'] == 'move' and ev.get('overshoot'):
                    prev_t = self.events[i - 1]['t'] if i > 0 else 0.0
                    budget = (ev['t'] - prev_t) / speed
                    # Анимация не дольше 15 мс и не больше 80% окна
                    anim_dur = min(0.015, budget * 0.8) if budget > 0.003 else 0.0

                    if anim_dur > 0.001:
                        _precise_sleep(target_wall - anim_dur - time.perf_counter())
                        if on_event:
                            on_event(ev)
                        self._dispatch_overshoot(ev, anim_dur, target_wall)
                    else:
                        _precise_sleep(target_wall - time.perf_counter())
                        if on_event:
                            on_event(ev)
                        self._dispatch(ev)
                else:
                    _precise_sleep(target_wall - time.perf_counter())
                    if on_event:
                        on_event(ev)
                    self._dispatch(ev)

                if on_progress and i % 100 == 0:
                    on_progress(i, len(self.events), ev['t'])

            if on_progress:
                on_progress(len(self.events), len(self.events), self.duration)
        finally:
            _unboost_thread()
            _timer_end()
