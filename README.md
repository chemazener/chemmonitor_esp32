# ChemMonitor ESP32

**Monitor de servidores en tiempo real con pantalla táctil ESP32 y pantalla USB secundaria.**

Desarrollado por **ChemaDev** & **ClaudeCode**

![License](https://img.shields.io/badge/license-MIT-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-green)
![ESP32](https://img.shields.io/badge/ESP32-D0WD--V3-red)

---

## Descripcion

ChemMonitor es un sistema de monitorizacion de servidores que muestra estadisticas del sistema (CPU, RAM, disco, red, GPU, procesos) en dos tipos de pantallas:

1. **ESP32 + TFT 3.5"** - Pantalla tactil conectada por WiFi
2. **Pantalla USB 3.5" IPS** - Conectada directamente por cable USB al PC

---

## Hardware soportado

### Opcion 1: ESP32 con pantalla TFT (WiFi)

| Componente | Especificaciones |
|---|---|
| **Microcontrolador** | ESP32-D0WD-V3 (rev 3.1), Dual Core 240MHz |
| **RAM** | 520KB SRAM (sin PSRAM) |
| **Flash** | 4MB SPI |
| **WiFi** | 802.11 b/g/n 2.4GHz |
| **Bluetooth** | BT 4.2 + BLE |
| **Pantalla** | ILI9488 TFT 3.5" 480x320 pixels, 16-bit color |
| **Touch** | XPT2046 resistivo |
| **USB-Serial** | CH340 |
| **Backlight** | PWM controlable |

**Conexiones SPI:**

| Pin ESP32 | Funcion |
|---|---|
| GPIO14 | SPI CLK |
| GPIO13 | SPI MOSI |
| GPIO12 | SPI MISO |
| GPIO15 | Display CS |
| GPIO2 | Display DC |
| GPIO27 | Backlight (PWM) |
| GPIO33 | Touch CS |
| GPIO36 | Touch IRQ |

### Opcion 2: Pantalla USB 3.5" IPS (cable directo)

| Componente | Especificaciones |
|---|---|
| **Tipo** | Pantalla secundaria USB Type-C IPS |
| **Resolucion** | 320x480 pixels |
| **Panel** | IPS, angulo de vision completo |
| **Conexion** | USB Type-C (2 puertos) |
| **Chip serial** | CH343 (WCH QinHeng Electronics) |
| **VID:PID** | 1A86:5722 |
| **Protocolo** | Serial RGB565 (compatible Turing Smart Screen) |
| **Uso tipico** | Monitor AIDA64, pantalla de stats para PC |
| **Precio** | ~13 EUR en AliExpress |

> Esta pantalla NO tiene touch ni microcontrolador programable. El PC renderiza las imagenes y las envia por serial.

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    PC / SERVIDOR                         │
│                                                          │
│  ┌──────────────────┐    ┌───────────────────────────┐  │
│  │ server_monitor.py │    │  chemmonitor_usb.py       │  │
│  │ (FastAPI + psutil)│    │  (Pillow + pyserial)      │  │
│  │ Puerto 8090       │    │  Renderiza + envia RGB565 │  │
│  └────────┬─────────┘    └──────────┬────────────────┘  │
│           │ HTTP JSON                │ USB Serial         │
└───────────┼──────────────────────────┼──────────────────┘
            │ WiFi                     │ Cable USB
            ▼                          ▼
    ┌───────────────┐         ┌────────────────┐
    │   ESP32 + TFT │         │  Pantalla USB  │
    │   480x320     │         │  320x480 IPS   │
    │   Touch       │         │  CH343 serial  │
    │   5 vistas    │         │  5 vistas      │
    └───────────────┘         └────────────────┘
```

---

## Funcionalidades

### Datos monitorizados
- CPU total y por core (hasta 16 cores)
- RAM usada/total/porcentaje
- Swap (memoria virtual)
- Disco usado/total/porcentaje
- Red: bytes enviados/recibidos, velocidad TX/RX
- Temperatura CPU y GPU (NVIDIA via pynvml)
- GPU load y VRAM (NVIDIA)
- Ping a internet (8.8.8.8)
- Uptime del sistema
- Bateria del portatil (porcentaje + cargando)
- Top 20 procesos por uso de memoria
- Conteo total de procesos

### ESP32 - Vistas tactiles (5)
1. **Dashboard** - Gauges CPU/RAM/Disco + barras + graficas + cores + procesos
2. **CPU** - Gauge grande + barras por core + grafica historial
3. **RAM** - Gauge grande + info disco + swap + ping
4. **Cores** - Todos los CPU cores a pantalla completa
5. **Procesos** - Tabla completa con PID, CPU%, MEM%

### ESP32 - Interaccion tactil
- **Swipe izquierda/derecha** - Cambiar vista
- **Swipe arriba/abajo** - Cambiar servidor (multiservidor)
- **Tap en barra inferior** - Vista anterior/siguiente
- **Tap en barra de estado** - Cambiar tema de color
- **Tap en proceso** - Matar proceso (vista Procesos)
- **Long press** - Slider de brillo

### ESP32 - Funciones avanzadas
- **2 temas de color**: Dark y Cyber
- **Screensaver Matrix**: Se activa tras 30s sin tocar
- **Gauges animados**: Interpolacion suave de valores
- **Multiservidor**: Hasta 4 servidores configurables
- **WiFi AP siempre activo**: Configurable desde 192.168.4.1
- **Portal cautivo**: Pagina web de configuracion embebida
- **SPIFFS**: Software del servidor descargable desde el ESP32
- **Boton reset**: En pantalla y en web

### Pantalla USB - Vistas (5)
1. **Dashboard** - Gauges + barras + GPU + bateria + graficas + cores + procesos
2. **CPU** - Gauge grande + cores + grafica historial
3. **RAM/GPU** - RAM + disco + info GPU NVIDIA completa
4. **Network** - Velocidades TX/RX + graficas + totales + procesos
5. **Reloj** - Hora grande + fecha + stats resumidos

### Pantalla USB - Controles de teclado
| Tecla | Accion |
|---|---|
| **F8** | Activar/desactivar auto-rotacion de vistas |
| **F9** | Siguiente vista |
| **F10** | Rotar pantalla 90 grados |
| **F11** | Vista anterior |
| **F12** | Pausar / reanudar |

### Pantalla USB - Funciones extra
- **Auto-rotacion de vistas** cada 15 segundos
- **Rotacion 90 grados automatica** cada vez que se enciende
- **Reconexion automatica** si se desconecta el USB
- **Pantalla "SIN SENAL"** cuando pierde conexion
- **Pantalla de despedida** al cerrar
- **Auto-deteccion de puerto** COM
- **Servicio de Windows** - arranca automaticamente con el PC

---

## Instalacion

### Requisitos
- Python 3.10 o superior
- pip (gestor de paquetes Python)

### Opcion A: Pantalla USB (la mas facil)

```bash
# 1. Clonar repositorio
git clone https://github.com/chemazener/chemmonitor_esp32.git
cd chemmonitor_esp32

# 2. Instalar dependencias
pip install psutil fastapi uvicorn pyserial Pillow pynput

# 3. Ejecutar
python usb_display/chemmonitor_usb.py

# 4. (Opcional) Instalar como servicio de Windows
# Doble clic en usb_display/install_service.bat
```

### Opcion B: ESP32 + TFT (WiFi)

**Paso 1: Flashear el ESP32**
```bash
# Necesitas PlatformIO
cd monitor-fw
pio run -t upload      # Flash firmware
pio run -t uploadfs    # Flash archivos web (SPIFFS)
```

**Paso 2: Configurar el ESP32**
1. Conecta al WiFi **"ChemMonitor-Setup"** (password: `12345678`)
2. Abre **http://192.168.4.1** en el navegador
3. Descarga el software del servidor (pestana Configurar)
4. Configura WiFi y la IP del servidor

**Paso 3: Instalar servidor en el PC**
```bash
# Windows: doble clic en install.bat
# Linux: chmod +x install.sh && ./install.sh
```

---

## Estructura del proyecto

```
chemmonitor_esp32/
├── README.md                          # Este archivo
├── server_monitor.py                  # Servidor Python (raiz, para uso directo)
├── dashboard-flow.html                # Diagrama interactivo del flujo del sistema
│
├── monitor-fw/                        # Firmware ESP32 (PlatformIO)
│   ├── platformio.ini                 # Configuracion de compilacion
│   ├── src/
│   │   └── main.cpp                   # Firmware principal (~1000 lineas)
│   └── data/                          # Archivos SPIFFS
│       ├── index.html                 # Portal web de configuracion
│       └── server/                    # Paquete descargable del servidor
│           ├── server_monitor.py      # Servidor Python (version SPIFFS)
│           ├── requirements.txt       # Dependencias Python
│           ├── install.bat            # Instalador Windows
│           └── install.sh             # Instalador Linux
│
└── usb_display/                       # Pantalla USB 3.5" IPS
    ├── chemmonitor_usb.py             # Script principal
    ├── chemmonitor_launcher.vbs       # Lanzador sin ventana
    └── install_service.bat            # Instalador de servicio Windows
```

---

## API del servidor

El servidor Python expone los siguientes endpoints:

| Endpoint | Metodo | Descripcion |
|---|---|---|
| `/` | GET | Pagina web con info del servidor |
| `/api/stats` | GET | JSON con todas las estadisticas del sistema |
| `/api/config` | GET | Hostname, plataforma, CPU count |
| `/api/kill/{pid}` | POST | Matar un proceso por PID |
| `/api/camera` | GET | Snapshot de webcam JPEG (requiere opencv-python) |
| `/api/stream` | GET | Server-Sent Events (tiempo real) |

### Ejemplo de respuesta `/api/stats`
```json
{
  "cpu": {"total": 12.5, "cores": [9.9, 2.8, 35.9, ...]},
  "memory": {"total": 17179869184, "used": 12474736640, "percent": 72.6},
  "swap": {"total": 8589934592, "used": 1073741824, "percent": 12.5},
  "disk": {"total": 548398850048, "used": 433564352512, "percent": 79.1},
  "network": {"bytes_sent": 4404019200, "bytes_recv": 14504108032},
  "temperature": {"cpu": 62.0, "gpu": 55},
  "uptime": 345600.5,
  "ping": 12.3,
  "processes": [...],
  "process_count": 251
}
```

---

## Configuracion de red

### Problema comun: AP Isolation

Si el ESP32 se conecta al WiFi pero no puede llegar al servidor Python, tu router puede tener **aislamiento de AP** activado (bloquea comunicacion entre dispositivos WiFi).

**Soluciones:**
1. Conectar el PC por **cable Ethernet** (WiFi-a-Ethernet si funciona)
2. Desactivar AP isolation en la configuracion del router
3. Usar la **pantalla USB** en lugar del ESP32 (sin WiFi, cable directo)

### IP y puertos
- Servidor Python: `http://[IP_DEL_PC]:8090`
- ESP32 config: `http://192.168.4.1` (via WiFi "ChemMonitor-Setup")
- ESP32 web: `http://[IP_ESP32]:80`

---

## Dependencias

### Python (servidor y pantalla USB)
```
psutil >= 5.9.0        # Estadisticas del sistema
fastapi >= 0.100.0     # API REST (solo servidor)
uvicorn >= 0.20.0      # Servidor ASGI (solo servidor)
pyserial >= 3.5        # Comunicacion serial (solo pantalla USB)
Pillow >= 9.0.0        # Renderizado de imagen (solo pantalla USB)
pynput >= 1.7.0        # Deteccion de teclado (solo pantalla USB)
pynvml                 # GPU NVIDIA (opcional)
opencv-python          # Webcam (opcional)
```

### ESP32 (PlatformIO)
```
bodmer/TFT_eSPI @ ^2.5.43      # Driver display ILI9488
bblanchon/ArduinoJson @ ^7.0.0  # Parser JSON
```

---

## Licencia

MIT License - Libre para uso personal y comercial.

---

## Creditos

Desarrollado por **ChemaDev** & **ClaudeCode**

Creado con Claude Code (Anthropic) - Abril 2026
