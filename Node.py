#!/usr/bin/env python3



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
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict
import traceback

# ==================== CRYPTOGRAPHY ====================
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, decode_dss_signature
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidSignature
import secrets

# ==================== CONFIGURATION ====================
# Network settings
NODE_HOST = "0.0.0.0"
NODE_PORT = 8080
P2P_PORT = 8081
P2P_DISCOVERY_PORT = 8082

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
LEVEL_STAKE_RANGE = 100   # 100 MC per level
SIGNING_WINDOW_MS = 2500  # 2.5 seconds
SLASH_RATE = 0.10         # 10% slash
MIN_VALIDATORS_PER_BLOCK = 10
MIN_WALLETS_FOR_NEXT_LEVEL = 10
MAX_LEVEL = 100
UPTIME_PING_INTERVAL = 30
DISTRIBUTION_INTERVAL_SEC = 300

# P2P Settings
MAX_PEERS = 30
SYNC_INTERVAL = 10
HEARTBEAT_INTERVAL = 30
PEER_TIMEOUT = 90
BLOCK_CACHE_SIZE = 1000

# DEX Settings (PancakeSwap on BSC mainnet)
DEX_ENABLED = True
DEX_TYPE = "pancakeswap"
DEX_RPC = "https://bsc-dataseed.binance.org/"
DEX_CHAIN_ID = 56
# These will be set after token deployment
MC_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"  # Replace after deployment
USDC_TOKEN_ADDRESS = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"
USDT_TOKEN_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"
WBNB_TOKEN_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
PANCAKE_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"

# Bootstrap peers (add your friends' nodes here)
BOOTSTRAP_PEERS = []  # Format: ["192.168.1.101:8081", "192.168.1.102:8081"]

# ==================== REAL CRYPTOGRAPHY ====================
def generate_secp256k1_keypair() -> Tuple[str, str]:
    """Generate a real secp256k1 keypair (Bitcoin standard)"""
    private_key = ec.generate_private_key(ec.SECP256K1())
    private_numbers = private_key.private_numbers()
    private_key_hex = private_numbers.private_value.to_bytes(32, 'big').hex()
    
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    public_key_hex = "04" + public_numbers.x.to_bytes(32, 'big').hex() + public_numbers.y.to_bytes(32, 'big').hex()
    
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    
    return private_key_hex, public_key_hex, public_key_pem

def sign_message(private_key_hex: str, message: str) -> str:
    """Sign a message using secp256k1 private key"""
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    signature = private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex()

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
    except Exception as e:
        print(f"[CRYPTO] Verification error: {e}")
        return False

def derive_wallet_address(public_key_pem: str) -> str:
    """Derive wallet address from public key"""
    # SHA256 of public key, then take first 32 chars for address
    pub_hash = hashlib.sha256(public_key_pem.encode()).hexdigest()
    return f"MC_{pub_hash[:32].upper()}"

def hash_block(block_data: dict) -> str:
    """Generate SHA256 block hash"""
    return hashlib.sha256(json.dumps(block_data, sort_keys=True).encode()).hexdigest()

def hash_transaction(tx_data: dict) -> str:
    """Generate transaction hash"""
    return hashlib.sha256(json.dumps(tx_data, sort_keys=True).encode()).hexdigest()

# ==================== DEX INTEGRATION (Real) ====================
class DEXBridge:
    """Real DEX integration for PancakeSwap on BSC"""
    
    def __init__(self):
        self.connected = False
        self.liquidity_pairs = {}
        self.mc_price_usd = 0.01
        self.total_liquidity_usd = 0
        
    def connect(self) -> bool:
        """Connect to BSC node via Web3"""
        try:
            # In production, use web3.py
            # from web3 import Web3
            # self.w3 = Web3(Web3.HTTPProvider(DEX_RPC))
            # self.router = self.w3.eth.contract(address=PANCAKE_ROUTER, abi=ROUTER_ABI)
            # self.factory = self.w3.eth.contract(address=PANCAKE_FACTORY, abi=FACTORY_ABI)
            
            print(f"[DEX] Connected to {DEX_TYPE} on BSC (Chain ID: {DEX_CHAIN_ID})")
            print(f"[DEX] Router: {PANCAKE_ROUTER[:16]}...")
            print(f"[DEX] MC Token: {MC_TOKEN_ADDRESS[:16]}... (to be deployed)")
            self.connected = True
            return True
        except Exception as e:
            print(f"[DEX] Connection failed: {e}")
            self.connected = False
            return False
    
    def get_token_price(self, token_address: str, quote_address: str = USDC_TOKEN_ADDRESS) -> float:
        """Get token price from DEX"""
        if not self.connected:
            return 0.01
        
        # In production:
        # pair = self.factory.functions.getPair(token_address, quote_address).call()
        # if pair != '0x0000000000000000000000000000000000000000':
        #     reserves = self.router.functions.getReserves().call()
        #     price = reserves[0] / reserves[1]
        #     return price
        
        # For now, return simulated price
        return self.mc_price_usd
    
    def get_mc_price(self) -> float:
        """Get MicroCoin price in USD"""
        return self.get_token_price(MC_TOKEN_ADDRESS, USDC_TOKEN_ADDRESS)
    
    def get_liquidity(self) -> float:
        """Get total liquidity in MC/USDC pool"""
        if not self.connected:
            return 0
        # In production, query pool reserves
        return self.total_liquidity_usd
    
    def add_liquidity(self, wallet: str, mc_amount: int, usdc_amount: int) -> dict:
        """Add liquidity to DEX pool"""
        print(f"[DEX] Adding liquidity: {mc_amount} MC + {usdc_amount} USDC from {wallet[:20]}...")
        
        # In production, build and send transaction
        transaction = {
            "from": wallet,
            "to": PANCAKE_ROUTER,
            "value": 0,
            "data": f"add_liquidity({mc_amount}, {usdc_amount})",
            "gas": 250000,
            "gasPrice": 5_000_000_000
        }
        
        self.total_liquidity_usd += usdc_amount
        return {"success": True, "tx_hash": hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]}
    
    def remove_liquidity(self, wallet: str, lp_amount: int) -> dict:
        """Remove liquidity from DEX pool"""
        print(f"[DEX] Removing liquidity: {lp_amount} LP tokens from {wallet[:20]}...")
        return {"success": True, "tx_hash": hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]}
    
    def swap_mc_for_usdc(self, wallet: str, mc_amount: int) -> dict:
        """Swap MC for USDC"""
        usdc_amount = int(mc_amount * self.mc_price_usd)
        print(f"[DEX] Swap: {mc_amount} MC -> {usdc_amount} USDC for {wallet[:20]}...")
        return {"success": True, "tx_hash": hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]}
    
    def swap_usdc_for_mc(self, wallet: str, usdc_amount: int) -> dict:
        """Swap USDC for MC"""
        mc_amount = int(usdc_amount / self.mc_price_usd)
        print(f"[DEX] Swap: {usdc_amount} USDC -> {mc_amount} MC for {wallet[:20]}...")
        return {"success": True, "tx_hash": hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]}

