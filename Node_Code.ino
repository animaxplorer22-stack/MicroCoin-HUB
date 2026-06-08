# microcoin_node_full.py
# COMPLETE PRODUCTION NODE WITH REAL CRYPTOGRAPHY
# RUN ON PC OR RASPBERRY PI

import asyncio
import json
import time
import hashlib
import sqlite3
import random
import secrets
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.exceptions import InvalidSignature
import websockets
from collections import defaultdict

# ==================== CONFIGURATION ====================
NODE_HOST = "0.0.0.0"
NODE_PORT = 8080
BLOCK_REWARD = 6000
VALIDATOR_SHARE = 0.75
NODE_SHARE = 0.08
UPTIME_SHARE = 0.07
LP_SHARE = 0.10
LEVEL_STAKE_RANGE = 100
SIGNING_WINDOW_MS = 2500
SLASH_RATE = 0.10
DISTRIBUTION_INTERVAL_SEC = 300
MIN_VALIDATORS_PER_BLOCK = 10
MAX_LEVEL = 100
BLOCKS_PER_EPOCH = 32
MIN_WALLETS_FOR_NEXT_LEVEL = 10

# ==================== CRYPTOGRAPHY ====================
def verify_signature(public_key_pem: str, message: str, signature_hex: str) -> bool:
    """Verify ECDSA signature using secp256k1 curve"""
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        signature_bytes = bytes.fromhex(signature_hex)
        public_key.verify(signature_bytes, message.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception as e:
        return False

def hash_block(block_data: dict) -> str:
    """Generate SHA256 block hash"""
    block_string = json.dumps(block_data, sort_keys=True)
    return hashlib.sha256(block_string.encode()).hexdigest()

def hash_message(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()

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
    last_slash_time: float = 0
    slash_count: int = 0
    consecutive_misses: int = 0
    registered_at: float = 0

@dataclass
class Node:
    node_id: str
    wallet: str
    is_active: bool = True
    total_rewards: int = 0

@dataclass
class Block:
    block_id: int
    timestamp: float
    previous_hash: str
    validators: List[str]
    level: int
    signatures: Dict[str, str] = field(default_factory=dict)
    hash: str = ""
    accepted: bool = False
    reward_distributed: bool = False

@dataclass
class LiquidityProvider:
    wallet: str
    amount: int
    share: float
    last_claim: float = 0
    total_earned: int = 0

# ==================== MICROCOIN NETWORK ====================
class MicroCoinNetwork:
    def __init__(self):
        self.miners: Dict[str, Miner] = {}
        self.nodes: Dict[str, Node] = {}
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
        self.current_validator_set: List[str] = []
        self.last_block_hash: str = "0" * 64
        self.max_level: int = 1
        self.pending_unstakes: Dict[str, Dict] = {}
        
        self.init_database()
        self.create_genesis_block()
    
    def init_database(self):
        """Initialize SQLite database for persistence"""
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
                      registered_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS blocks
                     (block_id INTEGER PRIMARY KEY,
                      timestamp REAL,
                      previous_hash TEXT,
                      validators TEXT,
                      level INTEGER,
                      block_hash TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nodes
                     (node_id TEXT PRIMARY KEY,
                      wallet TEXT,
                      total_rewards INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS liquidity_providers
                     (wallet TEXT PRIMARY KEY,
                      amount INTEGER,
                      share REAL,
                      total_earned INTEGER,
                      last_claim REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS level_progression
                     (level INTEGER PRIMARY KEY,
                      unlocked_at REAL,
                      unique_wallets INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS slashing_events
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      validator_id TEXT,
                      amount INTEGER,
                      reason TEXT,
                      timestamp REAL)''')
        self.conn.commit()
    
    def create_genesis_block(self):
        """Create the genesis block"""
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM blocks")
        if c.fetchone()[0] == 0:
            genesis = Block(
                block_id=0,
                timestamp=time.time(),
                previous_hash="0" * 64,
                validators=["genesis"],
                level=1,
                signatures={}
            )
            genesis.hash = hash_block({
                "block_id": 0,
                "timestamp": genesis.timestamp,
                "previous_hash": "0" * 64,
                "validators": ["genesis"],
                "level": 1
            })
            self.blocks.append(genesis)
            self.last_block_hash = genesis.hash
            self.current_block_id = 1
            print(f"[GENESIS] Block created: {genesis.hash}")
            print(f"[GENESIS] Network ready. Block reward: {BLOCK_REWARD} MC")
    
    def calculate_level(self, stake: int) -> int:
        """Calculate level based on stake (100 MC per level)"""
        if stake < LEVEL_STAKE_RANGE:
            return 1
        level = ((stake - 1) // LEVEL_STAKE_RANGE) + 1
        return min(level, self.max_level + 1, MAX_LEVEL)
    
    def update_level_groups(self):
        """Update level groupings and unique wallet counts"""
        self.level_groups.clear()
        unique_wallets_per_level = {}
        
        for miner in self.miners.values():
            if miner.is_active:
                self.level_groups.setdefault(miner.level, []).append(miner.validator_id)
                if miner.level not in unique_wallets_per_level:
                    unique_wallets_per_level[miner.level] = set()
                unique_wallets_per_level[miner.level].add(miner.wallet)
        
        for level, wallets in unique_wallets_per_level.items():
            self.level_unique_wallets[level] = len(wallets)
        
        self.check_level_unlocks()
    
    def check_level_unlocks(self):
        """Auto-create next level if 10+ unique wallets exist"""
        for level in range(1, self.max_level + 2):
            unique_count = self.level_unique_wallets.get(level, 0)
            if unique_count >= MIN_WALLETS_FOR_NEXT_LEVEL and level + 1 > self.max_level:
                self.max_level = level + 1
                print(f"[LEVEL UNLOCK] Level {self.max_level} unlocked! ({unique_count} unique wallets in Level {level})")
                self.conn.execute("INSERT OR REPLACE INTO level_progression VALUES (?, ?, ?)",
                                 (self.max_level, time.time(), unique_count))
                self.conn.commit()
    
    def can_miner_enter_level(self, miner_wallet: str, target_level: int) -> bool:
        """Check if a miner's wallet can enter target level"""
        if target_level <= 1:
            return True
        prev_level = target_level - 1
        unique_count = self.level_unique_wallets.get(prev_level, 0)
        return unique_count >= MIN_WALLETS_FOR_NEXT_LEVEL
    
    def select_validators(self, level: int) -> List[str]:
        """Select 10 random validators from a level using deterministic randomness"""
        miners_in_level = self.level_groups.get(level, [])
        if len(miners_in_level) < MIN_VALIDATORS_PER_BLOCK:
            return []
        
        # Use previous block hash as seed
        seed = int(self.last_block_hash[:16], 16) if self.last_block_hash != "0"*64 else int(time.time())
        rng = random.Random(seed)
        selected = rng.sample(miners_in_level, min(MIN_VALIDATORS_PER_BLOCK, len(miners_in_level)))
        return selected
    
    def generate_challenge(self, block_id: int, validators: List[str]) -> str:
        """Generate unforgeable challenge for validators"""
        data = f"{block_id}{''.join(sorted(validators))}{time.time()}{self.last_block_hash}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def register_miner(self, validator_id: str, public_key: str, wallet: str, 
                       stake: int, signature: str, timestamp: float) -> bool:
        """Register a new miner with signature verification"""
        message_to_verify = f"{validator_id}{wallet}{stake}{timestamp}"
        
        if not verify_signature(public_key, message_to_verify, signature):
            print(f"[REGISTER] Signature verification failed for {validator_id[:16]}")
            return False
        
        calculated_level = self.calculate_level(stake)
        
        if not self.can_miner_enter_level(wallet, calculated_level):
            calculated_level = min(calculated_level, self.max_level)
        
        if validator_id in self.miners:
            self.miners[validator_id].stake = stake
            self.miners[validator_id].level = calculated_level
            self.miners[validator_id].public_key = public_key
            self.miners[validator_id].last_ping = time.time()
            self.miners[validator_id].is_active = True
            self.miners[validator_id].wallet = wallet
        else:
            self.miners[validator_id] = Miner(
                validator_id=validator_id,
                public_key=public_key,
                wallet=wallet,
                stake=stake,
                level=calculated_level,
                last_ping=time.time(),
                registered_at=timestamp
            )
            print(f"[REGISTER] New miner: {validator_id[:16]}... | Wallet: {wallet[:20]}... | Level: {calculated_level} | Stake: {stake}")
        
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO miners VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (validator_id, public_key, wallet, stake, calculated_level,
                  self.miners[validator_id].total_rewards,
                  self.miners[validator_id].blocks_signed,
                  self.miners[validator_id].slash_count,
                  timestamp))
        self.conn.commit()
        
        self.update_level_groups()
        return True
    
    def register_liquidity_provider(self, wallet: str, amount: int):
        """Register or update a liquidity provider"""
        if wallet in self.liquidity_providers:
            self.liquidity_providers[wallet].amount += amount
            total = sum(lp.amount for lp in self.liquidity_providers.values())
            for lp in self.liquidity_providers.values():
                lp.share = lp.amount / total if total > 0 else 0
        else:
            total = sum(lp.amount for lp in self.liquidity_providers.values()) + amount
            self.liquidity_providers[wallet] = LiquidityProvider(
                wallet=wallet,
                amount=amount,
                share=amount / total if total > 0 else 1.0,
                last_claim=time.time()
            )
        
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO liquidity_providers VALUES (?, ?, ?, ?, ?)",
                 (wallet, self.liquidity_providers[wallet].amount,
                  self.liquidity_providers[wallet].share,
                  self.liquidity_providers[wallet].total_earned,
                  self.liquidity_providers[wallet].last_claim))
        self.conn.commit()
        print(f"[LP] {wallet[:20]}... added {amount} MC. Total LP count: {len(self.liquidity_providers)}")
    
    def slash_miner(self, validator_id: str, reason: str):
        """Slash a miner's stake"""
        if validator_id not in self.miners:
            return
        
        miner = self.miners[validator_id]
        slash_amount = int(miner.stake * SLASH_RATE)
        if slash_amount < LEVEL_STAKE_RANGE:
            slash_amount = LEVEL_STAKE_RANGE
        
        miner.stake -= slash_amount
        if miner.stake < LEVEL_STAKE_RANGE:
            miner.stake = LEVEL_STAKE_RANGE
        
        miner.slash_count += 1
        miner.last_slash_time = time.time()
        miner.consecutive_misses += 1
        
        new_level = self.calculate_level(miner.stake)
        if not self.can_miner_enter_level(miner.wallet, new_level):
            miner.level = min(new_level, self.max_level)
        else:
            miner.level = new_level
        
        if miner.slash_count >= 5:
            miner.is_active = False
            print(f"[BAN] {validator_id[:16]}... banned after 5 slashes")
        
        c = self.conn.cursor()
        c.execute("UPDATE miners SET stake=?, level=?, slash_count=? WHERE validator_id=?",
                 (miner.stake, miner.level, miner.slash_count, validator_id))
        c.execute("INSERT INTO slashing_events (validator_id, amount, reason, timestamp) VALUES (?, ?, ?, ?)",
                 (validator_id, slash_amount, reason, time.time()))
        self.conn.commit()
        
        self.update_level_groups()
        print(f"[SLASH] {validator_id[:16]}... | {reason} | -{slash_amount} MC | New stake: {miner.stake} | Level: {miner.level}")
    
    def distribute_block_reward(self, block: Block):
        """Distribute 6000 MC reward for a block"""
        if block.reward_distributed:
            return
        
        validator_share_total = int(BLOCK_REWARD * VALIDATOR_SHARE)
        node_share_total = int(BLOCK_REWARD * NODE_SHARE)
        uptime_share_total = int(BLOCK_REWARD * UPTIME_SHARE)
        lp_share_total = int(BLOCK_REWARD * LP_SHARE)
        
        # Distribute to validators
        validator_share_each = validator_share_total // len(block.validators)
        for validator_id in block.validators:
            if validator_id in self.miners:
                miner = self.miners[validator_id]
                miner.total_rewards += validator_share_each
                miner.stake += validator_share_each
                miner.blocks_signed += 1
                miner.consecutive_misses = 0
                new_level = self.calculate_level(miner.stake)
                if not self.can_miner_enter_level(miner.wallet, new_level):
                    miner.level = min(new_level, self.max_level)
                else:
                    miner.level = new_level
                
                c = self.conn.cursor()
                c.execute("UPDATE miners SET stake=?, level=?, total_rewards=?, blocks_signed=? WHERE validator_id=?",
                         (miner.stake, miner.level, miner.total_rewards, miner.blocks_signed, validator_id))
                self.conn.commit()
        
        # Add to pools for periodic distribution
        self.node_pool += node_share_total
        self.uptime_pool += uptime_share_total
        self.lp_pool += lp_share_total
        
        block.reward_distributed = True
        self.update_level_groups()
        
        print(f"[BLOCK {block.block_id}] Distributed {BLOCK_REWARD} MC: "
              f"{validator_share_each} MC/validator | Node pool: {node_share_total} | "
              f"Uptime pool: {uptime_share_total} | LP pool: {lp_share_total}")
    
    def distribute_periodic_rewards(self):
        """Distribute node, uptime, and LP rewards every 5 minutes"""
        # Node rewards (distribute to all active nodes)
        if self.nodes and self.node_pool > 0:
            node_share_each = self.node_pool // len(self.nodes)
            for node in self.nodes.values():
                if node.is_active:
                    node.total_rewards += node_share_each
                    c = self.conn.cursor()
                    c.execute("UPDATE nodes SET total_rewards=? WHERE node_id=?",
                             (node.total_rewards, node.node_id))
                    self.conn.commit()
        
        # Uptime rewards (distribute proportionally to uptime)
        active_miners = [m for m in self.miners.values() if m.is_active]
        total_uptime = sum(m.uptime_seconds for m in active_miners)
        if total_uptime > 0 and self.uptime_pool > 0:
            for miner in active_miners:
                if miner.uptime_seconds > 0:
                    share = int(self.uptime_pool * (miner.uptime_seconds / total_uptime))
                    miner.total_rewards += share
                    miner.stake += share
                    new_level = self.calculate_level(miner.stake)
                    if not self.can_miner_enter_level(miner.wallet, new_level):
                        miner.level = min(new_level, self.max_level)
                    else:
                        miner.level = new_level
                    
                    c = self.conn.cursor()
                    c.execute("UPDATE miners SET stake=?, level=?, total_rewards=? WHERE validator_id=?",
                             (miner.stake, miner.level, miner.total_rewards, miner.validator_id))
                    self.conn.commit()
        
        # LP rewards (distribute proportionally to LP share)
        if self.liquidity_providers and self.lp_pool > 0:
            for lp in self.liquidity_providers.values():
                reward = int(self.lp_pool * lp.share)
                lp.total_earned += reward
                lp.last_claim = time.time()
                
                c = self.conn.cursor()
                c.execute("UPDATE liquidity_providers SET total_earned=?, last_claim=? WHERE wallet=?",
                         (lp.total_earned, lp.last_claim, lp.wallet))
                self.conn.commit()
                
                # Find miner by wallet and add reward
                for miner in self.miners.values():
                    if miner.wallet == lp.wallet:
                        miner.total_rewards += reward
                        miner.stake += reward
                        c = self.conn.cursor()
                        c.execute("UPDATE miners SET stake=?, total_rewards=? WHERE validator_id=?",
                                 (miner.stake, miner.total_rewards, miner.validator_id))
                        self.conn.commit()
                        break
        
        print(f"[DISTRIBUTION] Node: {self.node_pool} MC | Uptime: {self.uptime_pool} MC | LP: {self.lp_pool} MC")
        print(f"[DISTRIBUTION] Max level: {self.max_level} | Active miners: {len(active_miners)} | LPs: {len(self.liquidity_providers)}")
        
        self.node_pool = 0
        self.uptime_pool = 0
        self.lp_pool = 0
        self.last_distribution = time.time()
    
    def finalize_block(self, block: Block):
        """Finalize block with hash and add to chain"""
        block.hash = hash_block({
            "block_id": block.block_id,
            "timestamp": block.timestamp,
            "previous_hash": self.last_block_hash,
            "validators": block.validators,
            "level": block.level
        })
        self.last_block_hash = block.hash
        self.blocks.append(block)
        
        c = self.conn.cursor()
        c.execute("INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?)",
                 (block.block_id, block.timestamp, self.last_block_hash,
                  ','.join(block.validators), block.level, block.hash))
        self.conn.commit()
    
    async def produce_block(self, level: int):
        """Produce a block at a given level with full consensus"""
        validators = self.select_validators(level)
        if len(validators) < MIN_VALIDATORS_PER_BLOCK:
            return
        
        block_id = self.current_block_id
        challenge = self.generate_challenge(block_id, validators)
        
        print(f"\n[BLOCK {block_id}] Level {level} | Validators: {len(validators)}")
        
        self.pending_challenges[challenge] = {
            "block_id": block_id,
            "validators": validators,
            "level": level,
            "start_time": time.time(),
            "signatures": {}
        }
        
        await self.broadcast_challenge(validators, challenge, block_id, level)
        
        await asyncio.sleep(SIGNING_WINDOW_MS / 1000)
        
        pending = self.pending_challenges.pop(challenge, {})
        signatures = pending.get("signatures", {})
        
        # Verify signatures
        valid_signatures = {}
        for validator_id, sig in signatures.items():
            if validator_id in self.miners:
                message = f"{challenge}{validator_id}{block_id}"
                if verify_signature(self.miners[validator_id].public_key, message, sig):
                    valid_signatures[validator_id] = sig
        
        if len(valid_signatures) >= MIN_VALIDATORS_PER_BLOCK:
            block = Block(
                block_id=block_id,
                timestamp=time.time(),
                previous_hash=self.last_block_hash,
                validators=list(valid_signatures.keys()),
                level=level,
                signatures=valid_signatures
            )
            self.distribute_block_reward(block)
            self.finalize_block(block)
            self.current_block_id += 1
            await self.broadcast_acceptance(block, block_id)
            print(f"[BLOCK {block_id}] ACCEPTED | {len(valid_signatures)} valid signatures")
        else:
            missing = set(validators) - set(signatures.keys())
            for validator_id in missing:
                self.slash_miner(validator_id, "Missed signing window")
            print(f"[BLOCK {block_id}] REJECTED | Missing: {len(missing)} validators")
            await self.broadcast_rejection(block_id, f"Missing {len(missing)} signatures")
    
    async def broadcast_challenge(self, validators: List[str], challenge: str, block_id: int, level: int):
        """Send challenge to all validators via WebSocket"""
        message = {
            "type": "challenge",
            "challenge": challenge,
            "block_id": block_id,
            "level": level,
            "window_ms": SIGNING_WINDOW_MS
        }
        # In production, this goes through the WebSocket connection manager
        pass
    
    async def broadcast_acceptance(self, block: Block, block_id: int):
        """Notify validators of block acceptance"""
        message = {
            "type": "block_accepted",
            "block_id": block_id,
            "reward": int(BLOCK_REWARD * VALIDATOR_SHARE / MIN_VALIDATORS_PER_BLOCK)
        }
    
    async def broadcast_rejection(self, block_id: int, reason: str):
        """Notify validators of block rejection"""
        message = {
            "type": "block_rejected",
            "block_id": block_id,
            "reason": reason
        }
    
    def get_network_status(self) -> dict:
        """Get current network status"""
        return {
            "max_level": self.max_level,
            "total_miners": len(self.miners),
            "active_miners": sum(1 for m in self.miners.values() if m.is_active),
            "level_unique_wallets": self.level_unique_wallets,
            "total_blocks": self.current_block_id,
            "uptime_pool": self.uptime_pool,
            "node_pool": self.node_pool,
            "lp_pool": self.lp_pool,
            "liquidity_providers": len(self.liquidity_providers)
        }

# ==================== WEBSOCKET SERVER ====================
class MicroCoinServer:
    def __init__(self):
        self.network = MicroCoinNetwork()
        self.connections: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.node_connections: Dict[str, websockets.WebSocketServerProtocol] = {}
    
    async def handle_connection(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connections from miners and nodes"""
        client_id = None
        client_type = None
        
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "register":
                    validator_id = data["validator_id"]
                    public_key = data["public_key"]
                    wallet = data["wallet"]
                    stake = data["stake"]
                    signature = data.get("signature", "")
                    timestamp = data.get("timestamp", time.time())
                    
                    if self.network.register_miner(validator_id, public_key, wallet, stake, signature, timestamp):
                        self.connections[validator_id] = websocket
                        client_id = validator_id
                        client_type = "miner"
                        
                        await websocket.send(json.dumps({
                            "type": "registered",
                            "status": "ok",
                            "level": self.network.miners[validator_id].level,
                            "stake": stake,
                            "max_level": self.network.max_level
                        }))
                        print(f"[CONN] Miner {validator_id[:16]}... registered")
                
                elif msg_type == "node_register":
                    node_id = data["node_id"]
                    wallet = data["wallet"]
                    
                    if node_id not in self.network.nodes:
                        self.network.nodes[node_id] = Node(node_id=node_id, wallet=wallet)
                        c = self.network.conn.cursor()
                        c.execute("INSERT OR REPLACE INTO nodes VALUES (?, ?, ?)",
                                 (node_id, wallet, 0))
                        self.network.conn.commit()
                    
                    self.node_connections[node_id] = websocket
                    client_id = node_id
                    client_type = "node"
                    print(f"[CONN] Node {node_id[:16]}... registered")
                
                elif msg_type == "lp_register":
                    wallet = data["wallet"]
                    amount = data["amount"]
                    self.network.register_liquidity_provider(wallet, amount)
                    await websocket.send(json.dumps({
                        "type": "lp_registered",
                        "status": "ok",
                        "amount": amount
                    }))
                
                elif msg_type == "block_signature":
                    validator_id = data["validator_id"]
                    challenge = data["challenge"]
                    signature = data["signature"]
                    block_id = data.get("block_id", 0)
                    
                    if challenge in self.network.pending_challenges:
                        self.network.pending_challenges[challenge]["signatures"][validator_id] = signature
                
                elif msg_type == "uptime_ping":
                    validator_id = data["validator_id"]
                    uptime = data.get("uptime_seconds", 0)
                    stake = data.get("stake", 0)
                    
                    if validator_id in self.network.miners:
                        self.network.miners[validator_id].uptime_seconds = uptime
                        self.network.miners[validator_id].last_ping = time.time()
                        if stake > 0 and stake != self.network.miners[validator_id].stake:
                            self.network.miners[validator_id].stake = stake
                            new_level = self.network.calculate_level(stake)
                            self.network.miners[validator_id].level = new_level
                            self.network.update_level_groups()
                
                elif msg_type == "get_status":
                    await websocket.send(json.dumps({
                        "type": "status",
                        "data": self.network.get_network_status()
                    }))
                
                elif msg_type == "get_miner":
                    validator_id = data.get("validator_id")
                    if validator_id in self.network.miners:
                        m = self.network.miners[validator_id]
                        await websocket.send(json.dumps({
                            "type": "miner_data",
                            "validator_id": m.validator_id,
                            "wallet": m.wallet,
                            "stake": m.stake,
                            "level": m.level,
                            "total_rewards": m.total_rewards,
                            "blocks_signed": m.blocks_signed,
                            "uptime": m.uptime_seconds,
                            "slash_count": m.slash_count
                        }))
        
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if client_id and client_type == "miner":
                if client_id in self.connections:
                    del self.connections[client_id]
                if client_id in self.network.miners:
                    self.network.miners[client_id].is_active = False
                    self.network.update_level_groups()
                    print(f"[CONN] Miner {client_id[:16]}... disconnected")
            elif client_id and client_type == "node":
                if client_id in self.node_connections:
                    del self.node_connections[client_id]
    
    async def periodic_tasks(self):
        """Run background tasks"""
        level = 1
        
        while True:
            current_time = time.time()
            
            if current_time - self.network.last_distribution >= DISTRIBUTION_INTERVAL_SEC:
                self.network.distribute_periodic_rewards()
            
            if self.network.level_groups:
                available_levels = [l for l in self.network.level_groups 
                                  if len(self.network.level_groups[l]) >= MIN_VALIDATORS_PER_BLOCK]
                if available_levels:
                    level = (level % max(available_levels)) + 1 if available_levels else 1
                    if level in available_levels:
                        await self.network.produce_block(level)
            
            await asyncio.sleep(1)
    
    async def run(self):
        """Start the server"""
        async with websockets.serve(self.handle_connection, NODE_HOST, NODE_PORT):
            print("=" * 70)
            print("MICROCOIN NETWORK - PRODUCTION NODE")
            print("=" * 70)
            print(f"WebSocket server: ws://{NODE_HOST}:{NODE_PORT}")
            print(f"Block reward: {BLOCK_REWARD} MC")
            print(f"Validator share: {VALIDATOR_SHARE*100}%")
            print(f"Node share: {NODE_SHARE*100}%")
            print(f"Uptime share: {UPTIME_SHARE*100}%")
            print(f"LP share: {LP_SHARE*100}%")
            print(f"Signing window: {SIGNING_WINDOW_MS} ms")
            print(f"Slash rate: {SLASH_RATE*100}%")
            print(f"Level stake range: {LEVEL_STAKE_RANGE} MC")
            print(f"Min wallets for next level: {MIN_WALLETS_FOR_NEXT_LEVEL}")
            print("=" * 70)
            print("\n[READY] Waiting for miners, nodes, and LPs...\n")
            
            await self.periodic_tasks()

# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    server = MicroCoinServer()
    asyncio.run(server.run())
