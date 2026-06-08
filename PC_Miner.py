#!/usr/bin/env python3
# MICROCOIN PC MINER - Full production miner for Windows/Linux/macOS
# Runs on any computer with Python 3.8+

import asyncio
import json
import time
import hashlib
import hmac
import secrets
import sqlite3
import platform
import psutil
import websockets
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, decode_dss_signature
from cryptography.exceptions import InvalidSignature
import os
import sys

# ==================== CONFIGURATION ====================
# EDIT THESE BEFORE RUNNING

NODE_WS_URL = "ws://localhost:8080"  # Change to your node IP
WALLET_FILE = "microcoin_wallet.json"  # Path to your wallet file
USE_BRIDGE = False  # Set to True if using WiFi bridge
BRIDGE_URL = "ws://192.168.1.200:8081"  # Bridge URL if USE_BRIDGE = True

# Mining settings
THREADS = 1  # Number of CPU threads to use (1 = validator mode only)
STAKE_AMOUNT = 100  # Initial stake in MicroCoins (100 = Level 1)

# ==================== CONSTANTS ====================
BLOCK_REWARD = 6000
LEVEL_STAKE_RANGE = 100
SIGNING_WINDOW_MS = 2500
SLASH_RATE = 0.10
VALIDATOR_SHARE = 0.75
NODE_SHARE = 0.08
UPTIME_SHARE = 0.07
LP_SHARE = 0.10
UPTIME_PING_INTERVAL = 30  # seconds
STATUS_INTERVAL = 60  # seconds
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 5  # seconds

# ==================== CRYPTO FUNCTIONS ====================
def generate_private_key() -> ec.EllipticCurvePrivateKey:
    """Generate a new secp256k1 private key"""
    return ec.generate_private_key(ec.SECP256K1())

