import time
import threading
import logging
import requests
import psutil
import os
import traceback
from flask import Flask, jsonify
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# ==========================
# CARGAR VARIABLES .env
# ==========================

base_path = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(base_path, ".env")

load_dotenv(env_path)

def str_to_bool(value):
    return str(value).lower() in ("true", "1", "yes")


class BatteryMonitor:

    # ==========================
    # INIT
    # ==========================

    def __init__(
        self,
        bot_token,
        chat_id,
        mqtt_enabled=False,
        mqtt_broker="localhost",
        mqtt_port=1883,
        mqtt_topic="server/battery",
        mqtt_user=None,
        mqtt_password=None,
        mqtt_tls=False,
        webhook_url=None,
        check_interval=30,
        api_port=5050
    ):

        self.bot_token = bot_token
        self.chat_id = chat_id
        self.check_interval = check_interval
        self.api_port = api_port

        self.current_state = "NORMAL"
        self.last_alert_time = 0

        # === Ruta absoluta del archivo actual ===
        base_path = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base_path, "battery_monitor.log")

        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s"
        )

        logging.info("=== Battery Monitor iniciado ===")

        # MQTT
        self.mqtt_enabled = mqtt_enabled
        self.mqtt_topic = mqtt_topic
        self.mqtt_client = None

        if mqtt_enabled:
            try:
                self.mqtt_client = mqtt.Client()

                # Autenticación
                if mqtt_user and mqtt_password:
                    self.mqtt_client.username_pw_set(mqtt_user, mqtt_password)

                # TLS opcional
                if mqtt_tls:
                    self.mqtt_client.tls_set()

                self.mqtt_client.connect(mqtt_broker, mqtt_port, 60)
                self.mqtt_client.loop_start()

                self.mqtt_client.publish(self.mqtt_topic, "Battery Monitor started")
                logging.info("MQTT conectado correctamente")

            except Exception:
                logging.error("Error conectando MQTT:\n" + traceback.format_exc())

        # Webhook
        self.webhook_url = webhook_url

        # API
        self.app = Flask(__name__)
        self._setup_routes()

    # ==========================
    # TELEGRAM
    # ==========================

    def send_telegram(self, message):
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message}
            requests.post(url, data=payload, timeout=5)
            logging.info(f"Telegram enviado: {message}")
        except Exception:
            logging.error("Error enviando Telegram:\n" + traceback.format_exc())

    # ==========================
    # MQTT
    # ==========================

    def publish_mqtt(self, data):
        if self.mqtt_enabled and self.mqtt_client:
            try:
                result = self.mqtt_client.publish(self.mqtt_topic, str(data))
                if result.rc != 0:
                    logging.warning("MQTT publish falló")
            except Exception:
                logging.error("Error publicando MQTT:\n" + traceback.format_exc())


    # ==========================
    # WEBHOOK OPCIONAL
    # ==========================

    def notify_webhook(self, data):
        if self.webhook_url:
            try:
                requests.post(self.webhook_url, json=data, timeout=3)
            except Exception:
                logging.error("Error enviando Webhook:\n" + traceback.format_exc())

    # ==========================
    # FUTURAS ACCIONES
    # ==========================

    def future_action(self, level):
        """
        🔌 IMPLEMENTACIÓN FUTURA

        Ejemplo:
        if level < 5:
            os.system("sudo shutdown now")
        """
        pass

    # ==========================
    # ESTADO BATERÍA
    # ==========================

    def get_battery_status(self):
        try:
            battery = psutil.sensors_battery()
            if battery:
                return {
                    "level": battery.percent,
                    "plugged": battery.power_plugged
                }
            return None
        except Exception:
            logging.error("Error leyendo batería:\n" + traceback.format_exc())
            return None

    # ==========================
    # MÁQUINA DE ESTADOS
    # ==========================

    def evaluate_state(self, level):
        if level < 10:
            return "CRITICAL_10"
        elif level < 30:
            return "CRITICAL_30"
        elif level < 60:
            return "WARNING_60"
        else:
            return "NORMAL"

    # ==========================
    # MONITOR LOOP
    # ==========================

    def monitor_loop(self):

        while True:
            try:
                status = self.get_battery_status()

                if status:
                    level = status["level"]
                    plugged = status["plugged"]
                    new_state = self.evaluate_state(level)
                    current_time = time.time()

                    logging.info(f"Nivel: {level}% | Conectado: {plugged}")

                    self.publish_mqtt(status)
                    self.notify_webhook(status)

                    if new_state != self.current_state:
                        logging.info(f"Cambio estado: {self.current_state} → {new_state}")
                        self.current_state = new_state
                        self.last_alert_time = 0

                    if self.current_state == "WARNING_60":
                        if self.last_alert_time == 0:
                            self.send_telegram(f"⚠️ Advertencia: batería al {level}%")
                            self.future_action(level)
                            self.last_alert_time = current_time

                    elif self.current_state == "CRITICAL_30":
                        if current_time - self.last_alert_time > 300:
                            self.send_telegram(f"🚨 Batería crítica: {level}%")
                            self.future_action(level)
                            self.last_alert_time = current_time

                    elif self.current_state == "CRITICAL_10":
                        if current_time - self.last_alert_time > 60:
                            self.send_telegram(f"🆘 BATERÍA MUY BAJA: {level}%")
                            self.future_action(level)
                            self.last_alert_time = current_time

                    elif self.current_state == "NORMAL":
                        self.last_alert_time = 0

                    if level == 100:
                        self.future_action(level)

                time.sleep(self.check_interval)

            except Exception:
                logging.critical("Error crítico en monitor_loop:\n" + traceback.format_exc())
                time.sleep(5)

    # ==========================
    # API INTERNA
    # ==========================

    def _setup_routes(self):

        @self.app.route("/battery", methods=["GET"])
        def battery_route():
            try:
                status = self.get_battery_status()
                if status:
                    return jsonify(status)
                return jsonify({"error": "No battery detected"}), 500
            except Exception:
                logging.error("Error en endpoint /battery:\n" + traceback.format_exc())
                return jsonify({"error": "Internal error"}), 500

    def run_api(self):
        try:
            self.app.run(host="0.0.0.0", port=self.api_port)
        except Exception:
            logging.critical("Error iniciando API:\n" + traceback.format_exc())

    # ==========================
    # START
    # ==========================

    def start(self):
        try:
            threading.Thread(target=self.monitor_loop, daemon=True).start()
            self.run_api()
        except Exception:
            logging.critical("Error iniciando servicio:\n" + traceback.format_exc())


