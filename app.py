"""
MacroRec GUI — запись и воспроизведение мыши + клавиатуры.
Запуск:  python app.py
Сборка:  build.bat
"""

import customtkinter as ctk
from tkinter import filedialog
import tkinter.ttk as ttk
import tkinter as tk
import threading
import time
import os
import json
import sys
import random
from datetime import datetime
from pathlib import Path
from pynput import keyboard as pynput_kb

sys.path.insert(0, str(Path(__file__).parent))
from macrorec import Recorder, Player

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Конфиг ────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / '.macrorec' / 'config.json'
DEFAULT_CFG = {
    'save_dir':       str(Path.home() / 'Documents'),
    'hotkey':         'Key.f9',
    'hotkey_display': 'F9',
    'speed':          1.0,
    'countdown':      10,
}

def _load_cfg() -> dict:
    if CONFIG_PATH.exists():
        try: return {**DEFAULT_CFG, **json.loads(CONFIG_PATH.read_text('utf-8'))}
        except Exception: pass
    return dict(DEFAULT_CFG)

def _save_cfg(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), 'utf-8')


# ── Утилиты ───────────────────────────────────────────────────────────────────
def _fmt(sec: float) -> str:
    m = int(sec) // 60
    s = sec % 60
    return f"{m}:{s:05.2f}"

def _speed_val(label: str) -> float:
    return {'0.5×': 0.5, '1×': 1.0, '1.5×': 1.5, '2×': 2.0}.get(label, 1.0)

def _key_display(key) -> str:
    try:
        ch = key.char
        return ch.upper() if ch else str(key).replace('Key.', '').upper()
    except AttributeError:
        return str(key).replace('Key.', '').upper()

def _ev_type_label(ev: dict) -> str:
    e = ev['e']
    if e == 'move':   return 'move'
    if e == 'scroll': return 'scroll'
    if e == 'click':  return f"click {'↓' if ev.get('down') else '↑'}"
    if e == 'key':    return f"key {'↓' if ev.get('down') else '↑'}"
    return e

def _ev_details(ev: dict) -> str:
    e = ev['e']
    if e == 'move':
        ovs = '  [перелёт]' if ev.get('overshoot') else ''
        return f"({ev['x']}, {ev['y']}){ovs}"
    if e == 'click':
        return f"{ev.get('btn', '?')}  @  ({ev['x']}, {ev['y']})"
    if e == 'key':
        return repr(ev.get('key', '?'))
    if e == 'scroll':
        return f"dy={ev.get('dy', 0)}  @  ({ev['x']}, {ev['y']})"
    return ''

def _humanize_events(events: list) -> list:
    """Добавляет ±50 мкс случайного джиттера к каждому событию."""
    MAX_JITTER = 0.000050
    result = []
    for ev in events:
        j = dict(ev)
        j['t'] = max(0.0, round(ev['t'] + random.uniform(-MAX_JITTER, MAX_JITTER), 7))
        result.append(j)
    result.sort(key=lambda e: e['t'])
    return result


# ── Стиль таблицы событий (тёмная тема) ──────────────────────────────────────
def _apply_tree_style():
    style = ttk.Style()
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('MR.Treeview',
        background='#1e1e1e', fieldbackground='#1e1e1e',
        foreground='#e0e0e0', rowheight=22,
        borderwidth=0, relief='flat',
        font=('Consolas', 10))
    style.configure('MR.Treeview.Heading',
        background='#2a2a2a', foreground='#aaaaaa',
        relief='flat', borderwidth=0,
        font=('Consolas', 10, 'bold'))
    style.map('MR.Treeview',
        background=[('selected', '#1a4a78')],
        foreground=[('selected', 'white')])
    style.map('MR.Treeview.Heading',
        background=[('active', '#333333')],
        foreground=[('active', '#cccccc')])


