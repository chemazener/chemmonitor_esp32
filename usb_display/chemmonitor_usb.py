#!/usr/bin/env python3
"""
ChemMonitor USB - Monitor de servidor en pantalla USB 3.5" IPS
Teclas globales (funcionan sin foco):
  F9  = Siguiente vista
  F10 = Rotar pantalla 90°
  F11 = Vista anterior
  F12 = Pausar/reanudar
Desarrollado por ChemaDev & ClaudeCode
"""
import serial
import serial.tools.list_ports
import struct
import time
import psutil
import platform
import socket
import threading
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from collections import deque
import math
import sys
import os

# ============== CONFIG ==============
SERIAL_PORT = "COM4"
BAUD_RATE = 115200
SCREEN_W = 320
SCREEN_H = 480
UPDATE_INTERVAL = 2
NUM_VIEWS = 5  # 0=Dashboard 1=CPU 2=RAM 3=Network 4=Clock

# ============== STATE ==============
current_view = 0
rotation = 0  # 0, 90, 180, 270
paused = False
lock = threading.Lock()

# ============== COLORS ==============
C_BG = (15, 17, 23)
C_CARD = (26, 29, 39)
C_BORDER = (42, 45, 58)
C_TEXT = (225, 228, 237)
C_MUTED = (139, 143, 163)
C_ACCENT = (99, 102, 241)
C_GREEN = (34, 197, 94)
C_YELLOW = (234, 179, 8)
C_RED = (239, 68, 68)
C_CYAN = (6, 182, 212)

# ============== PROTOCOL ==============
CMD_DISPLAY_BITMAP = 197
CMD_SET_BRIGHTNESS = 110

# ============== DATA ==============
cpu_hist = deque([0.0] * 60, maxlen=60)
ram_hist = deque([0.0] * 60, maxlen=60)
net_sent_hist = deque([0.0] * 60, maxlen=60)
net_recv_hist = deque([0.0] * 60, maxlen=60)
last_net = {'sent': 0, 'recv': 0, 'time': 0}
stats = {}
gpu_stats = {'temp': None, 'load': None, 'mem_pct': None, 'name': None}

# ============== FONTS (cached) ==============
_fonts = {}
def get_font(name, size):
    key = (name, size)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(name, size)
        except:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]

def F(size): return get_font("arial.ttf", size)
def FB(size): return get_font("arialbd.ttf", size)

# ============== GPU ==============
def init_gpu():
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu_stats['name'] = pynvml.nvmlDeviceGetName(handle)
        if isinstance(gpu_stats['name'], bytes):
            gpu_stats['name'] = gpu_stats['name'].decode()
        return handle
    except:
        return None

gpu_handle = init_gpu()

def update_gpu():
    if not gpu_handle: return
    try:
        import pynvml
        gpu_stats['temp'] = pynvml.nvmlDeviceGetTemperature(gpu_handle, 0)
        util = pynvml.nvmlDeviceGetUtilizationRates(gpu_handle)
        gpu_stats['load'] = util.gpu
        mem = pynvml.nvmlDeviceGetMemoryInfo(gpu_handle)
        gpu_stats['mem_pct'] = mem.used / mem.total * 100 if mem.total else 0
    except:
        pass