# ==========================
# EJECUCIÓN
# ==========================

if __name__ == "__main__":

    monitor = BatteryMonitor(
        bot_token="1163613591:AAFpZO27ogWiatfDqvtgDtyb-BwLhsrUxVg",
        chat_id="1060506861",
        mqtt_enabled=True,
        mqtt_broker="localhost",
        mqtt_port=1883,
        mqtt_topic="server/battery",
        mqtt_user="systemapp",
        mqtt_password="Yoko#9523",
        webhook_url=None, # opcional: "http://localhost:3000/battery-update"
        check_interval=30,
        api_port=5050
    )

    monitor.start()

if __name__ == "__main__":

    try:
        monitor = BatteryMonitor(
            bot_token=os.getenv("TOKEN_TLGM"),
            chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            mqtt_enabled=str_to_bool(os.getenv("MQTT_ENABLED")),
            #mqtt_broker=os.getenv("MQTT_BROKER"),
            #mqtt_port=int(os.getenv("MQTT_PORT", 1883)),
            #mqtt_topic=os.getenv("MQTT_TOPIC"),
            mqtt_user=os.getenv("MQTT_USER"),
            mqtt_password=os.getenv("MQTT_PASSWORD"),
            mqtt_tls=str_to_bool(os.getenv("MQTT_TLS")),
            #webhook_url=os.getenv("WEBHOOK_URL") or None,
            check_interval=30,
            api_port=5050
        )

        monitor.start()

    except Exception:
        logging.critical("Error iniciando aplicación:\n" + traceback.format_exc())

