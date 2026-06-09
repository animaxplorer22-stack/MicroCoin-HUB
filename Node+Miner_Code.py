#!/usr/bin/env python3
# MICROCOIN NODE + MINER - SINGLE FILE
# RUN THIS ONE FILE TO BE BOTH A NODE AND A MINER
# python3 node_miner.py

import asyncio
import json
import time
import hashlib
import sqlite3
import random
import os
import sys
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
import websockets
from collections import defaultdict
import threading

# ==================== CRYPTOGRAPHY ====================
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, decode_dss_signature
from cryptography.exceptions import InvalidSignature

# ==================== CONFIGURATION ====================
NODE_HOST = "0.0.0.0"
NODE_PORT = 8080

# TOKENOMICS
TOTAL_SUPPLY_CAP = 50_000_000_000
INITIAL_BLOCK_REWARD = 6000
HALVING_INTERVAL = 4_000_000
MINIMUM_BLOCK_REWARD = 1

# Reward Distribution Percentages
VALIDATOR_SHARE = 0.75
NODE_SHARE = 0.08
UPTIME_SHARE = 0.07
LP_SHARE = 0.10

# Consensus Parameters
LEVEL_STAKE_RANGE = 100
SIGNING_WINDOW_MS = 2500
SLASH_RATE = 0.10
MIN_VALIDATORS_PER_BLOCK = 10
MIN_WALLETS_FOR_NEXT_LEVEL = 10
MAX_LEVEL = 100
UPTIME_PING_INTERVAL = 30
DISTRIBUTION_INTERVAL_SEC = 300

# ==================== REAL CRYPTO FUNCTIONS ====================
def verify_signature(public_key_pem: str, message: str, signature_hex: str) -> bool:
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
    except:
        return False

def sign_message(private_key_hex: str, message: str) -> str:
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    signature = private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex()

def generate_wallet() -> tuple:
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
    return hashlib.sha256(json.dumps(block_data, sort_keys=True).encode()).hexdigest()

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
    registered_at: float = 0

@dataclass
class NetworkNode:
    node_id: str
    wallet: str
    is_active: bool = True
    total_rewards: int = 0
    registered_at: float = 0

@dataclass
class Block:
    block_id: int
    timestamp: float
    previous_hash: str
    validators: List[str]
    level: int
    signatures: Dict[str, str] = field(default_factory=dict)
    block_hash: str = ""
    accepted: bool = False
    reward_distributed: bool = False
    reward_amount: int = 0

@dataclass
class LiquidityProvider:
    wallet: str
    amount: int
    share: float
    last_claim: float = 0
    total_earned: int = 0