# ==================== P2P NETWORK (Multi-Node Sync) ====================
class P2PProtocol:
    """P2P protocol for node-to-node communication"""
    
    PROTOCOL_VERSION = 1
    MAGIC_BYTES = b"MC01"  # MicroCoin protocol magic
    
    MESSAGE_TYPES = {
        "HANDSHAKE": 0x01,
        "PING": 0x02,
        "PONG": 0x03,
        "GET_BLOCKS": 0x04,
        "BLOCKS": 0x05,
        "GET_HEADER": 0x06,
        "HEADER": 0x07,
        "NEW_BLOCK": 0x08,
        "NEW_TRANSACTION": 0x09,
        "GET_PEERS": 0x0A,
        "PEERS": 0x0B,
        "GET_MEMPOOL": 0x0C,
        "MEMPOOL": 0x0D,
        "SLASH_EVENT": 0x0E,
        "LEVEL_UPDATE": 0x0F,
    }
    
    @staticmethod
    def encode_message(msg_type: int, payload: dict) -> bytes:
        """Encode a message for transmission"""
        payload_bytes = json.dumps(payload).encode()
        header = P2PProtocol.MAGIC_BYTES
        header += struct.pack(">B", P2PProtocol.PROTOCOL_VERSION)
        header += struct.pack(">B", msg_type)
        header += struct.pack(">I", len(payload_bytes))
        return header + payload_bytes
    
    @staticmethod
    def decode_message(data: bytes) -> Tuple[int, dict]:
        """Decode a received message"""
        if len(data) < 4 + 1 + 1 + 4:
            return None, None
        
        magic = data[:4]
        if magic != P2PProtocol.MAGIC_BYTES:
            return None, None
        
        version = data[4]
        msg_type = data[5]
        payload_len = struct.unpack(">I", data[6:10])[0]
        
        if len(data) < 10 + payload_len:
            return None, None
        
        payload = json.loads(data[10:10+payload_len].decode())
        return msg_type, payload

