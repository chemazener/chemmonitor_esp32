#!/usr/bin/env python3
"""
ChemMonitor USB - Monitor de servidor en pantalla USB 3.5" IPS
Protocolo compatible con Turing Smart Screen / pantallas CH343 USB serial
Desarrollado por ChemaDev & ClaudeCode
"""
import serial
import struct
import time
import psutil
import platform
import socket
import threading
from PIL import Image, ImageDraw, ImageFont
from collections import deque
import math
import sys
import os

# ============== CONFIG ==============
SERIAL_PORT = "COM4"  # Change to your port
BAUD_RATE = 115200
SCREEN_W = 320
SCREEN_H = 480
UPDATE_INTERVAL = 2  # seconds

# ============== COLORS (RGB) ==============
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

# ============== PROTOCOL COMMANDS ==============
CMD_RESET = 101
CMD_CLEAR = 102
CMD_SCREEN_OFF = 108
CMD_SCREEN_ON = 109
CMD_SET_BRIGHTNESS = 110
CMD_SET_ORIENTATION = 121
CMD_DISPLAY_BITMAP = 197
CMD_HELLO = 69

# ============== DATA ==============
cpu_hist = deque([0.0] * 60, maxlen=60)
ram_hist = deque([0.0] * 60, maxlen=60)
stats = {}

# ============== SERIAL DISPLAY ==============
class USBDisplay:
    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud
        self.ser = None

    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1, rtscts=False)
            time.sleep(0.5)
            print(f"Connected to {self.port} at {self.baud} baud")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def send_command(self, cmd, payload=b''):
        """Send a command with optional payload"""
        if not self.ser:
            return
        try:
            self.ser.write(bytes([cmd]) + payload)
            self.ser.flush()
        except Exception as e:
            print(f"Send error: {e}")

    def set_brightness(self, level):
        """Set brightness 0-100"""
        val = int((100 - level) * 255 / 100)
        self.send_command(CMD_SET_BRIGHTNESS, bytes([val]))

    def set_orientation(self, orient=0):
        """Set screen orientation"""
        payload = bytearray(16)
        payload[0] = orient + 100
        # Width and height
        payload[1] = SCREEN_W >> 8
        payload[2] = SCREEN_W & 0xFF
        payload[3] = SCREEN_H >> 8
        payload[4] = SCREEN_H & 0xFF
        self.send_command(CMD_SET_ORIENTATION, bytes(payload))

    def image_to_rgb565(self, img):
        """Convert PIL Image to RGB565 little-endian bytes"""
        pixels = img.convert("RGB").load()
        w, h = img.size
        data = bytearray(w * h * 2)
        idx = 0
        for y in range(h):
            for x in range(w):
                r, g, b = pixels[x, y]
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                data[idx] = rgb565 & 0xFF      # little-endian
                data[idx + 1] = rgb565 >> 8
                idx += 2
        return bytes(data)

    def display_image(self, img, x=0, y=0):
        """Send a PIL Image to the screen at position (x, y)"""
        if not self.ser:
            return
        w, h = img.size
        ex = x + w - 1
        ey = y + h - 1

        # Build 6-byte header
        header = bytearray(6)
        header[0] = (x >> 2)
        header[1] = (((x & 3) << 6) + (y >> 4))
        header[2] = (((y & 15) << 4) + (ex >> 6))
        header[3] = (((ex & 63) << 2) + (ey >> 8))
        header[4] = (ey & 255)
        header[5] = CMD_DISPLAY_BITMAP

        rgb565 = self.image_to_rgb565(img)

        try:
            self.ser.write(header)
            # Send in chunks of width * 2 bytes
            chunk_size = w * 2
            for i in range(0, len(rgb565), chunk_size):
                self.ser.write(rgb565[i:i + chunk_size])
            self.ser.flush()
        except Exception as e:
            print(f"Display error: {e}")

    def display_full_image(self, img):
        """Send full screen image - try direct raw write if protocol doesn't work"""
        if not self.ser:
            return
        rgb565 = self.image_to_rgb565(img)
        try:
            # Method 1: Protocol with header
            self.display_image(img, 0, 0)
        except Exception as e:
            print(f"Display error: {e}")