# ==================== MICROCOIN NETWORK (NODE COMPONENT) ====================
class MicroCoinNetwork:
    def __init__(self):
        self.miners: Dict[str, Miner] = {}
        self.nodes: Dict[str, NetworkNode] = {}
        self.liquidity_providers: Dict[str, LiquidityProvider] = {}
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
        self.start_time = time.time()
        
        self.init_database()
        self.create_genesis_block()
        self.load_total_minted()
    
    def init_database(self):
        self.conn = sqlite3.connect('microcoin.db')
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS miners
                     (validator_id TEXT PRIMARY KEY,
                      public_key TEXT,
                      wallet TEXT,
                      stake INTEGER,
                      level INTEGER,
                      total_rewards INTEGER,
                      blocks_signed INTEGER,
                      slash_count INTEGER,
                      uptime_seconds INTEGER,
                      registered_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nodes
                     (node_id TEXT PRIMARY KEY,
                      wallet TEXT,
                      total_rewards INTEGER,
                      registered_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS blocks
                     (block_id INTEGER PRIMARY KEY,
                      timestamp REAL,
                      previous_hash TEXT,
                      validators TEXT,
                      level INTEGER,
                      block_hash TEXT,
                      reward_amount INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS liquidity_providers
                     (wallet TEXT PRIMARY KEY,
                      amount INTEGER,
                      share REAL,
                      total_earned INTEGER,
                      last_claim REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS supply_metrics
                     (key TEXT PRIMARY KEY,
                      value INTEGER,
                      updated_at REAL)''')
        self.conn.commit()
    
    def create_genesis_block(self):
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM blocks")
        if c.fetchone()[0] == 0:
            genesis = Block(block_id=0, timestamp=time.time(), previous_hash="0"*64, validators=["genesis"], level=1)
            genesis.block_hash = hash_block({"block_id":0,"timestamp":genesis.timestamp,"previous_hash":"0"*64,"validators":["genesis"],"level":1})
            genesis.reward_amount = 0
            self.blocks.append(genesis)
            self.last_block_hash = genesis.block_hash
            self.current_block_id = 1
            self.total_minted = 10000
            print("="*60)
            print("MICROCOIN NODE STARTED")
            print(f"Hard Cap: {TOTAL_SUPPLY_CAP:,} MC | Initial Reward: {INITIAL_BLOCK_REWARD} MC")
            print(f"Halving: Every {HALVING_INTERVAL:,} blocks | Reward Split: 75/8/7/10")
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
    
    def can_miner_enter_level(self, wallet: str, target: int) -> bool:
        if target <= 1:
            return True
        return self.level_unique_wallets.get(target - 1, 0) >= MIN_WALLETS_FOR_NEXT_LEVEL
    
    def select_validators(self, level: int) -> List[str]:
        miners = self.level_groups.get(level, [])
        if len(miners) < MIN_VALIDATORS_PER_BLOCK:
            return []
        seed = int(self.last_block_hash[:16], 16) if self.last_block_hash != "0"*64 else int(time.time())
        rng = random.Random(seed)
        return rng.sample(miners, MIN_VALIDATORS_PER_BLOCK)
    
    def generate_challenge(self, block_id: int, validators: List[str]) -> str:
        return hashlib.sha256(f"{block_id}{''.join(sorted(validators))}{time.time()}{self.last_block_hash}".encode()).hexdigest()
    
    def register_miner(self, vid: str, pubkey: str, wallet: str, stake: int, sig: str, ts: float) -> bool:
        if not verify_signature(pubkey, f"{vid}{wallet}{stake}{ts}", sig):
            return False
        level = self.calculate_level(stake)
        if not self.can_miner_enter_level(wallet, level):
            level = min(level, self.max_level)
        if vid in self.miners:
            self.miners[vid].stake = stake
            self.miners[vid].level = level
            self.miners[vid].last_ping = time.time()
            self.miners[vid].is_active = True
        else:
            self.miners[vid] = Miner(vid, pubkey, wallet, stake, level, registered_at=ts)
            print(f"[NODE] Miner registered: {vid[:16]}... | Level {level}")
        self.update_level_groups()
        return True
    
    def register_node(self, nid: str, wallet: str):
        if nid not in self.nodes:
            self.nodes[nid] = NetworkNode(nid, wallet, registered_at=time.time())
            print(f"[NODE] Node registered: {nid[:16]}...")
    
    def slash_miner(self, vid: str, reason: str):
        if vid not in self.miners:
            return
        m = self.miners[vid]
        slash = max(int(m.stake * SLASH_RATE), LEVEL_STAKE_RANGE)
        m.stake -= slash
        if m.stake < LEVEL_STAKE_RANGE:
            m.stake = LEVEL_STAKE_RANGE
        m.slash_count += 1
        m.consecutive_misses += 1
        new_level = self.calculate_level(m.stake)
        m.level = min(new_level, self.max_level) if not self.can_miner_enter_level(m.wallet, new_level) else new_level
        if m.slash_count >= 5:
            m.is_active = False
        self.update_level_groups()
        print(f"[SLASH] {vid[:16]}... -{slash} MC | Stake: {m.stake}")
    
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
                m.consecutive_misses = 0
                new_level = self.calculate_level(m.stake)
                m.level = min(new_level, self.max_level) if not self.can_miner_enter_level(m.wallet, new_level) else new_level
        self.node_pool += int(reward * NODE_SHARE)
        self.uptime_pool += int(reward * UPTIME_SHARE)
        self.lp_pool += int(reward * LP_SHARE)
        self.total_minted += reward
        block.reward_distributed = True
        print(f"[BLOCK {block.block_id}] Reward: {reward} MC | Validators: {validator_each} MC each")
    
    def distribute_periodic(self):
        if self.nodes and self.node_pool > 0:
            active = [n for n in self.nodes.values() if n.is_active]
            if active:
                share = self.node_pool // len(active)
                for n in active:
                    n.total_rewards += share
        if self.miners and self.uptime_pool > 0:
            total_uptime = sum(m.uptime_seconds for m in self.miners.values() if m.is_active)
            if total_uptime > 0:
                for m in self.miners.values():
                    if m.is_active and m.uptime_seconds > 0:
                        share = int(self.uptime_pool * (m.uptime_seconds / total_uptime))
                        m.total_rewards += share
                        m.stake += share
        self.node_pool = 0
        self.uptime_pool = 0
        self.lp_pool = 0
        self.last_distribution = time.time()
    
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
            block.block_hash = hash_block({"block_id":block_id,"timestamp":block.timestamp,"previous_hash":self.last_block_hash,"validators":block.validators,"level":level})
            self.last_block_hash = block.block_hash
            self.blocks.append(block)
            self.current_block_id += 1
            print(f"[BLOCK {block_id}] ACCEPTED | {len(valid_sigs)} signatures")
        else:
            missing = set(validators) - set(sigs.keys())
            for vid in missing:
                self.slash_miner(vid, "Missed signing")
            print(f"[BLOCK {block_id}] REJECTED | Missing: {len(missing)}")

# ==================== MINER COMPONENT ====================
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
        self.is_validator = False
        self.current_challenge = ""
        self.current_block_id = 0
        self.running = True
    
    def register(self):
        ts = time.time()
        msg = f"{self.validator_id}{self.address}{self.stake}{ts}"
        sig = sign_message(self.private_key, msg)
        self.network.register_miner(self.validator_id, self.public_key, self.address, self.stake, sig, ts)
    
    def add_uptime(self):
        self.uptime += 30
        if self.validator_id in self.network.miners:
            self.network.miners[self.validator_id].uptime_seconds = self.uptime
    
    async def run(self):
        self.register()
        print(f"[MINER] Started | ID: {self.validator_id[:16]}... | Stake: {self.stake}")
        
        last_uptime = time.time()
        last_status = time.time()
        
        while self.running:
            # Check for challenges
            for challenge, pending in self.network.pending_challenges.items():
                if self.validator_id in pending["validators"] and self.validator_id not in pending["signatures"]:
                    self.current_challenge = challenge
                    self.current_block_id = pending["block_id"]
                    msg = f"{self.current_challenge}{self.validator_id}{self.current_block_id}"
                    sig = sign_message(self.private_key, msg)
                    pending["signatures"][self.validator_id] = sig
                    print(f"[MINER] Signed block {self.current_block_id}")
                    break
            
            # Update uptime
            if time.time() - last_uptime > 30:
                self.add_uptime()
                last_uptime = time.time()
            
            # Print status
            if time.time() - last_status > 60:
                m = self.network.miners.get(self.validator_id)
                if m:
                    self.stake = m.stake
                    self.level = m.level
                    self.rewards = m.total_rewards
                    self.blocks = m.blocks_signed
                print(f"[MINER STATUS] Level: {self.level} | Stake: {self.stake} | Rewards: {self.rewards} | Blocks: {self.blocks}")
                last_status = time.time()
            
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
                    if self.network.register_miner(data["validator_id"], data["public_key"], data["wallet"], data.get("stake",100), data.get("signature",""), data.get("timestamp",time.time())):
                        self.connections[data["validator_id"]] = websocket
                        await websocket.send(json.dumps({"type":"registered","level":self.network.miners[data["validator_id"]].level if data["validator_id"] in self.network.miners else 1}))
                elif t == "node_register":
                    self.network.register_node(data["node_id"], data["wallet"])
                    self.connections[f"node_{data['node_id']}"] = websocket
                elif t == "block_signature":
                    if data["challenge"] in self.network.pending_challenges:
                        self.network.pending_challenges[data["challenge"]]["signatures"][data["validator_id"]] = data["signature"]
                elif t == "uptime_ping":
                    if data["validator_id"] in self.network.miners:
                        self.network.miners[data["validator_id"]].uptime_seconds = data.get("uptime_seconds",0)
        except:
            pass
    
    async def periodic(self):
        last_distro = time.time()
        while True:
            if time.time() - last_distro > DISTRIBUTION_INTERVAL_SEC:
                self.network.distribute_periodic()
                last_distro = time.time()
            await asyncio.sleep(1)
    
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
    
    async def run(self):
        async with websockets.serve(self.handle, NODE_HOST, NODE_PORT):
            print(f"[NODE] WebSocket: ws://{NODE_HOST}:{NODE_PORT}")
            await asyncio.gather(self.periodic(), self.block_production())

# ==================== MAIN ====================
async def main():
    print("="*60)
    print("MICROCOIN NODE + MINER - SINGLE FILE")
    print="="*60)
    
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
    
    # Create network (node component)
    network = MicroCoinNetwork()
    
    # Start WebSocket server (for other miners to connect)
    server = MicroCoinServer(network)
    asyncio.create_task(server.run())
    
    # Wait a moment for server to start
    await asyncio.sleep(1)
    
    # Start local miner (this machine also mines)
    miner = LocalMiner(network, address, private_key, public_key)
    await miner.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Stopped")
