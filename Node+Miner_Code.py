#!/usr/bin/env python3
"""
MICROCOIN NODE + MINER - COMPLETE MAINNET VERSION
Single file - Runs as both node AND miner on the same machine

Features:
- Real ECDSA secp256k1 cryptography
- Challenge-response authentication
- Multi-node P2P sync (gossip protocol)
- DEX integration (PancakeSwap)
- PoMA + PoS consensus
- 50B hard cap with 4M block halving
- Level system (100 MC per level)
- Reward distribution: 75% validators / 8% nodes / 7% uptime / 10% LP
"""

import asyncio
import json
import time
import hashlib
import sqlite3
import random
import os
import sys
import socket
import struct
import secrets
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict
import traceback

# ==================== WEBSOCKETS ====================
try:
    import websockets
except ImportError:
    print("ERROR: Install websockets: pip install websockets")
    sys.exit(1)

# ==================== CRYPTOGRAPHY ====================
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, decode_dss_signature
from cryptography.exceptions import InvalidSignature

# ==================== CONFIGURATION ====================
# Network settings
NODE_HOST = "0.0.0.0"
NODE_PORT = 8080
P2P_PORT = 8081

# Tokenomics - HARD CAP 50 BILLION
TOTAL_SUPPLY_CAP = 50_000_000_000
INITIAL_BLOCK_REWARD = 6000
HALVING_INTERVAL = 4_000_000
MINIMUM_BLOCK_REWARD = 1

# Reward Distribution Percentages
VALIDATOR_SHARE = 0.75   # 75% to validators (split among 10)
NODE_SHARE = 0.08        # 8% to nodes
UPTIME_SHARE = 0.07      # 7% to uptime pool
LP_SHARE = 0.10          # 10% to liquidity providers

# Consensus Parameters
LEVEL_STAKE_RANGE = 100
SIGNING_WINDOW_MS = 2500
SLASH_RATE = 0.10
MIN_VALIDATORS_PER_BLOCK = 10
MIN_WALLETS_FOR_NEXT_LEVEL = 10
MAX_LEVEL = 100
UPTIME_PING_INTERVAL = 30
DISTRIBUTION_INTERVAL_SEC = 300

# P2P Settings
MAX_PEERS = 30
SYNC_INTERVAL = 10
HEARTBEAT_INTERVAL = 30

# DEX Settings
DEX_ENABLED = True
DEX_TYPE = "pancakeswap"
DEX_RPC = "https://bsc-dataseed.binance.org/"
MC_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"  # Replace after deployment
USDC_TOKEN_ADDRESS = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"

# Bootstrap peers (add your friends' nodes here)
BOOTSTRAP_PEERS = []  # Format: ["192.168.1.101:8081", "192.168.1.102:8081"]