# ============== STATS COLLECTION ==============
def collect_stats():
    global stats
    cpu = psutil.cpu_percent(interval=0)
    cpu_cores = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/' if platform.system() != 'Windows' else 'C:/')
    net = psutil.net_io_counters()

    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            info = p.info
            if info['cpu_percent'] is not None and info['memory_percent'] is not None:
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x['memory_percent'] or 0, reverse=True)

    uptime = time.time() - psutil.boot_time()

    stats = {
        'cpu': cpu,
        'cpu_cores': cpu_cores,
        'mem_pct': mem.percent,
        'mem_used': mem.used,
        'mem_total': mem.total,
        'disk_pct': disk.percent,
        'disk_used': disk.used,
        'disk_total': disk.total,
        'net_sent': net.bytes_sent,
        'net_recv': net.bytes_recv,
        'procs': procs[:10],
        'proc_count': len(procs),
        'uptime': uptime,
        'hostname': socket.gethostname(),
    }

    cpu_hist.append(cpu)
    ram_hist.append(mem.percent)


# ============== RENDERING ==============
def fmt_bytes(b):
    if b >= 1e9: return f"{b/1e9:.1f}GB"
    if b >= 1e6: return f"{b/1e6:.0f}MB"
    return f"{b/1e3:.0f}KB"

def fmt_uptime(sec):
    d = int(sec // 86400)
    h = int(sec // 3600) % 24
    m = int(sec // 60) % 60
    if d > 0: return f"{d}d{h}h{m}m"
    return f"{h}h{m}m"

def gauge_color(val):
    if val > 90: return C_RED
    if val > 70: return C_YELLOW
    return C_ACCENT

def draw_gauge(draw, cx, cy, r, pct, color, label, font_big, font_small):
    """Draw a circular gauge"""
    # Background arc
    for a in range(-135, 136, 2):
        rad = math.radians(a)
        active = a <= -135 + int(270 * pct / 100)
        c = color if active else C_BORDER
        for t in range(4):
            x = int(cx + math.cos(rad) * (r - t))
            y = int(cy + math.sin(rad) * (r - t))
            if 0 <= x < SCREEN_W and 0 <= y < SCREEN_H:
                draw.point((x, y), fill=c)
    # Value
    txt = f"{pct:.0f}%"
    bbox = font_big.getbbox(txt)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy - 12), txt, fill=color, font=font_big)
    # Label
    bbox = font_small.getbbox(label)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy + 14), label, fill=C_MUTED, font=font_small)

def draw_bar(draw, x, y, w, h, pct, color):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=2, fill=C_BORDER)
    fw = int(w * pct / 100)
    if fw > 2:
        draw.rounded_rectangle([x, y, x + fw, y + h], radius=2, fill=color)

def draw_graph(draw, x, y, w, h, data, color, label, font):
    """Draw a mini graph"""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=C_CARD)
    draw.text((x + 4, y + 2), label, fill=C_MUTED, font=font)
    gx, gy, gw, gh = x + 2, y + 18, w - 4, h - 22
    # Grid
    for i in range(1, 4):
        ly = gy + gh * i // 4
        for lx in range(gx, gx + gw, 3):
            draw.point((lx, ly), fill=C_BORDER)
    # Line
    pts = list(data)
    if len(pts) < 2: return
    for i in range(1, len(pts)):
        x1 = gx + gw * (i - 1) // (len(pts) - 1)
        x2 = gx + gw * i // (len(pts) - 1)
        y1 = gy + gh - int(gh * pts[i - 1] / 100)
        y2 = gy + gh - int(gh * pts[i] / 100)
        draw.line([(x1, y1), (x2, y2)], fill=color, width=2)

def draw_cores(draw, x, y, w, h, cores, font):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=C_CARD)
    draw.text((x + 4, y + 2), "CPU CORES", fill=C_MUTED, font=font)
    if not cores: return
    n = min(len(cores), 12)
    bw = (w - 12) // n - 2
    bh = h - 30
    for i in range(n):
        bx = x + 6 + i * (bw + 2)
        by = y + 20
        c = C_RED if cores[i] > 90 else C_YELLOW if cores[i] > 60 else C_ACCENT
        draw.rectangle([bx, by, bx + bw, by + bh], fill=C_BORDER)
        fh = int(bh * cores[i] / 100)
        if fh > 0:
            draw.rectangle([bx, by + bh - fh, bx + bw, by + bh], fill=c)

def draw_processes(draw, x, y, w, h, procs, proc_count, font):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=C_CARD)
    draw.text((x + 4, y + 2), f"PROCESOS ({proc_count})", fill=C_MUTED, font=font)
    for i, p in enumerate(procs[:6]):
        py = y + 20 + i * 12
        name = p['name'][:14]
        draw.text((x + 4, py), name, fill=C_TEXT, font=font)
        cpu_c = C_RED if p['cpu_percent'] > 20 else C_MUTED
        draw.text((x + w - 60, py), f"{p['cpu_percent']:.0f}%", fill=cpu_c, font=font)
        mem_c = C_YELLOW if p['memory_percent'] > 3 else C_MUTED
        draw.text((x + w - 30, py), f"{p['memory_percent']:.1f}%", fill=mem_c, font=font)


