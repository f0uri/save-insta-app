#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Save Insta - Copyright 2026 Youssef Mansouri
"""
import os, sys, json, re, time, sqlite3, threading

import requests
import yt_dlp
import arabic_reshaper
from bidi.algorithm import get_display

def ar(text):
    """Reshape + bidi-reorder Arabic text for correct display in Kivy,
    which renders raw codepoints left-to-right without joining letters
    or applying the bidirectional algorithm."""
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.image import AsyncImage
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle, Line
from kivy.metrics import dp
from kivy.core.text import LabelBase, DEFAULT_FONT
from kivy.config import Config as KivyConfig
from kivy.animation import Animation
from kivy.uix.anchorlayout import AnchorLayout

# Keeps the focused TextInput visible above the on-screen keyboard on
# Android instead of letting the keyboard cover it.
Window.softinput_mode = 'below_target'

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoNaskhArabic-Regular.ttf")
if os.path.exists(_FONT_PATH):
    LabelBase.register(DEFAULT_FONT, _FONT_PATH)
else:
    # Font file missing from the repo/APK — Arabic text will render as
    # tofu boxes until fonts/NotoNaskhArabic-Regular.ttf is added and
    # 'ttf' is included in buildozer.spec's source.include_exts.
    pass

from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.image import Image as KvImage

# Brand palette — matches the app icon's gradient identity instead of a
# flat single accent color, so the in-app UI and the launcher icon feel
# like the same product.
BRAND_PURPLE = (0.588, 0.275, 0.863, 1)
BRAND_PINK = (1.0, 0.353, 0.510, 1)
BRAND_ORANGE = (1.0, 0.588, 0.235, 1)
IOS_BLUE = BRAND_PURPLE  # kept name for backward-compat with existing code
IOS_GRAY = (0.52, 0.5, 0.58, 1)
IOS_LIGHT_GRAY = (0.965, 0.96, 0.975, 1)
IOS_WHITE = (1, 1, 1, 1)
IOS_BLACK = (0.13, 0.11, 0.17, 1)
IOS_GREEN = (0.2, 0.78, 0.35, 1)
IOS_RED = (0.93, 0.27, 0.33, 1)
Window.clearcolor = IOS_LIGHT_GRAY

def _get_db_path():
    """Return a writable DB path. On Android, App.user_data_dir is the only
    reliably writable location (the APK's own source dir is read-only after
    install). Falls back to the script directory for desktop testing."""
    try:
        from kivy.app import App as _App
        app = _App.get_running_app()
        if app is not None:
            return os.path.join(app.user_data_dir, "saveinsta.db")
    except Exception:
        pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "saveinsta.db")

DB_PATH = None  # resolved lazily in init_db(), after App instance exists

def init_db():
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = _get_db_path()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, created_at REAL)')
    conn.commit()
    conn.close()

def cache_get(key):
    try:
        if DB_PATH is None:
            init_db()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value, created_at FROM cache WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        if row and time.time() - row[1] < 600:
            return json.loads(row[0])
    except Exception:
        pass
    return None

def cache_set(key, value):
    try:
        if DB_PATH is None:
            init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value), time.time()))
        conn.commit()
        conn.close()
    except Exception:
        pass

UAS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
]

def get_ua():
    import random
    return random.choice(UAS)

def scrape_profile(username):
    username = username.strip().lstrip("@")
    cached = cache_get(f"prof:{username}")
    if cached:
        return cached
    url = f"https://www.instagram.com/{username}/"
    headers = {
        "User-Agent": get_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.instagram.com/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 404:
            return {"error": "الحساب غير موجود"}
        if r.status_code == 429:
            return {"error": "تم الحظر مؤقتاً، جرب بعد 10 دقائق"}
        text = r.text
        m = re.search(r'window\._sharedData\s*=\s*({.+?});</script>', text)
        if m:
            data = json.loads(m.group(1))
            user = data['entry_data']['ProfilePage'][0]['graphql']['user']
            result = {
                "username": user.get('username', username),
                "full_name": user.get('full_name', ''),
                "biography": user.get('biography', ''),
                "followers": user.get('edge_followed_by', {}).get('count', 0),
                "following": user.get('edge_follow', {}).get('count', 0),
                "posts": user.get('edge_owner_to_timeline_media', {}).get('count', 0),
                "is_private": user.get('is_private', False),
                "is_verified": user.get('is_verified', False),
                "pic_url": user.get('profile_pic_url_hd', user.get('profile_pic_url', '')),
            }
            cache_set(f"prof:{username}", result)
            return result
        bio = ""
        bio_m = re.search(r'"biography":("(?:\\.|[^"\\])*"|null)', text)
        if bio_m and bio_m.group(1) != "null":
            try:
                bio = json.loads(bio_m.group(1))
            except:
                bio = bio_m.group(1).strip('"')
        name = ""
        name_m = re.search(r'"full_name":("(?:\\.|[^"\\])*"|null)', text)
        if name_m and name_m.group(1) != "null":
            try:
                name = json.loads(name_m.group(1))
            except:
                name = name_m.group(1).strip('"')
        followers = 0
        f_m = re.search(r'"edge_followed_by":\{"count":(\d+)\}', text)
        if f_m:
            followers = int(f_m.group(1))
        following = 0
        fg_m = re.search(r'"edge_follow":\{"count":(\d+)\}', text)
        if fg_m:
            following = int(fg_m.group(1))
        posts = 0
        p_m = re.search(r'"edge_owner_to_timeline_media":\{"count":(\d+)\}', text)
        if p_m:
            posts = int(p_m.group(1))
        is_private = '"is_private":true' in text
        is_verified = '"is_verified":true' in text
        pic_url = ""
        pic_m = re.search(r'"profile_pic_url_hd":"(https://[^"]+)"', text)
        if not pic_m:
            pic_m = re.search(r'"profile_pic_url":"(https://[^"]+)"', text)
        if pic_m:
            pic_url = pic_m.group(1)
        result = {
            "username": username,
            "full_name": name,
            "biography": bio,
            "followers": followers,
            "following": following,
            "posts": posts,
            "is_private": is_private,
            "is_verified": is_verified,
            "pic_url": pic_url,
        }
        cache_set(f"prof:{username}", result)
        return result
    except Exception as e:
        return {"error": f"خطأ: {str(e)[:200]}"}

class _SilentLogger:
    """yt-dlp writes progress/warning/error text straight to stdout/stderr
    by default. On Android, Kivy replaces sys.stdout/sys.stderr with a
    custom logcat stream that isn't fully file-compatible, which crashes
    yt-dlp with `'str' object has no attribute 'write'`. Passing an
    explicit logger sidesteps stdout/stderr entirely."""
    def debug(self, msg):
        pass
    def info(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        pass

def download_media(url, download_dir):
    os.makedirs(download_dir, exist_ok=True)
    opts = {
        'outtmpl': os.path.join(download_dir, '%(title).80s.%(ext)s'),
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'logger': _SilentLogger(),
        'user_agent': get_ua(),
        'retries': 2,
        'socket_timeout': 20,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, "فشل التحميل"
        files = [
            os.path.join(download_dir, f)
            for f in sorted(os.listdir(download_dir))
            if os.path.isfile(os.path.join(download_dir, f)) and not f.endswith(('.json', '.txt', '.part'))
        ]
        if not files:
            return None, "لم يتم العثور على ملفات"
        return files, None
    except Exception as e:
        return None, str(e)[:300]

class _SearchIcon(Widget):
    """Vector-drawn magnifying-glass icon — no external asset needed."""
    def __init__(self, color=IOS_WHITE, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (dp(18), dp(18)))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*color)
            self._circle = Line(circle=(0, 0, 0), width=dp(1.7))
            self._handle = Line(points=[0, 0, 0, 0], width=dp(1.7), cap='round')
        self.bind(pos=self._upd, size=self._upd)
        self._upd()

    def _upd(self, *a):
        r = self.width * 0.30
        cx = self.x + self.width * 0.40
        cy = self.y + self.height * 0.62
        self._circle.circle = (cx, cy, r)
        hx1 = cx + r * 0.72
        hy1 = cy - r * 0.72
        self._handle.points = [hx1, hy1, self.x + self.width * 0.92, self.y + self.height * 0.10]

class _DownloadIcon(Widget):
    """Vector-drawn download-arrow icon (stem + chevron + base line)."""
    def __init__(self, color=IOS_WHITE, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (dp(18), dp(18)))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*color)
            self._stem = Line(points=[0, 0, 0, 0], width=dp(1.8), cap='round')
            self._arrow = Line(points=[0, 0, 0, 0, 0, 0], width=dp(1.8), joint='round', cap='round')
            self._base = Line(points=[0, 0, 0, 0], width=dp(1.8), cap='round')
        self.bind(pos=self._upd, size=self._upd)
        self._upd()

    def _upd(self, *a):
        cx = self.x + self.width / 2
        top = self.y + self.height * 0.92
        mid = self.y + self.height * 0.42
        w = self.width * 0.34
        self._stem.points = [cx, top, cx, mid]
        self._arrow.points = [cx - w, mid + self.height * 0.05, cx, mid - self.height * 0.08, cx + w, mid + self.height * 0.05]
        self._base.points = [self.x + self.width * 0.12, self.y + self.height * 0.06,
                              self.x + self.width * 0.88, self.y + self.height * 0.06]

class IconButton(ButtonBehavior, BoxLayout):
    """A rounded, glass-shadowed button combining a vector icon + label.
    Replaces plain text buttons so actions read at a glance instead of
    relying on emoji glyphs the bundled font doesn't contain."""
    def __init__(self, text='', icon=None, bg_color=None, fg_color=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.spacing = dp(8)
        self.size_hint_y = None
        self.height = dp(50)
        self._bg = bg_color or BRAND_PURPLE
        fg = fg_color or IOS_WHITE
        with self.canvas.before:
            Color(0, 0, 0, 0.12)
            self._shadow = RoundedRectangle(radius=[dp(14)] * 4)
            self._color_instr = Color(*self._bg)
            self._rect = RoundedRectangle(radius=[dp(14)] * 4)
        self.bind(pos=self._upd, size=self._upd)
        self.add_widget(Widget())
        self._icon_widget = None
        if icon == 'search':
            self._icon_widget = _SearchIcon(color=fg)
        elif icon == 'download':
            self._icon_widget = _DownloadIcon(color=fg)
        if self._icon_widget:
            self.add_widget(self._icon_widget)
        self.label = Label(text=text, color=fg, font_size='16sp', bold=True, size_hint=(None, None))
        self.label.bind(texture_size=lambda inst, val: setattr(self.label, 'size', val))
        self.add_widget(self.label)
        self.add_widget(Widget())

    def set_bg(self, color):
        self._color_instr.rgba = color

    def set_fg(self, color):
        self.label.color = color
        if self._icon_widget:
            self._icon_widget.canvas.clear()
            with self._icon_widget.canvas:
                Color(*color)
            # icons are simple enough to just redraw via a fresh instance swap
            new_icon = type(self._icon_widget)(color=color)
            idx = self.children.index(self._icon_widget)
            self.remove_widget(self._icon_widget)
            self.add_widget(new_icon, index=idx)
            self._icon_widget = new_icon

    def _upd(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._shadow.pos = (self.x, self.y - dp(2))
        self._shadow.size = self.size

    def on_press(self):
        Animation.cancel_all(self, 'opacity')
        Animation(opacity=0.85, duration=0.06).start(self)

    def on_release(self):
        Animation.cancel_all(self, 'opacity')
        Animation(opacity=1, duration=0.12).start(self)

class iOSButton(Button):
    def __init__(self, bg_color=None, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_color = (0, 0, 0, 0)  # actual fill drawn manually below
        self._fill_color = bg_color or BRAND_PURPLE
        self.color = IOS_WHITE
        self.font_size = '16sp'
        self.bold = True
        self.size_hint_y = None
        self.height = dp(50)
        with self.canvas.before:
            Color(0, 0, 0, 0.12)
            self.shadow = RoundedRectangle(radius=[dp(12)] * 4)
            Color(*self._fill_color)
            self.rect = RoundedRectangle(radius=[dp(12)] * 4)
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *a):
        self.rect.pos = self.pos
        self.rect.size = self.size
        self.shadow.pos = (self.x, self.y - dp(2))
        self.shadow.size = self.size

    def on_press(self, *a):
        Animation.cancel_all(self, 'scale_y')
        anim = Animation(opacity=0.85, duration=0.06)
        anim.start(self)

    def on_release(self, *a):
        Animation.cancel_all(self, 'opacity')
        Animation(opacity=1, duration=0.12).start(self)

class iOSCard(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.padding = dp(16)
        self.spacing = dp(12)
        self.size_hint_y = None
        with self.canvas.before:
            Color(0, 0, 0, 0.06)
            self.shadow = RoundedRectangle(radius=[dp(18)] * 4)
            Color(1, 1, 1, 0.72)  # translucent "glass" fill
            self.rect = RoundedRectangle(radius=[dp(16)] * 4)
            Color(1, 1, 1, 0.9)
            self.border = Line(rounded_rectangle=(0, 0, 0, 0, dp(16)), width=dp(1))
        self.bind(pos=self._upd, size=self._upd)
        self.opacity = 0
        Animation(opacity=1, duration=0.25).start(self)

    def _upd(self, *a):
        self.rect.pos = self.pos
        self.rect.size = self.size
        self.shadow.pos = (self.x, self.y - dp(3))
        self.shadow.size = self.size
        self.border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(16))

class iOSInput(TextInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.multiline = False
        self.size_hint_y = None
        self.height = dp(50)
        self.font_size = '16sp'
        self.padding = [dp(16), dp(14), dp(16), dp(14)]
        self.background_normal = ''
        self.background_active = ''
        self.background_color = IOS_WHITE
        self.foreground_color = IOS_BLACK
        self.hint_text_color = IOS_GRAY
        self.cursor_color = IOS_BLUE

class ProfileCard(iOSCard):
    def __init__(self, data, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.bind(minimum_height=self.setter('height'))
        if data.get('pic_url'):
            img_c = BoxLayout(size_hint_y=None, height=dp(130), padding=dp(10))
            img_c.add_widget(Widget())
            img_c.add_widget(
                AsyncImage(
                    source=data['pic_url'],
                    size_hint=(None, None),
                    size=(dp(110), dp(110)),
                    allow_stretch=True,
                    keep_ratio=True,
                )
            )
            img_c.add_widget(Widget())
            self.add_widget(img_c)
        name = data.get('full_name') or data.get('username')
        self.add_widget(
            Label(
                text=ar(name),
                font_size='20sp',
                bold=True,
                color=IOS_BLACK,
                size_hint_y=None,
                height=dp(35),
            )
        )
        self.add_widget(
            Label(
                text=f"@{data.get('username')}",
                font_size='14sp',
                color=IOS_GRAY,
                size_hint_y=None,
                height=dp(25),
            )
        )
        stats = GridLayout(cols=3, size_hint_y=None, height=dp(60), padding=dp(5))
        for lbl, val in [
            ('منشورات', data.get('posts', 0)),
            ('متابعون', data.get('followers', 0)),
            ('يتابع', data.get('following', 0)),
        ]:
            b = BoxLayout(orientation='vertical')
            b.add_widget(
                Label(
                    text=f"{val:,}",
                    font_size='18sp',
                    bold=True,
                    color=IOS_BLACK,
                )
            )
            b.add_widget(Label(text=ar(lbl), font_size='12sp', color=IOS_GRAY))
            stats.add_widget(b)
        self.add_widget(stats)
        badges = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        badges.add_widget(Widget())
        status_color = IOS_RED if data.get('is_private') else IOS_GREEN
        badges.add_widget(
            Label(
                text=ar("خاص") if data.get('is_private') else ar("عام"),
                color=status_color,
                font_size='13sp',
            )
        )
        if data.get('is_verified'):
            badges.add_widget(
                Label(text=ar("موثّق"), color=IOS_BLUE, font_size='13sp')
            )
        badges.add_widget(Widget())
        self.add_widget(badges)
        bio = data.get('biography', 'لا يوجد بايو')
        self.add_widget(
            Label(
                text=ar(bio),
                font_size='14sp',
                color=IOS_BLACK,
                size_hint_y=None,
                height=dp(80),
            )
        )
        btns = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        cb = Button(
            text=ar('نسخ البايو'),
            background_normal='',
            background_color=(0.9, 0.9, 0.93, 1),
            color=IOS_BLUE,
            font_size='15sp',
            bold=True,
        )

        def copy_bio(instance):
            Clipboard.copy(bio)
            Popup(
                title='',
                content=Label(text=ar('تم نسخ البايو!'), color=IOS_GREEN, font_size='16sp'),
                size_hint=(0.8, 0.2),
            ).open()

        cb.bind(on_press=copy_bio)
        btns.add_widget(cb)
        db = Button(
            text=ar('صورة البروفايل'),
            background_normal='',
            background_color=IOS_BLUE,
            color=IOS_WHITE,
            font_size='15sp',
            bold=True,
        )

        def dl_pic(instance):
            if not data.get('pic_url'):
                Popup(
                    title='',
                    content=Label(text=ar('لا توجد صورة'), color=IOS_RED, font_size='16sp'),
                    size_hint=(0.8, 0.2),
                ).open()
                return
            threading.Thread(
                target=self._dl_t, args=(data.get('pic_url'), data.get('username'))
            ).start()

        db.bind(on_press=dl_pic)
        btns.add_widget(db)
        self.add_widget(btns)

    def _dl_t(self, url, username):
        try:
            try:
                from android.storage import primary_external_storage_path

                base = primary_external_storage_path()
            except:
                base = os.path.expanduser("~")
            sd = os.path.join(base, "Download", "SaveInsta")
            os.makedirs(sd, exist_ok=True)
            ext = ".png" if ".png" in url else ".jpg"
            path = os.path.join(sd, f"{username}_profile{ext}")
            r = requests.get(url, headers={"User-Agent": get_ua()}, timeout=20)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
                Clock.schedule_once(
                    lambda dt: Popup(
                        title='',
                        content=Label(text=ar('تم الحفظ'), color=IOS_GREEN, font_size='16sp'),
                        size_hint=(0.8, 0.2),
                    ).open(),
                    0,
                )
            else:
                Clock.schedule_once(
                    lambda dt: Popup(
                        title='',
                        content=Label(text=ar('فشل'), color=IOS_RED, font_size='16sp'),
                        size_hint=(0.8, 0.2),
                    ).open(),
                    0,
                )
        except Exception as e:
            Clock.schedule_once(
                lambda dt: Popup(
                    title='',
                    content=Label(
                        text=ar(str(e)[:80]), color=IOS_RED, font_size='14sp'
                    ),
                    size_hint=(0.8, 0.2),
                ).open(),
                0,
            )

class SaveInstaApp(App):
    def build(self):
        self.title = "Save Insta"
        init_db()  # runs now, once App.user_data_dir is available
        root = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        with root.canvas.before:
            Color(*IOS_LIGHT_GRAY)
            self.rect = RoundedRectangle(pos=root.pos, size=root.size)
        root.bind(size=self._upd, pos=self._upd)

        header = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(10))
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if os.path.exists(icon_path):
            header.add_widget(
                KvImage(source=icon_path, size_hint=(None, None), size=(dp(44), dp(44)))
            )
        title_box = BoxLayout(orientation='vertical', size_hint_x=None)
        title_box.bind(minimum_width=title_box.setter('width'))
        title_lbl = Label(
            text="[b]Save Insta[/b]",
            markup=True,
            font_size='24sp',
            color=IOS_BLACK,
            size_hint=(None, None),
            halign='left',
        )
        title_lbl.bind(texture_size=lambda i, v: setattr(title_lbl, 'size', v))
        title_box.add_widget(title_lbl)
        sub_lbl = Label(
            text="© 2026 Youssef Mansouri",
            font_size='11sp',
            color=IOS_GRAY,
            size_hint=(None, None),
            halign='left',
        )
        sub_lbl.bind(texture_size=lambda i, v: setattr(sub_lbl, 'size', v))
        title_box.add_widget(sub_lbl)
        header.add_widget(title_box)
        header.add_widget(Widget())
        root.add_widget(header)

        # thin brand-colored accent bar under the header, echoing the icon's gradient
        accent = BoxLayout(size_hint_y=None, height=dp(4))
        with accent.canvas:
            Color(*BRAND_PINK)
            self._a1 = RoundedRectangle(radius=[dp(2)] * 4)
            Color(*BRAND_PURPLE)
            self._a2 = RoundedRectangle(radius=[dp(2)] * 4)
            Color(*BRAND_ORANGE)
            self._a3 = RoundedRectangle(radius=[dp(2)] * 4)
        accent.bind(pos=self._upd_accent, size=self._upd_accent)
        self._accent = accent
        root.add_widget(accent)

        # segmented tab control (single glass pill housing both tabs)
        tabs_shell = BoxLayout(size_hint_y=None, height=dp(52), padding=dp(4), spacing=dp(4))
        with tabs_shell.canvas.before:
            Color(1, 1, 1, 0.6)
            self._tabs_bg = RoundedRectangle(radius=[dp(16)] * 4)
        tabs_shell.bind(pos=lambda i, v: setattr(self._tabs_bg, 'pos', v),
                         size=lambda i, v: setattr(self._tabs_bg, 'size', v))
        self.bs = IconButton(text=ar('بحث'), icon='search', bg_color=BRAND_PURPLE)
        self.bd = IconButton(text=ar('تحميل'), icon='download', bg_color=(0, 0, 0, 0), fg_color=IOS_GRAY)
        self.bs.bind(on_press=self.show_search)
        self.bd.bind(on_press=self.show_dl)
        tabs_shell.add_widget(self.bs)
        tabs_shell.add_widget(self.bd)
        root.add_widget(tabs_shell)

        self.content = BoxLayout()
        root.add_widget(self.content)
        self.show_search(None)
        return root

    def _upd(self, i, v):
        self.rect.pos = i.pos
        self.rect.size = i.size

    def _upd_accent(self, i, v):
        third = i.width / 3
        self._a1.pos = i.pos
        self._a1.size = (third, i.height)
        self._a2.pos = (i.x + third, i.y)
        self._a2.size = (third, i.height)
        self._a3.pos = (i.x + third * 2, i.y)
        self._a3.size = (third, i.height)

    def _recolor_tab(self, btn, bg, fg):
        btn.set_bg(bg)
        btn.set_fg(fg)

    def show_search(self, i):
        self._recolor_tab(self.bs, BRAND_PURPLE, IOS_WHITE)
        self._recolor_tab(self.bd, (0, 0, 0, 0), IOS_GRAY)
        self.content.clear_widgets()
        l = BoxLayout(orientation='vertical', spacing=dp(12), padding=dp(8))
        self.si = iOSInput(hint_text=ar('اسم المستخدم (بدون @)'))
        l.add_widget(self.si)
        b = IconButton(text=ar('بحث'), icon='search', bg_color=BRAND_PURPLE)
        b.bind(on_press=self.do_search)
        l.add_widget(b)
        self.rc = BoxLayout(orientation='vertical')
        sc = ScrollView()
        sc.add_widget(self.rc)
        l.add_widget(sc)
        self.content.add_widget(l)

    def show_dl(self, i):
        self._recolor_tab(self.bs, (0, 0, 0, 0), IOS_GRAY)
        self._recolor_tab(self.bd, BRAND_PURPLE, IOS_WHITE)
        self.content.clear_widgets()
        l = BoxLayout(orientation='vertical', spacing=dp(12), padding=dp(8))
        self.ui = iOSInput(hint_text=ar('رابط الريلز أو المنشور العام...'))
        l.add_widget(self.ui)
        b = IconButton(text=ar('تحميل الآن'), icon='download', bg_color=BRAND_PURPLE)
        b.bind(on_press=self.do_dl)
        l.add_widget(b)
        self.st = Label(
            text='',
            font_size='14sp',
            color=IOS_BLACK,
            size_hint_y=None,
            height=dp(100),
        )
        l.add_widget(self.st)
        self.content.add_widget(l)

    def do_search(self, i):
        u = self.si.text.strip()
        if not u:
            Popup(
                title='',
                content=Label(
                    text=ar('أدخل اسم المستخدم'), color=IOS_RED, font_size='16sp'
                ),
                size_hint=(0.8, 0.2),
            ).open()
            return
        self.rc.clear_widgets()
        self.rc.add_widget(
            Label(text=ar('جاري البحث...'), color=IOS_GRAY, font_size='16sp')
        )
        threading.Thread(target=self._s_th, args=(u,)).start()

    def _s_th(self, u):
        r = scrape_profile(u)
        Clock.schedule_once(lambda dt: self._sh(r), 0)

    def _sh(self, r):
        self.rc.clear_widgets()
        if r.get('error'):
            self.rc.add_widget(
                Label(text=ar(r['error']), color=IOS_RED, font_size='16sp')
            )
        else:
            self.rc.add_widget(ProfileCard(r))

    def do_dl(self, i):
        url = self.ui.text.strip()
        if not url:
            Popup(
                title='',
                content=Label(
                    text=ar('ألصق الرابط أولاً'), color=IOS_RED, font_size='16sp'
                ),
                size_hint=(0.8, 0.2),
            ).open()
            return
        self.st.text = ar('جاري التحميل...')
        threading.Thread(target=self._dl_th, args=(url,)).start()

    def _dl_th(self, url):
        try:
            try:
                from android.storage import primary_external_storage_path

                base = primary_external_storage_path()
            except:
                base = os.path.expanduser("~")
            sd = os.path.join(
                base, "Download", "SaveInsta", f"dl_{int(time.time())}"
            )
            files, err = download_media(url, sd)
            if err:
                Clock.schedule_once(
                    lambda dt: setattr(self.st, 'text', ar(f'فشل: {err}')), 0
                )
            else:
                names = '\n'.join([os.path.basename(f) for f in files])
                Clock.schedule_once(
                    lambda dt: setattr(
                        self.st, 'text', ar('تم التحميل:') + '\n' + names
                    ),
                    0,
                )
        except Exception as e:
            Clock.schedule_once(
                lambda dt: setattr(self.st, 'text', ar(f'خطأ: {str(e)[:100]}')), 0
            )

if __name__ == '__main__':
    SaveInstaApp().run()
