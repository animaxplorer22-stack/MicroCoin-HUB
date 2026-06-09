#!/usr/bin/env python3
# MICROCOIN NODE - COMPLETE MAINNET VERSION
# REAL ECDSA SECP256K1 SIGNATURES | 50B CAP | 4M HALVING
# REWARDS: 75% VALIDATORS | 8% NODES | 7% UPTIME | 10% LPs

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
from typing import Dict, List, Optional, Set, Tuple
import websockets
from collections import defaultdict

# ==================== CRYPTOGRAPHY ====================
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.exceptions import InvalidSignature

# ==================== CONFIGURATION ====================
NODE_HOST = "0.0.0.0"
NODE_PORT = 8080

# TOKENOMICS - HARD CAP 50 BILLION
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
    """REAL ECDSA secp256k1 signature verification"""
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

def hash_block(block_data: dict) -> str:
    """Generate SHA256 block hash"""
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
    last_slash_time: float = 0
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

# ==================== MICROCOIN NETWORK ====================
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
        """Initialize all database tables"""
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
        
        c.execute('''CREATE TABLE IF NOT EXISTS supply_metrics
                     (key TEXT PRIMARY KEY,
                      value INTEGER,
                      updated_at REAL)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS transactions
                     (tx_hash TEXT PRIMARY KEY,
                      from_wallet TEXT,
                      to_wallet TEXT,
                      amount INTEGER,
                      timestamp REAL,
                      block_id INTEGER)''')
        
        self.conn.commit()
    
    def create_genesis_block(self):
        """Create the genesis block with initial supply"""
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM blocks")
        if c.fetchone()[0] == 0:
            genesis = Block(
                block_id=0,
                timestamp=time.time(),
                previous_hash="0" * 64,
                validators=["genesis"],
                level=1
            )
            genesis.block_hash = hash_block({
                "block_id": 0,
                "timestamp": genesis.timestamp,
                "previous_hash": "0" * 64,
                "validators": ["genesis"],
                "level": 1
            })
            genesis.reward_amount = 0
            self.blocks.append(genesis)
            self.last_block_hash = genesis.block_hash
            self.current_block_id = 1
            self.total_minted = 10000
            self.update_supply_metric()
            
            print("=" * 60)
            print("MICROCOIN NETWORK - GENESIS")
            print("=" * 60)
            print(f"Genesis Block: {genesis.block_hash[:16]}...")
            print(f"Initial Supply: 10,000 MC")
            print(f"Hard Cap: {TOTAL_SUPPLY_CAP:,} MC")
            print(f"Remaining: {TOTAL_SUPPLY_CAP - self.total_minted:,} MC")
            print(f"Halving Every: {HALVING_INTERVAL:,} blocks")
            print(f"Initial Reward: {INITIAL_BLOCK_REWARD} MC")
            print(f"Reward Split: 75% Validators | 8% Nodes | 7% Uptime | 10% LP")
            print("=" * 60)
    
    def load_total_minted(self):
        """Load total minted coins from database"""
        c = self.conn.cursor()
        c.execute("SELECT SUM(reward_amount) FROM blocks WHERE reward_amount > 0")
        result = c.fetchone()[0]
        if result:
            self.total_minted = result + 10000
        else:
            self.total_minted = 10000
    
    def update_supply_metric(self):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO supply_metrics VALUES (?, ?, ?)",
                 ('total_minted', self.total_minted, time.time()))
        self.conn.commit()
    
    def get_current_block_reward(self) -> int:
        """Calculate reward based on 4,000,000 block halving and 50B cap"""
        remaining = TOTAL_SUPPLY_CAP - self.total_minted
        if remaining <= 0:
            return 0
        halvings = self.current_block_id // HALVING_INTERVAL
        reward = INITIAL_BLOCK_REWARD // (2 ** halvings)
        reward = max(reward, MINIMUM_BLOCK_REWARD)
        return min(reward, remaining)
    
    def get_remaining_supply(self) -> int:
        return TOTAL_SUPPLY_CAP - self.total_minted
    
    def get_supply_percentage(self) -> float:
        return (self.total_minted / TOTAL_SUPPLY_CAP) * 100 if TOTAL_SUPPLY_CAP > 0 else 0
    
    def get_current_halving(self) -> int:
        return self.current_block_id // HALVING_INTERVAL
    
    def calculate_level(self, stake: int) -> int:
        """Calculate level from stake (100 MC per level)"""
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
                print(f"[LEVEL UNLOCK] Level {self.max_level} unlocked | {unique_count} unique wallets")
                self.conn.execute("INSERT OR REPLACE INTO level_progression VALUES (?, ?, ?)",
                                 (self.max_level, time.time(), unique_count))
                self.conn.commit()
    
    def can_miner_enter_level(self, miner_wallet: str, target_level: int) -> bool:
        """Check if a miner's wallet can enter a target level (anti-Sybil)"""
        if target_level <= 1:
            return True
        unique_count = self.level_unique_wallets.get(target_level - 1, 0)
        return unique_count >= MIN_WALLETS_FOR_NEXT_LEVEL
    
    def select_validators(self, level: int) -> List[str]:
        """Select 10 random validators from a level"""
        miners_in_level = self.level_groups.get(level, [])
        if len(miners_in_level) < MIN_VALIDATORS_PER_BLOCK:
            return []
        seed = int(self.last_block_hash[:16], 16) if self.last_block_hash != "0"*64 else int(time.time())
        rng = random.Random(seed)
        return rng.sample(miners_in_level, MIN_VALIDATORS_PER_BLOCK)
    
    def generate_challenge(self, block_id: int, validators: List[str]) -> str:
        """Generate unique challenge for validators to sign"""
        data = f"{block_id}{''.join(sorted(validators))}{time.time()}{self.last_block_hash}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def register_miner(self, validator_id: str, public_key: str, wallet: str, 
                       stake: int, signature: str, timestamp: float) -> bool:
        """Register a new miner with signature verification"""
        reg_message = f"{validator_id}{wallet}{stake}{timestamp}"
        if not verify_signature(public_key, reg_message, signature):
            print(f"[REG] Signature verification FAILED for {validator_id[:16]}...")
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
            print(f"[REG] New miner: {validator_id[:16]}... | Level {calculated_level} | Stake {stake}")
        
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO miners VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (validator_id, public_key, wallet, stake, calculated_level,
                  self.miners[validator_id].total_rewards,
                  self.miners[validator_id].blocks_signed,
                  self.miners[validator_id].slash_count,
                  self.miners[validator_id].uptime_seconds,
                  timestamp))
        self.conn.commit()
        
        self.update_level_groups()
        return True
    
    def register_node(self, node_id: str, wallet: str):
        """Register a network node"""
        if node_id not in self.nodes:
            self.nodes[node_id] = NetworkNode(
                node_id=node_id,
                wallet=wallet,
                registered_at=time.time()
            )
            c = self.conn.cursor()
            c.execute("INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?)",
                     (node_id, wallet, 0, time.time()))
            self.conn.commit()
            print(f"[NODE] Node registered: {node_id[:16]}...")
    
    def register_liquidity_provider(self, wallet: str, amount: int):
        """Register or update a liquidity provider"""
        if wallet in self.liquidity_providers:
            self.liquidity_providers[wallet].amount += amount
        else:
            self.liquidity_providers[wallet] = LiquidityProvider(
                wallet=wallet,
                amount=amount,
                share=0.0,
                last_claim=time.time()
            )
        
        # Update shares
        total = sum(lp.amount for lp in self.liquidity_providers.values())
        for lp in self.liquidity_providers.values():
            lp.share = lp.amount / total if total > 0 else 0
        
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO liquidity_providers VALUES (?, ?, ?, ?, ?)",
                 (wallet, self.liquidity_providers[wallet].amount,
                  self.liquidity_providers[wallet].share,
                  self.liquidity_providers[wallet].total_earned,
                  self.liquidity_providers[wallet].last_claim))
        self.conn.commit()
        print(f"[LP] {wallet[:20]}... added {amount} MC | Total LPs: {len(self.liquidity_providers)}")
    
    def slash_miner(self, validator_id: str, reason: str):
        """Slash a miner's stake by 10%"""
        if validator_id not in self.miners:
            return
        
        miner = self.miners[validator_id]
        slash_amount = max(int(miner.stake * SLASH_RATE), LEVEL_STAKE_RANGE)
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
        print(f"[SLASH] {validator_id[:16]}... | -{slash_amount} MC | Stake: {miner.stake} | Level: {miner.level}")
    
    def distribute_block_reward(self, block: Block):
        """Distribute block reward according to percentages"""
        if block.reward_distributed:
            return
        
        reward = self.get_current_block_reward()
        block.reward_amount = reward
        
        if reward == 0:
            print(f"[BLOCK {block.block_id}] Cap reached - no new coins minted")
            block.reward_distributed = True
            return
        
        validator_total = int(reward * VALIDATOR_SHARE)
        node_total = int(reward * NODE_SHARE)
        uptime_total = int(reward * UPTIME_SHARE)
        lp_total = int(reward * LP_SHARE)
        
        # Distribute to validators (75% - split equally among 10)
        validator_each = validator_total // max(len(block.validators), 1)
        for vid in block.validators:
            if vid in self.miners:
                m = self.miners[vid]
                m.total_rewards += validator_each
                m.stake += validator_each
                m.blocks_signed += 1
                m.consecutive_misses = 0
                new_level = self.calculate_level(m.stake)
                if not self.can_miner_enter_level(m.wallet, new_level):
                    m.level = min(new_level, self.max_level)
                else:
                    m.level = new_level
                
                c = self.conn.cursor()
                c.execute("UPDATE miners SET stake=?, level=?, total_rewards=?, blocks_signed=? WHERE validator_id=?",
                         (m.stake, m.level, m.total_rewards, m.blocks_signed, vid))
                self.conn.commit()
        
        # Add to node pool (8% - distributed every 5 minutes)
        self.node_pool += node_total
        
        # Add to uptime pool (7% - distributed every 5 minutes)
        self.uptime_pool += uptime_total
        
        # Add to LP pool (10% - distributed every 5 minutes)
        self.lp_pool += lp_total
        
        # Update total minted
        self.total_minted += reward
        self.update_supply_metric()
        
        block.reward_distributed = True
        self.update_level_groups()
        
        remaining = self.get_remaining_supply()
        percent = self.get_supply_percentage()
        halving = self.get_current_halving()
        
        print(f"[BLOCK {block.block_id}] REWARD: {reward} MC")
        print(f"   └─ Validators ({len(block.validators)}): {validator_each} MC each")
        print(f"   └─ Node pool: {node_total} MC | Uptime pool: {uptime_total} MC | LP pool: {lp_total} MC")
        print(f"[SUPPLY] {self.total_minted:,} / {TOTAL_SUPPLY_CAP:,} ({percent:.4f}%) | Halving: {halving}")
        print(f"[REMAINING] {remaining:,} MC until cap")
    
    def distribute_periodic_rewards(self):
        """Distribute node, uptime, and LP rewards every 5 minutes"""
        # Distribute node rewards (8% - split equally among all active nodes)
        if self.nodes and self.node_pool > 0:
            active_nodes = [n for n in self.nodes.values() if n.is_active]
            if active_nodes:
                node_share = self.node_pool // len(active_nodes)
                for node in active_nodes:
                    node.total_rewards += node_share
                    c = self.conn.cursor()
                    c.execute("UPDATE nodes SET total_rewards=? WHERE node_id=?",
                             (node.total_rewards, node.node_id))
                    self.conn.commit()
                print(f"[DISTRO] Node rewards: {self.node_pool} MC distributed to {len(active_nodes)} nodes")
        
        # Distribute uptime rewards (7% - proportional to uptime)
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
            print(f"[DISTRO] Uptime rewards: {self.uptime_pool} MC distributed")
        
        # Distribute LP rewards (10% - proportional to LP share)
        if self.liquidity_providers and self.lp_pool > 0:
            for lp in self.liquidity_providers.values():
                reward = int(self.lp_pool * lp.share)
                lp.total_earned += reward
                lp.last_claim = time.time()
                
                c = self.conn.cursor()
                c.execute("UPDATE liquidity_providers SET total_earned=?, last_claim=? WHERE wallet=?",
                         (lp.total_earned, lp.last_claim, lp.wallet))
                self.conn.commit()
                
                # Also add to miner if they are a miner
                for miner in self.miners.values():
                    if miner.wallet == lp.wallet:
                        miner.total_rewards += reward
                        miner.stake += reward
                        c.execute("UPDATE miners SET stake=?, total_rewards=? WHERE validator_id=?",
                                 (miner.stake, miner.total_rewards, miner.validator_id))
                        self.conn.commit()
                        break
            
            print(f"[DISTRO] LP rewards: {self.lp_pool} MC distributed to {len(self.liquidity_providers)} LPs")
        
        # Reset pools
        self.node_pool = 0
        self.uptime_pool = 0
        self.lp_pool = 0
        self.last_distribution = time.time()
    
    def finalize_block(self, block: Block):
        """Finalize block and add to chain"""
        block.block_hash = hash_block({
            "block_id": block.block_id,
            "timestamp": block.timestamp,
            "previous_hash": self.last_block_hash,
            "validators": block.validators,
            "level": block.level
        })
        self.last_block_hash = block.block_hash
        self.blocks.append(block)
        
        c = self.conn.cursor()
        c.execute("INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (block.block_id, block.timestamp, self.last_block_hash,
                  ','.join(block.validators), block.level, block.block_hash, block.reward_amount))
        self.conn.commit()
    
    async def produce_block(self, level: int):
        """Produce a block at a given level"""
        validators = self.select_validators(level)
        if len(validators) < MIN_VALIDATORS_PER_BLOCK:
            return
        
        block_id = self.current_block_id
        challenge = self.generate_challenge(block_id, validators)
        
        self.pending_challenges[challenge] = {
            "block_id": block_id,
            "validators": validators,
            "level": level,
            "start_time": time.time(),
            "signatures": {}
        }
        
        # Wait for signatures (2.5 seconds)
        await asyncio.sleep(SIGNING_WINDOW_MS / 1000)
        
        pending = self.pending_challenges.pop(challenge, {})
        signatures = pending.get("signatures", {})
        
        # Verify each signature
        valid_sigs = {}
        for vid, sig in signatures.items():
            if vid in self.miners:
                message = f"{challenge}{vid}{block_id}"
                if verify_signature(self.miners[vid].public_key, message, sig):
                    valid_sigs[vid] = sig
        
        if len(valid_sigs) >= MIN_VALIDATORS_PER_BLOCK:
            block = Block(
                block_id=block_id,
                timestamp=time.time(),
                previous_hash=self.last_block_hash,
                validators=list(valid_sigs.keys()),
                level=level,
                signatures=valid_sigs
            )
            self.distribute_block_reward(block)
            self.finalize_block(block)
            self.current_block_id += 1
            print(f"[BLOCK {block_id}] ✅ ACCEPTED | {len(valid_sigs)}/{len(validators)} signatures | Level {level}")
        else:
            missing = set(validators) - set(signatures.keys())
            for vid in missing:
                self.slash_miner(vid, "Missed signing window")
            print(f"[BLOCK {block_id}] ❌ REJECTED | Missing: {len(missing)} signatures")
    
    async def run_block_production(self):
        """Continuous block production loop"""
        level = 1
        while True:
            if self.level_groups:
                available_levels = [l for l in self.level_groups 
                                   if len(self.level_groups[l]) >= MIN_VALIDATORS_PER_BLOCK]
                if available_levels:
                    level = (level % max(available_levels)) + 1 if available_levels else 1
                    if level in available_levels:
                        await self.produce_block(level)
            await asyncio.sleep(0.1)  # Small delay to prevent CPU overload

