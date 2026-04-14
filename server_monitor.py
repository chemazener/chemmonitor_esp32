#!/usr/bin/env python3
"""Server Monitor - Sirve stats del sistema para ESP32 display.
Compatible con Windows y Linux. Ejecutar: python server_monitor.py
"""
import psutil
import time
import json
import socket
import platform
import subprocess
import threading
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Cached ping result
ping_cache = {"ms": -1, "last": 0}

def update_ping():
    while True:
        try:
            if platform.system() == "Windows":
                r = subprocess.run(["ping", "-n", "1", "-w", "1000", "8.8.8.8"],
                                   capture_output=True, text=True, timeout=3)
                for line in r.stdout.split("\n"):
                    if "time=" in line.lower() or "tiempo=" in line.lower():
                        for part in line.split():
                            if part.lower().startswith("time=") or part.lower().startswith("tiempo="):
                                ping_cache["ms"] = float(part.split("=")[1].replace("ms","").replace("ms",""))
            else:
                r = subprocess.run(["ping", "-c", "1", "-W", "1", "8.8.8.8"],
                                   capture_output=True, text=True, timeout=3)
                for line in r.stdout.split("\n"):
                    if "time=" in line:
                        t = line.split("time=")[1].split(" ")[0]
                        ping_cache["ms"] = float(t)
        except Exception:
            ping_cache["ms"] = -1
        ping_cache["last"] = time.time()
        time.sleep(5)

threading.Thread(target=update_ping, daemon=True).start()

def get_cpu_temp():
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name in temps:
                for entry in temps[name]:
                    if entry.current and entry.current > 0:
                        return round(entry.current, 1)
    except Exception:
        pass
    # Windows fallback via WMI
    try:
        import wmi
        w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
        for sensor in w.Sensor():
            if sensor.SensorType == "Temperature" and "CPU" in sensor.Name:
                return round(float(sensor.Value), 1)
    except Exception:
        pass
    return None

def get_gpu_temp():
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        return temp
    except Exception:
        pass
    return None

def get_system_stats():
    cpu_percent = psutil.cpu_percent(interval=0)
    cpu_per_core = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage('/' if platform.system() != 'Windows' else 'C:/')
    net = psutil.net_io_counters()
    uptime = time.time() - psutil.boot_time()

    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
        try:
            info = p.info
            if info['cpu_percent'] is not None and info['memory_percent'] is not None:
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x['memory_percent'] or 0, reverse=True)

    return {
        "timestamp": time.time() * 1000,
        "cpu": {"total": cpu_percent, "cores": cpu_per_core},
        "memory": {
            "total": mem.total, "used": mem.used,
            "available": mem.available, "percent": mem.percent
        },
        "swap": {
            "total": swap.total, "used": swap.used, "percent": swap.percent
        },
        "disk": {
            "total": disk.total, "used": disk.used,
            "free": disk.free, "percent": disk.percent
        },
        "network": {
            "bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv
        },
        "temperature": {
            "cpu": get_cpu_temp(), "gpu": get_gpu_temp()
        },
        "uptime": uptime,
        "ping": ping_cache["ms"],
        "processes": procs[:20],
        "process_count": len(procs)
    }

@app.get("/api/stats")
def stats():
    return get_system_stats()

@app.get("/api/config")
def config():
    return {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "cpu_count": psutil.cpu_count(),
        "ip": socket.gethostbyname(socket.gethostname())
    }

@app.post("/api/kill/{pid}")
def kill_process(pid: int):
    try:
        p = psutil.Process(pid)
        p.terminate()
        return {"status": "ok", "pid": pid}
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return {"status": "error", "message": str(e)}

# Camera endpoint (optional, requires opencv-python)
camera = None
@app.get("/api/camera")
def camera_snapshot():
    global camera
    try:
        import cv2
        if camera is None:
            camera = cv2.VideoCapture(0)
        ret, frame = camera.read()
        if not ret:
            return Response(status_code=503)
        frame = cv2.resize(frame, (160, 120))
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
        return Response(content=jpeg.tobytes(), media_type="image/jpeg")
    except ImportError:
        return JSONResponse({"error": "opencv-python not installed"}, status_code=501)

async def event_stream():
    while True:
        data = get_system_stats()
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(1)

@app.get("/api/stream")
def stream():
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/", response_class=HTMLResponse)
def index():
    return "<html><body><h1>Server Monitor API</h1><p>Running on " + socket.gethostname() + "</p><p><a href='/api/stats'>/api/stats</a> - JSON stats</p><p><a href='/api/config'>/api/config</a> - Server info</p></body></html>"

if __name__ == "__main__":
    ip = "0.0.0.0"
    port = 8090
    print(f"Server Monitor starting on http://{ip}:{port}")
    print(f"Hostname: {socket.gethostname()}")
    print(f"Platform: {platform.system()}")
    uvicorn.run(app, host=ip, port=port, log_level="warning")
