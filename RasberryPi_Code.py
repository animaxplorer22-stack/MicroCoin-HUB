#!/usr/bin/env python3
# microcoin_rpi_miner.py
# ONE FILE - NO DEPENDENCIES NEEDED
# Runs on any Raspberry Pi with default Python 3

import json
import time
import hashlib
import os
import sys
import random
import socket
import threading
from datetime import datetime

# ==================== CONFIGURATION ====================
# EDIT THESE TWO LINES ONLY
NODE_IP = "192.168.1.100"  # Change to your node IP address
WALLET_ADDRESS = "MC_YourWalletAddressHere"  # Change to your wallet address

STAKE_AMOUNT = 100
SIGNING_WINDOW_SECONDS = 2.5
UPTIME_PING_SECONDS = 30

# ==================== NO DEPENDENCIES BEYOND THIS POINT ====================

class SimpleWebSocket:
    """Simple WebSocket client - no external libraries"""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
    
    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.host, self.port))
            
            # WebSocket handshake
            key = base64_encode(b"0123456789abcde")
            handshake = f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            self.sock.send(handshake.encode())
            response = self.sock.recv(1024)
            
            if "101" in response.decode():
                self.connected = True
                print("[WS] Connected to node")
                return True
            return False
        except Exception as e:
            print(f"[WS] Connection failed: {e}")
            return False
    
    def send(self, message):
        if not self.connected or not self.sock:
            return False
        try:
            # Simple frame (no masking for server)
            frame = b'\x81' + bytes([len(message)]) + message.encode()
            self.sock.send(frame)
            return True
        except:
            self.connected = False
            return False
    
    def receive(self):
        if not self.connected or not self.sock:
            return None
        try:
            self.sock.settimeout(0.1)
            data = self.sock.recv(4096)
            if data and len(data) > 2:
                # Skip frame header
                payload = data[2:2+data[1]]
                return payload.decode()
            return None
        except socket.timeout:
            return None
        except:
            self.connected = False
            return None

def base64_encode(data):
    """Simple base64 encoding"""
    b64_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    result = []
    for i in range(0, len(data), 3):
        chunk = data[i:i+3]
        if len(chunk) == 3:
            n = (chunk[0] << 16) | (chunk[1] << 8) | chunk[2]
            result.append(b64_chars[(n >> 18) & 0x3F])
            result.append(b64_chars[(n >> 12) & 0x3F])
            result.append(b64_chars[(n >> 6) & 0x3F])
            result.append(b64_chars[n & 0x3F])
    return ''.join(result)

