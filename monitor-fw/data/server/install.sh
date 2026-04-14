#!/bin/bash
cd "$(dirname "$0")"
echo "============================================"
echo "  Server Monitor - Instalador para Linux"
echo "============================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 no encontrado. Instala con:"
    echo "  sudo apt install python3 python3-pip   (Debian/Ubuntu)"
    echo "  sudo dnf install python3 python3-pip   (Fedora)"
    echo "  sudo pacman -S python python-pip       (Arch)"
    exit 1
fi

echo "[OK] Python3 encontrado"
python3 --version
echo ""

# Install dependencies
echo "Instalando dependencias..."
pip3 install -r requirements.txt --quiet --break-system-packages 2>/dev/null || \
pip3 install -r requirements.txt --quiet
if [ $? -ne 0 ]; then
    echo "[ERROR] Fallo al instalar dependencias"
    echo "Prueba: pip3 install -r requirements.txt --user"
    exit 1
fi
echo "[OK] Dependencias instaladas"
echo ""

# Get IP
echo "Direccion IP de este equipo:"
ip -4 addr show | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | grep -v '127.0.0' | head -3
echo ""

# Create start script
cat > start_monitor.sh << 'SCRIPT'
#!/bin/bash
echo "Iniciando Server Monitor en puerto 8090..."
echo "Presiona Ctrl+C para detener"
python3 server_monitor.py
SCRIPT
chmod +x start_monitor.sh

# Create systemd service (optional)
cat > server-monitor.service << 'SERVICE'
[Unit]
Description=Server Monitor for ESP32
After=network.target

[Service]
Type=simple
WorkingDirectory=WORKDIR
ExecStart=/usr/bin/python3 server_monitor.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
sed -i "s|WORKDIR|$(pwd)|g" server-monitor.service

echo "[OK] Creado start_monitor.sh"
echo "[OK] Creado server-monitor.service (systemd)"
echo ""
echo "============================================"
echo "  Instalacion completada!"
echo ""
echo "  Para iniciar manualmente:"
echo "    ./start_monitor.sh"
echo ""
echo "  Para instalar como servicio (auto-arranque):"
echo "    sudo cp server-monitor.service /etc/systemd/system/"
echo "    sudo systemctl enable --now server-monitor"
echo ""
echo "  Luego configura la IP de este PC en el"
echo "  ESP32 desde: http://192.168.4.1"
echo "============================================"