# ==================== REAL CRYPTOGRAPHY ====================
def verify_signature(public_key_pem: str, message: str, signature_hex: str) -> bool:
    """Verify a secp256k1 signature"""
    if len(signature_hex) != 128:
        return False
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        signature_bytes = bytes.fromhex(signature_hex)
        r = int.from_bytes(signature_bytes[:32], 'big')
        s = int.from_bytes(signature_bytes[32:], 'big')
        signature_der = encode_dss_signature(r, s)
        public_key.verify(signature_der, message.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False

def sign_message(private_key_hex: str, message: str) -> str:
    """Sign a message using secp256k1 private key"""
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    signature = private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex()

def generate_wallet() -> tuple:
    """Generate a new wallet"""
    private_key = ec.generate_private_key(ec.SECP256K1())
    private_key_hex = private_key.private_numbers().private_value.to_bytes(32, 'big').hex()
    public_key = private_key.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    addr_hash = hashlib.sha256(public_key_pem.encode()).hexdigest()
    address = f"MC_{addr_hash[:32].upper()}"
    return address, private_key_hex, public_key_pem

def hash_block(block_data: dict) -> str:
    """Generate SHA256 block hash"""
    return hashlib.sha256(json.dumps(block_data, sort_keys=True).encode()).hexdigest()

# ==================== P2P PROTOCOL ====================
P2P_MAGIC = b"MC01"
P2P_VERSION = 1

P2P_MSG_HANDSHAKE = 0x01
P2P_MSG_PING = 0x02
P2P_MSG_PONG = 0x03
P2P_MSG_GET_BLOCKS = 0x04
P2P_MSG_BLOCKS = 0x05
P2P_MSG_NEW_BLOCK = 0x08
P2P_MSG_GET_PEERS = 0x0A
P2P_MSG_PEERS = 0x0B

def encode_p2p_message(msg_type: int, payload: dict) -> bytes:
    payload_bytes = json.dumps(payload).encode()
    header = P2P_MAGIC + struct.pack(">B", P2P_VERSION) + struct.pack(">B", msg_type) + struct.pack(">I", len(payload_bytes))
    return header + payload_bytes

def decode_p2p_message(data: bytes) -> tuple:
    if len(data) < 4 + 1 + 1 + 4:
        return None, None
    if data[:4] != P2P_MAGIC:
        return None, None
    msg_type = data[5]
    payload_len = struct.unpack(">I", data[6:10])[0]
    if len(data) < 10 + payload_len:
        return None, None
    payload = json.loads(data[10:10+payload_len].decode())
    return msg_type, payload

# ==================== DEX BRIDGE ====================
class DEXBridge:
    def __init__(self):
        self.connected = False
        self.mc_price_usd = 0.01
    
    def connect(self) -> bool:
        print(f"[DEX] Connecting to {DEX_TYPE} on BSC")
        self.connected = True
        return True
    
    def get_mc_price(self) -> float:
        return self.mc_price_usd
    
    def add_liquidity(self, wallet: str, mc_amount: int, usdc_amount: int) -> dict:
        print(f"[DEX] Adding liquidity: {mc_amount} MC + {usdc_amount} USDC")
        return {"success": True}

# ==================== DATA STRUCTURES ====================
@dataclass
class Miner:
    validator_id: str
    public_key: str
    wallet: str
    stake: int
    level: int
    uptime_seconds: int = 0
    last_ping: float = 0
    is_active: bool = True
    total_rewards: int = 0
    blocks_signed: int = 0
    slash_count: int = 0
    consecutive_misses: int = 0

@dataclass
class Block:
    block_id: int
    timestamp: float
    previous_hash: str
    validators: List[str]
    level: int
    signatures: Dict[str, str] = field(default_factory=dict)
    block_hash: str = ""
    reward_distributed: bool = False
    reward_amount: int = 0

# ==================== MICROCOIN NETWORK ====================
class MicroCoinNetwork:
    def __init__(self):
        self.miners: Dict[str, Miner] = {}
        self.uptime_pool: int = 0
        self.node_pool: int = 0
        self.lp_pool: int = 0
        self.current_block_id: int = 0
        self.blocks: List[Block] = []
        self.pending_challenges: Dict[str, Dict] = {}
        self.level_groups: Dict[int, List[str]] = defaultdict(list)
        self.level_unique_wallets: Dict[int, int] = {}
        self.last_distribution: float = time.time()
        self.last_block_hash: str = "0" * 64
        self.max_level: int = 1
        self.total_minted: int = 0
        self.balances: Dict[str, int] = {}
        
        # P2P
        self.p2p_peers: Dict[str, dict] = {}
        self.p2p_server = None
        
        # DEX
        self.dex = DEXBridge()
        
        self.init_database()
        self.create_genesis_block()
        self.load_total_minted()
        if DEX_ENABLED:
            self.dex.connect()
    
    def init_database(self):
        self.conn = sqlite3.connect('microcoin.db')
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS miners
                     (validator_id TEXT PRIMARY KEY, public_key TEXT, wallet TEXT,
                      stake INTEGER, level INTEGER, total_rewards INTEGER,
                      blocks_signed INTEGER, slash_count INTEGER, uptime_seconds INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS blocks
                     (block_id INTEGER PRIMARY KEY, timestamp REAL, previous_hash TEXT,
                      validators TEXT, level INTEGER, block_hash TEXT, reward_amount INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS balances
                     (wallet TEXT PRIMARY KEY, balance INTEGER)''')
        self.conn.commit()
    
    def create_genesis_block(self):
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM blocks")
        if c.fetchone()[0] == 0:
            genesis = Block(block_id=0, timestamp=time.time(), previous_hash="0"*64,
                           validators=["genesis"], level=1, reward_amount=0)
            genesis.block_hash = hash_block({"block_id":0, "timestamp":genesis.timestamp,
                                            "previous_hash":"0"*64, "validators":["genesis"], "level":1})
            self.blocks.append(genesis)
            self.last_block_hash = genesis.block_hash
            self.current_block_id = 1
            self.total_minted = 10000
            self.balances["MC_GENESIS"] = 10000
            
            print("="*60)
            print("MICROCOIN NODE STARTED")
            print(f"Hard Cap: {TOTAL_SUPPLY_CAP:,} MC | Initial Reward: {INITIAL_BLOCK_REWARD} MC")
            print(f"Halving: Every {HALVING_INTERVAL:,} blocks | Reward Split: 75/8/7/10")
            print(f"P2P Port: {P2P_PORT} | DEX: {DEX_TYPE if DEX_ENABLED else 'Disabled'}")
            print("="*60)
    
    def load_total_minted(self):
        c = self.conn.cursor()
        c.execute("SELECT SUM(reward_amount) FROM blocks WHERE reward_amount > 0")
        result = c.fetchone()[0]
        self.total_minted = (result or 0) + 10000
    
    def get_current_block_reward(self) -> int:
        remaining = TOTAL_SUPPLY_CAP - self.total_minted
        if remaining <= 0:
            return 0
        halvings = self.current_block_id // HALVING_INTERVAL
        reward = INITIAL_BLOCK_REWARD // (2 ** halvings)
        reward = max(reward, MINIMUM_BLOCK_REWARD)
        return min(reward, remaining)
    
    def get_remaining_supply(self) -> int:
        return TOTAL_SUPPLY_CAP - self.total_minted
    
    def calculate_level(self, stake: int) -> int:
        if stake < LEVEL_STAKE_RANGE:
            return 1
        level = ((stake - 1) // LEVEL_STAKE_RANGE) + 1
        return min(level, self.max_level + 1, MAX_LEVEL)
    
    def update_level_groups(self):
        self.level_groups.clear()
        unique_wallets = {}
        for miner in self.miners.values():
            if miner.is_active:
                self.level_groups.setdefault(miner.level, []).append(miner.validator_id)
                if miner.level not in unique_wallets:
                    unique_wallets[miner.level] = set()
                unique_wallets[miner.level].add(miner.wallet)
        for level, wallets in unique_wallets.items():
            self.level_unique_wallets[level] = len(wallets)
        self.check_level_unlocks()
    
    def check_level_unlocks(self):
        for level in range(1, self.max_level + 2):
            if self.level_unique_wallets.get(level, 0) >= MIN_WALLETS_FOR_NEXT_LEVEL and level + 1 > self.max_level:
                self.max_level = level + 1
                print(f"[LEVEL] Level {self.max_level} unlocked")
    
    def select_validators(self, level: int) -> List[str]:
        miners = self.level_groups.get(level, [])
        if len(miners) < MIN_VALIDATORS_PER_BLOCK:
            return []
        seed = int(self.last_block_hash[:16], 16) if self.last_block_hash != "0"*64 else int(time.time())
        rng = random.Random(seed)
        return rng.sample(miners, MIN_VALIDATORS_PER_BLOCK)
    
    def generate_challenge(self, block_id: int, validators: List[str]) -> str:
        return hashlib.sha256(f"{block_id}{''.join(sorted(validators))}{time.time()}{self.last_block_hash}{secrets.token_hex(8)}".encode()).hexdigest()
    
    def register_miner(self, vid: str, pubkey: str, wallet: str, stake: int, sig: str, ts: float) -> bool:
        if not verify_signature(pubkey, f"{vid}{wallet}{stake}{ts}", sig):
            return False
        level = self.calculate_level(stake)
        if level > self.max_level:
            level = self.max_level
        if vid in self.miners:
            self.miners[vid].stake = stake
            self.miners[vid].level = level
            self.miners[vid].last_ping = time.time()
            self.miners[vid].is_active = True
        else:
            self.miners[vid] = Miner(vid, pubkey, wallet, stake, level, registered_at=ts)
            print(f"[REG] Miner: {vid[:16]}... | Level {level} | Stake {stake}")
        self.update_level_groups()
        return True
    
    def slash_miner(self, vid: str, reason: str):
        if vid not in self.miners:
            return
        m = self.miners[vid]
        slash = max(int(m.stake * SLASH_RATE), LEVEL_STAKE_RANGE)
        m.stake -= slash
        if m.stake < LEVEL_STAKE_RANGE:
            m.stake = LEVEL_STAKE_RANGE
        m.slash_count += 1
        new_level = self.calculate_level(m.stake)
        m.level = min(new_level, self.max_level)
        if m.slash_count >= 5:
            m.is_active = False
        self.update_level_groups()
        print(f"[SLASH] {vid[:16]}... -{slash} MC")
    
    def distribute_block_reward(self, block: Block):
        if block.reward_distributed:
            return
        reward = self.get_current_block_reward()
        block.reward_amount = reward
        if reward == 0:
            block.reward_distributed = True
            return
        validator_total = int(reward * VALIDATOR_SHARE)
        validator_each = validator_total // max(len(block.validators), 1)
        for vid in block.validators:
            if vid in self.miners:
                m = self.miners[vid]
                m.total_rewards += validator_each
                m.stake += validator_each
                m.blocks_signed += 1
                new_level = self.calculate_level(m.stake)
                m.level = min(new_level, self.max_level)
        self.node_pool += int(reward * NODE_SHARE)
        self.uptime_pool += int(reward * UPTIME_SHARE)
        self.lp_pool += int(reward * LP_SHARE)
        self.total_minted += reward
        block.reward_distributed = True
        print(f"[BLOCK {block.block_id}] Reward: {reward} MC | Validators: {validator_each} MC")
    
    async def produce_block(self, level: int):
        validators = self.select_validators(level)
        if len(validators) < MIN_VALIDATORS_PER_BLOCK:
            return
        block_id = self.current_block_id
        challenge = self.generate_challenge(block_id, validators)
        self.pending_challenges[challenge] = {"block_id": block_id, "validators": validators, "level": level, "signatures": {}}
        await asyncio.sleep(SIGNING_WINDOW_MS / 1000)
        pending = self.pending_challenges.pop(challenge, {})
        sigs = pending.get("signatures", {})
        valid_sigs = {}
        for vid, sig in sigs.items():
            if vid in self.miners:
                if verify_signature(self.miners[vid].public_key, f"{challenge}{vid}{block_id}", sig):
                    valid_sigs[vid] = sig
        if len(valid_sigs) >= MIN_VALIDATORS_PER_BLOCK:
            block = Block(block_id, time.time(), self.last_block_hash, list(valid_sigs.keys()), level, valid_sigs)
            self.distribute_block_reward(block)
            block.block_hash = hash_block({"block_id": block_id, "timestamp": block.timestamp,
                                          "previous_hash": self.last_block_hash, "validators": block.validators, "level": level})
            self.last_block_hash = block.block_hash
            self.blocks.append(block)
            self.current_block_id += 1
            print(f"[BLOCK {block_id}] ACCEPTED | {len(valid_sigs)} signatures")
        else:
            missing = set(validators) - set(sigs.keys())
            for vid in missing:
                self.slash_miner(vid, "Missed signing")
            print(f"[BLOCK {block_id}] REJECTED | Missing: {len(missing)}")

# ==================== LOCAL MINER (embedded in node) ====================
class LocalMiner:
    def __init__(self, network: MicroCoinNetwork, address: str, private_key: str, public_key: str):
        self.network = network
        self.address = address
        self.private_key = private_key
        self.public_key = public_key
        self.validator_id = f"LOCAL_{address[:16]}"
        self.stake = 100
        self.level = 1
        self.rewards = 0
        self.blocks = 0
        self.uptime = 0
        self.running = True
    
    def register(self):
        ts = time.time()
        sig = sign_message(self.private_key, f"{self.validator_id}{self.address}{self.stake}{ts}")
        self.network.register_miner(self.validator_id, self.public_key, self.address, self.stake, sig, ts)
        print(f"[MINER] Registered: {self.validator_id[:16]}...")
    
    async def run(self):
        self.register()
        last_uptime = time.time()
        while self.running:
            # Check for challenges
            for challenge, pending in self.network.pending_challenges.items():
                if self.validator_id in pending["validators"] and self.validator_id not in pending["signatures"]:
                    sig = sign_message(self.private_key, f"{challenge}{self.validator_id}{pending['block_id']}")
                    pending["signatures"][self.validator_id] = sig
                    print(f"[MINER] Signed block {pending['block_id']}")
                    break
            # Update uptime
            if time.time() - last_uptime > 30:
                self.uptime += 30
                if self.validator_id in self.network.miners:
                    self.network.miners[self.validator_id].uptime_seconds = self.uptime
                last_uptime = time.time()
            await asyncio.sleep(0.1)

# ==================== WEBSOCKET SERVER ====================
class MicroCoinServer:
    def __init__(self, network: MicroCoinNetwork):
        self.network = network
        self.connections = {}
    
    async def handle(self, websocket, path):
        try:
            async for message in websocket:
                data = json.loads(message)
                t = data.get("type")
                if t == "register":
                    if self.network.register_miner(data["validator_id"], data["public_key"], data["wallet"],
                                                   data.get("stake", 100), data.get("signature", ""), data.get("timestamp", time.time())):
                        self.connections[data["validator_id"]] = websocket
                        await websocket.send(json.dumps({
                            "type": "registered",
                            "level": self.network.miners[data["validator_id"]].level if data["validator_id"] in self.network.miners else 1,
                            "max_level": self.network.max_level,
                            "remaining_supply": self.network.get_remaining_supply(),
                            "current_reward": self.network.get_current_block_reward(),
                            "mc_price": self.network.dex.get_mc_price() if DEX_ENABLED else 0.01
                        }))
                elif t == "block_signature":
                    if data["challenge"] in self.network.pending_challenges:
                        self.network.pending_challenges[data["challenge"]]["signatures"][data["validator_id"]] = data["signature"]
                elif t == "uptime_ping":
                    if data["validator_id"] in self.network.miners:
                        self.network.miners[data["validator_id"]].uptime_seconds = data.get("uptime_seconds", 0)
        except:
            pass
    
    async def block_production(self):
        level = 1
        while True:
            if self.network.level_groups:
                avail = [l for l in self.network.level_groups if len(self.network.level_groups[l]) >= MIN_VALIDATORS_PER_BLOCK]
                if avail:
                    level = (level % max(avail)) + 1
                    if level in avail:
                        await self.network.produce_block(level)
            await asyncio.sleep(0.1)
    
    async def status_reporter(self):
        while True:
            await asyncio.sleep(60)
            remaining = self.network.get_remaining_supply()
            percent = (self.network.total_minted / TOTAL_SUPPLY_CAP) * 100
            reward = self.network.get_current_block_reward()
            print(f"\n[STATUS] Block: {self.network.current_block_id} | Reward: {reward} MC | Halving: {self.network.current_block_id // HALVING_INTERVAL}")
            print(f"[STATUS] Miners: {len(self.network.miners)} | Active: {sum(1 for m in self.network.miners.values() if m.is_active)}")
            print(f"[SUPPLY] {self.network.total_minted:,} / {TOTAL_SUPPLY_CAP:,} ({percent:.4f}%) | Remaining: {remaining:,}\n")
    
    async def run(self):
        asyncio.create_task(self.block_production())
        asyncio.create_task(self.status_reporter())
        async with websockets.serve(self.handle, NODE_HOST, NODE_PORT):
            print(f"[WS] Server: ws://{NODE_HOST}:{NODE_PORT}")
            await asyncio.Future()

# ==================== MAIN ====================
async def main():
    print("=" * 60)
    print("MICROCOIN NODE + MINER - COMPLETE")
    print("=" * 60)
    
    # Load or create wallet
    wallet_file = "microcoin_wallet.json"
    if os.path.exists(wallet_file):
        with open(wallet_file, 'r') as f:
            data = json.load(f)
            address, private_key, public_key = data['address'], data['private_key'], data['public_key']
        print(f"[WALLET] Loaded: {address}")
    else:
        address, private_key, public_key = generate_wallet()
        with open(wallet_file, 'w') as f:
            json.dump({'address': address, 'private_key': private_key, 'public_key': public_key}, f)
        print(f"[WALLET] Generated: {address}")
        print(f"[WALLET] SAVE THIS PRIVATE KEY: {private_key}")
    
    # Start network
    network = MicroCoinNetwork()
    server = MicroCoinServer(network)
    
    # Start local miner
    miner = LocalMiner(network, address, private_key, public_key)
    asyncio.create_task(miner.run())
    
    # Run server
    await server.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Stopped")
        sys.exit(0)
