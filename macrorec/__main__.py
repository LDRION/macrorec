"""
MacroRec — запись и воспроизведение ввода (мышь + клавиатура)

Команды:
  python -m macrorec record [output.rec] [--hz 60]
  python -m macrorec play   <file.rec>   [--speed 1.0]
  python -m macrorec info   <file.rec>
"""

import sys
import os
import time
import json
import threading
from datetime import datetime


# ── Утилиты терминала ─────────────────────────────────────────────────────────

def _fmt_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = seconds % 60
    return f"{m}:{s:05.2f}"


def _clear_line():
    print('\r' + ' ' * 80 + '\r', end='', flush=True)


# ── record ────────────────────────────────────────────────────────────────────

def cmd_record(args: list):
    from macrorec.recorder import Recorder

    # Парсим аргументы
    output = None
    hz = 60.0
    i = 0
    while i < len(args):
        if args[i] == '--hz' and i + 1 < len(args):
            hz = float(args[i + 1]); i += 2
        elif not args[i].startswith('--'):
            output = args[i]; i += 1
        else:
            i += 1

    if not output:
        output = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.rec"

    print()
    print(f"  🔴  ЗАПИСЬ НАЧАЛАСЬ")
    print(f"  Файл    : {output}")
    print(f"  Мышь Hz : {hz}")
    print(f"  Стоп    : нажми Enter в этом окне\n")

    stop_event = threading.Event()

    def _wait_enter():
        sys.stdin.readline()
        stop_event.set()

    t_input = threading.Thread(target=_wait_enter, daemon=True)
    t_input.start()

    rec = Recorder(mouse_hz=hz)
    rec.start()
    t_start = time.time()

    try:
        while not stop_event.is_set():
            elapsed = time.time() - t_start
            print(
                f"\r  ⏺  {_fmt_time(elapsed)}  |  {rec.count:,} событий",
                end='', flush=True
            )
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    rec.stop()
    _clear_line()

    if not rec.events:
        print("  ⚠️  Событий не записано.")
        return

    path = rec.save(output)

    print(f"  ✅  Сохранено: {path}")
    print(f"      Длительность : {_fmt_time(rec.duration)}")
    print(f"      Событий      : {rec.count:,}")
    print()


# ── play ──────────────────────────────────────────────────────────────────────

def cmd_play(args: list):
    from macrorec.replayer import Player

    path = None
    speed = 1.0
    i = 0
    while i < len(args):
        if args[i] == '--speed' and i + 1 < len(args):
            speed = float(args[i + 1]); i += 2
        elif not args[i].startswith('--'):
            path = args[i]; i += 1
        else:
            i += 1

    if not path:
        print("  ❌  Укажи файл: python -m macrorec play session.rec")
        return

    if not os.path.exists(path):
        print(f"  ❌  Файл не найден: {path}")
        return

    player = Player(path)
    duration = player.duration
    count    = player.event_count
    eta      = duration / speed

    print()
    print(f"  ▶️   ВОСПРОИЗВЕДЕНИЕ")
    print(f"  Файл       : {path}")
    print(f"  Длительность: {_fmt_time(duration)}")
    print(f"  Событий    : {count:,}")
    print(f"  Скорость   : {speed}×  (займёт ~{_fmt_time(eta)})")
    print(f"\n  Старт через 3 секунды... (Ctrl+C — отмена)\n")

    for c in range(3, 0, -1):
        print(f"\r  {c}...", end='', flush=True)
        time.sleep(1)
    _clear_line()

    wall_start = time.time()

    def on_progress(idx, total, t_event):
        pct     = idx / total * 100 if total else 0
        elapsed = time.time() - wall_start
        print(
            f"\r  ▶  {_fmt_time(t_event)} / {_fmt_time(duration)}"
            f"  |  {pct:5.1f}%"
            f"  |  стена {_fmt_time(elapsed)}",
            end='', flush=True
        )

    try:
        player.play(speed=speed, on_progress=on_progress)
    except KeyboardInterrupt:
        print("\n\n  ⏹  Остановлено.")
        return

    _clear_line()
    print(f"  ✅  Готово. Сыграли за {_fmt_time(time.time() - wall_start)}")
    print()


# ── info ──────────────────────────────────────────────────────────────────────

def cmd_info(args: list):
    if not args:
        print("  ❌  Укажи файл: python -m macrorec info session.rec")
        return

    path = args[0]
    if not os.path.exists(path):
        print(f"  ❌  Файл не найден: {path}")
        return

    with open(path, encoding='utf-8') as f:
        lines = f.readlines()

    meta   = json.loads(lines[0])
    events = [json.loads(l) for l in lines[1:] if l.strip()]

    counts = {}
    for ev in events:
        counts[ev['e']] = counts.get(ev['e'], 0) + 1

    # Статистика клавиш
    key_counts: dict = {}
    for ev in events:
        if ev['e'] == 'key' and ev.get('down'):
            k = ev.get('key', '?')
            key_counts[k] = key_counts.get(k, 0) + 1
    top_keys = sorted(key_counts.items(), key=lambda x: -x[1])[:10]

    size_kb = os.path.getsize(path) / 1024

    print()
    print(f"  📄  {path}")
    print(f"  {'─' * 46}")
    print(f"  Версия         : {meta.get('version', '?')}")
    print(f"  Длительность   : {_fmt_time(meta.get('duration', 0))}")
    print(f"  Событий        : {meta.get('event_count', len(events)):,}")
    print(f"  Размер файла   : {size_kb:.1f} KB")
    print(f"\n  Типы событий:")
    labels = {'move': 'движение мыши', 'click': 'клики', 'scroll': 'скролл', 'key': 'клавиши'}
    for k, v in sorted(counts.items()):
        print(f"    {labels.get(k, k):<20} {v:>8,}")
    if top_keys:
        print(f"\n  Топ клавиш:")
        for k, v in top_keys:
            print(f"    {repr(k):<24} {v:>6,}×")
    print()


# ── Точка входа ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print()
        print("  MacroRec — запись и воспроизведение ввода")
        print()
        print("  Команды:")
        print("    python -m macrorec record [file.rec] [--hz 60]")
        print("    python -m macrorec play   <file.rec> [--speed 1.0]")
        print("    python -m macrorec info   <file.rec>")
        print()
        return

    cmd  = args[0]
    rest = args[1:]

    if cmd == 'record':
        cmd_record(rest)
    elif cmd == 'play':
        cmd_play(rest)
    elif cmd == 'info':
        cmd_info(rest)
    else:
        print(f"  ❌  Неизвестная команда: {cmd}")
        print("  Доступны: record, play, info")


if __name__ == '__main__':
    main()