# ── Главное окно ──────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = _load_cfg()
        _apply_tree_style()

        self.title("MacroRec")
        self.geometry("720x820")
        self.minsize(580, 660)
        self.resizable(True, True)

        # Состояние записи / плеера
        self._recorder:     Recorder | None = None
        self._is_recording: bool = False
        self._is_playing:   bool = False
        self._capture_mode: bool = False
        self._record_start: float = 0.0
        self._hk_listener:  pynput_kb.Listener | None = None

        # Состояние редактора
        self._edit_events:   list  = []
        self._edit_path:     str   = ''
        self._edit_meta:     dict  = {}
        self._edit_modified: bool  = False
        self._sel_idx:       int   = -1
        self._edit_cap_mode: bool  = False  # захват клавиши в редакторе

        self._build_ui()
        self._start_global_hotkey()
        self._tick()

    # ══════════════════════════════════════════════════════════════════════════
    # ПОСТРОЕНИЕ UI
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color='transparent')
        hdr.pack(fill='x', padx=20, pady=(14, 4))
        ctk.CTkLabel(hdr, text="🎙  MacroRec",
                     font=ctk.CTkFont(size=22, weight='bold')).pack(side='left')
        ctk.CTkLabel(hdr, text="v1.1",
                     font=ctk.CTkFont(size=12), text_color='gray').pack(side='left', padx=8, pady=6)

        self._tabs = ctk.CTkTabview(self, corner_radius=10)
        self._tabs.pack(fill='both', expand=True, padx=14, pady=(0, 14))

        self._build_rec_play_tab(self._tabs.add("⏺  Запись / ▶ Плеер"))
        self._build_editor_tab(self._tabs.add("✏  Редактор"))

    # ── Вкладка: Запись + Плеер ───────────────────────────────────────────────

    def _build_rec_play_tab(self, parent):
        # ── Запись ───────────────────────────────────────────────────────
        rf = ctk.CTkFrame(parent, corner_radius=12)
        rf.pack(fill='x', padx=4, pady=(4, 6))

        ctk.CTkLabel(rf, text="⏺  ЗАПИСЬ",
                     font=ctk.CTkFont(size=13, weight='bold')).pack(anchor='w', padx=16, pady=(12, 6))

        row_dir = ctk.CTkFrame(rf, fg_color='transparent')
        row_dir.pack(fill='x', padx=12, pady=(0, 6))
        ctk.CTkLabel(row_dir, text="📂  Папка:", width=100, anchor='w').pack(side='left')
        self._dir_var = ctk.StringVar(value=self.cfg['save_dir'])
        ctk.CTkEntry(row_dir, textvariable=self._dir_var).pack(side='left', fill='x', expand=True, padx=(0, 6))
        ctk.CTkButton(row_dir, text="...", width=38, command=self._browse_dir).pack(side='left')

        row_hk = ctk.CTkFrame(rf, fg_color='transparent')
        row_hk.pack(fill='x', padx=12, pady=(0, 12))
        ctk.CTkLabel(row_hk, text="⌨️  Клавиша:", width=100, anchor='w').pack(side='left')
        self._hk_lbl = ctk.CTkLabel(
            row_hk, text=self.cfg['hotkey_display'],
            width=64, font=ctk.CTkFont(size=14, weight='bold'),
            fg_color=('#555', '#3a3a3a'), corner_radius=6)
        self._hk_lbl.pack(side='left', padx=(0, 10))
        self._hk_btn = ctk.CTkButton(row_hk, text="Изменить", width=100,
                                      command=self._begin_capture_hotkey)
        self._hk_btn.pack(side='left')
        self._hk_hint = ctk.CTkLabel(row_hk, text="", text_color='orange',
                                      font=ctk.CTkFont(size=12))
        self._hk_hint.pack(side='left', padx=8)

        self._rec_btn = ctk.CTkButton(
            rf, text="⏺  НАЧАТЬ ЗАПИСЬ",
            height=52, font=ctk.CTkFont(size=16, weight='bold'),
            fg_color='#c0392b', hover_color='#e74c3c',
            command=self._toggle_record)
        self._rec_btn.pack(fill='x', padx=12, pady=(0, 6))

        self._rec_status = ctk.CTkLabel(rf, text="",
                                         font=ctk.CTkFont(size=12), text_color='#e74c3c')
        self._rec_status.pack(pady=(0, 10))

        # ── Воспроизведение ───────────────────────────────────────────────
        pf = ctk.CTkFrame(parent, corner_radius=12)
        pf.pack(fill='x', padx=4, pady=(0, 6))

        ctk.CTkLabel(pf, text="▶️  ВОСПРОИЗВЕДЕНИЕ",
                     font=ctk.CTkFont(size=13, weight='bold')).pack(anchor='w', padx=16, pady=(12, 6))

        row_file = ctk.CTkFrame(pf, fg_color='transparent')
        row_file.pack(fill='x', padx=12, pady=(0, 6))
        ctk.CTkLabel(row_file, text="📄  Файл:", width=100, anchor='w').pack(side='left')
        self._file_entry = ctk.CTkEntry(row_file, placeholder_text="Выбери .rec файл")
        self._file_entry.pack(side='left', fill='x', expand=True, padx=(0, 6))
        ctk.CTkButton(row_file, text="...", width=38, command=self._browse_file).pack(side='left')

        row_spd = ctk.CTkFrame(pf, fg_color='transparent')
        row_spd.pack(fill='x', padx=12, pady=(0, 12))
        ctk.CTkLabel(row_spd, text="⚡  Скорость:", width=100, anchor='w').pack(side='left')
        self._speed_seg = ctk.CTkSegmentedButton(
            row_spd, values=["0.5×", "1×", "1.5×", "2×"],
            command=lambda v: self.cfg.update({'speed': _speed_val(v)}))
        self._speed_seg.pack(side='left')
        self._speed_seg.set("1×")

        self._play_btn = ctk.CTkButton(
            pf, text="▶  ЗАПУСТИТЬ",
            height=52, font=ctk.CTkFont(size=16, weight='bold'),
            fg_color='#1e8449', hover_color='#27ae60',
            command=self._start_play)
        self._play_btn.pack(fill='x', padx=12, pady=(0, 6))

        self._play_status = ctk.CTkLabel(pf, text="",
                                          font=ctk.CTkFont(size=12), text_color='#2ecc71')
        self._play_status.pack(pady=(0, 10))

        # ── Лог ──────────────────────────────────────────────────────────
        lf = ctk.CTkFrame(parent, corner_radius=12)
        lf.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        ctk.CTkLabel(lf, text="Лог событий",
                     font=ctk.CTkFont(size=11), text_color='gray').pack(anchor='w', padx=12, pady=(6, 0))
        self._log_box = ctk.CTkTextbox(lf, font=ctk.CTkFont(family='Consolas', size=11))
        self._log_box.pack(fill='both', expand=True, padx=8, pady=(2, 8))
        self._log_box.configure(state='disabled')

    # ── Вкладка: Редактор ─────────────────────────────────────────────────────

    def _build_editor_tab(self, parent):
        # Строка загрузки файла
        top = ctk.CTkFrame(parent, fg_color='transparent')
        top.pack(fill='x', padx=4, pady=(4, 2))

        ctk.CTkLabel(top, text="📄", width=28).pack(side='left')
        self._edit_path_entry = ctk.CTkEntry(top, placeholder_text="Загрузи .rec файл...")
        self._edit_path_entry.pack(side='left', fill='x', expand=True, padx=4)
        ctk.CTkButton(top, text="...", width=36,
                      command=self._editor_browse).pack(side='left', padx=(0, 4))
        ctk.CTkButton(top, text="Загрузить", width=86,
                      command=self._editor_load).pack(side='left', padx=(0, 6))
        ctk.CTkButton(
            top, text="🎲  Уникализировать", width=164,
            fg_color='#6c3483', hover_color='#9b59b6',
            command=self._editor_humanize,
        ).pack(side='left')

        # Строка с информацией о загруженном файле
        self._edit_info_lbl = ctk.CTkLabel(
            parent, text="Файл не загружен",
            font=ctk.CTkFont(size=11), text_color='gray')
        self._edit_info_lbl.pack(anchor='w', padx=8, pady=(0, 2))

        # ── Таблица событий ───────────────────────────────────────────────
        tree_outer = ctk.CTkFrame(parent, corner_radius=8)
        tree_outer.pack(fill='both', expand=True, padx=4, pady=(0, 4))

        tree_inner = tk.Frame(tree_outer, bg='#1e1e1e')
        tree_inner.pack(fill='both', expand=True, padx=4, pady=4)

        vsb = ttk.Scrollbar(tree_inner, orient='vertical')
        vsb.pack(side='right', fill='y')

        self._tree = ttk.Treeview(
            tree_inner, style='MR.Treeview',
            columns=('t_ms', 'type_col', 'details'),
            show='headings',
            yscrollcommand=vsb.set,
            selectmode='browse',
        )
        vsb.config(command=self._tree.yview)

        self._tree.heading('t_ms',     text='Время, мс')
        self._tree.heading('type_col', text='Тип')
        self._tree.heading('details',  text='Детали')
        self._tree.column('t_ms',     width=110, minwidth=80,  stretch=False, anchor='e')
        self._tree.column('type_col', width=80,  minwidth=60,  stretch=False, anchor='center')
        self._tree.column('details',  width=360, minwidth=120, stretch=True,  anchor='w')

        self._tree.pack(side='left', fill='both', expand=True)
        self._tree.bind('<<TreeviewSelect>>', self._on_tree_select)

        # ── Панель редактирования ─────────────────────────────────────────
        self._edit_panel = ctk.CTkFrame(parent, corner_radius=8)
        self._edit_panel.pack(fill='x', padx=4, pady=(0, 4))
        self._show_edit_placeholder()

        # ── Кнопки сохранения ─────────────────────────────────────────────
        save_row = ctk.CTkFrame(parent, fg_color='transparent')
        save_row.pack(fill='x', padx=4, pady=(0, 4))
        ctk.CTkButton(
            save_row, text="💾  Сохранить текущий", width=200,
            fg_color='#1a5276', hover_color='#2e86c1',
            command=self._editor_save_current,
        ).pack(side='left', padx=(0, 8))
        ctk.CTkButton(
            save_row, text="💾  Сохранить как...", width=180,
            fg_color='#1e8449', hover_color='#27ae60',
            command=self._editor_save_as,
        ).pack(side='left')
        self._save_status_lbl = ctk.CTkLabel(
            save_row, text="", font=ctk.CTkFont(size=11), text_color='gray')
        self._save_status_lbl.pack(side='left', padx=12)

    # ══════════════════════════════════════════════════════════════════════════
    # РЕДАКТОР — загрузка файла
    # ══════════════════════════════════════════════════════════════════════════

    def _editor_browse(self):
        f = filedialog.askopenfilename(
            title="Выбери запись",
            filetypes=[("MacroRec", "*.rec"), ("Все файлы", "*.*")],
            initialdir=self._dir_var.get(),
        )
        if f:
            self._edit_path_entry.delete(0, 'end')
            self._edit_path_entry.insert(0, f)

    def _editor_load(self):
        path = self._edit_path_entry.get().strip()
        if not path or not os.path.exists(path):
            self._edit_info_lbl.configure(text="Файл не найден", text_color='#e74c3c')
            return
        try:
            p = Player(path)
            self._edit_events = [dict(ev) for ev in p.events]
            self._edit_meta = dict(p.metadata)
            self._edit_path = path
            self._edit_modified = False
            self._sel_idx = -1
            self._refresh_tree()
            self._show_edit_placeholder()
            n   = len(self._edit_events)
            dur = self._edit_meta.get('duration', 0)
            self._edit_info_lbl.configure(
                text=f"{os.path.basename(path)}  |  {n:,} событий  |  {_fmt(dur)}",
                text_color='gray')
            self._save_status_lbl.configure(text="", text_color='gray')
        except Exception as exc:
            self._edit_info_lbl.configure(text=f"Ошибка: {exc}", text_color='#e74c3c')

    def _refresh_tree(self):
        self._tree.delete(*self._tree.get_children())
        for i, ev in enumerate(self._edit_events):
            self._tree.insert('', 'end', iid=str(i), values=(
                f"{ev['t'] * 1000:.3f}",
                _ev_type_label(ev),
                _ev_details(ev),
            ))
        if 0 <= self._sel_idx < len(self._edit_events):
            self._tree.selection_set(str(self._sel_idx))
            self._tree.see(str(self._sel_idx))

    def _update_tree_row(self, idx: int):
        ev = self._edit_events[idx]
        self._tree.item(str(idx), values=(
            f"{ev['t'] * 1000:.3f}",
            _ev_type_label(ev),
            _ev_details(ev),
        ))

    # ══════════════════════════════════════════════════════════════════════════
    # РЕДАКТОР — выбор события
    # ══════════════════════════════════════════════════════════════════════════

    def _on_tree_select(self, _event):
        sel = self._tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self._sel_idx = idx
        self._show_edit_panel(idx, self._edit_events[idx])

    # ── Панель редактирования ─────────────────────────────────────────────────

    def _show_edit_placeholder(self):
        self._clear_edit_panel()
        ctk.CTkLabel(
            self._edit_panel,
            text="Выбери событие в таблице для редактирования",
            font=ctk.CTkFont(size=12), text_color='gray',
        ).pack(pady=10)

    def _clear_edit_panel(self):
        for w in self._edit_panel.winfo_children():
            w.destroy()
        self._edit_cap_mode = False

    def _show_edit_panel(self, idx: int, ev: dict):
        self._clear_edit_panel()

        inner = ctk.CTkFrame(self._edit_panel, fg_color='transparent')
        inner.pack(fill='x', padx=12, pady=(8, 6))

        # Поле времени (есть у всех событий)
        t_row = ctk.CTkFrame(inner, fg_color='transparent')
        t_row.pack(fill='x', pady=(0, 4))
        ctk.CTkLabel(t_row, text="Время (мс):", width=130, anchor='w').pack(side='left')
        t_var = ctk.StringVar(value=f"{ev['t'] * 1000:.4f}")
        ctk.CTkEntry(t_row, textvariable=t_var, width=130).pack(side='left', padx=(0, 8))
        ctk.CTkButton(t_row, text="Применить", width=90,
                      command=lambda: self._apply_time(idx, t_var)).pack(side='left')

        e = ev['e']
        if e == 'key':
            self._build_key_edit(inner, idx, ev)
        elif e == 'move':
            self._build_move_edit(inner, idx, ev)
        elif e == 'click':
            ctk.CTkLabel(
                inner,
                text=f"Клик {'↓ нажатие' if ev.get('down') else '↑ отжатие'}  •  "
                     f"кнопка: {ev.get('btn', '?')}  •  позиция ({ev['x']}, {ev['y']})",
                font=ctk.CTkFont(size=11), text_color='gray',
            ).pack(anchor='w', pady=2)
        elif e == 'scroll':
            ctk.CTkLabel(
                inner,
                text=f"Прокрутка  dy={ev.get('dy', 0)}  •  позиция ({ev['x']}, {ev['y']})",
                font=ctk.CTkFont(size=11), text_color='gray',
            ).pack(anchor='w', pady=2)

    # ── Редактирование клавиши ────────────────────────────────────────────────

    def _build_key_edit(self, parent, idx: int, ev: dict):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', pady=4)

        direction = "↓ нажатие" if ev.get('down') else "↑ отжатие"
        ctk.CTkLabel(row, text=f"Клавиша ({direction}):", width=160, anchor='w').pack(side='left')
        self._key_lbl = ctk.CTkLabel(
            row, text=repr(ev.get('key', '?')),
            width=90, font=ctk.CTkFont(size=13, weight='bold'),
            fg_color=('#555', '#3a3a3a'), corner_radius=6)
        self._key_lbl.pack(side='left', padx=(0, 10))
        self._key_cap_btn = ctk.CTkButton(row, text="Сменить", width=90,
                                           command=lambda: self._begin_key_capture(idx))
        self._key_cap_btn.pack(side='left')
        self._key_cap_hint = ctk.CTkLabel(row, text="", text_color='orange',
                                           font=ctk.CTkFont(size=11))
        self._key_cap_hint.pack(side='left', padx=8)

    def _begin_key_capture(self, idx: int):
        if self._edit_cap_mode:
            return
        self._edit_cap_mode = True
        self._key_cap_btn.configure(state='disabled')
        self._key_cap_hint.configure(text="← нажми новую клавишу")
        self._key_lbl.configure(text="?")

        def _listen():
            def _on_press(key):
                if not self._edit_cap_mode:
                    return False
                self._edit_cap_mode = False
                try:
                    ch = key.char
                    key_str = ch if ch is not None else str(key)
                except AttributeError:
                    key_str = str(key)
                self.after(0, lambda: self._apply_key_change(idx, key_str, key))
                return False
            with pynput_kb.Listener(on_press=_on_press) as lst:
                lst.join()

        threading.Thread(target=_listen, daemon=True).start()

    def _apply_key_change(self, idx: int, key_str: str, key_obj):
        old_key = self._edit_events[idx].get('key', '')
        ev = self._edit_events[idx]
        ev['key'] = key_str
        ev['vk'] = getattr(key_obj, 'vk', None)

        # Обновляем парное событие (противоположное up/down с тем же ключом)
        is_down = ev.get('down', True)
        for j, other in enumerate(self._edit_events):
            if j != idx and other.get('e') == 'key' \
                    and other.get('key') == old_key \
                    and other.get('down') != is_down:
                other['key'] = key_str
                other['vk'] = getattr(key_obj, 'vk', None)
                self._update_tree_row(j)
                break

        self._update_tree_row(idx)
        self._key_lbl.configure(text=repr(key_str))
        self._key_cap_btn.configure(state='normal')
        self._key_cap_hint.configure(text="")
        self._mark_modified()

    # ── Редактирование перемещения мыши + перелёт ────────────────────────────

    def _build_move_edit(self, parent, idx: int, ev: dict):
        ovs = ev.get('overshoot') or {}

        ctk.CTkLabel(
            parent,
            text=f"Перемещение курсора в ({ev['x']}, {ev['y']})",
            font=ctk.CTkFont(size=11), text_color='gray',
        ).pack(anchor='w', pady=(0, 6))

        sep = ctk.CTkFrame(parent, height=1, fg_color='#3a3a3a')
        sep.pack(fill='x', pady=(0, 6))

        ctk.CTkLabel(parent,
                     text="Эффект перелёта курсора",
                     font=ctk.CTkFont(size=12, weight='bold')).pack(anchor='w')
        ctk.CTkLabel(parent,
                     text="Курсор пролетает чуть дальше цели и плавно возвращается обратно.",
                     font=ctk.CTkFont(size=10), text_color='gray').pack(anchor='w', pady=(0, 6))

        # Переключатель вкл/выкл
        ovs_row = ctk.CTkFrame(parent, fg_color='transparent')
        ovs_row.pack(fill='x', pady=(0, 4))
        ctk.CTkLabel(ovs_row, text="Эффект:", width=120, anchor='w').pack(side='left')
        self._ovs_var = ctk.StringVar(value="Вкл" if ovs else "Выкл")

        # Блок с настройками (покажем/скроем по переключателю)
        detail_frame = ctk.CTkFrame(parent, fg_color='transparent')

        ratio_row = ctk.CTkFrame(detail_frame, fg_color='transparent')
        ratio_row.pack(fill='x', pady=2)
        ctk.CTkLabel(ratio_row, text="Выброс (0.05–0.5):", width=180, anchor='w').pack(side='left')
        self._ratio_var = ctk.StringVar(value=f"{ovs.get('ratio', 0.10):.2f}")
        ctk.CTkEntry(ratio_row, textvariable=self._ratio_var, width=80).pack(side='left')
        ctk.CTkLabel(ratio_row, text="← доля от расстояния до цели",
                     font=ctk.CTkFont(size=10), text_color='gray').pack(side='left', padx=8)

        sharp_row = ctk.CTkFrame(detail_frame, fg_color='transparent')
        sharp_row.pack(fill='x', pady=2)
        ctk.CTkLabel(sharp_row, text="Резкость (1.0–10.0):", width=180, anchor='w').pack(side='left')
        self._sharp_var = ctk.StringVar(value=f"{ovs.get('sharpness', 3.0):.1f}")
        ctk.CTkEntry(sharp_row, textvariable=self._sharp_var, width=80).pack(side='left')
        ctk.CTkLabel(sharp_row, text="← 1 = плавно, 10 = очень резко",
                     font=ctk.CTkFont(size=10), text_color='gray').pack(side='left', padx=8)

        apply_btn = ctk.CTkButton(detail_frame, text="Применить эффект", width=160,
                                   command=lambda: self._apply_overshoot(idx))
        apply_btn.pack(anchor='w', pady=(6, 0))

        def _toggle_detail(v):
            if v == "Вкл":
                detail_frame.pack(fill='x', pady=(0, 4))
            else:
                detail_frame.pack_forget()
                self._edit_events[idx].pop('overshoot', None)
                self._update_tree_row(idx)
                self._mark_modified()

        ovs_seg = ctk.CTkSegmentedButton(
            ovs_row, values=["Выкл", "Вкл"],
            variable=self._ovs_var, width=130,
            command=_toggle_detail)
        ovs_seg.pack(side='left')

        # Показываем сразу, если уже включено
        if ovs:
            detail_frame.pack(fill='x', pady=(0, 4))

    def _apply_overshoot(self, idx: int):
        try:
            ratio    = max(0.01, min(1.0,  float(self._ratio_var.get())))
            sharp    = max(0.5,  min(20.0, float(self._sharp_var.get())))
        except ValueError:
            return
        if self._ovs_var.get() == "Вкл":
            self._edit_events[idx]['overshoot'] = {'ratio': ratio, 'sharpness': sharp}
        else:
            self._edit_events[idx].pop('overshoot', None)
        self._update_tree_row(idx)
        self._mark_modified()

    # ══════════════════════════════════════════════════════════════════════════
    # РЕДАКТОР — изменение времени
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_time(self, idx: int, t_var: ctk.StringVar):
        try:
            new_t = max(0.0, float(t_var.get()) / 1000.0)
        except ValueError:
            return
        self._edit_events[idx]['t'] = round(new_t, 6)
        self._update_tree_row(idx)
        self._mark_modified()

    def _mark_modified(self):
        self._edit_modified = True
        self._save_status_lbl.configure(
            text="Есть несохранённые изменения", text_color='orange')

    # ══════════════════════════════════════════════════════════════════════════
    # РЕДАКТОР — уникализация таймингов
    # ══════════════════════════════════════════════════════════════════════════

    def _editor_humanize(self):
        if not self._edit_events:
            return
        self._edit_events = _humanize_events(self._edit_events)
        self._sel_idx = -1
        self._refresh_tree()
        self._show_edit_placeholder()
        self._mark_modified()
        name = os.path.basename(self._edit_path) if self._edit_path else ''
        self._edit_info_lbl.configure(
            text=f"{name}  |  {len(self._edit_events):,} событий  |  🎲 тайминги уникализированы",
            text_color='#9b59b6')

    # ══════════════════════════════════════════════════════════════════════════
    # РЕДАКТОР — сохранение
    # ══════════════════════════════════════════════════════════════════════════

    def _save_events_to(self, path: str):
        events = sorted(self._edit_events, key=lambda e: e['t'])
        meta = dict(self._edit_meta)
        meta['event_count'] = len(events)
        meta['duration'] = round(events[-1]['t'], 3) if events else 0
        with open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(meta, ensure_ascii=False) + '\n')
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        self._edit_path = path
        self._edit_modified = False
        self._save_status_lbl.configure(
            text=f"Сохранено: {os.path.basename(path)}", text_color='#2ecc71')

    def _editor_save_current(self):
        if not self._edit_events:
            return
        if not self._edit_path:
            self._editor_save_as()
            return
        self._save_events_to(self._edit_path)

    def _editor_save_as(self):
        if not self._edit_events:
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить как",
            defaultextension='.rec',
            filetypes=[("MacroRec", "*.rec"), ("Все файлы", "*.*")],
            initialdir=self._dir_var.get(),
            initialfile=os.path.basename(self._edit_path) if self._edit_path else 'session.rec',
        )
        if path:
            self._save_events_to(path)
            self._edit_path_entry.delete(0, 'end')
            self._edit_path_entry.insert(0, path)

    # ══════════════════════════════════════════════════════════════════════════
    # ЛОГ
    # ══════════════════════════════════════════════════════════════════════════

    def _log(self, msg: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self._log_box.configure(state='normal')
        self._log_box.insert('end', f"[{ts}]  {msg}\n")
        self._log_box.see('end')
        self._log_box.configure(state='disabled')

    def _log_async(self, msg: str):
        self.after(0, lambda: self._log(msg))

    # ══════════════════════════════════════════════════════════════════════════
    # ПАПКА / ФАЙЛ
    # ══════════════════════════════════════════════════════════════════════════

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self._dir_var.get())
        if d:
            self._dir_var.set(d)
            self.cfg['save_dir'] = d
            _save_cfg(self.cfg)

    def _browse_file(self):
        f = filedialog.askopenfilename(
            title="Выбери запись",
            filetypes=[("MacroRec", "*.rec"), ("Все файлы", "*.*")],
            initialdir=self._dir_var.get(),
        )
        if f:
            self._file_entry.delete(0, 'end')
            self._file_entry.insert(0, f)

    # ══════════════════════════════════════════════════════════════════════════
    # ГОРЯЧАЯ КЛАВИША (запись)
    # ══════════════════════════════════════════════════════════════════════════

    def _begin_capture_hotkey(self):
        if self._capture_mode:
            return
        self._capture_mode = True
        self._hk_btn.configure(state='disabled')
        self._hk_hint.configure(text="← нажми любую клавишу")
        self._hk_lbl.configure(text="?")

        def _listen():
            with pynput_kb.Listener(on_press=self._on_new_hotkey_pressed) as lst:
                lst.join()

        threading.Thread(target=_listen, daemon=True).start()

    def _on_new_hotkey_pressed(self, key):
        if not self._capture_mode:
            return False
        self._capture_mode = False
        display = _key_display(key)
        self.cfg['hotkey']         = str(key)
        self.cfg['hotkey_display'] = display
        _save_cfg(self.cfg)
        self.after(0, lambda: self._hk_lbl.configure(text=display))
        self.after(0, lambda: self._hk_btn.configure(state='normal'))
        self.after(0, lambda: self._hk_hint.configure(text=""))
        self._log_async(f"Горячая клавиша: {display}")
        threading.Timer(0.1, self._start_global_hotkey).start()
        return False

    def _start_global_hotkey(self):
        if self._hk_listener:
            try: self._hk_listener.stop()
            except Exception: pass

        def _listen():
            with pynput_kb.Listener(on_press=self._on_global_key) as lst:
                self._hk_listener = lst
                lst.join()

        threading.Thread(target=_listen, daemon=True).start()

    def _on_global_key(self, key):
        if self._capture_mode or self._edit_cap_mode:
            return
        if str(key) == self.cfg.get('hotkey', ''):
            self.after(0, self._toggle_record)

    # ══════════════════════════════════════════════════════════════════════════
    # ЗАПИСЬ
    # ══════════════════════════════════════════════════════════════════════════

    def _toggle_record(self):
        if self._is_playing:
            return
        if self._is_recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        self._is_recording = True
        self._recorder = Recorder(mouse_hz=60)
        self._recorder.start()
        self._record_start = time.time()
        self._rec_btn.configure(
            text="⏹  ЗАВЕРШИТЬ ЗАПИСЬ",
            fg_color='#626567', hover_color='#7f8c8d')
        self._log(f"⏺  Запись началась  (стоп → {self.cfg['hotkey_display']})")

    def _stop_record(self):
        if not self._is_recording or not self._recorder:
            return
        self._is_recording = False
        self._recorder.stop()

        save_dir = self._dir_var.get() or str(Path.home())
        os.makedirs(save_dir, exist_ok=True)
        fname = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.rec"
        path  = os.path.join(save_dir, fname)
        self._recorder.save(path)

        dur   = self._recorder.duration
        count = self._recorder.count
        self._rec_btn.configure(text="⏺  НАЧАТЬ ЗАПИСЬ",
                                fg_color='#c0392b', hover_color='#e74c3c')
        self._rec_status.configure(text="")
        self._log(f"✅  {fname}  |  {_fmt(dur)}  |  {count:,} событий")
        self._file_entry.delete(0, 'end')
        self._file_entry.insert(0, path)

    # ══════════════════════════════════════════════════════════════════════════
    # ВОСПРОИЗВЕДЕНИЕ
    # ══════════════════════════════════════════════════════════════════════════

    def _start_play(self):
        if self._is_playing or self._is_recording:
            return
        path = self._file_entry.get().strip()
        if not path or not os.path.exists(path):
            self._log("❌  Выбери .rec файл")
            return
        self._is_playing = True
        self._play_btn.configure(state='disabled')
        speed = self.cfg.get('speed', 1.0)
        threading.Thread(target=self._play_thread, args=(path, speed), daemon=True).start()

    def _play_thread(self, path: str, speed: float):
        countdown = self.cfg.get('countdown', 10)
        for i in range(countdown, 0, -1):
            self.after(0, lambda c=i: self._play_status.configure(
                text=f"⏳  Запуск через {c} сек...", text_color='orange'))
            time.sleep(1)

        self.after(0, lambda: self._log(
            f"▶  Воспроизведение: {os.path.basename(path)}  ×{speed}"))

        try:
            player = Player(path)
            dur = player.duration

            def on_progress(idx, total, t_ev):
                pct = idx / total * 100 if total else 0
                self.after(0, lambda p=pct, tv=t_ev: self._play_status.configure(
                    text=f"▶  {_fmt(tv)} / {_fmt(dur)}   {p:.0f}%",
                    text_color='#2ecc71'))

            player.play(speed=speed, on_progress=on_progress)
            self.after(0, lambda: self._log(f"✅  Завершено: {os.path.basename(path)}"))

        except Exception as exc:
            self.after(0, lambda e=str(exc): self._log(f"❌  {e}"))

        finally:
            self._is_playing = False
            self.after(0, lambda: self._play_btn.configure(state='normal'))
            self.after(0, lambda: self._play_status.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════════
    # ТИК
    # ══════════════════════════════════════════════════════════════════════════

    def _tick(self):
        if self._is_recording and self._recorder:
            elapsed = time.time() - self._record_start
            self._rec_status.configure(
                text=f"⏺  {_fmt(elapsed)}   {self._recorder.count:,} событий")
        self.after(100, self._tick)

    # ══════════════════════════════════════════════════════════════════════════
    # ЗАКРЫТИЕ
    # ══════════════════════════════════════════════════════════════════════════

    def destroy(self):
        if self._is_recording and self._recorder:
            self._recorder.stop()
        if self._hk_listener:
            try: self._hk_listener.stop()
            except Exception: pass
        _save_cfg(self.cfg)
        super().destroy()


if __name__ == '__main__':
    App().mainloop()