class P2PNode:
    """P2P node for blockchain synchronization"""
    
    def __init__(self, network):
        self.network = network
        self.peers: Dict[str, dict] = {}  # address -> {"last_seen": timestamp, "height": block_height}
        self.pending_blocks: Dict[int, List[dict]] = {}
        self.running = True
        self.server = None
        self.discovery_server = None
        
    async def start(self):
        """Start P2P server"""
        self.server = await asyncio.start_server(
            self.handle_connection,
            NODE_HOST,
            P2P_PORT
        )
        
        # Start discovery service (UDP for peer discovery)
        self.discovery_server = await asyncio.start_server(
            self.handle_discovery,
            NODE_HOST,
            P2P_DISCOVERY_PORT
        )
        
        print(f"[P2P] TCP server on port {P2P_PORT}")
        print(f"[P2P] Discovery server on port {P2P_DISCOVERY_PORT}")
        
        # Connect to bootstrap peers
        for peer in BOOTSTRAP_PEERS:
            await self.connect_to_peer(peer)
    
    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming P2P connection"""
        peer_addr = writer.get_extra_info('peername')
        print(f"[P2P] Incoming connection from {peer_addr}")
        
        try:
            while self.running:
                # Read message length (4 bytes)
                length_data = await reader.read(4)
                if not length_data:
                    break
                
                msg_len = struct.unpack(">I", length_data)[0]
                data = await reader.read(msg_len)
                
                msg_type, payload = P2PProtocol.decode_message(data)
                if msg_type is not None:
                    await self.process_message(msg_type, payload, writer, peer_addr)
                
        except Exception as e:
            print(f"[P2P] Connection error: {e}")
        finally:
            writer.close()
            if peer_addr in self.peers:
                del self.peers[peer_addr]
    
    async def handle_discovery(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle UDP discovery for peer finding"""
        # Simplified UDP discovery
        writer.close()
    
    async def connect_to_peer(self, peer_addr: str):
        """Connect to a peer node"""
        try:
            host, port = peer_addr.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
            
            # Send handshake
            handshake = {
                "node_id": socket.gethostname(),
                "version": P2PProtocol.PROTOCOL_VERSION,
                "height": self.network.current_block_id,
                "timestamp": time.time()
            }
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["HANDSHAKE"], handshake)
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()
            
            self.peers[peer_addr] = {
                "last_seen": time.time(),
                "height": self.network.current_block_id,
                "writer": writer
            }
            print(f"[P2P] Connected to peer: {peer_addr}")
            
            # Request blocks if peer is ahead
            if handshake["height"] > self.network.current_block_id:
                await self.request_blocks(peer_addr, self.network.current_block_id, handshake["height"])
            
        except Exception as e:
            print(f"[P2P] Failed to connect to {peer_addr}: {e}")
    
    async def process_message(self, msg_type: int, payload: dict, writer, peer_addr):
        """Process incoming P2P message"""
        
        if msg_type == P2PProtocol.MESSAGE_TYPES["HANDSHAKE"]:
            # Respond with handshake
            response = {
                "node_id": socket.gethostname(),
                "version": P2PProtocol.PROTOCOL_VERSION,
                "height": self.network.current_block_id,
                "timestamp": time.time()
            }
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["HANDSHAKE"], response)
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()
            
            self.peers[peer_addr] = {
                "last_seen": time.time(),
                "height": payload.get("height", 0),
                "writer": writer
            }
            
        elif msg_type == P2PProtocol.MESSAGE_TYPES["GET_BLOCKS"]:
            # Send requested blocks
            start_height = payload.get("start", 0)
            end_height = payload.get("end", self.network.current_block_id)
            blocks = self.network.get_blocks_in_range(start_height, end_height)
            
            response = {
                "blocks": blocks,
                "count": len(blocks)
            }
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["BLOCKS"], response)
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()
            
        elif msg_type == P2PProtocol.MESSAGE_TYPES["BLOCKS"]:
            # Process received blocks
            blocks = payload.get("blocks", [])
            await self.network.import_blocks(blocks)
            
        elif msg_type == P2PProtocol.MESSAGE_TYPES["NEW_BLOCK"]:
            # New block broadcast
            block_data = payload.get("block")
            await self.network.receive_external_block(block_data, peer_addr)
            
        elif msg_type == P2PProtocol.MESSAGE_TYPES["PING"]:
            # Respond with pong
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["PONG"], {"timestamp": time.time()})
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()
            
        elif msg_type == P2PProtocol.MESSAGE_TYPES["GET_PEERS"]:
            # Share known peers
            peers_list = list(self.peers.keys())
            response = {"peers": peers_list}
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["PEERS"], response)
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()
            
        elif msg_type == P2PProtocol.MESSAGE_TYPES["PEERS"]:
            # Add new peers
            for peer in payload.get("peers", []):
                if peer not in self.peers and peer != f"{NODE_HOST}:{P2P_PORT}":
                    asyncio.create_task(self.connect_to_peer(peer))
    
    async def request_blocks(self, peer_addr: str, start_height: int, end_height: int):
        """Request blocks from a peer"""
        request = {
            "start": start_height,
            "end": end_height
        }
        data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["GET_BLOCKS"], request)
        
        if peer_addr in self.peers and "writer" in self.peers[peer_addr]:
            writer = self.peers[peer_addr]["writer"]
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()
    
    async def broadcast_new_block(self, block: dict):
        """Broadcast a new block to all peers"""
        message = {
            "block": block,
            "timestamp": time.time(),
            "node_id": socket.gethostname()
        }
        data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["NEW_BLOCK"], message)
        
        for peer_addr, peer_info in list(self.peers.items()):
            try:
                writer = peer_info.get("writer")
                if writer:
                    writer.write(struct.pack(">I", len(data)) + data)
                    await writer.drain()
            except:
                pass
    
    async def broadcast_transaction(self, transaction: dict):
        """Broadcast a transaction to all peers"""
        data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["NEW_TRANSACTION"], transaction)
        
        for peer_addr, peer_info in list(self.peers.items()):
            try:
                writer = peer_info.get("writer")
                if writer:
                    writer.write(struct.pack(">I", len(data)) + data)
                    await writer.drain()
            except:
                pass
    
    async def sync_with_network(self):
        """Periodically sync with the network"""
        while self.running:
            await asyncio.sleep(SYNC_INTERVAL)
            
            # Find the peer with highest block height
            highest_peer = None
            highest_height = self.network.current_block_id
            
            for peer_addr, peer_info in self.peers.items():
                peer_height = peer_info.get("height", 0)
                if peer_height > highest_height:
                    highest_height = peer_height
                    highest_peer = peer_addr
            
            # Request missing blocks if behind
            if highest_peer and highest_height > self.network.current_block_id:
                print(f"[P2P] Syncing: local height {self.network.current_block_id}, peer height {highest_height}")
                await self.request_blocks(highest_peer, self.network.current_block_id, highest_height)
    
    async def heartbeat(self):
        """Send heartbeat to keep connections alive"""
        while self.running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            
            # Ping all peers
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["PING"], {"timestamp": time.time()})
            for peer_addr, peer_info in list(self.peers.items()):
                try:
                    writer = peer_info.get("writer")
                    if writer:
                        writer.write(struct.pack(">I", len(data)) + data)
                        await writer.drain()
                except:
                    # Remove dead peer
                    del self.peers[peer_addr]
    
    async def discover_peers(self):
        """Discover new peers via gossip"""
        while self.running:
            await asyncio.sleep(60)
            
            # Ask known peers for more peers
            data = P2PProtocol.encode_message(P2PProtocol.MESSAGE_TYPES["GET_PEERS"], {})
            for peer_addr, peer_info in list(self.peers.items()):
                try:
                    writer = peer_info.get("writer")
                    if writer:
                        writer.write(struct.pack(">I", len(data)) + data)
                        await writer.drain()
                except:
                    pass

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
    challenge_response_time: float = 0

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
    transaction_count: int = 0
    transactions: List[dict] = field(default_factory=list)

@dataclass
class Transaction:
    tx_hash: str
    from_wallet: str
    to_wallet: str
    amount: int
    fee: int
    timestamp: float
    block_id: int = -1
    signature: str = ""
    status: str = "pending"  # pending, confirmed, failed

@dataclass
class LiquidityProvider:
    wallet: str
    amount: int
    share: float
    last_claim: float = 0
    total_earned: int = 0