def render_dashboard():
    """Render the full dashboard as a PIL Image"""
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), C_BG)
    draw = ImageDraw.Draw(img)

    # Load fonts
    try:
        font_big = ImageFont.truetype("arial.ttf", 18)
        font_med = ImageFont.truetype("arial.ttf", 13)
        font_small = ImageFont.truetype("arial.ttf", 10)
        font_title = ImageFont.truetype("arialbd.ttf", 16)
    except:
        font_big = ImageFont.load_default()
        font_med = font_big
        font_small = font_big
        font_title = font_big

    s = stats
    if not s:
        draw.text((10, 10), "Loading...", fill=C_TEXT, font=font_title)
        return img

    W, H = SCREEN_W, SCREEN_H

    # Header
    draw.text((8, 6), "CHEMMONITOR", fill=C_TEXT, font=font_title)
    draw.text((8, 24), s.get('hostname', ''), fill=C_ACCENT, font=font_small)
    draw.line([(0, 38), (W, 38)], fill=C_BORDER)

    # Gauges
    cpu = s.get('cpu', 0)
    ram = s.get('mem_pct', 0)
    disk = s.get('disk_pct', 0)

    draw_gauge(draw, 55, 82, 32, cpu, gauge_color(cpu), "CPU", font_big, font_small)
    draw_gauge(draw, 160, 82, 32, ram, C_YELLOW if ram > 70 else C_CYAN, "RAM", font_big, font_small)
    draw_gauge(draw, 265, 82, 32, disk, C_YELLOW if disk > 70 else C_YELLOW, "DISCO", font_big, font_small)

    # Info bars
    draw.line([(0, 120), (W, 120)], fill=C_BORDER)

    # RAM bar
    draw.text((8, 125), "RAM", fill=C_MUTED, font=font_small)
    draw.text((W - 8 - font_small.getlength(f"{fmt_bytes(s['mem_used'])}/{fmt_bytes(s['mem_total'])}"), 125),
              f"{fmt_bytes(s['mem_used'])}/{fmt_bytes(s['mem_total'])}", fill=C_TEXT, font=font_small)
    draw_bar(draw, 8, 138, W - 16, 8, ram, C_CYAN)

    # Disk bar
    draw.text((8, 150), "DISCO", fill=C_MUTED, font=font_small)
    draw.text((W - 8 - font_small.getlength(f"{fmt_bytes(s['disk_used'])}/{fmt_bytes(s['disk_total'])}"), 150),
              f"{fmt_bytes(s['disk_used'])}/{fmt_bytes(s['disk_total'])}", fill=C_TEXT, font=font_small)
    draw_bar(draw, 8, 163, W - 16, 8, disk, C_YELLOW)

    # Network
    draw.text((8, 176), f"TX {fmt_bytes(s['net_sent'])}", fill=C_GREEN, font=font_small)
    draw.text((120, 176), f"RX {fmt_bytes(s['net_recv'])}", fill=C_YELLOW, font=font_small)
    draw.text((230, 176), f"Up: {fmt_uptime(s['uptime'])}", fill=C_MUTED, font=font_small)

    draw.line([(0, 190), (W, 190)], fill=C_BORDER)

    # Graphs
    draw_graph(draw, 2, 194, W // 2 - 3, 80, cpu_hist, C_ACCENT, "CPU", font_small)
    draw_graph(draw, W // 2 + 1, 194, W // 2 - 3, 80, ram_hist, C_CYAN, "RAM", font_small)

    # Cores & Processes
    cores = s.get('cpu_cores', [])
    procs = s.get('procs', [])
    draw_cores(draw, 2, 278, W // 2 - 3, 80, cores, font_small)
    draw_processes(draw, W // 2 + 1, 278, W // 2 - 3, 80, procs, s.get('proc_count', 0), font_small)

    # Status bar
    draw.line([(0, H - 18), (W, H - 18)], fill=C_BORDER)
    draw.text((4, H - 15), "ONLINE", fill=C_GREEN, font=font_small)
    draw.text((W - 8 - font_small.getlength("ChemaDev & ClaudeCode"), H - 15),
              "ChemaDev & ClaudeCode", fill=C_MUTED, font=font_small)

    return img


def render_no_signal():
    """Render a 'no signal' screen"""
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), C_BG)
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype("arialbd.ttf", 22)
        font_med = ImageFont.truetype("arial.ttf", 13)
        font_small = ImageFont.truetype("arial.ttf", 10)
    except:
        font_big = ImageFont.load_default()
        font_med = font_big
        font_small = font_big

    W, H = SCREEN_W, SCREEN_H

    # Pulsing border
    draw.rectangle([0, 0, W - 1, H - 1], outline=C_RED, width=2)

    # Icon: big X or signal bars
    cx, cy = W // 2, H // 2 - 40
    r = 35
    draw.arc([cx - r, cy - r, cx + r, cy + r], 0, 360, fill=C_RED, width=3)
    draw.line([(cx - 18, cy - 18), (cx + 18, cy + 18)], fill=C_RED, width=3)
    draw.line([(cx - 18, cy + 18), (cx + 18, cy - 18)], fill=C_RED, width=3)

    # Text
    txt = "SIN SENAL"
    bbox = font_big.getbbox(txt)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy + 50), txt, fill=C_RED, font=font_big)

    draw.text((cx - 80, cy + 85), "ChemMonitor USB desconectado", fill=C_MUTED, font=font_med)
    draw.text((cx - 70, cy + 110), "Esperando conexion...", fill=C_MUTED, font=font_med)

    t = time.strftime("%H:%M:%S")
    bbox = font_med.getbbox(t)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy + 145), t, fill=C_ACCENT, font=font_med)

    draw.text((cx - 75, H - 20), "ChemaDev & ClaudeCode", fill=C_BORDER, font=font_small)
    return img


