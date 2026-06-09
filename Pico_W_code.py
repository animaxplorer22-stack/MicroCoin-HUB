# pico_w_miner.py
# Raspberry Pi Pico W Miner for MicroCoin
# Uses built-in MicroPython modules only

import network
import ujson
import uhashlib
import ubinascii
import machine
import time
import uasyncio as asyncio
import random
import gc

# ==================== CONFIGURATION ====================
# EDIT THESE TWO LINES
WIFI_SSID = "your_wifi_ssid"
WIFI_PASSWORD = "your_wifi_password"
NODE_URL = "ws://192.168.1.100:8080"  # Change to your node IP

# Wallet (generate once, save here)
WALLET_ADDRESS = "MC_PICO_W_YOUR_ADDRESS"
PRIVATE_KEY = ""  # Leave empty to generate new on first run

# Staking
STAKE_AMOUNT = 100

# ==================== CONSTANTS ====================
LED_PIN = 25  # Built-in LED on Pico W
SIGNING_WINDOW_MS = 2500
UPTIME_PING_SECONDS = 30

# ==================== WEBSOCKET CLIENT ====================
class SimpleWebSocket:
    def __init__(self):
        self.sock = None
    
    async def connect(self, url):
        # Parse ws://host:port
        if url.startswith("ws://"):
            url = url[5:]
        host, port = url.split(":")
        port = int(port)
        
        import socket
        addr = socket.getaddrinfo(host, port)[0][-1]
        self.sock = socket.socket()
        self.sock.settimeout(5)
        self.sock.connect(addr)
        
        # WebSocket handshake
        key = ubinascii.b2a_base64(b"0123456789abcde").decode().strip()
        handshake = f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        self.sock.send(handshake.encode())
        response = self.sock.recv(1024)
        if b"101" not in response:
            return False
        
        self.sock.settimeout(0.1)
        return True
    
    def send(self, data):
        if not self.sock:
            return
        # WebSocket frame
        frame = b'\x81' + bytes([len(data)]) + data.encode()
        self.sock.send(frame)
    
    async def receive(self):
        if not self.sock:
            return None
        try:
            data = self.sock.recv(4096)
            if data and len(data) > 2:
                # Skip frame header
                payload = data[2:2+data[1]]
                return payload.decode()
        except:
            pass
        return None
    
    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

# ==================== CRYPTO ====================
def sha256(data):
    return uhashlib.sha256(data.encode()).digest()

def hexlify(data):
    return ubinascii.hexlify(data).decode()

def generate_wallet():
    # Generate random private key from MAC address + random
    import uos
    import urandom
    mac = ubinascii.hexlify(network.WLAN().config('mac')).decode()
    random_bytes = urandom.getrandbits(128)
    private_key = hexlify(sha256(mac + str(random_bytes)))
    public_key = hexlify(sha256(private_key + "microcoin_pub"))
    address = "MC_PICO_W_" + public_key[:28].upper()
    return {"address": address, "private_key": private_key}

def sign_challenge(challenge, block_id, address, private_key):
    data = f"{challenge}{address}{block_id}{STAKE_AMOUNT}"
    return hexlify(sha256(data + private_key))

# ================= = WIFI ====================
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    
    print("Connecting to WiFi", end="")
    for i in range(30):
        if wlan.isconnected():
            print("\nWiFi connected!")
            print(f"IP: {wlan.ifconfig()[0]}")
            return True
        print(".", end="")
        time.sleep(1)
    print("\nWiFi failed!")
    return False

# ==================== LED CONTROL ====================
led = machine.Pin(LED_PIN, machine.Pin.OUT)

def blink(times=1, duration=0.1):
    for _ in range(times):
        led.value(1)
        time.sleep(duration)
        led.value(0)
        time.sleep(duration)

def set_led(state):
    led.value(state)