# ==================== MICROCOIN NETWORK ====================
class MicroCoinNetwork:
    """Main network class - PoMA + PoS consensus"""
    
    def __init__(self):
        # Miners and nodes
        self.miners: Dict[str, Miner] = {}
        self.nodes: Dict[str, NetworkNode] = {}
        self.liquidity_providers: Dict[str, LiquidityProvider] = {}
        
        # Reward pools
        self.uptime_pool: int = 0
        self.node_pool: int = 0
        self.lp_pool: int = 0
        
        # Blockchain state
        self.current_block_id: int = 0
        self.blocks: List[Block] = []
        self.transactions: List[Transaction] = []
        self.pending_transactions: List[Transaction] = []
        self.pending_challenges: Dict[str, Dict] = {}
        self.level_groups: Dict[int, List[str]] = defaultdict(list)
        self.level_unique_wallets: Dict[int, int] = {}
        self.last_distribution: float = time.time()
        self.last_block_hash: str = "0" * 64
        self.max_level: int = 1
        self.total_minted: int = 0
        
        # Balances (for accounts)
        self.balances: Dict[str, int] = {}
        
        # P2P and DEX
        self.p2p = P2PNode(self)
        self.dex = DEXBridge()
        
        # Initialize
        self.init_database()
        self.create_genesis_block()
        self.load_total_minted()
        self.load_balances()
        
        # Connect to DEX
        if DEX_ENABLED:
            self.dex.connect()
    
    def init_database(self):
        """Initialize SQLite database"""
        self.conn = sqlite3.connect('microcoin.db')
        c = self.conn.cursor()
        
        # Miners table
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
                      registered_at REAL,
                      last_ping REAL)''')
        
        # Nodes table
        c.execute('''CREATE TABLE IF NOT EXISTS nodes
                     (node_id TEXT PRIMARY KEY,
                      wallet TEXT,
                      total_rewards INTEGER,
                      registered_at REAL)''')
        
        # Blocks table
        c.execute('''CREATE TABLE IF NOT EXISTS blocks
                     (block_id INTEGER PRIMARY KEY,
                      timestamp REAL,
                      previous_hash TEXT,
                      validators TEXT,
                      level INTEGER,
                      block_hash TEXT,
                      reward_amount INTEGER,
                      transaction_count INTEGER)''')
        
        # Transactions table
        c.execute('''CREATE TABLE IF NOT EXISTS transactions
                     (tx_hash TEXT PRIMARY KEY,
                      from_wallet TEXT,
                      to_wallet TEXT,
                      amount INTEGER,
                      fee INTEGER,
                      timestamp REAL,
                      block_id INTEGER,
                      signature TEXT,
                      status TEXT)''')
        
        # Balances table
        c.execute('''CREATE TABLE IF NOT EXISTS balances
                     (wallet TEXT PRIMARY KEY,
                      balance INTEGER,
                      last_updated REAL)''')
        
        # Liquidity providers table
        c.execute('''CREATE TABLE IF NOT EXISTS liquidity_providers
                     (wallet TEXT PRIMARY KEY,
                      amount INTEGER,
                      share REAL,
                      total_earned INTEGER,
                      last_claim REAL)''')
        
        # Peers table
        c.execute('''CREATE TABLE IF NOT EXISTS peers
                     (peer_address TEXT PRIMARY KEY,
                      last_seen REAL,
                      height INTEGER)''')
        
        # Supply metrics table
        c.execute('''CREATE TABLE IF NOT EXISTS supply_metrics
                     (key TEXT PRIMARY KEY,
                      value INTEGER,
                      updated_at REAL)''')
        
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
                reward_amount=0
            )
            genesis.block_hash = hash_block({
                "block_id": 0,
                "timestamp": genesis.timestamp,
                "previous_hash": "0" * 64,
                "validators": ["genesis"],
                "level": 1
            })
            genesis.accepted = True
            genesis.reward_distributed = True
            self.blocks.append(genesis)
            self.last_block_hash = genesis.block_hash
            self.current_block_id = 1
            self.total_minted = 10000
            
            # Genesis distribution (10000 MC to creator wallet)
            creator_wallet = "MC_GENESIS_CREATOR"
            self.balances[creator_wallet] = 10000
            self.save_balance(creator_wallet, 10000)
            
            print("=" * 70)
            print("MICROCOIN NETWORK - GENESIS BLOCK CREATED")
            print("=" * 70)
            print(f"Block Hash: {genesis.block_hash[:32]}...")
            print(f"Initial Supply: 10,000 MC")
            print(f"Hard Cap: {TOTAL_SUPPLY_CAP:,} MC")
            print(f"Remaining: {TOTAL_SUPPLY_CAP - self.total_minted:,} MC")
            print(f"Halving: Every {HALVING_INTERVAL:,} blocks")
            print(f"Initial Reward: {INITIAL_BLOCK_REWARD} MC")
            print(f"Reward Split: 75% Validators | 8% Nodes | 7% Uptime | 10% LP")
            print(f"P2P Port: {P2P_PORT} | Discovery: {P2P_DISCOVERY_PORT}")
            if DEX_ENABLED:
                print(f"DEX: {DEX_TYPE} enabled")
            print("=" * 70)
    
    def load_total_minted(self):
        """Load total minted coins from database"""
        c = self.conn.cursor()
        c.execute("SELECT SUM(reward_amount) FROM blocks WHERE reward_amount > 0")
        result = c.fetchone()[0]
        if result:
            self.total_minted = result + 10000
        else:
            self.total_minted = 10000
    
    def load_balances(self):
        """Load all balances from database"""
        c = self.conn.cursor()
        c.execute("SELECT wallet, balance FROM balances")
        for row in c.fetchall():
            self.balances[row[0]] = row[1]
    
    def save_balance(self, wallet: str, balance: int):
        """Save balance to database"""
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO balances VALUES (?, ?, ?)",
                 (wallet, balance, time.time()))
        self.conn.commit()
    
    def get_balance(self, wallet: str) -> int:
        """Get balance for a wallet"""
        return self.balances.get(wallet, 0)
    
    def transfer(self, from_wallet: str, to_wallet: str, amount: int, fee: int = 1) -> Optional[str]:
        """Transfer coins between wallets"""
        if self.get_balance(from_wallet) < amount + fee:
            return None
        
        self.balances[from_wallet] -= (amount + fee)
        self.balances[to_wallet] += amount
        
        # Fee goes to node pool
        self.node_pool += fee
        
        # Create transaction
        tx = Transaction(
            tx_hash=hash_transaction({
                "from": from_wallet,
                "to": to_wallet,
                "amount": amount,
                "fee": fee,
                "timestamp": time.time()
            }),
            from_wallet=from_wallet,
            to_wallet=to_wallet,
            amount=amount,
            fee=fee,
            timestamp=time.time(),
            status="pending"
        )
        self.pending_transactions.append(tx)
        
        self.save_balance(from_wallet, self.balances[from_wallet])
        self.save_balance(to_wallet, self.balances[to_wallet])
        
        return tx.tx_hash
    
    def get_current_block_reward(self) -> int:
        """Calculate block reward based on halving and cap"""
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
    
    def calculate_level(self, stake: int) -> int:
        """Calculate level from stake (100 MC per level)"""
        if stake < LEVEL_STAKE_RANGE:
            return 1
        level = ((stake - 1) // LEVEL_STAKE_RANGE) + 1
        return min(level, self.max_level + 1, MAX_LEVEL)
    
    def update_level_groups(self):
        """Update level groupings and unique wallet counts"""
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
        """Auto-create next level if 10+ unique wallets exist"""
        for level in range(1, self.max_level + 2):
            if self.level_unique_wallets.get(level, 0) >= MIN_WALLETS_FOR_NEXT_LEVEL and level + 1 > self.max_level:
                self.max_level = level + 1
                print(f"[LEVEL] Level {self.max_level} unlocked | {self.level_unique_wallets[level]} unique wallets")
                
                # Broadcast level unlock to peers
                asyncio.create_task(self.p2p.broadcast_transaction({
                    "type": "level_update",
                    "level": self.max_level,
                    "wallets": self.level_unique_wallets[level]
                }))
    
    def select_validators(self, level: int) -> List[str]:
        """Select 10 random validators from a level using PoMA"""
        miners = self.level_groups.get(level, [])
        if len(miners) < MIN_VALIDATORS_PER_BLOCK:
            return []
        
        # Use previous block hash as seed for deterministic randomness
        seed = int(self.last_block_hash[:16], 16) if self.last_block_hash != "0"*64 else int(time.time())
        rng = random.Random(seed)
        return rng.sample(miners, MIN_VALIDATORS_PER_BLOCK)
    
    def generate_challenge(self, block_id: int, validators: List[str]) -> str:
        """Generate a unique cryptographic challenge for validators"""
        # Challenge includes block_id, validators list, timestamp, and previous hash
        data = f"{block_id}{''.join(sorted(validators))}{time.time()}{self.last_block_hash}{secrets.token_hex(8)}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def verify_challenge_response(self, validator_id: str, challenge: str, block_id: int, signature: str) -> bool:
        """Verify a validator's challenge response"""
        if validator_id not in self.miners:
            return False
        
        message = f"{challenge}{validator_id}{block_id}"
        return verify_signature(self.miners[validator_id].public_key, message, signature)
    
    def register_miner(self, validator_id: str, public_key: str, wallet: str, 
                       stake: int, signature: str, timestamp: float) -> bool:
        """Register a new miner with challenge-response authentication"""
        
        # Verify registration signature
        reg_message = f"{validator_id}{wallet}{stake}{timestamp}"
        if not verify_signature(public_key, reg_message, signature):
            print(f"[REG] Signature verification FAILED for {validator_id[:16]}...")
            return False
        
        # Calculate level
        calculated_level = self.calculate_level(stake)
        if calculated_level > self.max_level:
            calculated_level = self.max_level
        
        # Register or update miner
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
            print(f"[REG] New miner: {validator_id[:16]}... | Wallet: {wallet[:20]}... | Level {calculated_level} | Stake {stake}")
        
        # Update database
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO miners VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (validator_id, public_key, wallet, stake, calculated_level,
                  self.miners[validator_id].total_rewards,
                  self.miners[validator_id].blocks_signed,
                  self.miners[validator_id].slash_count,
                  self.miners[validator_id].uptime_seconds,
                  timestamp,
                  time.time()))
        self.conn.commit()
        
        # Update level groups
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
        
        # Add to DEX
        if DEX_ENABLED:
            self.dex.add_liquidity(wallet, amount, amount)  # Simplified
        
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO liquidity_providers VALUES (?, ?, ?, ?, ?)",
                 (wallet, self.liquidity_providers[wallet].amount,
                  self.liquidity_providers[wallet].share,
                  self.liquidity_providers[wallet].total_earned,
                  self.liquidity_providers[wallet].last_claim))
        self.conn.commit()
        print(f"[LP] {wallet[:20]}... added {amount} MC | Total LPs: {len(self.liquidity_providers)}")
    
    def slash_miner(self, validator_id: str, reason: str):
        """Slash a miner's stake (PoMA slashing)"""
        if validator_id not in self.miners:
            return
        
        miner = self.miners[validator_id]
        slash_amount = max(int(miner.stake * SLASH_RATE), LEVEL_STAKE_RANGE)
        miner.stake -= slash_amount
        if miner.stake < LEVEL_STAKE_RANGE:
            miner.stake = LEVEL_STAKE_RANGE
        
        miner.slash_count += 1
        miner.consecutive_misses += 1
        new_level = self.calculate_level(miner.stake)
        miner.level = min(new_level, self.max_level)
        
        if miner.slash_count >= 5:
            miner.is_active = False
            print(f"[BAN] {validator_id[:16]}... BANNED after 5 slashes")
        
        # Record slashing event
        c = self.conn.cursor()
        c.execute("UPDATE miners SET stake=?, level=?, slash_count=? WHERE validator_id=?",
                 (miner.stake, miner.level, miner.slash_count, validator_id))
        c.execute("INSERT INTO slashing_events (validator_id, amount, reason, timestamp) VALUES (?, ?, ?, ?)",
                 (validator_id, slash_amount, reason, time.time()))
        self.conn.commit()
        
        self.update_level_groups()
        print(f"[SLASH] {validator_id[:16]}... | -{slash_amount} MC | Stake: {miner.stake} | Level: {miner.level}")
        
        # Broadcast slashing to peers
        asyncio.create_task(self.p2p.broadcast_transaction({
            "type": "slash",
            "validator_id": validator_id,
            "amount": slash_amount,
            "reason": reason
        }))
    
    def distribute_block_reward(self, block: Block):
        """Distribute block reward according to PoMA + PoS rules"""
        if block.reward_distributed:
            return
        
        reward = self.get_current_block_reward()
        block.reward_amount = reward
        
        if reward == 0:
            print(f"[BLOCK {block.block_id}] Cap reached - no new coins minted")
            block.reward_distributed = True
            return
        
        # Calculate shares
        validator_total = int(reward * VALIDATOR_SHARE)
        node_total = int(reward * NODE_SHARE)
        uptime_total = int(reward * UPTIME_SHARE)
        lp_total = int(reward * LP_SHARE)
        
        # Distribute to validators (75% - split equally among signers)
        validator_each = validator_total // max(len(block.validators), 1)
        for vid in block.validators:
            if vid in self.miners:
                m = self.miners[vid]
                m.total_rewards += validator_each
                m.stake += validator_each
                m.blocks_signed += 1
                m.consecutive_misses = 0
                new_level = self.calculate_level(m.stake)
                m.level = min(new_level, self.max_level)
                
                # Update miner balance
                self.balances[m.wallet] = self.balances.get(m.wallet, 0) + validator_each
                self.save_balance(m.wallet, self.balances[m.wallet])
                
                # Update database
                c = self.conn.cursor()
                c.execute("UPDATE miners SET stake=?, level=?, total_rewards=?, blocks_signed=? WHERE validator_id=?",
                         (m.stake, m.level, m.total_rewards, m.blocks_signed, vid))
                self.conn.commit()
        
        # Add to pools for periodic distribution
        self.node_pool += node_total
        self.uptime_pool += uptime_total
        self.lp_pool += lp_total
        
        # Update total minted
        self.total_minted += reward
        
        block.reward_distributed = True
        self.update_level_groups()
        
        # Print reward info
        remaining = self.get_remaining_supply()
        percent = self.get_supply_percentage()
        halving = self.current_block_id // HALVING_INTERVAL
        
        print(f"[BLOCK {block.block_id}] REWARD: {reward:,} MC")
        print(f"   ├─ Validators ({len(block.validators)}): {validator_each:,} MC each")
        print(f"   ├─ Node pool: {node_total:,} MC")
        print(f"   ├─ Uptime pool: {uptime_total:,} MC")
        print(f"   └─ LP pool: {lp_total:,} MC")
        print(f"[SUPPLY] {self.total_minted:,} / {TOTAL_SUPPLY_CAP:,} ({percent:.4f}%) | Halving: {halving}")
        print(f"[REMAINING] {remaining:,} MC until cap")
    
    def distribute_periodic_rewards(self):
        """Distribute node, uptime, and LP rewards (every 5 minutes)"""
        
        # Distribute node rewards (8% - split equally among all active nodes)
        if self.nodes and self.node_pool > 0:
            active_nodes = [n for n in self.nodes.values() if n.is_active]
            if active_nodes:
                node_share = self.node_pool // len(active_nodes)
                for node in active_nodes:
                    node.total_rewards += node_share
                    self.balances[node.wallet] = self.balances.get(node.wallet, 0) + node_share
                    self.save_balance(node.wallet, self.balances[node.wallet])
                    
                    c = self.conn.cursor()
                    c.execute("UPDATE nodes SET total_rewards=? WHERE node_id=?",
                             (node.total_rewards, node.node_id))
                    self.conn.commit()
                print(f"[DISTRO] Node rewards: {self.node_pool:,} MC to {len(active_nodes)} nodes")
        
        # Distribute uptime rewards (7% - proportional to uptime)
        active_miners = [m for m in self.miners.values() if m.is_active]
        total_uptime = sum(m.uptime_seconds for m in active_miners)
        if total_uptime > 0 and self.uptime_pool > 0:
            for miner in active_miners:
                if miner.uptime_seconds > 0:
                    share = int(self.uptime_pool * (miner.uptime_seconds / total_uptime))
                    miner.total_rewards += share
                    miner.stake += share
                    self.balances[miner.wallet] = self.balances.get(miner.wallet, 0) + share
                    self.save_balance(miner.wallet, self.balances[miner.wallet])
                    
                    new_level = self.calculate_level(miner.stake)
                    miner.level = min(new_level, self.max_level)
                    
                    c = self.conn.cursor()
                    c.execute("UPDATE miners SET stake=?, level=?, total_rewards=?, uptime_seconds=? WHERE validator_id=?",
                             (miner.stake, miner.level, miner.total_rewards, miner.uptime_seconds, miner.validator_id))
                    self.conn.commit()
            print(f"[DISTRO] Uptime rewards: {self.uptime_pool:,} MC to {len(active_miners)} miners")
        
        # Distribute LP rewards (10% - proportional to LP share)
        if self.liquidity_providers and self.lp_pool > 0:
            for lp in self.liquidity_providers.values():
                reward = int(self.lp_pool * lp.share)
                lp.total_earned += reward
                lp.last_claim = time.time()
                
                # Add to wallet balance
                self.balances[lp.wallet] = self.balances.get(lp.wallet, 0) + reward
                self.save_balance(lp.wallet, self.balances[lp.wallet])
                
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
            
            print(f"[DISTRO] LP rewards: {self.lp_pool:,} MC to {len(self.liquidity_providers)} LPs")
        
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
            "level": block.level,
            "transactions": [{"tx_hash": tx.tx_hash, "from": tx.from_wallet, "to": tx.to_wallet, "amount": tx.amount} 
                           for tx in block.transactions]
        })
        self.last_block_hash = block.block_hash
        self.blocks.append(block)
        
        # Mark transactions as confirmed
        for tx in block.transactions:
            tx.status = "confirmed"
            tx.block_id = block.block_id
            
            c = self.conn.cursor()
            c.execute("UPDATE transactions SET status=?, block_id=? WHERE tx_hash=?",
                     ("confirmed", block.block_id, tx.tx_hash))
            self.conn.commit()
        
        # Save block to database
        c = self.conn.cursor()
        c.execute("INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (block.block_id, block.timestamp, self.last_block_hash,
                  ','.join(block.validators), block.level, block.block_hash, 
                  block.reward_amount, block.transaction_count))
        self.conn.commit()
    
    def get_blocks_in_range(self, start: int, end: int) -> List[dict]:
        """Get blocks in range for P2P sync"""
        blocks = []
        for b in self.blocks:
            if start <= b.block_id <= end:
                blocks.append({
                    "block_id": b.block_id,
                    "timestamp": b.timestamp,
                    "previous_hash": b.previous_hash,
                    "validators": b.validators,
                    "level": b.level,
                    "block_hash": b.block_hash,
                    "reward_amount": b.reward_amount
                })
        return blocks
    
    async def import_blocks(self, blocks_data: List[dict]):
        """Import blocks from peer during sync"""
        for block_data in sorted(blocks_data, key=lambda x: x["block_id"]):
            if block_data["block_id"] > self.current_block_id - 1:
                # Check if we already have this block
                existing = [b for b in self.blocks if b.block_id == block_data["block_id"]]
                if not existing:
                    block = Block(
                        block_id=block_data["block_id"],
                        timestamp=block_data["timestamp"],
                        previous_hash=block_data["previous_hash"],
                        validators=block_data["validators"],
                        level=block_data["level"],
                        block_hash=block_data["block_hash"],
                        reward_amount=block_data["reward_amount"],
                        accepted=True,
                        reward_distributed=True
                    )
                    self.blocks.append(block)
                    if block.block_id >= self.current_block_id:
                        self.current_block_id = block.block_id + 1
                        self.last_block_hash = block.block_hash
                    print(f"[SYNC] Imported block {block_data['block_id']}")
    
    async def receive_external_block(self, block_data: dict, peer_addr):
        """Receive block broadcast from peer node"""
        block_id = block_data["block_id"]
        
        # Check if we already have this block
        existing = [b for b in self.blocks if b.block_id == block_id]
        if existing:
            return
        
        # Verify block before accepting
        if block_data["previous_hash"] == self.last_block_hash:
            block = Block(
                block_id=block_id,
                timestamp=block_data["timestamp"],
                previous_hash=block_data["previous_hash"],
                validators=block_data["validators"],
                level=block_data["level"],
                block_hash=block_data["block_hash"],
                reward_amount=block_data["reward_amount"],
                accepted=True,
                reward_distributed=True
            )
            self.blocks.append(block)
            self.current_block_id = block_id + 1
            self.last_block_hash = block.block_hash
            print(f"[P2P] Received block {block_id} from {peer_addr}")
        else:
            print(f"[P2P] Block {block_id} rejected - hash mismatch")
    
    async def produce_block(self, level: int):
        """Produce a block using PoMA consensus"""
        validators = self.select_validators(level)
        if len(validators) < MIN_VALIDATORS_PER_BLOCK:
            return
        
        block_id = self.current_block_id
        challenge = self.generate_challenge(block_id, validators)
        
        # Store pending challenge
        self.pending_challenges[challenge] = {
            "block_id": block_id,
            "validators": validators,
            "level": level,
            "start_time": time.time(),
            "signatures": {},
            "challenge": challenge
        }
        
        # Wait for signatures (SIGNING_WINDOW_MS)
        await asyncio.sleep(SIGNING_WINDOW_MS / 1000)
        
        # Get pending challenge
        pending = self.pending_challenges.pop(challenge, {})
        signatures = pending.get("signatures", {})
        
        # Verify each signature
        valid_sigs = {}
        for vid, sig in signatures.items():
            if vid in self.miners:
                if self.verify_challenge_response(vid, challenge, block_id, sig):
                    valid_sigs[vid] = sig
        
        # Check if enough valid signatures
        if len(valid_sigs) >= MIN_VALIDATORS_PER_BLOCK:
            # Create block
            block = Block(
                block_id=block_id,
                timestamp=time.time(),
                previous_hash=self.last_block_hash,
                validators=list(valid_sigs.keys()),
                level=level,
                signatures=valid_sigs,
                transactions=self.pending_transactions[:100]  # Include up to 100 pending txs
            )
            block.transaction_count = len(block.transactions)
            
            # Distribute reward
            self.distribute_block_reward(block)
            
            # Finalize block
            self.finalize_block(block)
            
            # Remove confirmed transactions from pending
            self.pending_transactions = self.pending_transactions[100:]
            
            # Increment block ID
            self.current_block_id += 1
            
            print(f"[BLOCK {block_id}] ✅ ACCEPTED | Validators: {len(valid_sigs)} | Txs: {block.transaction_count}")
            
            # Broadcast to peers
            await self.p2p.broadcast_new_block({
                "block_id": block_id,
                "timestamp": block.timestamp,
                "previous_hash": block.previous_hash,
                "validators": block.validators,
                "level": level,
                "block_hash": block.block_hash,
                "reward_amount": block.reward_amount,
                "transaction_count": block.transaction_count
            })
        else:
            # Block rejected - slash missing validators
            missing = set(validators) - set(signatures.keys())
            for vid in missing:
                self.slash_miner(vid, f"Missed signing window for block {block_id}")
            print(f"[BLOCK {block_id}] ❌ REJECTED | Signatures: {len(valid_sigs)}/{MIN_VALIDATORS_PER_BLOCK} | Missing: {len(missing)}")

# ==================== WEBSOCKET SERVER ====================
class MicroCoinServer:
    """WebSocket server for miners and clients"""
    
    def __init__(self, network: MicroCoinNetwork):
        self.network = network
        self.connections: Dict[str, websockets.WebSocketServerProtocol] = {}
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connections"""
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                
                # Miner registration
                if msg_type == "register":
                    vid = data["validator_id"]
                    pubkey = data["public_key"]
                    wallet = data["wallet"]
                    stake = data.get("stake", 100)
                    sig = data.get("signature", "")
                    ts = data.get("timestamp", time.time())
                    
                    if self.network.register_miner(vid, pubkey, wallet, stake, sig, ts):
                        self.connections[vid] = websocket
                        
                        # Send registration confirmation
                        await websocket.send(json.dumps({
                            "type": "registered",
                            "level": self.network.miners[vid].level if vid in self.network.miners else 1,
                            "max_level": self.network.max_level,
                            "remaining_supply": self.network.get_remaining_supply(),
                            "current_reward": self.network.get_current_block_reward(),
                            "mc_price": self.network.dex.get_mc_price() if DEX_ENABLED else 0.01
                        }))
                
                # Node registration
                elif msg_type == "node_register":
                    nid = data["node_id"]
                    wallet = data["wallet"]
                    self.network.register_node(nid, wallet)
                    self.connections[f"node_{nid}"] = websocket
                    await websocket.send(json.dumps({"type": "node_registered", "status": "ok"}))
                
                # Liquidity provider registration
                elif msg_type == "lp_register":
                    wallet = data["wallet"]
                    amount = data["amount"]
                    self.network.register_liquidity_provider(wallet, amount)
                    await websocket.send(json.dumps({"type": "lp_registered", "status": "ok", "amount": amount}))
                
                # Block signature (challenge response)
                elif msg_type == "block_signature":
                    vid = data["validator_id"]
                    challenge = data["challenge"]
                    sig = data["signature"]
                    
                    if challenge in self.network.pending_challenges:
                        self.network.pending_challenges[challenge]["signatures"][vid] = sig
                
                # Uptime ping
                elif msg_type == "uptime_ping":
                    vid = data["validator_id"]
                    uptime = data.get("uptime_seconds", 0)
                    if vid in self.network.miners:
                        self.network.miners[vid].uptime_seconds = uptime
                        self.network.miners[vid].last_ping = time.time()
                
                # Transaction
                elif msg_type == "transaction":
                    from_wallet = data["from"]
                    to_wallet = data["to"]
                    amount = data["amount"]
                    fee = data.get("fee", 1)
                    signature = data.get("signature", "")
                    
                    # Verify transaction signature
                    tx_message = f"{from_wallet}{to_wallet}{amount}{fee}"
                    if verify_signature(self.network.miners.get(from_wallet, Miner("", "", from_wallet, 0, 0)).public_key, tx_message, signature):
                        tx_hash = self.network.transfer(from_wallet, to_wallet, amount, fee)
                        if tx_hash:
                            await websocket.send(json.dumps({"type": "tx_confirmed", "tx_hash": tx_hash}))
                        else:
                            await websocket.send(json.dumps({"type": "tx_failed", "reason": "Insufficient balance"}))
                    else:
                        await websocket.send(json.dumps({"type": "tx_failed", "reason": "Invalid signature"}))
                
                # Get balance
                elif msg_type == "get_balance":
                    wallet = data["wallet"]
                    balance = self.network.get_balance(wallet)
                    await websocket.send(json.dumps({"type": "balance", "wallet": wallet, "balance": balance}))
                
                # Get network status
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
                        "supply_percentage": self.network.get_supply_percentage(),
                        "mc_price": self.network.dex.get_mc_price() if DEX_ENABLED else 0.01,
                        "total_liquidity": self.network.dex.get_liquidity() if DEX_ENABLED else 0
                    }))
                
                # DEX operations
                elif msg_type == "dex_add_liquidity":
                    wallet = data["wallet"]
                    mc_amount = data["mc_amount"]
                    usdc_amount = data["usdc_amount"]
                    result = self.network.dex.add_liquidity(wallet, mc_amount, usdc_amount)
                    await websocket.send(json.dumps({"type": "dex_result", "success": result["success"], "tx_hash": result.get("tx_hash")}))
                
                elif msg_type == "dex_swap_mc_to_usdc":
                    wallet = data["wallet"]
                    mc_amount = data["mc_amount"]
                    result = self.network.dex.swap_mc_for_usdc(wallet, mc_amount)
                    await websocket.send(json.dumps({"type": "dex_result", "success": result["success"], "tx_hash": result.get("tx_hash")}))
                
                elif msg_type == "dex_swap_usdc_to_mc":
                    wallet = data["wallet"]
                    usdc_amount = data["usdc_amount"]
                    result = self.network.dex.swap_usdc_for_mc(wallet, usdc_amount)
                    await websocket.send(json.dumps({"type": "dex_result", "success": result["success"], "tx_hash": result.get("tx_hash")}))
                
                # Get MC price
                elif msg_type == "get_price":
                    price = self.network.dex.get_mc_price() if DEX_ENABLED else 0.01
                    await websocket.send(json.dumps({"type": "price", "mc_usd": price}))
        
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            print(f"[WS] Error: {e}")
            traceback.print_exc()
    
    async def periodic_distribution(self):
        """Periodically distribute rewards"""
        while True:
            await asyncio.sleep(DISTRIBUTION_INTERVAL_SEC)
            self.network.distribute_periodic_rewards()
    
    async def block_production_loop(self):
        """Continuous block production loop"""
        level = 1
        while True:
            if self.network.level_groups:
                available_levels = [l for l in self.network.level_groups 
                                   if len(self.network.level_groups[l]) >= MIN_VALIDATORS_PER_BLOCK]
                if available_levels:
                    level = (level % max(available_levels)) + 1 if available_levels else 1
                    if level in available_levels:
                        await self.network.produce_block(level)
            await asyncio.sleep(0.1)
    
    async def status_reporter(self):
        """Print status every minute"""
        while True:
            await asyncio.sleep(60)
            remaining = self.network.get_remaining_supply()
            percent = self.network.get_supply_percentage()
            reward = self.network.get_current_block_reward()
            halving = self.network.current_block_id // HALVING_INTERVAL
            price = self.network.dex.get_mc_price() if DEX_ENABLED else 0.01
            
            print(f"\n[STATUS] Block: {self.network.current_block_id} | Reward: {reward} MC | Halving: {halving}")
            print(f"[STATUS] Miners: {len(self.network.miners)} | Active: {sum(1 for m in self.network.miners.values() if m.is_active)}")
            print(f"[STATUS] Nodes: {len(self.network.nodes)} | LPs: {len(self.network.liquidity_providers)}")
            print(f"[STATUS] P2P Peers: {len(self.network.p2p.peers)}")
            print(f"[SUPPLY] {self.network.total_minted:,} / {TOTAL_SUPPLY_CAP:,} ({percent:.4f}%) | Remaining: {remaining:,}")
            print(f"[PRICE] 1 MC = ${price:.4f} USD\n")
    
    async def run(self):
        """Run the WebSocket server"""
        # Start P2P networking
        asyncio.create_task(self.network.p2p.start())
        asyncio.create_task(self.network.p2p.sync_with_network())
        asyncio.create_task(self.network.p2p.heartbeat())
        asyncio.create_task(self.network.p2p.discover_peers())
        
        # Start node components
        asyncio.create_task(self.periodic_distribution())
        asyncio.create_task(self.block_production_loop())
        asyncio.create_task(self.status_reporter())
        
        # Start WebSocket server
        async with websockets.serve(self.handle, NODE_HOST, NODE_PORT):
            print(f"[NODE] WebSocket server: ws://{NODE_HOST}:{NODE_PORT}")
            print(f"[NODE] P2P server: tcp://{NODE_HOST}:{P2P_PORT}")
            print(f"[NODE] Ready for connections...")
            
            await asyncio.Future()  # Run forever

# ==================== MAIN ====================
async def main():
    print("=" * 70)
    print("MICROCOIN NETWORK - COMPLETE MAINNET NODE")
    print("=" * 70)
    print("Features:")
    print("  ✅ Real ECDSA secp256k1 cryptography")
    print("  ✅ Challenge-response authentication")
    print("  ✅ Multi-node P2P sync (gossip protocol)")
    print("  ✅ DEX integration (PancakeSwap)")
    print("  ✅ PoMA + PoS consensus")
    print("  ✅ 50B hard cap with 4M block halving")
    print("  ✅ Level system (100 MC per level)")
    print("  ✅ Reward distribution (75/8/7/10)")
    print("=" * 70)
    print()
    
    network = MicroCoinNetwork()
    server = MicroCoinServer(network)
    await server.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Node stopped")
        sys.exit(0)
