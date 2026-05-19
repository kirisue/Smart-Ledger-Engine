import os
import sys
import sqlite3
import json
import re
import random
import threading
import difflib
import subprocess
import shutil
import copy
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
from tkinter import ttk
import traceback

# ======= 导入必备解析库 =======
try:
    import pandas as pd
    import pdfplumber
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ======= 语音引擎组件 =======
try:
    import keyboard
    import pyaudio
    import wave
    import winsound
    import speech_recognition as sr
    import edge_tts
    import asyncio
    import pygame
    HAS_VOICE_ENGINE = True
except ImportError as e:
    HAS_VOICE_ENGINE = False
    print(f"语音引擎缺失: {e}")

# ================= 路径与环境配置 =================
def app_dir():
    if getattr(sys, "frozen", False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = app_dir()
DB_PATH = os.path.join(BASE_DIR, "ledger.db")
LOG_PATH = os.path.join(BASE_DIR, "解析诊断日志.txt")
CONFIG_FILE = os.path.join(BASE_DIR, "user_settings.json")
TEMPLATE_FILE = os.path.join(BASE_DIR, "preset.json")
ICON_PNG_NAME = "icon_64.png"
ICON_ICO_NAME = "icon.ico"

# ================= 🚀 核心：纯外部配置驱动引擎 =================

# 泛用财务状态 (默认模板，不含任何私有数据)
GENERIC_TEMPLATE = {
    "app_title": "极简智能账务收支系统 (全领域泛用版)",
    "calc_mode": "accumulate", 
    "terms": {
        "entity": "交易方/账户", "tag": "项目/标签", "flow_title": "收支流向",
        "flow_in": "流入", "flow_out": "流出", "flow_zero": "冲平/坏账",
        "amount": "数值/金额", "scene": "业务场景",
        "win_title": "🔴 记为应收/流入！", "lose_title": "🔵 记为应付/流出！",
        "voice_win": "新增一笔流入！", "voice_lose": "新增一笔流出！"
    },
    "scenes": ["不选择场景", "日常餐饮", "交通出行", "网购购物", "团队建设", "项目分润", "娱乐消费", "其他支出"],
    "ai_rules": {
        "role_prompt": "你是一个智能财务副官。提取交易方、项目标签、资金流向和变动金额。",
        "extraction_rule": "流入/应收记流向为'流入'，流出/应付记流向为'流出'。提取具体的数字作为金额，如未提及则金额为1。没有名字填'默认'。"
    },
    "lists": {"team_whitelist": {}, "name_whitelist": {}, "blacklist_map": {}, "common_blacklist": []},
    "streaks": {
        "win": {"3": ["📈 资金连续流入！", "✨ 进账连连～"], "5": ["💰 财神爷附体了吧！", "🔥 余额疯狂上涨～"], "10": ["🚀 收入破十连！", "🏆 赢麻了！"]},
        "lose": {"3": ["📉 怎么一直在往外掏钱？", "💦 注意预算。"], "5": ["💸 钱包君在滴血啊...", "🥶 全是支出。"], "7": ["🛑 赶紧踩个刹车吧！"]}
    }
}

def load_template():
    """加载外部配置：明文/密文双模无感自适应内核
    优先尝试 UTF-8 明文 JSON 解析；若失败则自动走 Base64+XOR 解密通道。"""
    tpl = copy.deepcopy(GENERIC_TEMPLATE)
    if os.path.exists(TEMPLATE_FILE):
        try:
            with open(TEMPLATE_FILE, 'rb') as f:
                raw_data = f.read()
        except Exception as e:
            print(f"读取模板文件失败: {e}")
            return tpl

        external = None

        # —— 第一路：明文解析 ——
        try:
            text_data = raw_data.decode('utf-8')
            external = json.loads(text_data)
        except Exception:
            # —— 第二路：密文解密 ——
            try:
                import base64
                decoded = base64.b64decode(raw_data)
                obfuscate_key = b"kirisu_ledger_dynamic_secret_2026"
                decrypted_bytes = bytes([b ^ obfuscate_key[i % len(obfuscate_key)] for i, b in enumerate(decoded)])
                text_data = decrypted_bytes.decode('utf-8')
                external = json.loads(text_data)
            except Exception as e2:
                print(f"加载外部配置失败（明文/密文均无效）: {e2}")
                return tpl

        # —— 配置合并（两路共用） ——
       # —— 配置合并（两路共用） ——
        for key in ("app_title", "calc_mode", "terms", "scenes", "ai_rules", "lists", "streaks", "export_templates"):
            if key in external:
                if isinstance(external[key], dict) and isinstance(tpl.get(key), dict):
                    tpl[key].update(external[key])
                else:
                    tpl[key] = external[key]
    return tpl

APP_TPL = load_template()
TERMS = APP_TPL.get("terms", {})
CALC_MODE = APP_TPL.get("calc_mode", "accumulate")

def T(key, default=""): return TERMS.get(key, default)

# ================= 动态加载彩蛋与名单 =================
session_streak_type = None  
session_streak_count = 0    
CACHED_NAMES = []
CACHED_TEAMS = []
LAST_RECORDED_NAME = ""
LAST_RECORDED_TEAM = ""

# 从外部 JSON 动态加载的名单数据（初始为空）
LISTS = APP_TPL.get("lists", {})
TEAM_WHITELIST_MAP = LISTS.get("team_whitelist", {})
NAME_WHITELIST_MAP = LISTS.get("name_whitelist", {})
BLACKLIST_MAP = LISTS.get("blacklist_map", {})
COMMON_BLACKLIST = set(LISTS.get("common_blacklist", []))

# ================= 动态 UI 配置 =================
DEFAULT_CONFIG = {
    "font_level": 1, 
    "ui_order": ["team", "name", "faction", "amount", "options"], 
    "show_battle": True, "ptt_hotkey": "F4", "tts_voice": "晓晓 (温柔女声)", "tts_rate": "正常"
}

VOICE_MAP = {
    "晓晓 (温柔女声)": "zh-CN-XiaoxiaoNeural", "云希 (干练男声)": "zh-CN-YunxiNeural",
    "云健 (沉稳大叔)": "zh-CN-YunjianNeural", "晓伊 (软萌萝莉)": "zh-CN-XiaoyiNeural"
}
RATE_MAP = {"正常": "+0%", "加快 15%": "+15%", "加快 30%": "+30%", "极致语速": "+50%", "放慢 15%": "-15%"}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: 
                cfg = {**DEFAULT_CONFIG, **json.load(f)}
                if "amount" not in cfg["ui_order"]: cfg["ui_order"].insert(3, "amount")
                if CALC_MODE == "121_replace" and "amount" in cfg["ui_order"]: cfg["ui_order"].remove("amount")
                return cfg
        except: pass
    cfg_ret = copy.deepcopy(DEFAULT_CONFIG)
    if CALC_MODE == "121_replace" and "amount" in cfg_ret["ui_order"]: cfg_ret["ui_order"].remove("amount")
    return cfg_ret

USER_CFG = load_config()
FONT_CONFIG = [{"label": 11, "entry": 11, "btn": 11, "title": 13}, {"label": 13, "entry": 13, "btn": 12, "title": 15}, {"label": 16, "entry": 17, "btn": 15, "title": 18}]

def get_font(type_name, bold=False):
    cfg = FONT_CONFIG[USER_CFG.get("font_level", 1)]
    size = cfg.get(type_name, 11)
    weight = "bold" if bold or (USER_CFG.get("font_level", 1) == 2 and type_name in ["label", "entry"]) else "normal"
    return ("微软雅黑", size, weight)

def resource_path(rel_path):
    base = getattr(sys, "_MEIPASS", None)
    if base: cand = os.path.join(base, rel_path); return cand if os.path.exists(cand) else os.path.join(app_dir(), rel_path)
    return os.path.join(app_dir(), rel_path)

def now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_logic_date():
    now = datetime.now()
    if now.hour < 2: return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")

def team_display(team): return team if team else "无" + T("tag", "标签")
def kind_name(kind_code):return T("kind_fight", T("flow_zero", "冲平")) if kind_code == "fight" else T("kind_normal", "标准")
def fmt_num(val): return int(val) if float(val).is_integer() else float(val)

def center_window(win, width, height, parent=None):
    win.update_idletasks()
    if parent and parent.winfo_viewable():
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (width // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (height // 2)
    else:
        x = (win.winfo_screenwidth() - width) // 2
        y = (win.winfo_screenheight() - height) // 2
    win.geometry('%dx%d+%d+%d' % (width, height, x, y))

# ================= 数据库 API =================
def safe_add_column(conn, table, col_def):
    try: conn.execute("ALTER TABLE {} ADD COLUMN {}".format(table, col_def)); conn.commit()
    except sqlite3.OperationalError: pass

def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, team_tag TEXT, balance REAL DEFAULT 0, last_seen TEXT, faction TEXT, kind TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, team_tag TEXT, action TEXT NOT NULL, time TEXT NOT NULL, delta REAL, faction TEXT, kind TEXT, note TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS blacklist_names (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS blacklist_teams (id INTEGER PRIMARY KEY AUTOINCREMENT, team_tag TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS whitelist_names (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS whitelist_teams (id INTEGER PRIMARY KEY AUTOINCREMENT, team_tag TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")  
    conn.commit()
    safe_add_column(conn, "accounts", "faction TEXT"); safe_add_column(conn, "accounts", "kind TEXT")
    safe_add_column(conn, "records", "delta REAL"); safe_add_column(conn, "records", "faction TEXT")
    safe_add_column(conn, "records", "kind TEXT"); safe_add_column(conn, "records", "note TEXT")
    conn.close(); refresh_caches() 

def refresh_caches():
    global CACHED_NAMES, CACHED_TEAMS
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT DISTINCT name FROM accounts WHERE name IS NOT NULL AND name != ''")
    names = [row[0] for row in c.fetchall()]
    c.execute("SELECT DISTINCT team_tag FROM accounts WHERE team_tag IS NOT NULL AND team_tag != ''")
    teams = [row[0] for row in c.fetchall()]
    conn.close()
    CACHED_NAMES = list(set(names) | COMMON_BLACKLIST)
    CACHED_TEAMS = list(set(teams))

def get_saved_battle():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute("SELECT value FROM settings WHERE key='locked_date'"); locked_date = c.fetchone()
        scene_list = APP_TPL.get("scenes", ["默认场景"]); scene_list = scene_list if scene_list else ["默认场景"]
        if locked_date and locked_date[0] == get_logic_date():
            c.execute("SELECT value FROM settings WHERE key='last_battle'"); r = c.fetchone()
            return r[0] if r else scene_list[0]
        return scene_list[0]
    except: return APP_TPL.get("scenes", ["默认场景"])[0]
    finally: conn.close()

def save_battle(battle_name):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('locked_date', ?)", (get_logic_date(),))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('last_battle', ?)", (battle_name,))
        conn.commit()
    except: pass
    finally: conn.close()

def refresh_battle_stats():
    if 'lbl_battle_stats' not in globals() or not lbl_battle_stats.winfo_exists(): return
    t_date = get_logic_date(); conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT note FROM records WHERE time LIKE ?", (f"{t_date}%",)); rows = c.fetchall(); conn.close()
    stats = {}
    scene_list = APP_TPL.get("scenes", ["默认场景"]); scene_list = scene_list if scene_list else ["默认场景"]
    for r in rows:
        note = r[0] if r[0] else ""
        m = re.search(r"\[(.*?)\]", note)
        if m:
            b = m.group(1)
            if b != scene_list[0] and b in scene_list: stats[b] = stats.get(b, 0) + 1
    if stats:
        stat_strs = [f"{k}({v}笔)" for k, v in stats.items()]
        lbl_battle_stats.config(text=f"📊 今日{T('scene', '场景')}: " + " | ".join(stat_strs))
    else: lbl_battle_stats.config(text=f"📊 今日{T('scene', '场景')}: 暂无数据")

# ================= 🚀 幽灵防卡死联想组件 =================
class AutocompleteEntry(tk.Entry):
    def __init__(self, master, get_words_func, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.get_words_func = get_words_func
        self.popup = None
        self.listbox = None
        self._timer = None
        self._hide_timer = None
        self.bind('<KeyRelease>', self.handle_keyrelease)
        self.bind('<FocusOut>', self.schedule_hide)
        self.bind('<Down>', self.move_down)
        self.bind('<Up>', self.move_up)
        self.bind('<Return>', self.select_item)

    def handle_keyrelease(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Left', 'Right', 'Tab', 'Escape', 'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R'): return
        if self._timer: self.after_cancel(self._timer)
        self._timer = self.after(150, self.do_update)

    def do_update(self):
        typed = self.get().strip()
        if not typed: self.hide_popup(); return
        words = self.get_words_func()
        hits = [w for w in words if typed.lower() in w.lower()][:30]
        if hits: self.show_popup(hits)
        else: self.hide_popup()

    def show_popup(self, hits):
        if not self.popup:
            self.popup = tk.Toplevel(self.winfo_toplevel())
            self.popup.wm_overrideredirect(True); self.popup.attributes('-topmost', True)
            self.listbox = tk.Listbox(self.popup, font=self.cget('font'), selectbackground="#1976d2", selectforeground="white", activestyle="none")
            self.listbox.pack(fill=tk.BOTH, expand=True)
            self.listbox.bind('<ButtonRelease-1>', self.select_item)
        self.listbox.delete(0, tk.END)
        for h in hits: self.listbox.insert(tk.END, h)
        x = self.winfo_rootx(); y = self.winfo_rooty() + self.winfo_height(); w = self.winfo_width(); h = min(len(hits) * 25 + 4, 160)
        self.popup.geometry(f"{w}x{h}+{x}+{y}")
        self.popup.deiconify()

    def schedule_hide(self, event=None):
        if self._hide_timer: self.after_cancel(self._hide_timer)
        self._hide_timer = self.after(200, self.do_hide)

    def do_hide(self):
        try: focus = self.focus_get()
        except KeyError: return
        if focus == self or (self.listbox and focus == self.listbox): return
        if self.popup: self.popup.withdraw()
            
    def hide_popup(self, event=None):
        if self.popup: self.popup.withdraw()

    def move_down(self, event):
        if self.popup and self.popup.state() == 'normal':
            sel = self.listbox.curselection()
            if not sel: self.listbox.selection_set(0); self.listbox.see(0)
            else:
                idx = sel[0]; self.listbox.selection_clear(idx)
                if idx < self.listbox.size() - 1: self.listbox.selection_set(idx + 1); self.listbox.see(idx + 1)
                else: self.listbox.selection_set(idx)
            return "break"

    def move_up(self, event):
        if self.popup and self.popup.state() == 'normal':
            sel = self.listbox.curselection()
            if sel:
                idx = sel[0]; self.listbox.selection_clear(idx)
                if idx > 0: self.listbox.selection_set(idx - 1); self.listbox.see(idx - 1)
                else: self.listbox.selection_set(0)
            return "break"

    def select_item(self, event=None):
        if self.popup and self.popup.state() == 'normal':
            sel = self.listbox.curselection()
            if sel:
                text = self.listbox.get(sel[0]); self.delete(0, tk.END); self.insert(0, text)
                self.hide_popup(); self.focus_set()
            return "break"

def get_last_recorded():
    global LAST_RECORDED_NAME, LAST_RECORDED_TEAM
    if LAST_RECORDED_NAME: return LAST_RECORDED_NAME, LAST_RECORDED_TEAM
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT name, team_tag FROM records ORDER BY id DESC LIMIT 1")
    r = c.fetchone(); conn.close()
    if r: LAST_RECORDED_NAME, LAST_RECORDED_TEAM = r[0], (r[1] if r[1] else "")
    return LAST_RECORDED_NAME, LAST_RECORDED_TEAM

# ================= 核心手工结算逻辑 =================
def get_fuzzy_match(text, kind='name'):
    if not text: return ""
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    if kind == 'team': c.execute("SELECT DISTINCT team_tag FROM accounts WHERE team_tag != '' AND team_tag IS NOT NULL")
    else: c.execute("SELECT DISTINCT name FROM accounts WHERE name != '' AND name IS NOT NULL")
    db_items = [row[0] for row in c.fetchall() if row[0]]
    conn.close()
    if not db_items: return text
    matches = difflib.get_close_matches(text, db_items, n=1, cutoff=0.6)
    return matches[0] if matches else text

def get_list_data(table_prefix, is_name):
    table = f"{table_prefix}_names" if is_name else f"{table_prefix}_teams"
    field = "name" if is_name else "team_tag"
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(f"SELECT {field} FROM {table} ORDER BY {field} COLLATE NOCASE")
    data = [x[0] for x in c.fetchall()]; conn.close(); return data

def is_in_list(table_prefix, is_name, value):
    if not value: return False
    table = f"{table_prefix}_names" if is_name else f"{table_prefix}_teams"
    field = "name" if is_name else "team_tag"
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(f"SELECT 1 FROM {table} WHERE {field}=?", (value,)); r = c.fetchone(); conn.close()
    return r is not None

def is_name_blacklisted(name): return is_in_list("blacklist", True, name)
def is_team_blacklisted(team): return is_in_list("blacklist", False, team)
def is_name_whitelisted(name): return is_in_list("whitelist", True, name)
def is_team_whitelisted(team): return is_in_list("whitelist", False, team)

def add_to_list(table_prefix, is_name, value):
    table = f"{table_prefix}_names" if is_name else f"{table_prefix}_teams"
    field = "name" if is_name else "team_tag"
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try: c.execute(f"INSERT INTO {table} ({field}) VALUES (?)", (value,)); conn.commit(); ok = True
    except: ok = False
    finally: conn.close()
    return ok

def del_from_list(table_prefix, is_name, value):
    table = f"{table_prefix}_names" if is_name else f"{table_prefix}_teams"
    field = "name" if is_name else "team_tag"
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(f"DELETE FROM {table} WHERE {field}=?", (value,)); conn.commit(); conn.close()

def add_record(name, team, action, delta, faction, kind, note=None):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO records (name, team_tag, action, time, delta, faction, kind, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
              (name, team, action, now_str(), delta, faction, kind, note)); conn.commit(); conn.close()

def create_account(name, team, balance, faction, kind):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO accounts (name, team_tag, balance, last_seen, faction, kind) VALUES (?, ?, ?, ?, ?, ?)", 
              (name, team, balance, now_str(), faction, kind)); conn.commit(); conn.close()

def update_account(acc_id, balance, name=None, faction=None, kind=None):
    sets = ["balance=?", "last_seen=?"]; params = [balance, now_str()]
    if name is not None: sets.append("name=?"); params.append(name)
    if faction is not None: sets.append("faction=?"); params.append(faction)
    if kind is not None: sets.append("kind=?"); params.append(kind)
    params.append(acc_id)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE accounts SET {} WHERE id=?".format(", ".join(sets)), tuple(params)); conn.commit(); conn.close()

def find_account(name, team):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    if team: c.execute("SELECT * FROM accounts WHERE team_tag=? ORDER BY id DESC LIMIT 1", (team,))
    else: c.execute("SELECT * FROM accounts WHERE name=? ORDER BY id DESC LIMIT 1", (name,))
    r = c.fetchone(); conn.close(); return r

def unpack_account(row):
    acc_id, name, team = row[0], row[1], row[2]
    balance = row[3] if row[3] is not None else 0; 
    last_seen = row[4] if len(row) > 4 and row[4] else ""
    faction = row[5] if len(row) > 5 else None; kind = row[6] if len(row) > 6 else None
    return acc_id, name, team, float(balance), last_seen, faction, kind

def find_old_team_by_name(name):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT team_tag FROM accounts WHERE name=? AND team_tag IS NOT NULL AND team_tag<>'' ORDER BY id DESC LIMIT 1", (name,))
    r = c.fetchone(); conn.close(); return r[0] if r else None

def next_team_variant(base_team):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT team_tag FROM accounts WHERE team_tag=? OR team_tag LIKE ?", (base_team, base_team + "#%"))
    tags = [x[0] for x in c.fetchall() if x and x[0]]; conn.close()
    max_n = 1; prefix = base_team + "#"
    for t in tags:
        if t == base_team: max_n = max(max_n, 1)
        elif t.startswith(prefix):
            try: max_n = max(max_n, int(t[len(prefix):]))
            except: pass
    return "{}#{}".format(base_team, max_n + 1)

def parse_name_input(text):
    if text in NAME_WHITELIST_MAP: return NAME_WHITELIST_MAP[text]
    if text in BLACKLIST_MAP: return BLACKLIST_MAP[text][0]
    return text

def parse_team_input(text):
    if text in TEAM_WHITELIST_MAP: return TEAM_WHITELIST_MAP[text]
    return text

def check_blacklist(name, team):
    warnings = []
    if name in BLACKLIST_MAP: warnings.append(BLACKLIST_MAP[name][1])
    if team in BLACKLIST_MAP: warnings.append(BLACKLIST_MAP[team][1])
    if name in COMMON_BLACKLIST: warnings.append(f"⚠️ 公共风险名单: {name}")
    if team in COMMON_BLACKLIST: warnings.append(f"⚠️ 公共风险名单{T('tag', '标签')}: {team}")
    return warnings

def check_whitelist(name, team):
    if name in NAME_WHITELIST_MAP: return True
    if team in TEAM_WHITELIST_MAP: return True
    return False

# ================= 🚀 连击语音彩蛋系统 =================
def check_streak(is_win):
    global session_streak_type, session_streak_count
    current_type = "win" if is_win else "lose"
    if session_streak_type == current_type:
        session_streak_count += 1
    else:
        session_streak_type = current_type
        session_streak_count = 1
        
    streaks = APP_TPL.get("streaks", {})
    streak_map = streaks.get(session_streak_type, {})
    thresholds = sorted([int(k) for k in streak_map.keys()], reverse=True)
    
    for t in thresholds:
        if session_streak_count == int(t):
            # 60%概率出彩蛋
            if random.random() < 0.6:
                msgs = streak_map[str(t)]
                return random.choice(msgs)
            else:
                return None
    return None

# ================= 🚀 语音对讲机模块 =================
last_spoken_text = ""

def get_ai_key():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try: c.execute("SELECT value FROM settings WHERE key='ai_key'"); r = c.fetchone(); return r[0] if r else ""
    except: return ""
    finally: conn.close()

def speak_text(text):
    """纯粹的本地 TTS 播报（后台线程执行，不阻塞 UI）"""
    global last_spoken_text
    if not text: return
    last_spoken_text = text
    if not HAS_VOICE_ENGINE: return

    def _speak():
        try:
            voice_key = USER_CFG.get("tts_voice", "晓晓 (温柔女声)")
            rate_key = USER_CFG.get("tts_rate", "正常")
            voice_id = VOICE_MAP.get(voice_key, "zh-CN-XiaoxiaoNeural")
            rate_str = RATE_MAP.get(rate_key, "+0%")
            temp_file = os.path.join(BASE_DIR, "_tts_temp.mp3")
            async def _do_tts():
                communicate = edge_tts.Communicate(text, voice_id, rate=rate_str)
                await communicate.save(temp_file)
            asyncio.run(_do_tts())
            if os.path.exists(temp_file):
                pygame.mixer.init()
                pygame.mixer.music.load(temp_file)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                pygame.mixer.music.stop()
                pygame.mixer.quit()
                try: os.remove(temp_file)
                except: pass
        except Exception as e:
            print(f"TTS 播报异常: {e}")

    threading.Thread(target=_speak, daemon=True).start()

def replay_last_voice():
    if last_spoken_text: speak_text(last_spoken_text)

def copy_cb(text):
    if text is None: return
    try:
        import pyperclip
        pyperclip.copy(str(text))
    except:
        r = tk.Tk(); r.withdraw(); r.clipboard_clear(); r.clipboard_append(str(text)); r.update(); r.destroy()
def fmt_line(n, tm, d, t, nt):
    try: d = float(d)
    except: d = 0
        
    b_name = ""
    ext = nt if nt else ""
    import re
    m = re.match(r"^\[(.*?)\]\s*(.*)", ext)
    if m:
        b_name = m.group(1)
        ext = m.group(2).strip()
        
    # 🌟 去 JSON 中寻找定制的导出格式，如果没有，就使用标准泛用格式保底
    tpls = APP_TPL.get("export_templates", {})
    
    if d > 0:
        fmt_str = tpls.get("flow_out", "{time} 【{team}】 {name} ➡ 流出/支出 | 数额: {amount} {remark}")
    elif d < 0:
        fmt_str = tpls.get("flow_in", "{time} 【{team}】 {name} ➡ 流入/收入 | " + T('amount', '数额') + ": {amount} {remark}")
    else:
        fmt_str = tpls.get("flow_zero", "{time} 【{team}】 {name} ➡ " + T('flow_zero', '冲平/坏账') + " {remark}")

    # 执行底层字符串智能替换 (支持替换的占位符有 {time}, {team}, {name}, {amount}, {remark})
    res = fmt_str.replace("{time}", str(t))
    res = res.replace("{team}", team_display(tm))
    res = res.replace("{name}", str(n))
    res = res.replace("{amount}", str(fmt_num(abs(d))))
    res = res.replace("{remark}", f"(备注:{ext})" if ext else "")
    
    return res.strip()
    
def export_multi_rows(table, sel):
    if not sel: return
    rows = []
    for iid in sel:
        v = table.item(iid, "values")
        if not v: continue
        if len(v) >= 8: 
            try: d = float(str(v[5]).strip().replace("+", ""))
            except: d = 0
            rows.append((v[0], v[1], d, v[6], v[7]))
        else: 
            try: b = float(str(v[4]).strip())
            except: b = 0
            d = 1 if b > 0 else (-1 if b < 0 else 0) if CALC_MODE == "121_replace" else -b
            rows.append((v[0], v[1], d, v[5] if len(v) > 5 else now_str(), ""))
            
    rows.sort(key=lambda x: x[3])
    lines = [fmt_line(n, tm, d, t, nt) for n, tm, d, t, nt in rows]
    text = "\n".join(lines).strip()
    
    if text:
        copy_cb(text)
        if len(sel) == 1: 
            messagebox.showinfo("已导出本条", "本条数据已复制到剪贴板！\n\n" + text)
        else: 
            messagebox.showinfo("批量导出成功", f"成功导出并复制了 {len(rows)} 条选中数据！")

def export_btn_handler():
    sel = records_table.selection()
    t = records_table
    if not sel:
        sel = accounts_table.selection()
        t = accounts_table
        
    if not sel:
        if not messagebox.askyesno("未选择", "未在任何表格中选中数据。\n是否为您自动导出历史最近 20 条记录？"): 
            return
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name, team_tag, delta, time, note FROM records ORDER BY id DESC LIMIT 20")
        rows = list(reversed(c.fetchall()))
        conn.close()
        
        lines = [fmt_line(n, tm, d, t, nt) for n, tm, d, t, nt in rows]
        text = "\n".join(lines).strip()
        if text:
            copy_cb(text)
            messagebox.showinfo("已复制", "已自动复制最近20条历史记录！")
    else:
        export_multi_rows(t, sel)

def export_full_history():
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, team_tag, faction, action, delta, time, note FROM records ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    
    if not rows: 
        messagebox.showinfo("提示", "数据库中暂无任何历史数据！")
        return
    
    lines = [f"📊 [{APP_TPL.get('app_title', '泛用记账引擎')}] 全量历史战绩报账单", "-"*45]
    for i, r in enumerate(rows, 1):
        line_text = fmt_line(r[0], r[1], r[4], r[5], r[6])
        lines.append(f"{i}. {line_text}")
        
    lines.append("-" * 45 + "\n*数据由引擎生成*")
    full_text = "\n".join(lines)
    copy_cb(full_text)
    
    if messagebox.askyesno("全量导出成功", f"共提取了 {len(rows)} 条历史记录！\n内容已复制到剪贴板，您可以直接去群里粘贴。\n\n是否同时将它保存为 txt 文本文件留底备份？"):
        path = filedialog.asksaveasfilename(title="保存全量账单", defaultextension=".txt", filetypes=[("Text Files", "*.txt")], initialfile=f"全量账单_{datetime.now().strftime('%Y%m%d')}.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(full_text)

def import_template():
    path = filedialog.askopenfilename(title="选择业务模板 JSON 文件", filetypes=[("JSON", "*.json")])
    if not path: return
    try:
        with open(path, 'rb') as f:
            raw_data = f.read()
        
        # 🛡️ 智能验证第一路：尝试明文解析
        try:
            text_data = raw_data.decode('utf-8')
            tpl = json.loads(text_data)
        except Exception:
            # 🛡️ 智能验证第二路：尝试密文解密解析
            import base64
            decoded = base64.b64decode(raw_data)
            obfuscate_key = b"kirisu_ledger_dynamic_secret_2026"
            decrypted_bytes = bytes([b ^ obfuscate_key[i % len(obfuscate_key)] for i, b in enumerate(decoded)])
            text_data = decrypted_bytes.decode('utf-8')
            tpl = json.loads(text_data)

        # 校验文件是否真的是我们的业务模板
        if not isinstance(tpl, dict) or "terms" not in tpl: 
            messagebox.showerror("错误", "无效的模板格式：缺少核心业务字典。")
            return
        
        # 🌟 核心安全升级：无论群友导入的是明文还是密文，本地存盘时一律强行自动加密！
        clean_text = json.dumps(tpl, ensure_ascii=False, separators=(',', ':'))
        import base64
        obfuscate_key = b"kirisu_ledger_dynamic_secret_2026"
        raw_bytes = clean_text.encode('utf-8')
        encrypted_bytes = bytes([b ^ obfuscate_key[i % len(obfuscate_key)] for i, b in enumerate(raw_bytes)])
        final_output = base64.b64encode(encrypted_bytes)
        
        target = os.path.join(BASE_DIR, "preset.json")
        with open(target, 'wb') as f:
            f.write(final_output)
            
        messagebox.showinfo("成功", "业务模板已成功导入并自动完成底层加密锁死！\n请重启程序以生效。")
    except Exception as e: 
        messagebox.showerror("错误", f"导入失败：文件可能已损坏，或非本软件支持的格式。\n\n详情: {e}")

# ================= 核心提交逻辑 =================
def handle_submit(from_ai=False):
    global LAST_RECORDED_NAME, LAST_RECORDED_TEAM
    name = entry_name.get().strip()
    team = entry_team.get().strip()
    faction = faction_var.get()
    is_fight = fight_var.get()
    use_bl = use_common_bl_var.get()
    try: amount = float(amount_var.get()) if amount_var.get().strip() else 1.0
    except: amount = 1.0

    if not name and not team:
        messagebox.showwarning("提示", f"请填写{T('entity', '交易方')}或{T('tag', '标签')}！")
        return
    if not is_fight and faction == "请选择":
        messagebox.showwarning("提示", f"请选择{T('flow_title', '收支流向')}！")
        return

    name = parse_name_input(name)
    team = parse_team_input(team)

    if use_bl:
        warnings = check_blacklist(name, team)
        if warnings:
            warn_text = "\n".join(warnings)
            if not messagebox.askyesno("⚠️ 风险拦截", f"检测到以下风险:\n{warn_text}\n\n是否仍然继续记录？"):
                return

    if is_fight:
        faction = T("flow_zero", "冲平/坏账")
        kind = "fight"
        action = T("flow_zero", "冲平/坏账")
    else:
        kind = "normal"
        if faction == T("flow_in", "流入"):
            action = T("flow_in", "流入")
        else:
            action = T("flow_out", "流出")

    # 精确的 is_win 赋值
    is_win = None

    if CALC_MODE == "121_replace":
        raw_delta = -1 if faction == T("flow_in", "流入") else 1
        acc = find_account(name, team)
        if acc:
            acc_id, _, _, balance, _, _, _ = unpack_account(acc)
            if balance == 0:
                delta = raw_delta
                new_balance = delta
                action = "首次/重判"
                is_win = (delta < 0)
            elif balance < 0:
                delta = 1
                new_balance = 1
                action = f"转为{T('flow_out', '流出')}(+1)"
                is_win = False
            else:
                delta = -1
                new_balance = -1
                action = f"转为{T('flow_in', '流入')}(-1)"
                is_win = True
            update_account(acc_id, new_balance)
        else:
            delta = raw_delta
            new_balance = delta
            create_account(name, team, new_balance, faction, kind)
            is_win = (delta < 0)
    else:
        delta = -amount if faction == T("flow_in", "流入") else amount
        acc = find_account(name, team)
        if acc:
            acc_id, _, _, balance, _, _, _ = unpack_account(acc)
            new_balance = balance + delta
            update_account(acc_id, new_balance)
        else:
            new_balance = delta
            create_account(name, team, new_balance, faction, kind)
        is_win = (delta < 0)

    # 冲平不记输赢
    if is_fight:
        is_win = None

    battle = battle_var.get()
    note = f"[{battle}]" if battle and battle != APP_TPL.get("scenes", ["默认场景"])[0] else None
    add_record(name, team, action, delta, faction, kind, note)

    LAST_RECORDED_NAME = name
    LAST_RECORDED_TEAM = team

    streak_msg = check_streak(is_win) if is_win is not None else None
    
    if  streak_msg:
        speak_text(streak_msg)
        show_result_popup(name, team, action, delta, new_balance, streak_msg)
    else:
        voice_text = ""
        # 🌟 核心修复：如果是打架队，不进行输赢播报
        if is_fight:
            voice_text = ""
        # 🌟 核心修复：直接根据最终严谨推导的 is_win 结果来决定播报什么，彻底跟下拉框解耦！
        elif is_win:
            voice_text = T("voice_win", "新增一笔流入！")
        else:
            voice_text = T("voice_lose", "新增一笔流出！")

        if enable_voice_var.get() and voice_text:
            speak_text(voice_text)
        show_result_popup(name, team, action, delta, new_balance)

    entry_name.delete(0, tk.END)
    entry_team.delete(0, tk.END)
    faction_var.set("请选择")
    fight_var.set(False)
    amount_var.set("1")
    refresh_all_tables()

def show_result_popup(name, team, action, delta, new_balance, streak_msg=None):
    top = tk.Toplevel(root)
    top.title("结算提示")
    center_window(top, 450, 260, root)
    top.transient(root)
    
    if delta > 0:
        big_text = T("lose_title", "🔵 记为应付/流出！")
        color = "#1976d2"
    elif delta < 0:
        big_text = T("win_title", "🔴 记为应收/流入！")
        color = "#d32f2f"
    else:
        big_text = "⚪ 冲平/坏账"
        color = "#666666"

    tk.Label(top, text=big_text, font=("微软雅黑", 28, "bold"), fg=color).pack(pady=(20, 5))
    if streak_msg:
        tk.Label(top, text=f"【连击×{session_streak_count}】 {streak_msg}", font=("微软雅黑", 11, "bold"), fg="#e65100").pack(pady=2)

    sub_text = f"{T('entity', '交易方')}: {name} | {T('tag', '标签')}: {team_display(team)}\n{T('amount', '金额')}: {fmt_num(abs(delta))} | 余额: {fmt_num(new_balance)}"
    tk.Label(top, text=sub_text, font=("微软雅黑", 11), fg="#666666", justify="center").pack(pady=5)
    tk.Button(top, text="我知道了 (2.5秒自动消失)", width=22, command=top.destroy, font=("微软雅黑", 11, "bold"), bg="#f0f0f0").pack(pady=10)
    top.after(2500, lambda: top.destroy() if top.winfo_exists() else None)

# ================= 🚀 语音对讲机 PTT 线程 =================
class PTTThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.recognizer = sr.Recognizer()
        self.is_running = True

    def run(self):
        if not HAS_VOICE_ENGINE: return
        hotkey = USER_CFG.get("ptt_hotkey", "F4")
        if 'lbl_ai_status' in globals():
            root.after(0, lambda: lbl_ai_status.config(text=f"🎧 对讲机就绪：长按 [{hotkey}] 键说话", fg="#2e7d32"))
        
        while self.is_running:
            try:
                keyboard.wait(hotkey)
                winsound.Beep(1000, 150)
                if 'lbl_ai_status' in globals(): root.after(0, lambda: lbl_ai_status.config(text=f"🎙️ 正在录音... (松开 {hotkey} 结束)", fg="#d32f2f"))
                
                try: mic_str = mic_var.get()
                except NameError: mic_str = ""
                    
                mic_idx = None
                if mic_str:
                    try: mic_idx = int(mic_str.split(" - ")[0])
                    except: pass

                p = pyaudio.PyAudio()
                stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, input_device_index=mic_idx, frames_per_buffer=1024)
                frames = []
                
                while keyboard.is_pressed(hotkey):
                    try:
                        data = stream.read(1024, exception_on_overflow=False)
                        frames.append(data)
                    except: pass
                    
                stream.stop_stream(); stream.close(); p.terminate()
                
                winsound.Beep(800, 100)
                if 'lbl_ai_status' in globals(): root.after(0, lambda: lbl_ai_status.config(text="🧠 Whisper 大脑提词中...", fg="#1976d2"))
                
                temp_wav = os.path.join(BASE_DIR, "temp_ptt.wav")
                wf = wave.open(temp_wav, 'wb')
                wf.setnchannels(1); wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
                wf.setframerate(16000); wf.writeframes(b''.join(frames)); wf.close()
                
                with sr.AudioFile(temp_wav) as source:
                    audio_data = self.recognizer.record(source)
                    
                    # 🌟 动态获取 Whisper 底层听力字典，没有则使用通用保底
                    whisper_p = APP_TPL.get("ai_rules", {}).get("whisper_prompt", "这是一段普通话语音，包含：交易方、项目标签、收支流向、流入、流出、记账、冲平、默认名等词汇。")
                    
                    text = self.recognizer.recognize_whisper(
                        audio_data, 
                        language="zh",
                        initial_prompt=whisper_p
                    )
                    
                    if text:
                        if 'ai_entry' in globals():
                            root.after(0, lambda: ai_entry.delete(0, tk.END))
                            root.after(0, lambda: ai_entry.insert(0, text))
                        root.after(0, run_ai_command)
                    else:
                        if 'lbl_ai_status' in globals(): root.after(0, lambda: lbl_ai_status.config(text=f"🎧 对讲机就绪：长按 [{hotkey}] 键说话", fg="#2e7d32"))

            except Exception as e:
                err_name = type(e).__name__
                if err_name == "UnknownValueError": err_msg = "未检测到有效声音(麦克风选错或太小声)"
                else: err_msg = str(e) if str(e) else err_name
                
                if "soundfile" in err_msg.lower(): err_msg = "缺失音频库，请在CMD运行: pip install soundfile"
                if 'lbl_ai_status' in globals(): root.after(0, lambda em=err_msg: lbl_ai_status.config(text=f"🎧 待命 (错误: {em[:40]})", fg="#d32f2f"))

def start_voice_assistant():
    if not shutil.which("ffmpeg") and not os.path.exists(os.path.join(BASE_DIR, "ffmpeg.exe")):
        messagebox.showerror("🚨 缺失核心组件", "Whisper 大脑需要 FFmpeg 才能解码录音！\n\n您需要：\n1. 去网上下载一个 FFmpeg。\n2. 把解压出来的 ffmpeg.exe 复制到当前记账软件的这个文件夹下。")
        return
    global assistant_thread
    if not HAS_VOICE_ENGINE:
        messagebox.showerror("缺少库", "未安装对讲机核心库，请按照提示在 CMD 安装相关 pip 库！")
        return
    if 'assistant_thread' in globals() and assistant_thread.is_alive():
        messagebox.showinfo("提示", "对讲机已经在后台运行中了！")
        return
    assistant_thread = PTTThread()
    assistant_thread.start()

def execute_ai_result(item):
    n = str(item.get("name", "")).strip(); t = str(item.get("team", "")).strip()
    fac = str(item.get("my_faction", "")).strip(); is_fight = item.get("is_fight", False)
    last_n, last_t = get_last_recorded()

    if "默认" in n or "上把" in n:
        n = last_n
        if not n: speak_text("未找到历史记录，无法使用默认名字"); return
    else: n = get_fuzzy_match(n, 'name')

    if "默认" in t or "上把" in t: t = last_t
    else: t = get_fuzzy_match(t, 'team')

    entry_name.delete(0, tk.END); entry_name.insert(0, n)
    entry_team.delete(0, tk.END); entry_team.insert(0, t)

    if fac in [T("flow_in", "流入"), T("flow_out", "流出")]: faction_var.set(fac)

    fight_var.set(is_fight); faction_combo.config(state="disabled" if is_fight else "readonly")
    handle_submit(from_ai=True)

def run_ai_command():
    text = ai_entry.get().strip()
    if not text: return
    api_key = get_ai_key()
    if not api_key: messagebox.showwarning("未配置 AI", "请在【工具/设置】菜单中配置 DeepSeek API 密钥！"); return
    lbl_ai_status.config(text="🤖 思考中..."); root.update_idletasks()

    def _task():
        try:
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            sys_prompt = APP_TPL.get("ai_rules", {}).get("role_prompt", "你是一个智能财务副官。提取交易方、项目标签、资金流向和变动金额。")
            data = {"model": "deepseek-chat", "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": text}], "temperature": 0.1, "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=data, timeout=15)
            if resp.status_code == 200:
                res = resp.json()['choices'][0]['message']['content']
                root.after(0, lambda: execute_ai_result(json.loads(res)))
            else: root.after(0, lambda: messagebox.showerror("AI 报错", f"API 拒绝请求: {resp.status_code}"))
        except Exception as e: root.after(0, lambda: messagebox.showerror("网络/解析错误", str(e)))
        finally:
            hotkey = USER_CFG.get("ptt_hotkey", "F4")
            if 'lbl_ai_status' in globals(): root.after(0, lambda: lbl_ai_status.config(text=f"🎧 对讲机待命... [{hotkey}]", fg="#2e7d32"))
            if 'ai_entry' in globals(): root.after(0, lambda: ai_entry.delete(0, tk.END))

    threading.Thread(target=_task, daemon=True).start()

# ================= 名单管理窗口 =================
def open_list_window(title_str, table_prefix, is_name):
    win = tk.Toplevel(root); win.title(title_str); center_window(win, 420, 360, root); win.transient(root)
    tk.Label(win, text=T('entity', '交易方') if is_name else T('tag', '标签'), font=get_font("label")).pack(pady=6)

    entry = tk.Entry(win, width=35, font=get_font("entry")); entry.pack(pady=4)
    listbox = tk.Listbox(win, width=45, height=12, font=get_font("entry")); listbox.pack(pady=8, padx=10, fill=tk.BOTH, expand=True)

    def reload_list():
        listbox.delete(0, tk.END)
        for item in get_list_data(table_prefix, is_name): listbox.insert(tk.END, item)

    def add_item():
        v = entry.get().strip()
        if not v: return
        if not add_to_list(table_prefix, is_name, v): messagebox.showwarning("提示", "已存在于该名单")
        entry.delete(0, tk.END); reload_list(); refresh_caches()

    def del_item():
        sel = listbox.curselection()
        if not sel: return
        item_to_del = listbox.get(sel[0])
        if messagebox.askyesno("⚠️ 防呆二次确认", f"确定要将【{item_to_del}】从该名单中永久删除吗？"):
            del_from_list(table_prefix, is_name, item_to_del); reload_list(); refresh_caches()

    btn_frame = tk.Frame(win); btn_frame.pack(pady=6)
    tk.Button(btn_frame, text="添加", width=10, command=add_item, font=get_font("btn")).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="删除选中", width=10, command=del_item, font=get_font("btn")).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="刷新", width=10, command=reload_list, font=get_font("btn")).pack(side=tk.LEFT, padx=6)
    reload_list()

# ================= 表格右键菜单 =================
def delete_multiple(table, sel):
    if not messagebox.askyesno("防抖确认", f"⚠️ 警告：确定要彻底删除选中的 {len(sel)} 条数据吗？\n删除后不可恢复！"): return
    conn = sqlite3.connect(DB_PATH); is_rec = False
    for iid in sel:
        v = table.item(iid, "values")
        if not v: continue
        tm_tag = v[1] if v[1] and v[1] != "无" + T("tag", "标签") else ""

        if len(v) >= 8: is_rec = True; conn.execute("DELETE FROM records WHERE name=? AND (team_tag=? OR team_tag IS NULL) AND time=? AND action=?", (v[0], tm_tag, v[6], v[4]))
        else: conn.execute("DELETE FROM accounts WHERE name=? AND (team_tag=? OR team_tag IS NULL)", (v[0], tm_tag))
    conn.commit(); conn.close(); refresh_all_tables()
    if is_rec: messagebox.showinfo("删除成功", "记录已彻底删除！\n\n📌 提示：如果是不小心记错账了，删除历史记录后，该人的【当前账户余额】不会自动倒退。\n最稳妥的方法是：直接在左边『当前账目』里右键把他的账户也删了，然后重新录入一笔！")
    else: messagebox.showinfo("删除成功", "选中账户已彻底清空！")

def change_team_tag_gui(name, old_team_display):
    o_t = "" if old_team_display == ("无" + T("tag", "标签")) else old_team_display

    top = tk.Toplevel(root); top.title(f"🔄 更换{T('tag', '标签')}并同步历史"); center_window(top, 400, 220, root); top.transient(root); top.grab_set()
    tk.Label(top, text=f"为【{name}】更换新{T('tag', '标签')}", font=get_font("title", True), fg="#1976d2").pack(pady=(15, 5))
    tk.Label(top, text=f"原{T('tag', '标签')}: {old_team_display}", font=get_font("label"), fg="gray").pack()
    new_team_var = tk.StringVar(); entry = tk.Entry(top, textvariable=new_team_var, font=get_font("entry"), width=25); entry.pack(pady=10); entry.focus_set()

    def do_update():
        new_t = new_team_var.get().strip()
        if not new_t: messagebox.showwarning("提示", f"请输入新{T('tag', '标签')}！", parent=top); return
        if new_t == o_t: messagebox.showinfo("提示", f"新{T('tag', '标签')}与原{T('tag', '标签')}相同，无需修改。", parent=top); top.destroy(); return

        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT id, balance FROM accounts WHERE name=? AND team_tag=?", (name, new_t)); existing = c.fetchone()
        c.execute("SELECT id, balance FROM accounts WHERE name=? AND (team_tag=? OR team_tag IS NULL)", (name, o_t)); current = c.fetchone()
        if existing and current and existing[0] != current[0]:
            new_bal = existing[1] + current[1]
            c.execute("UPDATE accounts SET balance=?, last_seen=? WHERE id=?", (new_bal, now_str(), existing[0]))
            c.execute("DELETE FROM accounts WHERE id=?", (current[0],))
        else:
            c.execute("UPDATE accounts SET team_tag=?, last_seen=? WHERE name=? AND (team_tag=? OR team_tag IS NULL)", (new_t, now_str(), name, o_t))
        c.execute("UPDATE records SET team_tag=?, note = note || ? WHERE name=? AND (team_tag=? OR team_tag IS NULL)", (new_t, f" (原{T('tag', '标签')}:{o_t})", name, o_t))
        conn.commit(); conn.close(); refresh_caches(); refresh_all_tables()
        messagebox.showinfo("同步成功", f"🎉 已将【{name}】的历史记录和当前账户的{T('tag', '标签')}全部同步为：{new_t}", parent=root); top.destroy()


    tk.Button(top, text="确认同步修改", command=do_update, font=get_font("btn", True), bg="#4caf50", fg="white", width=18).pack(pady=5)

def table_right_click_menu(t, e):
    rid = t.identify_row(e.y)
    if not rid: return
    sel = t.selection()
    if rid not in sel: t.selection_set(rid); t.focus(rid); sel = (rid,)
    m = tk.Menu(root, tearoff=0)
    if len(sel) == 1:
        v = t.item(sel[0], "values")
        if not v: return
        m.add_command(label=f"复制{T('entity', '交易方')}", command=lambda: copy_cb(v[0])); m.add_command(label=f"复制{T('tag', '标签')}", command=lambda: copy_cb(v[1])); m.add_separator()
        m.add_command(label=f"🔄 换{T('tag', '标签')} (同步历史)", command=lambda: change_team_tag_gui(v[0], v[1])); m.add_separator()

        if len(v) >= 8: m.add_command(label="❌ 删除该条历史记录 (防手抖)", command=lambda: delete_multiple(t, sel))
        else: m.add_command(label="❌ 删除此账户(完全清空此人)", command=lambda: delete_multiple(t, sel))
    else: 
        m.add_command(label=f"❌ 批量删除这 {len(sel)} 条", command=lambda: delete_multiple(t, sel))

    m.tk_popup(e.x_root, e.y_root)

def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存配置失败: {e}")

def refresh_all_tables():
    kw_a = search_kw_acc.get().strip(); kw_r = search_kw_rec.get().strip()

    for r in accounts_table.get_children(): accounts_table.delete(r)
    for r in records_table.get_children(): records_table.delete(r)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(f"SELECT name, team_tag, faction, kind, balance, last_seen FROM accounts {'WHERE name LIKE ? OR team_tag LIKE ? OR last_seen LIKE ?' if kw_a else ''} ORDER BY last_seen DESC LIMIT 500", (f"%{kw_a}%", f"%{kw_a}%", f"%{kw_a}%") if kw_a else ())
    for r in c.fetchall(): accounts_table.insert("", tk.END, values=(r[0], r[1], r[2] or "未设置", kind_name(r[3]), fmt_num(r[4]), r[5]), tags=(("win_tag",) if r[4] > 0 else (("lose_tag",) if r[4] < 0 else ())))
    c.execute(f"SELECT name, team_tag, faction, kind, action, delta, time, note FROM records {'WHERE name LIKE ? OR team_tag LIKE ? OR time LIKE ?' if kw_r else ''} ORDER BY id DESC LIMIT 1200", (f"%{kw_r}%", f"%{kw_r}%", f"%{kw_r}%") if kw_r else ())
    for r in c.fetchall(): records_table.insert("", tk.END, values=(r[0], r[1], r[2] or "未设置", kind_name(r[3]), r[4], fmt_num(r[5]), r[6], r[7] or ""), tags=(("win_tag",) if (r[5] or 0) < 0 else (("lose_tag",) if (r[5] or 0) > 0 else ())))
    conn.close(); refresh_battle_stats()

# ================= 🌟 GUI 初始化 =================
try:
    init_db()
    root = tk.Tk()

    root.title(APP_TPL.get("app_title", "通用记账系统"))
    root.geometry("1280x800")
    center_window(root, 1280, 800)

    try:
        if os.path.exists(resource_path(ICON_ICO_NAME)): root.iconbitmap(resource_path(ICON_ICO_NAME))
        elif os.path.exists(resource_path(ICON_PNG_NAME)): root.iconphoto(True, tk.PhotoImage(file=resource_path(ICON_PNG_NAME)))
    except: pass

    # 🌟 全局控制变量
    enable_voice_var = tk.BooleanVar(value=True)
    speaker_var = tk.StringVar(value="默认设备(仅自己)")
    voice_var = tk.StringVar(value=USER_CFG.get("tts_voice", "晓晓 (温柔女声)"))
    rate_var = tk.StringVar(value=USER_CFG.get("tts_rate", "正常"))

    # ================= 界面排版自定义 =================
    def open_custom_ui_win():
        win = tk.Toplevel(root)
        win.title("界面视觉与排版设置")
        center_window(win, 460, 680, root)
        win.transient(root)
        win.grab_set()

        tk.Label(win, text="1. 场景显示设置", font=("", 11, "bold"), fg="#1976d2").pack(pady=(15, 5))
        show_battle_var = tk.BooleanVar(value=USER_CFG.get("show_battle", True))
        tk.Checkbutton(win, text=f"显示右侧的{T('scene', '场景')}选择框 (取消勾选即隐藏)", variable=show_battle_var, font=("", 10, "bold"), fg="#d32f2f").pack()

        tk.Label(win, text="\n2. 字体大小调节 (适合大屏)", font=("", 11, "bold"), fg="#1976d2").pack(pady=(10, 5))
        f_var = tk.IntVar(value=USER_CFG.get("font_level", 1))
        f_frame = tk.Frame(win); f_frame.pack()
        for i, txt in enumerate(["标准经典版", "清晰适中版 (推荐)", "指挥大字加粗版"]):
            tk.Radiobutton(f_frame, text=txt, variable=f_var, value=i, font=("", 10)).pack(anchor="w", pady=2)

        tk.Label(win, text="\n3. 左侧模块排版顺序 (填数字1-4)", font=("", 11, "bold"), fg="#1976d2").pack(pady=(10, 5))
        order_vars = {}
        names_map = {"team": f"{T('tag', '标签')}输入框", "name": f"{T('entity', '交易方')}输入框", "faction": f"{T('flow_title', '收支流向')}选择框", "amount": "金额输入框", "options": "底部选项框"}

        current_order = USER_CFG.get("ui_order", ["team", "name", "faction", "amount", "options"])
        
        for k in current_order:
            if k not in names_map: continue
            f = tk.Frame(win); f.pack(pady=4)
            tk.Label(f, text=names_map[k], width=15, anchor="w", font=("", 10)).pack(side=tk.LEFT, padx=10)
            v = tk.StringVar(value=str(current_order.index(k) + 1))
            tk.Entry(f, textvariable=v, width=8, font=("", 10), justify="center").pack(side=tk.LEFT)
            order_vars[k] = v

        tk.Label(win, text="\n4. 智能对讲机快捷键", font=("", 11, "bold"), fg="#1976d2").pack(pady=(10, 5))
        hotkey_var = tk.StringVar(value=USER_CFG.get("ptt_hotkey", "F4"))
        f_hk = tk.Frame(win); f_hk.pack()
        tk.Label(f_hk, text="长按对讲热键:", font=("", 10)).pack(side=tk.LEFT)
        tk.Entry(f_hk, textvariable=hotkey_var, width=15, font=("", 10), justify="center").pack(side=tk.LEFT, padx=5)

        def save_and_restart():
            USER_CFG["show_battle"] = show_battle_var.get()
            USER_CFG["font_level"] = f_var.get()
            USER_CFG["ptt_hotkey"] = hotkey_var.get().strip() or "F4"
            try:
                new_order = sorted(order_vars.keys(), key=lambda k: int(order_vars[k].get()))
                USER_CFG["ui_order"] = new_order
                save_config(USER_CFG)
                messagebox.showinfo("保存成功", "设置已保存！\n\n请关闭并重新打开软件使新排版/快捷键生效。")
                win.destroy()
            except: 
                messagebox.showerror("错误拦截", "排序号必须填写纯数字！")

        tk.Button(win, text="💾 保存并应用设置", bg="#4caf50", fg="white", width=22, height=2, font=("", 10, "bold"), command=save_and_restart).pack(pady=20)

    # ================= 语音播报高级配置 =================
    def open_tts_config_win():
        win = tk.Toplevel(root)
        win.title("📢 语音播报高级配置")
        center_window(win, 380, 320, root)
        win.transient(root)
        win.grab_set()

        tk.Label(win, text="1. 声音投送通道 (解决YY无声)", font=("", 11, "bold"), fg="#1976d2").pack(pady=(15, 5))
        tk.Label(win, text="如果没有装虚拟声卡，请保持【默认设备】", font=("", 9), fg="gray").pack()

        spk_combo = ttk.Combobox(win, textvariable=speaker_var, state="readonly", width=28)
        spk_combo.pack(pady=5)
        
        try:
            if HAS_VOICE_ENGINE:
                p = pyaudio.PyAudio()
                speakers = ["默认设备(仅自己)"]
                for i in range(p.get_device_count()):
                    info = p.get_device_info_by_index(i)
                    if info.get('maxOutputChannels') > 0:
                        name = info.get('name')
                        try: name = name.encode('cp1252').decode('gbk')
                        except: pass
                        speakers.append(f"{i} - {name}")
                p.terminate()
                spk_combo['values'] = speakers
        except Exception as e:
            print("加载音频设备失败:", e)

        tk.Label(win, text="\n2. AI 音色与语速", font=("", 11, "bold"), fg="#1976d2").pack(pady=(10, 5))
        opt_frame = tk.Frame(win)
        opt_frame.pack(pady=5)

        tk.Label(opt_frame, text="🗣️音色:", font=get_font("label")).pack(side=tk.LEFT)
        voice_combo = ttk.Combobox(opt_frame, textvariable=voice_var, values=list(VOICE_MAP.keys()), state="readonly", width=12)
        voice_combo.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(opt_frame, text="⏱️语速:", font=get_font("label")).pack(side=tk.LEFT)
        rate_combo = ttk.Combobox(opt_frame, textvariable=rate_var, values=list(RATE_MAP.keys()), state="readonly", width=8)
        rate_combo.pack(side=tk.LEFT)

        def save_and_close():
            USER_CFG["tts_voice"] = voice_var.get()
            USER_CFG["tts_rate"] = rate_var.get()
            save_config(USER_CFG)
            win.destroy()

        tk.Button(win, text="💾 保存配置", bg="#4caf50", fg="white", font=get_font("btn", True), width=15, command=save_and_close).pack(pady=20)

    # ================= 菜单栏 =================
    menubar = tk.Menu(root); root.config(menu=menubar)
    lm = tk.Menu(menubar, tearoff=0); menubar.add_cascade(label="名单管理", menu=lm)
    lm.add_command(label=f"➕ 白名单({T('entity', '交易方')})", command=lambda: open_list_window(f"{T('entity', '交易方')}白名单(信誉)", "whitelist", True))
    lm.add_command(label=f"➕ 白名单({T('tag', '标签')})", command=lambda: open_list_window(f"{T('tag', '标签')}白名单(信誉)", "whitelist", False))
    lm.add_separator()
    lm.add_command(label=f"⛔ 避雷名单({T('entity', '交易方')})", command=lambda: open_list_window(f"{T('entity', '交易方')}避雷名单", "blacklist", True))
    lm.add_command(label=f"⛔ 避雷名单({T('tag', '标签')})", command=lambda: open_list_window(f"{T('tag', '标签')}避雷名单", "blacklist", False))


    tm = tk.Menu(menubar, tearoff=0); menubar.add_cascade(label="工具/设置", menu=tm)
    tm.add_command(label="🎨 界面视觉与排版设置 (设置快捷键)", command=open_custom_ui_win)
    tm.add_separator()
    tm.add_command(label="📢 语音播报高级设置 (声卡/音色/语速)", command=open_tts_config_win)
    tm.add_separator()
    def set_ai_key():
        top = tk.Toplevel(root)
        top.title("⚙️ AI 大脑配置 (DeepSeek)")
        center_window(top, 420, 200, root)
        top.transient(root)
        top.grab_set()
        tk.Label(top, text="请输入 DeepSeek API 密钥", font=("", 12, "bold"), fg="#1976d2").pack(pady=(15, 5))
        tk.Label(top, text="密钥仅保存在本地 ledger.db 中，不会上传。", font=("", 9), fg="gray").pack()
        key_var = tk.StringVar(value=get_ai_key())
        tk.Entry(top, textvariable=key_var, width=40, font=("", 11)).pack(pady=10)
        def save_key():
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ai_key', ?)", (key_var.get().strip(),))
            conn.commit(); conn.close()
            messagebox.showinfo("成功", "API 密钥已保存！"); top.destroy()
        tk.Button(top, text="💾 保存密钥", bg="#4caf50", fg="white", font=("", 11, "bold"), command=save_key).pack(pady=10)

    tm.add_command(label="⚙️ AI 大脑配置 (DeepSeek)", command=set_ai_key)
    tm.add_separator()
    tm.add_command(label="📥 导入/切换业务模板 (.json)", command=import_template)


    # ================= AI 智能识别副官 =================
    ai_toggle_frame = tk.Frame(root)
    ai_toggle_frame.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(10, 0))

    ai_container = tk.Frame(root, bg="#fff3e0", bd=1, relief=tk.SOLID)

    def toggle_ai_panel():
        if ai_container.winfo_ismapped():
            ai_container.pack_forget()
            btn_toggle_ai.config(text="➕ 展开智能语音识别对讲机 (实验性功能·准确率受限)", bg="#f0f0f0")
        else:
            ai_container.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(5, 5), after=ai_toggle_frame)
            btn_toggle_ai.config(text="➖ 收起语音识别", bg="#e0e0e0")

    btn_toggle_ai = tk.Button(ai_toggle_frame, text="➕ 展开智能语音识别对讲机 (实验性功能·准确率受限)", bg="#f0f0f0", relief=tk.FLAT, font=("", 10, "bold"), fg="#9e9e9e", command=toggle_ai_panel)
    btn_toggle_ai.pack(fill=tk.X)

    ai_top_frame = tk.Frame(ai_container, bg="#fff3e0")
    ai_top_frame.pack(fill=tk.X, padx=5, pady=(10, 5))
    ai_bot_frame = tk.Frame(ai_container, bg="#fff3e0")
    ai_bot_frame.pack(fill=tk.X, padx=5, pady=(0, 10))

    btn_start = tk.Button(ai_top_frame, text="🚀 启动后台对讲机", command=start_voice_assistant, bg="#2e7d32", fg="white", font=("", 10, "bold"))
    btn_start.pack(side=tk.LEFT, padx=5)

    tk.Label(ai_top_frame, text="🎤录音:", bg="#fff3e0", font=("", 9, "bold")).pack(side=tk.LEFT)
    mic_var = tk.StringVar()
    mic_combo = ttk.Combobox(ai_top_frame, textvariable=mic_var, state="readonly", width=12)
    mic_combo.pack(side=tk.LEFT, padx=2)

    lbl_ai_status = tk.Label(ai_top_frame, text="⏸️ 语音未启动 (需右键管理员运行方可热键)", bg="#fff3e0", fg="#d32f2f", font=("", 9, "bold"))
    lbl_ai_status.pack(side=tk.LEFT, padx=10)

    def refresh_mics():
        if not HAS_VOICE_ENGINE: return
        try:
            p = pyaudio.PyAudio()
            mics = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get('maxInputChannels') > 0:
                    name = info.get('name')
                    try: name = name.encode('cp1252').decode('gbk')
                    except: pass
                    mics.append(f"{i} - {name}")
            p.terminate()
            mic_combo['values'] = mics
            if mics: mic_combo.current(0)
        except: pass

    refresh_mics()

    ai_entry = tk.Entry(ai_bot_frame, width=30, font=("", 10))
    ai_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    tk.Button(ai_bot_frame, text="⚡ 强行执行文本", command=run_ai_command, bg="#ff9800", fg="white", font=("", 9, "bold")).pack(side=tk.LEFT, padx=5)

    # ================= 🌟 模块：可拖拽悬浮窗 =================
    def open_mini_window():
        mini_win = tk.Toplevel(root)
        mini_win.title("指挥台")
        mini_win.geometry("280x240+100+100")
        mini_win.attributes("-topmost", True)
        mini_win.overrideredirect(True)
        mini_win.configure(bg="#f4f6f8", highlightbackground="#bdbdbd", highlightthickness=1)

        def start_drag(event):
            mini_win._drag_start_x = event.x; mini_win._drag_start_y = event.y

        def dragging(event):
            x = mini_win.winfo_x() + (event.x - mini_win._drag_start_x)
            y = mini_win.winfo_y() + (event.y - mini_win._drag_start_y)
            mini_win.geometry(f"+{x}+{y}")

        drag_bar = tk.Frame(mini_win, bg="#1976d2", height=25); drag_bar.pack(fill=tk.X)
        drag_bar.bind("<ButtonPress-1>", start_drag); drag_bar.bind("<B1-Motion>", dragging)
        tk.Label(drag_bar, text="🎮 悬浮指挥台 (按住拖拽)", bg="#1976d2", fg="white", font=("", 9)).pack(side=tk.LEFT, padx=5)
        tk.Button(drag_bar, text="❌", bg="#d32f2f", fg="white", bd=0, command=mini_win.destroy).pack(side=tk.RIGHT, padx=5)

        m_name = tk.StringVar(mini_win); m_team = tk.StringVar(mini_win)
        m_fac = tk.StringVar(mini_win, value="请选择"); m_fight = tk.BooleanVar(mini_win, value=False)

        row_n = tk.Frame(mini_win, bg="#f4f6f8"); row_n.pack(pady=5)
        tk.Label(row_n, text=f"{T('entity', '交易方')}:", bg="#f4f6f8").pack(side=tk.LEFT); tk.Entry(row_n, textvariable=m_name, width=15).pack(side=tk.LEFT)
        row_t = tk.Frame(mini_win, bg="#f4f6f8"); row_t.pack(pady=5)
        tk.Label(row_t, text=f"{T('tag', '标签')}:", bg="#f4f6f8").pack(side=tk.LEFT); tk.Entry(row_t, textvariable=m_team, width=15).pack(side=tk.LEFT)


        row_f = tk.Frame(mini_win, bg="#f4f6f8"); row_f.pack(pady=5)
        tk.Radiobutton(row_f, text=T("flow_in", "流入"), variable=m_fac, value=T("flow_in", "流入"), bg="#f4f6f8").pack(side=tk.LEFT)
        tk.Radiobutton(row_f, text=T("flow_out", "流出"), variable=m_fac, value=T("flow_out", "流出"), bg="#f4f6f8").pack(side=tk.LEFT)
        tk.Checkbutton(row_f, text=T("flow_zero", "冲平/坏账"), variable=m_fight, bg="#f4f6f8").pack(side=tk.LEFT)


        def mini_submit():
            n = m_name.get().strip(); t = m_team.get().strip()
            if not n and not t: return
            entry_name.delete(0, tk.END); entry_name.insert(0, n)
            entry_team.delete(0, tk.END); entry_team.insert(0, t)
            faction_var.set(m_fac.get()); fight_var.set(m_fight.get())
            handle_submit(from_ai=False); m_name.set(""); m_team.set("")

        btn_f = tk.Frame(mini_win, bg="#f4f6f8"); btn_f.pack(pady=10)
        tk.Button(btn_f, text="⚡ 确认结算", bg="#4caf50", fg="white", font=("", 10, "bold"), command=mini_submit).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_f, text="🔁 补按重读", bg="#ff9800", fg="white", font=("", 10), command=replay_last_voice).pack(side=tk.LEFT, padx=5)

    # ================= 左侧控制面板 =================
    frame_left = tk.Frame(root)
    frame_left.pack(side=tk.LEFT, padx=20, pady=20, fill=tk.Y)

    left_blocks = {}
    left_blocks["team"] = tk.Frame(frame_left)
    tk.Label(left_blocks["team"], text=f"{T('tag', '标签')}（可空，支持模糊匹配）", font=get_font("label")).pack(anchor="w")

    entry_team = AutocompleteEntry(left_blocks["team"], lambda: CACHED_TEAMS, width=35, font=get_font("entry"))
    entry_team.pack(pady=5, fill=tk.X)

    left_blocks["name"] = tk.Frame(frame_left)
    tk.Label(left_blocks["name"], text=f"{T('entity', '交易方')}（必填，支持模糊匹配）", font=get_font("label")).pack(anchor="w", pady=(10, 0))

    entry_name = AutocompleteEntry(left_blocks["name"], lambda: CACHED_NAMES, width=35, font=get_font("entry"))
    entry_name.pack(pady=5, fill=tk.X)

    left_blocks["faction"] = tk.Frame(frame_left)
    tk.Label(left_blocks["faction"], text=f"{T('flow_title', '收支流向')}（必选）", font=get_font("label")).pack(anchor="w", pady=(12, 0))

    faction_var = tk.StringVar(value="请选择")
    faction_combo = ttk.Combobox(left_blocks["faction"], textvariable=faction_var, values=["请选择", T("flow_in", "流入"), T("flow_out", "流出")], state="readonly", width=36)

    faction_combo.pack(pady=5, fill=tk.X)

    left_blocks["amount"] = tk.Frame(frame_left)
    tk.Label(left_blocks["amount"], text=f"{T('amount', '金额')}（默认1）", font=get_font("label")).pack(anchor="w", pady=(12, 0))
    amount_var = tk.StringVar(value="1")
    tk.Entry(left_blocks["amount"], textvariable=amount_var, width=35, font=get_font("entry")).pack(pady=5, fill=tk.X)

    left_blocks["options"] = tk.Frame(frame_left)
    fight_var = tk.BooleanVar(value=False)
    tk.Checkbutton(left_blocks["options"], text=f"{T('flow_zero', '冲平/坏账')}（不记收支）", variable=fight_var, command=lambda: faction_combo.config(state="disabled" if fight_var.get() else "readonly"), font=get_font("label")).pack(anchor="w", pady=(8, 0))

    use_common_bl_var = tk.BooleanVar(value=True)
    tk.Checkbutton(left_blocks["options"], text="启用公共避雷名单", variable=use_common_bl_var, font=get_font("label")).pack(anchor="w")

    for k in USER_CFG.get("ui_order", ["team", "name", "faction", "amount", "options"]): 
        if k in left_blocks: left_blocks[k].pack(fill=tk.X)

    # 🌟 极简版战术语音控制区
    mini_tts_frame = tk.Frame(frame_left)
    mini_tts_frame.pack(fill=tk.X, pady=(10, 5))
    tk.Checkbutton(mini_tts_frame, text="📢 自动播报", variable=enable_voice_var, font=get_font("label", True), fg="#d32f2f").pack(side=tk.LEFT)
    tk.Button(mini_tts_frame, text="🔁 补按重读", command=replay_last_voice, bg="#ff9800", fg="white", font=get_font("btn", True), width=10).pack(side=tk.RIGHT)

    tk.Button(frame_left, text="确认结算 / 记账", width=28, height=2, bg="#f0f0f0", font=get_font("btn", True), command=lambda: handle_submit(False)).pack(pady=(15, 5))

    def quick_blacklist():
        name = entry_name.get().strip()
        if name:
            if add_to_list("blacklist", True, name): 
                messagebox.showinfo("避雷成功", f"已将【{name}】加入避雷名单！")
                refresh_caches()
            else: 
                messagebox.showwarning("提示", f"【{name}】已在避雷名单中。")
        else: 
            messagebox.showwarning("防呆提示", f"请先填写{T('entity', '交易方')}！")


    tk.Button(frame_left, text="⚡ 快速避雷此人", width=28, command=quick_blacklist, bg="#ffebee", fg="#c62828", font=get_font("btn")).pack(pady=5)
    tk.Button(frame_left, text="🎮 开启迷你悬浮窗 (可拖拽/隐藏)", command=open_mini_window, bg="#e3f2fd", font=get_font("btn", True)).pack(pady=(5, 10), fill=tk.X)
    ef = tk.Frame(frame_left)
    ef.pack(fill=tk.X, pady=(0, 10))
    tk.Button(ef, text="批量导出(可多选)", width=13, font=get_font("btn"), command=export_btn_handler).pack(side=tk.LEFT, padx=(0, 5))
    tk.Button(ef, text="全量账单(所有)", width=13, font=get_font("btn"), command=export_full_history, bg="#e8f5e9").pack(side=tk.LEFT)
    tk.Label(frame_left, text=f"提示：在右侧表格可按 Ctrl+点击 多选\n右键可【更换{T('tag', '标签')}】或【删除数据】\n", justify="left", font=("", 10)).pack(anchor="w", pady=10)


    # ================= 右侧表格区与战场锁定机制 =================
    frame_right = tk.Frame(root)
    frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

    frame_right_top = tk.Frame(frame_right)
    frame_right_top.pack(fill=tk.X, pady=(0, 5))
    tk.Label(frame_right_top, text="💾 换机须知：同目录下 ledger.db 是全部数据，直接拷走！", fg="#d32f2f", font=("", 10, "bold")).pack(side=tk.LEFT)

    if USER_CFG.get("show_battle", True):
        bf = tk.Frame(frame_right)
        bf.pack(fill=tk.X, pady=(0, 10))
        tk.Label(bf, text=f"🗺️ 自动备注{T('scene', '场景')}：", fg="#1976d2", font=("", 11, "bold")).pack(side=tk.LEFT)
        
        scene_list = APP_TPL.get("scenes", ["默认场景"])
        battle_var = tk.StringVar(value=get_saved_battle())
        battle_combo = ttk.Combobox(bf, textvariable=battle_var, values=scene_list, state="readonly", width=15)
        battle_combo.pack(side=tk.LEFT, padx=5)
        
        def lock_battle():
            b = battle_var.get()
            if b == scene_list[0]:
                messagebox.showinfo("取消锁定", "已取消锁定。")
            else:
                save_battle(b)
                messagebox.showinfo("锁定成功", f"今日{T('scene', '场景')}已锁定为：【{b}】")
            refresh_battle_stats()

        tk.Button(bf, text="🔒 锁定今日", command=lock_battle, font=get_font("btn"), bg="#e3f2fd").pack(side=tk.LEFT, padx=5)

        lbl_battle_stats = tk.Label(bf, text=f"📊 今日{T('scene', '场景')}: 暂无", fg="#e65100", font=("", 11, "bold"))
        lbl_battle_stats.pack(side=tk.LEFT, padx=15)
    else:
        battle_var = tk.StringVar(value=APP_TPL.get("scenes", ["默认场景"])[0])

    nb = ttk.Notebook(frame_right)
    nb.pack(fill=tk.BOTH, expand=True)

    t_a = ttk.Frame(nb)
    nb.add(t_a, text="当前账目")
    sf_a = tk.Frame(t_a)
    sf_a.pack(fill=tk.X, padx=6, pady=8)
    tk.Label(sf_a, text="账目查询:", font=get_font("label")).pack(side=tk.LEFT)
    search_kw_acc = tk.StringVar()
    tk.Entry(sf_a, textvariable=search_kw_acc, width=30, font=get_font("entry")).pack(side=tk.LEFT, padx=6)
    tk.Button(sf_a, text="查询", width=8, command=refresh_all_tables, font=get_font("btn")).pack(side=tk.LEFT, padx=4)
    tk.Button(sf_a, text="清空", width=8, command=lambda: [search_kw_acc.set(""), refresh_all_tables()], font=get_font("btn")).pack(side=tk.LEFT)

    acc_cols = (T("entity", "交易方/账户"), T("tag", "项目/标签"), T("flow_title", "收支流向"), "性质", "状态/余额", "最近操作")
    accounts_table = ttk.Treeview(t_a, columns=acc_cols, show="headings", selectmode="extended")
    for c in acc_cols: 
        accounts_table.heading(c, text=c)
        accounts_table.column(c, width=170, anchor="w" if c==acc_cols[0] else "center")

    accounts_table.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    t_r = ttk.Frame(nb)
    nb.add(t_r, text="历史记录")
    sf_r = tk.Frame(t_r)
    sf_r.pack(fill=tk.X, padx=6, pady=8)
    tk.Label(sf_r, text="历史查询:", font=get_font("label")).pack(side=tk.LEFT)
    search_kw_rec = tk.StringVar()
    tk.Entry(sf_r, textvariable=search_kw_rec, width=30, font=get_font("entry")).pack(side=tk.LEFT, padx=6)
    tk.Button(sf_r, text="查询", width=8, command=refresh_all_tables, font=get_font("btn")).pack(side=tk.LEFT, padx=4)
    tk.Button(sf_r, text="清空", width=8, command=lambda: [search_kw_rec.set(""), refresh_all_tables()], font=get_font("btn")).pack(side=tk.LEFT)

    rec_cols = (T("entity", "交易方/账户"), T("tag", "项目/标签"), T("flow_title", "收支流向"), "性质", "操作", "金额/动作", "时间", "备注")
    records_table = ttk.Treeview(t_r, columns=rec_cols, show="headings", selectmode="extended")
    for c in rec_cols: 
        records_table.heading(c, text=c)
        records_table.column(c, width=230 if c in ("时间","备注") else 140, anchor="w" if c in (rec_cols[0],"备注") else "center")

    records_table.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    accounts_table.tag_configure("win_tag", foreground="#d32f2f")
    accounts_table.tag_configure("lose_tag", foreground="#1976d2")
    records_table.tag_configure("win_tag", foreground="#d32f2f")
    records_table.tag_configure("lose_tag", foreground="#1976d2")

    accounts_table.bind("<Button-3>", lambda e: table_right_click_menu(accounts_table, e))
    records_table.bind("<Button-3>", lambda e: table_right_click_menu(records_table, e))

    refresh_all_tables()
    root.mainloop()

except Exception as e:
    print(f"程序启动异常: {e}")
    traceback.print_exc()
    messagebox.showerror("启动错误", f"程序启动失败: {e}\n\n请检查配置文件格式是否正确。")