# ==================== MICROCOIN MINER ====================
class PicoMiner:
    def __init__(self):
        self.ws = None
        self.mining = False
        self.stats = {
            "stake": STAKE_AMOUNT,
            "level": 1,
            "rewards": 0,
            "blocks": 0,
            "slashes": 0,
            "uptime": 0
        }
        self.wallet = None
        self.is_validator = False
        self.current_challenge = ""
        self.current_block_id = 0
        self.last_challenge_time = 0
        self.uptime_counter = 0
        self.last_uptime = 0
        
        # Load or create wallet
        self.load_wallet()
        self.calculate_level()
    
    def calculate_level(self):
        self.stats["level"] = ((self.stats["stake"] - 1) // 100) + 1
        if self.stats["level"] < 1:
            self.stats["level"] = 1
        if self.stats["level"] > 100:
            self.stats["level"] = 100
    
    def load_wallet(self):
        try:
            with open("wallet.json", "r") as f:
                import ujson
                self.wallet = ujson.load(f)
            print(f"Loaded wallet: {self.wallet['address']}")
        except:
            print("Generating new wallet...")
            self.wallet = generate_wallet()
            import ujson
            with open("wallet.json", "w") as f:
                ujson.dump(self.wallet, f)
            print(f"New wallet: {self.wallet['address']}")
            print("SAVE THIS PRIVATE KEY:", self.wallet['private_key'])
    
    def add_log(self, msg):
        t = time.localtime()
        timestamp = f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
        print(f"[{timestamp}] {msg}")
    
    def add_reward(self, amount):
        self.stats["rewards"] += amount
        self.stats["stake"] += amount
        self.stats["blocks"] += 1
        self.calculate_level()
        self.save_stats()
        self.add_log(f"REWARD: +{amount} MC | Total: {self.stats['rewards']} | Level: {self.stats['level']}")
        blink(2, 0.05)
    
    def handle_slash(self):
        slash = int(self.stats["stake"] * 0.1)
        if slash < 100:
            slash = 100
        self.stats["stake"] -= slash
        if self.stats["stake"] < 100:
            self.stats["stake"] = 100
        self.stats["slashes"] += 1
        self.calculate_level()
        self.save_stats()
        self.add_log(f"SLASHED! -{slash} MC | Stake: {self.stats['stake']}")
        blink(5, 0.1)
        return self.stats["slashes"] < 5
    
    def save_stats(self):
        try:
            with open("stats.json", "w") as f:
                import ujson
                ujson.dump(self.stats, f)
        except:
            pass
    
    def load_stats(self):
        try:
            with open("stats.json", "r") as f:
                import ujson
                self.stats = ujson.load(f)
        except:
            pass
    
    def register(self):
        if not self.ws:
            return
        msg = {
            "type": "register",
            "validator_id": f"PICO_{self.wallet['address'][:16]}",
            "public_key": self.wallet['address'],
            "wallet": self.wallet['address'],
            "stake": self.stats["stake"],
            "level": self.stats["level"],
            "rewards": self.stats["rewards"],
            "blocks": self.stats["blocks"],
            "timestamp": time.time()
        }
        self.ws.send(ujson.dumps(msg))
        self.add_log("Registered with node")
    
    async def handle_message(self, data):
        try:
            msg = ujson.loads(data)
            t = msg.get("type", "")
            
            if t == "registered":
                self.add_log(f"Registration confirmed - Level {msg.get('level')}")
                set_led(1)
            
            elif t == "challenge":
                self.current_challenge = msg.get("challenge", "")
                self.current_block_id = msg.get("block_id", 0)
                self.last_challenge_time = time.ticks_ms()
                self.is_validator = True
                
                signature = sign_challenge(
                    self.current_challenge,
                    self.current_block_id,
                    self.wallet['address'],
                    self.wallet['private_key']
                )
                
                response = {
                    "type": "block_signature",
                    "validator_id": f"PICO_{self.wallet['address'][:16]}",
                    "challenge": self.current_challenge,
                    "signature": signature,
                    "level": self.stats["level"],
                    "stake": self.stats["stake"],
                    "block_id": self.current_block_id,
                    "timestamp": time.time()
                }
                self.ws.send(ujson.dumps(response))
                self.add_log(f"Signed block {self.current_block_id}")
            
            elif t == "block_accepted":
                self.is_validator = False
                self.add_reward(msg.get("reward", 450))
                self.add_log(f"Block {msg.get('block_id')} ACCEPTED!")
            
            elif t == "block_rejected":
                self.is_validator = False
                self.add_log(f"Block {msg.get('block_id')} REJECTED")
            
            elif t == "slash":
                if not self.handle_slash():
                    self.mining = False
        
        except Exception as e:
            self.add_log(f"Parse error: {e}")
    
    async def send_uptime(self):
        if self.ws:
            msg = {
                "type": "uptime_ping",
                "validator_id": f"PICO_{self.wallet['address'][:16]}",
                "uptime_seconds": self.uptime_counter,
                "stake": self.stats["stake"],
                "level": self.stats["level"]
            }
            self.ws.send(ujson.dumps(msg))
    
    async def run(self):
        self.load_stats()
        
        self.add_log("="*40)
        self.add_log("MICROCOIN PICO W MINER")
        self.add_log("="*40)
        self.add_log(f"Wallet: {self.wallet['address']}")
        self.add_log(f"Stake: {self.stats['stake']} MC")
        self.add_log(f"Level: {self.stats['level']}")
        self.add_log("="*40)
        
        if not connect_wifi():
            self.add_log("WiFi failed! Restarting...")
            machine.reset()
        
        self.ws = SimpleWebSocket()
        
        while True:
            try:
                if not self.ws.sock:
                    self.add_log("Connecting to node...")
                    if await self.ws.connect(NODE_URL):
                        self.register()
                    else:
                        await asyncio.sleep(5)
                        continue
                
                # Send uptime ping
                if time.time() - self.last_uptime > UPTIME_PING_SECONDS:
                    await self.send_uptime()
                    self.uptime_counter += UPTIME_PING_SECONDS
                    self.last_uptime = time.time()
                
                # Check signing timeout
                if self.is_validator and (time.ticks_ms() - self.last_challenge_time) > SIGNING_WINDOW_MS:
                    self.add_log(f"Missed block {self.current_block_id}")
                    self.handle_slash()
                    self.is_validator = False
                
                # Receive messages
                data = await self.ws.receive()
                if data:
                    await self.handle_message(data)
                
                # Print status every 60 seconds
                if self.uptime_counter % 60 == 0 and self.uptime_counter > 0:
                    self.add_log(f"STATUS: Level {self.stats['level']} | Stake {self.stats['stake']} | Blocks {self.stats['blocks']} | Rewards {self.stats['rewards']}")
                
                await asyncio.sleep(0.1)
                
            except Exception as e:
                self.add_log(f"Error: {e}")
                if self.ws:
                    self.ws.close()
                await asyncio.sleep(5)

# ==================== MAIN ====================
async def main():
    miner = PicoMiner()
    await miner.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped")
        machine.reset()