def auto_detect_port():
    """Try to find the USB display port automatically"""
    import serial.tools.list_ports
    for p in serial.tools.list_ports.comports():
        # CH343 with our display serial number
        if "1A86" in (p.vid and hex(p.vid) or "") or "CH34" in (p.description or ""):
            print(f"Auto-detected: {p.device} ({p.description})")
            return p.device
        if "USB" in (p.description or "") and "Serial" in (p.description or ""):
            print(f"Possible match: {p.device} ({p.description})")
            return p.device
    return None


# ============== MAIN ==============
def main():
    print("=" * 50)
    print("  ChemMonitor USB - Pantalla 3.5\" IPS")
    print("  by ChemaDev & ClaudeCode")
    print("=" * 50)

    # Auto-detect or use argument
    port = SERIAL_PORT
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        detected = auto_detect_port()
        if detected:
            port = detected

    print(f"Puerto: {port}")
    print(f"Resolucion: {SCREEN_W}x{SCREEN_H}")
    print(f"Actualizacion cada {UPDATE_INTERVAL}s")
    print("Ctrl+C para salir")
    print()

    display = USBDisplay(port, BAUD_RATE)
    connected = False

    try:
        while True:
            # Reconnect loop
            if not connected:
                print(f"Conectando a {port}...", end=" ")
                if display.connect():
                    connected = True
                    display.set_brightness(80)
                    time.sleep(0.2)
                    print("OK!")
                else:
                    print("FAIL - reintentando en 3s...")
                    # Try to show no-signal if already was connected before
                    time.sleep(3)
                    continue

            t0 = time.time()

            try:
                # Collect stats
                collect_stats()

                # Render dashboard
                img = render_dashboard()

                # Send to display
                display.display_full_image(img)

                # Timing
                elapsed = time.time() - t0
                sleep_time = max(0.1, UPDATE_INTERVAL - elapsed)
                print(f"\r[{time.strftime('%H:%M:%S')}] CPU:{stats.get('cpu', 0):.0f}% "
                      f"RAM:{stats.get('mem_pct', 0):.0f}% "
                      f"render:{elapsed:.2f}s   ", end="", flush=True)
                time.sleep(sleep_time)

            except (serial.SerialException, OSError) as e:
                print(f"\nConexion perdida: {e}")
                connected = False
                display.close()
                display = USBDisplay(port, BAUD_RATE)

                # Show no-signal screen on reconnect
                print("Mostrando pantalla sin senal...")
                try:
                    display2 = USBDisplay(port, BAUD_RATE)
                    if display2.connect():
                        no_sig = render_no_signal()
                        display2.display_full_image(no_sig)
                        display2.close()
                except:
                    pass

                time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nSaliendo...")
        # Show goodbye screen
        try:
            if connected:
                img = Image.new("RGB", (SCREEN_W, SCREEN_H), C_BG)
                draw = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("arialbd.ttf", 18)
                    font_s = ImageFont.truetype("arial.ttf", 12)
                except:
                    font = ImageFont.load_default()
                    font_s = font
                draw.text((SCREEN_W // 2 - 80, SCREEN_H // 2 - 20), "CHEMMONITOR", fill=C_ACCENT, font=font)
                draw.text((SCREEN_W // 2 - 50, SCREEN_H // 2 + 10), "Desconectado", fill=C_MUTED, font=font_s)
                display.display_full_image(img)
        except:
            pass
    finally:
        display.close()
        print("Desconectado")


if __name__ == "__main__":
    main()