# ==================== WEBSOCKET SERVER ====================
class MicroCoinServer:
    def __init__(self):
        self.network = MicroCoinNetwork()
        self.connections: Dict[str, websockets.WebSocketServerProtocol] = {}
    
    async def handle_connection(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connections"""
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "register":
                    vid = data["validator_id"]
                    pubkey = data["public_key"]
                    wallet = data["wallet"]
                    stake = data.get("stake", 100)
                    sig = data.get("signature", "")
                    ts = data.get("timestamp", time.time())
                    
                    if self.network.register_miner(vid, pubkey, wallet, stake, sig, ts):
                        self.connections[vid] = websocket
                        await websocket.send(json.dumps({
                            "type": "registered",
                            "level": self.network.miners[vid].level if vid in self.network.miners else 1,
                            "max_level": self.network.max_level,
                            "remaining_supply": self.network.get_remaining_supply(),
                            "current_reward": self.network.get_current_block_reward()
                        }))
                
                elif msg_type == "node_register":
                    nid = data["node_id"]
                    wallet = data["wallet"]
                    self.network.register_node(nid, wallet)
                    self.connections[f"node_{nid}"] = websocket
                    await websocket.send(json.dumps({
                        "type": "node_registered",
                        "status": "ok"
                    }))
                
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
                    vid = data["validator_id"]
                    challenge = data["challenge"]
                    sig = data["signature"]
                    if challenge in self.network.pending_challenges:
                        self.network.pending_challenges[challenge]["signatures"][vid] = sig
                
                elif msg_type == "uptime_ping":
                    vid = data["validator_id"]
                    uptime = data.get("uptime_seconds", 0)
                    if vid in self.network.miners:
                        self.network.miners[vid].uptime_seconds = uptime
                        self.network.miners[vid].last_ping = time.time()
                
                elif msg_type == "get_status":
                    await websocket.send(json.dumps({
                        "type": "status",
                        "block_id": self.network.current_block_id,
                        "total_miners": len(self.network.miners),
                        "active_miners": sum(1 for m in self.network.miners.values() if m.is_active),
                        "total_nodes": len(self.network.nodes),
                        "total_lps": len(self.network.liquidity_providers),
                        "max_level": self.network.max_level,
                        "current_reward": self.network.get_current_block_reward(),
                        "total_minted": self.network.total_minted,
                        "remaining_supply": self.network.get_remaining_supply(),
                        "supply_percentage": self.network.get_supply_percentage()
                    }))
        
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            print(f"[ERROR] {e}")
    
    async def periodic_tasks(self):
        """Run periodic tasks"""
        last_status = time.time()
        last_distribution = time.time()
        
        while True:
            # Distribute periodic rewards every 5 minutes
            if time.time() - last_distribution >= DISTRIBUTION_INTERVAL_SEC:
                self.network.distribute_periodic_rewards()
                last_distribution = time.time()
            
            # Print status every 60 seconds
            if time.time() - last_status >= 60:
                remaining = self.network.get_remaining_supply()
                percent = self.network.get_supply_percentage()
                reward = self.network.get_current_block_reward()
                halving = self.network.get_current_halving()
                
                print(f"\n[STATUS] Block {self.network.current_block_id} | Reward: {reward} MC | Halving: {halving}")
                print(f"[STATUS] Miners: {len(self.network.miners)} | Active: {sum(1 for m in self.network.miners.values() if m.is_active)}")
                print(f"[STATUS] Nodes: {len(self.network.nodes)} | LPs: {len(self.network.liquidity_providers)}")
                print(f"[SUPPLY] {self.network.total_minted:,} / {TOTAL_SUPPLY_CAP:,} ({percent:.4f}%) | Remaining: {remaining:,}\n")
                last_status = time.time()
            
            await asyncio.sleep(1)
    
    async def run(self):
        """Start the server"""
        # Start block production in background
        asyncio.create_task(self.network.run_block_production())
        
        # Start WebSocket server
        async with websockets.serve(self.handle_connection, NODE_HOST, NODE_PORT):
            print("=" * 60)
            print("MICROCOIN NODE - MAINNET READY")
            print("=" * 60)
            print(f"WebSocket: ws://{NODE_HOST}:{NODE_PORT}")
            print(f"Hard cap: {TOTAL_SUPPLY_CAP:,} MC")
            print(f"Initial reward: {INITIAL_BLOCK_REWARD} MC")
            print(f"Halving interval: {HALVING_INTERVAL:,} blocks")
            print(f"Signing window: {SIGNING_WINDOW_MS} ms")
            print(f"Min validators: {MIN_VALIDATORS_PER_BLOCK}")
            print(f"Level stake range: {LEVEL_STAKE_RANGE} MC/level")
            print(f"Min wallets for next level: {MIN_WALLETS_FOR_NEXT_LEVEL}")
            print("=" * 60)
            print("\n[READY] Waiting for miners, nodes, and LPs to connect...\n")
            
            await self.periodic_tasks()

# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    try:
        server = MicroCoinServer()
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Node stopped")
        sys.exit(0)