class RPiMiner:
    def __init__(self):
        self.wallet_address = WALLET_ADDRESS
        self.node_ip = NODE_IP
        self.node_port = 8080
        
        # State
        self.current_stake = STAKE_AMOUNT
        self.level = 1
        self.total_rewards = 0
        self.blocks_signed = 0
        self.slash_count = 0
        self.uptime = 0
        
        # Validator state
        self.is_validator = False
        self.current_challenge = ""
        self.current_block_id = 0
        self.last_challenge_time = 0
        
        # Network
        self.ws = None
        self.running = True
        
        # Load saved stats
        self.load_stats()
        self.calculate_level()
    
    def calculate_level(self):
        self.level = ((self.current_stake - 1) // 100) + 1
        if self.level < 1:
            self.level = 1
        if self.level > 100:
            self.level = 100
    
    def load_stats(self):
        stats_file = "rpi_miner_stats.json"
        if os.path.exists(stats_file):
            try:
                with open(stats_file, "r") as f:
                    data = json.load(f)
                    self.total_rewards = data.get("rewards", 0)
                    self.blocks_signed = data.get("blocks", 0)
                    self.slash_count = data.get("slashes", 0)
                    self.current_stake = data.get("stake", STAKE_AMOUNT)
                    self.calculate_level()
            except:
                pass
    
    def save_stats(self):
        with open("rpi_miner_stats.json", "w") as f:
            json.dump({
                "rewards": self.total_rewards,
                "blocks": self.blocks_signed,
                "slashes": self.slash_count,
                "stake": self.current_stake
            }, f)
    
    def sign_challenge(self, challenge, block_id):
        """Simple signature (for production, use real crypto)"""
        # Combine data
        data = f"{challenge}{self.wallet_address}{block_id}{self.current_stake}"
        # Simple hash as signature
        signature = hashlib.sha256(data.encode()).hexdigest()
        return signature
    
    def send_message(self, msg_type, **kwargs):
        msg = {"type": msg_type, **kwargs}
        if self.ws:
            self.ws.send(json.dumps(msg))
    
    def register(self):
        self.send_message("register",
            validator_id=f"RPI_{self.wallet_address[:16]}",
            public_key="rpi_miner_key",
            wallet=self.wallet_address,
            stake=self.current_stake,
            level=self.level,
            rewards=self.total_rewards,
            blocks=self.blocks_signed,
            uptime=self.uptime,
            timestamp=time.time()
        )
        print(f"[REG] Registered with node")
    
    def send_uptime(self):
        self.uptime += UPTIME_PING_SECONDS
        self.send_message("uptime_ping",
            validator_id=f"RPI_{self.wallet_address[:16]}",
            uptime_seconds=self.uptime,
            stake=self.current_stake,
            level=self.level
        )
    
    def add_reward(self, amount):
        self.total_rewards += amount
        self.current_stake += amount
        self.blocks_signed += 1
        self.calculate_level()
        self.save_stats()
        print(f"[REWARD] +{amount} MC | Total: {self.total_rewards} | Stake: {self.current_stake} | Level: {self.level}")
    
    def handle_slash(self):
        slash_amount = int(self.current_stake * 0.1)
        if slash_amount < 100:
            slash_amount = 100
        self.current_stake -= slash_amount
        if self.current_stake < 100:
            self.current_stake = 100
        self.slash_count += 1
        self.calculate_level()
        self.save_stats()
        print(f"[SLASH] -{slash_amount} MC | New stake: {self.current_stake} | Level: {self.level}")
        return self.slash_count < 5
    
    def handle_message(self, msg):
        if "type" not in msg:
            return
        
        t = msg["type"]
        
        if t == "challenge":
            self.current_challenge = msg.get("challenge", "")
            self.current_block_id = msg.get("block_id", 0)
            self.last_challenge_time = time.time()
            self.is_validator = True
            
            # Sign the block
            signature = self.sign_challenge(self.current_challenge, self.current_block_id)
            self.send_message("block_signature",
                validator_id=f"RPI_{self.wallet_address[:16]}",
                challenge=self.current_challenge,
                signature=signature,
                level=self.level,
                stake=self.current_stake,
                block_id=self.current_block_id,
                timestamp=time.time()
            )
            print(f"[SIGN] Block {self.current_block_id}")
        
        elif t == "block_accepted":
            self.add_reward(msg.get("reward", 0))
            self.is_validator = False
            print(f"[ACCEPT] Block {msg.get('block_id')}")
        
        elif t == "block_rejected":
            self.is_validator = False
            print(f"[REJECT] Block {msg.get('block_id')}: {msg.get('reason', 'Unknown')}")
        
        elif t == "slash":
            if not self.handle_slash():
                self.running = False
    
    def run(self):
        print("\n" + "="*50)
        print("MICROCOIN RASPBERRY PI MINER")
        print("="*50)
        print(f"Wallet: {self.wallet_address}")
        print(f"Node: {self.node_ip}:{self.node_port}")
        print(f"Initial Stake: {self.current_stake} MC")
        print(f"Level: {self.level}")
        print("="*50 + "\n")
        
        # Connect to node
        self.ws = SimpleWebSocket(self.node_ip, self.node_port)
        if not self.ws.connect():
            print("[ERROR] Cannot connect to node. Check if node is running.")
            return
        
        self.register()
        
        last_uptime = time.time()
        last_status = time.time()
        
        # Get Raspberry Pi info
        try:
            with open("/sys/firmware/devicetree/base/model", "r") as f:
                rpi_model = f.read().strip()
        except:
            rpi_model = "Unknown"
        
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = int(f.read().strip()) / 1000
        except:
            temp = 0
        
        print(f"[INFO] Running on: {rpi_model}")
        print(f"[INFO] Temperature: {temp:.1f}°C")
        print("[INFO] Miner ready. Waiting for challenges...\n")
        
        while self.running:
            # Receive messages
            raw = self.ws.receive()
            if raw:
                try:
                    msg = json.loads(raw)
                    self.handle_message(msg)
                except:
                    pass
            
            # Send uptime ping
            if time.time() - last_uptime > UPTIME_PING_SECONDS:
                self.send_uptime()
                last_uptime = time.time()
            
            # Check challenge timeout
            if self.is_validator and (time.time() - self.last_challenge_time) > SIGNING_WINDOW_SECONDS:
                print("[TIMEOUT] Missed signing window")
                self.handle_slash()
                self.is_validator = False
            
            # Print status every 60 seconds
            if time.time() - last_status > 60:
                last_status = time.time()
                print(f"\n[STATUS] Level: {self.level} | Stake: {self.current_stake} MC | Blocks: {self.blocks_signed} | Rewards: {self.total_rewards} MC | Temp: {temp:.1f}°C\n")
            
            time.sleep(0.1)
        
        print("\n[STOP] Miner stopped")

if __name__ == "__main__":
    miner = RPiMiner()
    try:
        miner.run()
    except KeyboardInterrupt:
        print("\n[STOP] Shutting down...")
        miner.save_stats()