def private_key_to_hex(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Convert private key to hex string"""
    return private_key.private_numbers().private_value.to_bytes(32, 'big').hex()

def hex_to_private_key(hex_key: str) -> ec.EllipticCurvePrivateKey:
    """Convert hex string to private key"""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    private_value = int(hex_key, 16)
    return ec.derive_private_key(private_value, ec.SECP256K1())

def get_public_key_pem(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Get public key in PEM format"""
    public_key = private_key.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')

def get_wallet_address(public_key_pem: str) -> str:
    """Generate wallet address from public key"""
    # SHA256 of public key, then take first 32 chars
    public_key_hash = hashlib.sha256(public_key_pem.encode()).hexdigest()
    return f"MC_{public_key_hash[:32].upper()}"

def sign_message(private_key: ec.EllipticCurvePrivateKey, message: str) -> str:
    """Sign a message using ECDSA secp256k1"""
    signature = private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    # Encode as 64-byte hex (r + s, each 32 bytes)
    r_bytes = r.to_bytes(32, 'big')
    s_bytes = s.to_bytes(32, 'big')
    return (r_bytes + s_bytes).hex()

def verify_signature(public_key_pem: str, message: str, signature_hex: str) -> bool:
    """Verify ECDSA signature"""
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        signature_bytes = bytes.fromhex(signature_hex)
        # Split into r and s (each 32 bytes)
        r = int.from_bytes(signature_bytes[:32], 'big')
        s = int.from_bytes(signature_bytes[32:], 'big')
        signature_der = encode_dss_signature(r, s)
        public_key.verify(signature_der, message.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False

# ==================== WALLET MANAGEMENT ====================
@dataclass
class Wallet:
    address: str
    public_key_pem: str
    private_key_hex: str
    created_at: float
    
    @classmethod
    def create_new(cls) -> 'Wallet':
        private_key = generate_private_key()
        private_key_hex = private_key_to_hex(private_key)
        public_key_pem = get_public_key_pem(private_key)
        address = get_wallet_address(public_key_pem)
        return cls(
            address=address,
            public_key_pem=public_key_pem,
            private_key_hex=private_key_hex,
            created_at=time.time()
        )
    
    @classmethod
    def load(cls, filename: str) -> Optional['Wallet']:
        if not os.path.exists(filename):
            return None
        with open(filename, 'r') as f:
            data = json.load(f)
        return cls(
            address=data['address'],
            public_key_pem=data['public_key_pem'],
            private_key_hex=data['private_key_hex'],
            created_at=data['created_at']
        )
    
    def save(self, filename: str):
        with open(filename, 'w') as f:
            json.dump({
                'address': self.address,
                'public_key_pem': self.public_key_pem,
                'private_key_hex': self.private_key_hex,
                'created_at': self.created_at
            }, f, indent=2)
    
    def get_private_key(self) -> ec.EllipticCurvePrivateKey:
        return hex_to_private_key(self.private_key_hex)

# ==================== PC MINER ====================
class PCMiner:
    def __init__(self, wallet: Wallet, node_url: str):
        self.wallet = wallet
        self.node_url = node_url
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.is_validator = False
        self.current_challenge = ""
        self.current_block_id = 0
        self.last_challenge_time = 0
        self.last_uptime_ping = 0
        self.last_status_report = 0
        self.uptime_seconds = 0
        self.start_time = time.time()
        
        # Stats
        self.total_rewards = 0
        self.blocks_signed = 0
        self.consecutive_misses = 0
        self.slash_count = 0
        self.reconnect_attempts = 0
        
        # Stake and level
        self.current_stake = STAKE_AMOUNT
        self.current_level = self.calculate_level()
        
        # Database for local stats
        self.init_database()
        self.load_stats()
    
    def calculate_level(self) -> int:
        level = ((self.current_stake - 1) // LEVEL_STAKE_RANGE) + 1
        return max(1, min(level, 100))
    
    def init_database(self):
        self.conn = sqlite3.connect('pc_miner_stats.db')
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS miner_stats
                     (key TEXT PRIMARY KEY, value REAL, updated_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS blocks_mined
                     (block_id INTEGER PRIMARY KEY, timestamp REAL, reward INTEGER)''')
        self.conn.commit()
    
    def load_stats(self):
        c = self.conn.cursor()
        c.execute("SELECT value FROM miner_stats WHERE key = 'total_rewards'")
        row = c.fetchone()
        if row:
            self.total_rewards = int(row[0])
        c.execute("SELECT value FROM miner_stats WHERE key = 'blocks_signed'")
        row = c.fetchone()
        if row:
            self.blocks_signed = int(row[0])
        c.execute("SELECT value FROM miner_stats WHERE key = 'slash_count'")
        row = c.fetchone()
        if row:
            self.slash_count = int(row[0])
        c.execute("SELECT value FROM miner_stats WHERE key = 'stake'")
        row = c.fetchone()
        if row:
            self.current_stake = int(row[0])
            self.current_level = self.calculate_level()
    
    def save_stats(self):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO miner_stats VALUES (?, ?, ?)",
                 ('total_rewards', self.total_rewards, time.time()))
        c.execute("INSERT OR REPLACE INTO miner_stats VALUES (?, ?, ?)",
                 ('blocks_signed', self.blocks_signed, time.time()))
        c.execute("INSERT OR REPLACE INTO miner_stats VALUES (?, ?, ?)",
                 ('slash_count', self.slash_count, time.time()))
        c.execute("INSERT OR REPLACE INTO miner_stats VALUES (?, ?, ?)",
                 ('stake', self.current_stake, time.time()))
        self.conn.commit()
    
    def add_reward(self, reward: int):
        self.total_rewards += reward
        self.current_stake += reward
        self.blocks_signed += 1
        self.consecutive_misses = 0
        self.current_level = self.calculate_level()
        self.save_stats()
        
        # Save block record
        c = self.conn.cursor()
        c.execute("INSERT INTO blocks_mined VALUES (?, ?, ?)",
                 (self.current_block_id, time.time(), reward))
        self.conn.commit()
        
        print(f"💰 REWARD: +{reward} MC | Total: {self.total_rewards} | Stake: {self.current_stake} | Level: {self.current_level} | Blocks: {self.blocks_signed}")
    
    def handle_slash(self):
        slash_amount = max(int(self.current_stake * SLASH_RATE), LEVEL_STAKE_RANGE)
        self.current_stake -= slash_amount
        if self.current_stake < LEVEL_STAKE_RANGE:
            self.current_stake = LEVEL_STAKE_RANGE
        self.consecutive_misses += 1
        self.slash_count += 1
        self.current_level = self.calculate_level()
        self.save_stats()
        
        print(f"⚠️ SLASHED: -{slash_amount} MC | New stake: {self.current_stake} | Level: {self.current_level} | Misses: {self.consecutive_misses}")
        
        if self.slash_count >= 5:
            print("🚫 BANNED: Too many slashes! Restart miner to rejoin.")
            return False
        return True
    
    async def send_message(self, msg_type: str, **kwargs):
        """Send a message to the node"""
        message = {"type": msg_type, **kwargs}
        if self.websocket:
            try:
                await self.websocket.send(json.dumps(message))
            except Exception as e:
                print(f"Failed to send message: {e}")
    
    async def register(self):
        """Register with the node"""
        timestamp = time.time()
        message_to_sign = f"{self.wallet.address}{self.current_stake}{timestamp}"
        signature = sign_message(self.wallet.get_private_key(), message_to_sign)
        
        await self.send_message(
            "register",
            validator_id=self.wallet.address[:32],
            public_key=self.wallet.public_key_pem,
            wallet=self.wallet.address,
            stake=self.current_stake,
            level=self.current_level,
            rewards=self.total_rewards,
            blocks=self.blocks_signed,
            uptime=int(self.uptime_seconds),
            timestamp=timestamp,
            signature=signature
        )
        print(f"📡 Registered with node: {self.wallet.address[:20]}...")
    
    async def send_uptime_ping(self):
        """Send uptime ping to node"""
        self.uptime_seconds = time.time() - self.start_time
        await self.send_message(
            "uptime_ping",
            validator_id=self.wallet.address[:32],
            uptime_seconds=int(self.uptime_seconds),
            stake=self.current_stake,
            level=self.current_level
        )
    
    async def sign_block(self):
        """Sign the current challenge"""
        message_to_sign = f"{self.current_challenge}{self.wallet.address[:32]}{self.current_block_id}"
        signature = sign_message(self.wallet.get_private_key(), message_to_sign)
        
        await self.send_message(
            "block_signature",
            validator_id=self.wallet.address[:32],
            challenge=self.current_challenge,
            signature=signature,
            level=self.current_level,
            stake=self.current_stake,
            block_id=self.current_block_id,
            timestamp=time.time()
        )
        print(f"✍️ Signed block {self.current_block_id}")
    
    async def handle_message(self, message: dict):
        """Process incoming messages from node"""
        msg_type = message.get("type")
        
        if msg_type == "registered":
            print(f"✅ Registration confirmed | Level: {message.get('level')} | Max Level: {message.get('max_level')}")
        
        elif msg_type == "challenge":
            self.current_challenge = message.get("challenge", "")
            self.current_block_id = message.get("block_id", 0)
            self.last_challenge_time = time.time()
            self.is_validator = True
            await self.sign_block()
        
        elif msg_type == "block_accepted":
            reward = message.get("reward", 0)
            self.add_reward(reward)
            self.is_validator = False
            print(f"✅ Block {message.get('block_id')} ACCEPTED | Reward: {reward} MC")
        
        elif msg_type == "block_rejected":
            reason = message.get("reason", "Unknown")
            print(f"❌ Block {message.get('block_id')} REJECTED: {reason}")
            self.is_validator = False
        
        elif msg_type == "slash":
            print(f"⚠️ SLASH command received")
            if not self.handle_slash():
                await self.websocket.close()
        
        elif msg_type == "level_update":
            new_stake = message.get("stake", self.current_stake)
            if new_stake != self.current_stake:
                self.current_stake = new_stake
                self.current_level = self.calculate_level()
                self.save_stats()
                print(f"📊 Level update: Stake {self.current_stake} | Level {self.current_level}")
        
        elif msg_type == "status":
            data = message.get("data", {})
            print(f"📊 Network Status: Max Level {data.get('max_level')} | Miners {data.get('active_miners')} | LPs {data.get('liquidity_providers')}")
    
    async def listen(self):
        """Main WebSocket loop"""
        uri = self.node_url
        self.reconnect_attempts = 0
        
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                    self.websocket = ws
                    print(f"🔌 Connected to {uri}")
                    self.reconnect_attempts = 0
                    
                    await self.register()
                    
                    async for raw_message in ws:
                        try:
                            message = json.loads(raw_message)
                            await self.handle_message(message)
                        except json.JSONDecodeError:
                            print(f"Invalid JSON: {raw_message[:100]}")
                        
                        # Send uptime ping periodically
                        if time.time() - self.last_uptime_ping > UPTIME_PING_INTERVAL:
                            await self.send_uptime_ping()
                            self.last_uptime_ping = time.time()
                        
                        # Print status periodically
                        if time.time() - self.last_status_report > STATUS_INTERVAL:
                            self.print_status()
                            self.last_status_report = time.time()
                        
                        # Check for signing timeout
                        if self.is_validator and (time.time() - self.last_challenge_time) > (SIGNING_WINDOW_MS / 1000):
                            print(f"⏰ Signing timeout! Missed block {self.current_block_id}")
                            if not self.handle_slash():
                                break
                            self.is_validator = False
                    
            except websockets.exceptions.ConnectionClosed as e:
                print(f"Connection closed: {e}")
            except Exception as e:
                print(f"Connection error: {e}")
            
            self.reconnect_attempts += 1
            if self.reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
                print(f"Max reconnect attempts reached. Exiting.")
                break
            
            delay = RECONNECT_DELAY * min(self.reconnect_attempts, 10)
            print(f"Reconnecting in {delay} seconds... (Attempt {self.reconnect_attempts})")
            await asyncio.sleep(delay)
    
    def print_status(self):
        """Print miner status"""
        uptime_hours = self.uptime_seconds / 3600
        print(f"\n{'='*50}")
        print(f"MICROCOIN PC MINER STATUS")
        print(f"{'='*50}")
        print(f"Wallet: {self.wallet.address[:20]}...")
        print(f"Level: {self.current_level} | Stake: {self.current_stake} MC")
        print(f"Rewards: {self.total_rewards} MC | Blocks: {self.blocks_signed}")
        print(f"Slash count: {self.slash_count} | Misses: {self.consecutive_misses}")
        print(f"Uptime: {uptime_hours:.1f} hours")
        print(f"Status: {'🟢 Validating' if self.is_validator else '🟡 Idle'}")
        print(f"{'='*50}\n")
    
    async def run(self):
        """Main run loop"""
        print(f"\n{'='*50}")
        print(f"MICROCOIN PC MINER v1.0")
        print(f"{'='*50}")
        print(f"Wallet: {self.wallet.address}")
        print(f"Initial Stake: {self.current_stake} MC")
        print(f"Initial Level: {self.current_level}")
        print(f"Node: {self.node_url}")
        print(f"{'='*50}\n")
        
        await self.listen()

# ==================== SYSTEM INFO ====================
def print_system_info():
    """Print system information for debugging"""
    print(f"System: {platform.system()} {platform.release()}")
    print(f"Python: {platform.python_version()}")
    print(f"CPU Count: {psutil.cpu_count()}")
    print(f"RAM: {psutil.virtual_memory().total / (1024**3):.1f} GB")

# ==================== MAIN ENTRY POINT ====================
async def main():
    print("\n🔷 MICROCOIN PC MINER 🔷\n")
    print_system_info()
    
    # Load or create wallet
    wallet = Wallet.load(WALLET_FILE)
    if not wallet:
        print(f"No wallet found. Creating new wallet...")
        wallet = Wallet.create_new()
        wallet.save(WALLET_FILE)
        print(f"✅ New wallet created!")
        print(f"   Address: {wallet.address}")
        print(f"   Saved to: {WALLET_FILE}")
        print(f"\n⚠️ BACKUP THIS FILE: {os.path.abspath(WALLET_FILE)}")
    else:
        print(f"✅ Wallet loaded: {wallet.address}")
    
    # Determine node URL
    node_url = BRIDGE_URL if USE_BRIDGE else NODE_WS_URL
    
    # Create and run miner
    miner = PCMiner(wallet, node_url)
    
    try:
        await miner.run()
    except KeyboardInterrupt:
        print("\n\n🛑 Miner stopped by user")
        miner.save_stats()
        print(f"Final stats - Rewards: {miner.total_rewards} MC | Blocks: {miner.blocks_signed}")

if __name__ == "__main__":
    asyncio.run(main())