# ============== SERIAL DISPLAY ==============
class USBDisplay:
    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud
        self.ser = None

    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1, rtscts=False)
            time.sleep(0.3)
            return True
        except:
            return False

    def close(self):
        if self.ser and self.ser.is_open:
            try: self.ser.close()
            except: pass

    def set_brightness(self, level):
        if not self.ser: return
        val = int((100 - level) * 255 / 100)
        try: self.ser.write(bytes([CMD_SET_BRIGHTNESS, val])); self.ser.flush()
        except: pass

    def image_to_rgb565(self, img):
        pixels = img.convert("RGB").tobytes()
        data = bytearray(len(pixels) // 3 * 2)
        idx = 0
        for i in range(0, len(pixels), 3):
            r, g, b = pixels[i], pixels[i+1], pixels[i+2]
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            data[idx] = rgb565 & 0xFF
            data[idx+1] = rgb565 >> 8
            idx += 2
        return bytes(data)

    def display_image(self, img):
        if not self.ser: return
        w, h = img.size
        ex, ey = w - 1, h - 1
        header = bytearray(6)
        header[0] = 0
        header[1] = 0
        header[2] = (ex >> 6)
        header[3] = ((ex & 63) << 2) + (ey >> 8)
        header[4] = ey & 255
        header[5] = CMD_DISPLAY_BITMAP
        rgb565 = self.image_to_rgb565(img)
        try:
            self.ser.write(header)
            for i in range(0, len(rgb565), w * 2):
                self.ser.write(rgb565[i:i + w * 2])
            self.ser.flush()
        except Exception as e:
            raise serial.SerialException(str(e))

# ============== KEYBOARD ==============
def setup_keyboard():
    try:
        from pynput import keyboard
        def on_press(key):
            global current_view, rotation, paused
            with lock:
                if key == keyboard.Key.f9:
                    current_view = (current_view + 1) % NUM_VIEWS
                    print(f"\n>> Vista {current_view}")
                elif key == keyboard.Key.f11:
                    current_view = (current_view - 1) % NUM_VIEWS
                    print(f"\n>> Vista {current_view}")
                elif key == keyboard.Key.f10:
                    rotation = (rotation + 90) % 360
                    print(f"\n>> Rotacion {rotation}°")
                elif key == keyboard.Key.f12:
                    paused = not paused
                    print(f"\n>> {'PAUSADO' if paused else 'ACTIVO'}")
        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        print("Teclado: F9=Siguiente F10=Rotar F11=Anterior F12=Pausar")
        return True
    except ImportError:
        print("pynput no instalado - sin control por teclado")
        return False

# ============== STATS ==============
def fmt_bytes(b):
    if b >= 1e9: return f"{b/1e9:.1f}GB"
    if b >= 1e6: return f"{b/1e6:.0f}MB"
    return f"{b/1e3:.0f}KB"

def fmt_uptime(sec):
    d, h, m = int(sec // 86400), int(sec // 3600) % 24, int(sec // 60) % 60
    return f"{d}d{h}h{m}m" if d else f"{h}h{m}m"

def fmt_rate(bps):
    if bps >= 1e6: return f"{bps/1e6:.1f}MB/s"
    if bps >= 1e3: return f"{bps/1e3:.0f}KB/s"
    return f"{bps:.0f}B/s"

def collect_stats():
    global stats, last_net
    cpu = psutil.cpu_percent(interval=0)
    cores = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/' if platform.system() != 'Windows' else 'C:/')
    net = psutil.net_io_counters()
    now = time.time()

    # Net rates
    dt = now - last_net['time'] if last_net['time'] else 1
    sent_rate = (net.bytes_sent - last_net['sent']) / dt if last_net['time'] else 0
    recv_rate = (net.bytes_recv - last_net['recv']) / dt if last_net['time'] else 0
    last_net = {'sent': net.bytes_sent, 'recv': net.bytes_recv, 'time': now}

    # Battery
    bat = psutil.sensors_battery()

    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            info = p.info
            if info['cpu_percent'] is not None and info['memory_percent'] is not None:
                procs.append(info)
        except: pass
    procs.sort(key=lambda x: x['memory_percent'] or 0, reverse=True)

    update_gpu()

    stats = {
        'cpu': cpu, 'cores': cores,
        'mem_pct': mem.percent, 'mem_used': mem.used, 'mem_total': mem.total,
        'disk_pct': disk.percent, 'disk_used': disk.used, 'disk_total': disk.total,
        'net_sent': net.bytes_sent, 'net_recv': net.bytes_recv,
        'sent_rate': sent_rate, 'recv_rate': recv_rate,
        'procs': procs[:10], 'proc_count': len(procs),
        'uptime': time.time() - psutil.boot_time(),
        'hostname': socket.gethostname(),
        'battery': {'pct': bat.percent, 'charging': bat.power_plugged} if bat else None,
        'gpu': gpu_stats.copy(),
    }
    cpu_hist.append(cpu)
    ram_hist.append(mem.percent)
    net_sent_hist.append(min(sent_rate / 1024, 1000))
    net_recv_hist.append(min(recv_rate / 1024, 1000))

# ============== DRAWING HELPERS ==============
def gauge_color(v):
    return C_RED if v > 90 else C_YELLOW if v > 70 else C_ACCENT

def draw_gauge(draw, cx, cy, r, pct, color, label):
    for a in range(-135, 136, 2):
        rad = math.radians(a)
        active = a <= -135 + int(270 * pct / 100)
        c = color if active else C_BORDER
        for t in range(4):
            x = int(cx + math.cos(rad) * (r - t))
            y = int(cy + math.sin(rad) * (r - t))
            draw.point((x, y), fill=c)
    txt = f"{pct:.0f}%"
    bbox = FB(16).getbbox(txt)
    draw.text((cx - (bbox[2]-bbox[0])//2, cy - 10), txt, fill=color, font=FB(16))
    bbox = F(9).getbbox(label)
    draw.text((cx - (bbox[2]-bbox[0])//2, cy + 12), label, fill=C_MUTED, font=F(9))

def draw_gauge_big(draw, cx, cy, r, pct, color, label):
    for a in range(-135, 136, 1):
        rad = math.radians(a)
        active = a <= -135 + int(270 * pct / 100)
        c = color if active else C_BORDER
        for t in range(6):
            x = int(cx + math.cos(rad) * (r - t))
            y = int(cy + math.sin(rad) * (r - t))
            draw.point((x, y), fill=c)
    txt = f"{pct:.0f}%"
    bbox = FB(28).getbbox(txt)
    draw.text((cx - (bbox[2]-bbox[0])//2, cy - 16), txt, fill=color, font=FB(28))
    bbox = FB(12).getbbox(label)
    draw.text((cx - (bbox[2]-bbox[0])//2, cy + 20), label, fill=C_MUTED, font=FB(12))

def draw_bar(draw, x, y, w, h, pct, color):
    draw.rounded_rectangle([x, y, x+w, y+h], 2, fill=C_BORDER)
    fw = int(w * pct / 100)
    if fw > 2: draw.rounded_rectangle([x, y, x+fw, y+h], 2, fill=color)

def draw_graph(draw, x, y, w, h, data, color, label):
    draw.rounded_rectangle([x, y, x+w, y+h], 4, fill=C_CARD)
    draw.text((x+4, y+2), label, fill=C_MUTED, font=F(9))
    cur = list(data)[-1] if data else 0
    draw.text((x+w-35, y+2), f"{cur:.0f}", fill=color, font=F(9))
    gx, gy, gw, gh = x+2, y+16, w-4, h-20
    for i in range(1, 4):
        ly = gy + gh*i//4
        for lx in range(gx, gx+gw, 3): draw.point((lx, ly), fill=C_BORDER)
    pts = list(data)
    mx = max(max(pts) if pts else 1, 1)
    if label in ("CPU", "RAM"): mx = 100
    for i in range(1, len(pts)):
        x1 = gx + gw*(i-1)//(len(pts)-1)
        x2 = gx + gw*i//(len(pts)-1)
        y1 = gy + gh - int(gh * pts[i-1] / mx)
        y2 = gy + gh - int(gh * pts[i] / mx)
        draw.line([(x1,y1),(x2,y2)], fill=color, width=2)

def draw_cores(draw, x, y, w, h, cores):
    draw.rounded_rectangle([x, y, x+w, y+h], 4, fill=C_CARD)
    draw.text((x+4, y+2), "CORES", fill=C_MUTED, font=F(9))
    if not cores: return
    n = min(len(cores), 12)
    bw = max(2, (w-12)//n - 2)
    bh = h - 28
    for i in range(n):
        bx = x + 6 + i*(bw+2)
        by = y + 18
        c = C_RED if cores[i] > 90 else C_YELLOW if cores[i] > 60 else C_ACCENT
        draw.rectangle([bx, by, bx+bw, by+bh], fill=C_BORDER)
        fh = int(bh * cores[i] / 100)
        if fh > 0: draw.rectangle([bx, by+bh-fh, bx+bw, by+bh], fill=c)

def draw_procs(draw, x, y, w, h, procs, count):
    draw.rounded_rectangle([x, y, x+w, y+h], 4, fill=C_CARD)
    draw.text((x+4, y+2), f"PROCS ({count})", fill=C_MUTED, font=F(9))
    for i, p in enumerate(procs[:min(6, (h-20)//12)]):
        py = y + 18 + i*12
        draw.text((x+4, py), p['name'][:14], fill=C_TEXT, font=F(9))
        draw.text((x+w-55, py), f"{p['cpu_percent']:.0f}%", fill=C_RED if p['cpu_percent']>20 else C_MUTED, font=F(9))
        draw.text((x+w-28, py), f"{p['memory_percent']:.1f}", fill=C_YELLOW if p['memory_percent']>3 else C_MUTED, font=F(9))

def draw_status_bar(draw, W, H):
    draw.line([(0, H-16), (W, H-16)], fill=C_BORDER)
    # View dots
    for i in range(NUM_VIEWS):
        c = C_ACCENT if i == current_view else C_BORDER
        draw.ellipse([W//2-20+i*10, H-11, W//2-14+i*10, H-5], fill=c)
    draw.text((4, H-14), f"R{rotation}°", fill=C_MUTED, font=F(8))
    draw.text((W-120, H-14), "F9/F11:Vista F10:Rotar", fill=C_BORDER, font=F(8))

# ============== VIEWS ==============
def render_dashboard(W, H):
    img = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    s = stats
    if not s: return img

    # Header
    draw.text((8, 4), "CHEMMONITOR", fill=C_TEXT, font=FB(14))
    draw.text((8, 22), s['hostname'], fill=C_ACCENT, font=F(9))
    draw.line([(0, 36), (W, 36)], fill=C_BORDER)

    # Gauges
    draw_gauge(draw, 55, 76, 28, s['cpu'], gauge_color(s['cpu']), "CPU")
    draw_gauge(draw, 160, 76, 28, s['mem_pct'], C_YELLOW if s['mem_pct']>70 else C_CYAN, "RAM")
    draw_gauge(draw, 265, 76, 28, s['disk_pct'], C_YELLOW, "DISCO")

    draw.line([(0, 112), (W, 112)], fill=C_BORDER)

    # Bars
    draw.text((8, 116), f"RAM {fmt_bytes(s['mem_used'])}/{fmt_bytes(s['mem_total'])}", fill=C_MUTED, font=F(9))
    draw_bar(draw, 8, 129, W-16, 7, s['mem_pct'], C_CYAN)
    draw.text((8, 140), f"DISCO {fmt_bytes(s['disk_used'])}/{fmt_bytes(s['disk_total'])}", fill=C_MUTED, font=F(9))
    draw_bar(draw, 8, 153, W-16, 7, s['disk_pct'], C_YELLOW)

    # GPU + Net + Uptime
    gpu = s['gpu']
    y = 165
    if gpu['temp'] is not None:
        draw.text((8, y), f"GPU {gpu['temp']}°C {gpu['load']}%", fill=C_GREEN, font=F(9))
    draw.text((140, y), f"TX {fmt_rate(s['sent_rate'])}", fill=C_GREEN, font=F(9))
    draw.text((230, y), f"RX {fmt_rate(s['recv_rate'])}", fill=C_YELLOW, font=F(9))
    y += 14
    bat = s.get('battery')
    if bat:
        bc = C_GREEN if bat['charging'] else (C_RED if bat['pct'] < 20 else C_TEXT)
        draw.text((8, y), f"BAT {bat['pct']:.0f}%{'⚡' if bat['charging'] else ''}", fill=bc, font=F(9))
    draw.text((140, y), f"Up: {fmt_uptime(s['uptime'])}", fill=C_MUTED, font=F(9))

    draw.line([(0, y+14), (W, y+14)], fill=C_BORDER)
    gy = y + 18

    # Graphs
    draw_graph(draw, 2, gy, W//2-3, 70, cpu_hist, C_ACCENT, "CPU")
    draw_graph(draw, W//2+1, gy, W//2-3, 70, ram_hist, C_CYAN, "RAM")

    # Cores + Processes
    draw_cores(draw, 2, gy+74, W//2-3, 70, s['cores'])
    draw_procs(draw, W//2+1, gy+74, W//2-3, 70, s['procs'], s['proc_count'])

    draw_status_bar(draw, W, H)
    return img

def render_cpu_view(W, H):
    img = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    s = stats
    if not s: return img

    draw_gauge_big(draw, W//2, 70, 50, s['cpu'], gauge_color(s['cpu']), "CPU")
    draw.text((W//2-40, 130), f"{len(s['cores'])} cores", fill=C_TEXT, font=FB(14))

    # Per-core bars
    cores = s['cores']
    n = min(len(cores), 12)
    bw = max(4, (W-20)//n - 3)
    for i in range(n):
        bx = 10 + i*(bw+3)
        c = C_RED if cores[i] > 90 else C_YELLOW if cores[i] > 60 else C_ACCENT
        draw.rectangle([bx, 155, bx+bw, 195], fill=C_BORDER)
        fh = int(40 * cores[i] / 100)
        if fh > 0: draw.rectangle([bx, 195-fh, bx+bw, 195], fill=c)
        draw.text((bx, 198), str(i), fill=C_MUTED, font=F(8))

    # Big graph
    draw_graph(draw, 4, 215, W-8, H-215-20, cpu_hist, C_ACCENT, "CPU HISTORY")

    # Top processes by CPU
    draw.text((8, 150), "", fill=C_MUTED, font=F(9))

    draw_status_bar(draw, W, H)
    return img

def render_ram_view(W, H):
    img = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    s = stats
    if not s: return img

    draw_gauge_big(draw, 80, 70, 50, s['mem_pct'], C_YELLOW if s['mem_pct']>70 else C_CYAN, "RAM")

    # Info
    draw.text((170, 40), f"{fmt_bytes(s['mem_used'])}", fill=C_TEXT, font=FB(16))
    draw.text((170, 60), f"de {fmt_bytes(s['mem_total'])}", fill=C_MUTED, font=F(11))
    draw.text((170, 85), f"Disco: {s['disk_pct']:.0f}%", fill=C_YELLOW, font=F(11))
    draw_bar(draw, 170, 100, W-180, 8, s['disk_pct'], C_YELLOW)

    gpu = s['gpu']
    if gpu['name']:
        draw.text((8, 135), gpu['name'][:30], fill=C_GREEN, font=F(9))
        if gpu['temp']: draw.text((8, 148), f"Temp: {gpu['temp']}°C  Load: {gpu['load']}%  VRAM: {gpu['mem_pct']:.0f}%", fill=C_MUTED, font=F(9))
        draw_bar(draw, 8, 162, W-16, 6, gpu['load'] or 0, C_GREEN)

    draw.line([(0, 175), (W, 175)], fill=C_BORDER)

    # Big graph
    draw_graph(draw, 4, 180, W-8, H-180-20, ram_hist, C_CYAN, "RAM HISTORY")

    draw_status_bar(draw, W, H)
    return img

def render_network_view(W, H):
    img = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    s = stats
    if not s: return img

    draw.text((8, 6), "NETWORK", fill=C_TEXT, font=FB(16))
    draw.text((8, 28), s['hostname'], fill=C_ACCENT, font=F(10))
    draw.line([(0, 44), (W, 44)], fill=C_BORDER)

    # Current rates big
    draw.text((20, 55), "UPLOAD", fill=C_MUTED, font=F(10))
    draw.text((20, 70), fmt_rate(s['sent_rate']), fill=C_GREEN, font=FB(20))
    draw.text((20, 100), "DOWNLOAD", fill=C_MUTED, font=F(10))
    draw.text((20, 115), fmt_rate(s['recv_rate']), fill=C_YELLOW, font=FB(20))

    # Totals
    draw.text((200, 55), "Total TX", fill=C_MUTED, font=F(9))
    draw.text((200, 68), fmt_bytes(s['net_sent']), fill=C_TEXT, font=FB(12))
    draw.text((200, 100), "Total RX", fill=C_MUTED, font=F(9))
    draw.text((200, 113), fmt_bytes(s['net_recv']), fill=C_TEXT, font=FB(12))

    draw.line([(0, 145), (W, 145)], fill=C_BORDER)

    # Graphs
    draw_graph(draw, 4, 150, W-8, 80, net_sent_hist, C_GREEN, "TX KB/s")
    draw_graph(draw, 4, 235, W-8, 80, net_recv_hist, C_YELLOW, "RX KB/s")

    # Top processes
    draw.line([(0, 320), (W, 320)], fill=C_BORDER)
    draw.text((8, 325), "TOP PROCESOS", fill=C_MUTED, font=F(9))
    for i, p in enumerate(s['procs'][:8]):
        py = 340 + i*14
        draw.text((8, py), p['name'][:18], fill=C_TEXT, font=F(9))
        draw.text((W-80, py), f"CPU:{p['cpu_percent']:.0f}%", fill=C_MUTED, font=F(9))
        draw.text((W-35, py), f"{p['memory_percent']:.1f}%", fill=C_MUTED, font=F(9))

    draw_status_bar(draw, W, H)
    return img

def render_clock_view(W, H):
    img = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    s = stats

    # Big clock
    t = time.strftime("%H:%M")
    sec = time.strftime(":%S")
    date = time.strftime("%A %d %B %Y")

    bbox = FB(48).getbbox(t)
    tw = bbox[2] - bbox[0]
    draw.text((W//2 - tw//2 - 10, H//2 - 80), t, fill=C_TEXT, font=FB(48))
    draw.text((W//2 + tw//2 - 10, H//2 - 55), sec, fill=C_ACCENT, font=FB(22))

    bbox = F(12).getbbox(date)
    tw = bbox[2] - bbox[0]
    draw.text((W//2 - tw//2, H//2 - 15), date, fill=C_MUTED, font=F(12))

    draw.line([(40, H//2+5), (W-40, H//2+5)], fill=C_BORDER)

    # Mini stats
    if s:
        y = H//2 + 20
        draw.text((W//2-100, y), f"CPU {s['cpu']:.0f}%", fill=gauge_color(s['cpu']), font=FB(14))
        draw.text((W//2, y), f"RAM {s['mem_pct']:.0f}%", fill=C_CYAN, font=FB(14))
        y += 25
        draw.text((W//2-100, y), f"TX {fmt_rate(s['sent_rate'])}", fill=C_GREEN, font=F(11))
        draw.text((W//2, y), f"RX {fmt_rate(s['recv_rate'])}", fill=C_YELLOW, font=F(11))
        y += 20
        draw.text((W//2-100, y), f"Up: {fmt_uptime(s['uptime'])}", fill=C_MUTED, font=F(11))
        bat = s.get('battery')
        if bat:
            draw.text((W//2, y), f"BAT {bat['pct']:.0f}%", fill=C_GREEN if bat['charging'] else C_TEXT, font=F(11))
        gpu = s['gpu']
        if gpu['temp']:
            y += 20
            draw.text((W//2-100, y), f"GPU {gpu['temp']}°C {gpu['load']}%", fill=C_GREEN, font=F(11))

    draw_status_bar(draw, W, H)
    return img

def render_no_signal(W, H):
    img = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    cx, cy = W//2, H//2 - 30
    draw.rectangle([0, 0, W-1, H-1], outline=C_RED, width=2)
    r = 35
    draw.arc([cx-r, cy-r, cx+r, cy+r], 0, 360, fill=C_RED, width=3)
    draw.line([(cx-18, cy-18), (cx+18, cy+18)], fill=C_RED, width=3)
    draw.line([(cx-18, cy+18), (cx+18, cy-18)], fill=C_RED, width=3)
    draw.text((cx-55, cy+50), "SIN SENAL", fill=C_RED, font=FB(20))
    draw.text((cx-80, cy+80), "Esperando conexion...", fill=C_MUTED, font=F(12))
    draw.text((cx-30, cy+110), time.strftime("%H:%M:%S"), fill=C_ACCENT, font=F(14))
    draw.text((cx-70, H-20), "ChemaDev & ClaudeCode", fill=C_BORDER, font=F(9))
    return img

# ============== RENDER DISPATCH ==============
RENDERERS = [render_dashboard, render_cpu_view, render_ram_view, render_network_view, render_clock_view]

def render_current():
    with lock:
        view = current_view
        rot = rotation
    W, H = SCREEN_W, SCREEN_H
    img = RENDERERS[view](W, H)
    if rot != 0:
        img = img.rotate(-rot, expand=True)
    return img

# ============== MAIN ==============
def auto_detect_port():
    for p in serial.tools.list_ports.comports():
        desc = p.description or ""
        hwid = p.hwid or ""
        if "1A86" in hwid and "5722" in hwid:
            return p.device
        if "CH34" in desc and "USB" in desc:
            return p.device
    for p in serial.tools.list_ports.comports():
        if "USB" in (p.description or "") and "Serial" in (p.description or ""):
            return p.device
    return None

def main():
    print("=" * 50)
    print("  ChemMonitor USB - Pantalla 3.5\" IPS")
    print("  by ChemaDev & ClaudeCode")
    print("=" * 50)

    port = sys.argv[1] if len(sys.argv) > 1 else (auto_detect_port() or SERIAL_PORT)
    print(f"Puerto: {port}")
    print(f"Resolucion: {SCREEN_W}x{SCREEN_H}")

    setup_keyboard()
    print()

    display = USBDisplay(port, BAUD_RATE)
    connected = False

    try:
        while True:
            if not connected:
                print(f"Conectando a {port}...", end=" ", flush=True)
                if display.connect():
                    connected = True
                    display.set_brightness(80)
                    print("OK!")
                else:
                    print("FAIL")
                    time.sleep(3)
                    continue

            try:
                if not paused:
                    t0 = time.time()
                    collect_stats()
                    img = render_current()
                    display.display_image(img)
                    elapsed = time.time() - t0
                    print(f"\r[{time.strftime('%H:%M:%S')}] V{current_view} R{rotation}° "
                          f"CPU:{stats.get('cpu',0):.0f}% RAM:{stats.get('mem_pct',0):.0f}% "
                          f"{elapsed:.2f}s   ", end="", flush=True)
                    time.sleep(max(0.1, UPDATE_INTERVAL - elapsed))
                else:
                    print(f"\r[PAUSADO] F12 para reanudar   ", end="", flush=True)
                    time.sleep(0.5)

            except (serial.SerialException, OSError) as e:
                print(f"\nDesconectado: {e}")
                connected = False
                display.close()
                display = USBDisplay(port, BAUD_RATE)
                try:
                    d2 = USBDisplay(port, BAUD_RATE)
                    if d2.connect():
                        d2.display_image(render_no_signal(SCREEN_W, SCREEN_H))
                        d2.close()
                except: pass
                time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nSaliendo...")
        try:
            if connected:
                img = Image.new("RGB", (SCREEN_W, SCREEN_H), C_BG)
                draw = ImageDraw.Draw(img)
                draw.text((SCREEN_W//2-70, SCREEN_H//2-15), "CHEMMONITOR", fill=C_ACCENT, font=FB(18))
                draw.text((SCREEN_W//2-45, SCREEN_H//2+10), "Desconectado", fill=C_MUTED, font=F(12))
                display.display_image(img)
        except: pass
    finally:
        display.close()
        print("Bye!")

if __name__ == "__main__":
    main()
