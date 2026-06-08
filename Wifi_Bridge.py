# wifi_bridge_full.py
# COMPLETE WIFI BRIDGE FOR MINERS BEHIND NAT
# RUN ON A COMPUTER IN THE SAME NETWORK AS MINERS
# For only arduinos, and PC as the wifi bridge
import asyncio
import json
import websockets
import time
from collections import defaultdict

# ==================== CONFIGURATION ====================
BRIDGE_HOST = "0.0.0.0"
BRIDGE_PORT = 8081
NODE_WS_URL = "ws://your_node_ip:8080"  # Change to your node IP

# ==================== BRIDGE CLASS ====================
class WiFiBridge:
    def __init__(self):
        self.miner_connections: dict = {}
        self.node_connection = None
        self.pending_messages: dict = defaultdict(list)
        self.miner_uptime: dict = {}
        self.last_heartbeat: dict = {}
        self.stats = {
            "messages_forwarded": 0,
            "miners_connected": 0,
            "last_reconnect": 0
        }
    
    async def forward_to_node(self, message: dict):
        """Forward message from miner to main node"""
        if self.node_connection and self.node_connection.open:
            try:
                await self.node_connection.send(json.dumps(message))
                self.stats["messages_forwarded"] += 1
                return True
            except:
                self.node_connection = None
                return False
        else:
            # Store for when node reconnects
            miner_id = message.get("validator_id")
            if miner_id:
                self.pending_messages[miner_id].append(message)
            return False
    
    async def forward_to_miner(self, miner_id: str, message: dict):
        """Forward message from node to specific miner"""
        if miner_id in self.miner_connections:
            try:
                await self.miner_connections[miner_id].send(json.dumps(message))
                return True
            except:
                # Remove dead connection
                del self.miner_connections[miner_id]
        return False
    
    async def broadcast_to_miners(self, message: dict):
        """Broadcast message to all connected miners"""
        dead_miners = []
        for miner_id, ws in self.miner_connections.items():
            try:
                await ws.send(json.dumps(message))
            except:
                dead_miners.append(miner_id)
        
        for miner_id in dead_miners:
            del self.miner_connections[miner_id]
    
    async def handle_miner(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle incoming miner connections"""
        miner_id = None
        connected_at = time.time()
        
        try:
            async for message in websocket:
                data = json.loads(message)
                
                if data.get("type") == "register":
                    miner_id = data["validator_id"]
                    self.miner_connections[miner_id] = websocket
                    self.miner_uptime[miner_id] = connected_at
                    self.last_heartbeat[miner_id] = time.time()
                    self.stats["miners_connected"] = len(self.miner_connections)
                    print(f"[BRIDGE] Miner {miner_id[:16]}... connected | Total: {self.stats['miners_connected']}")
                    
                    # Send any pending messages
                    if miner_id in self.pending_messages:
                        for pending in self.pending_messages[miner_id]:
                            await self.forward_to_miner(miner_id, pending)
                        del self.pending_messages[miner_id]
                
                elif data.get("type") == "uptime_ping":
                    if miner_id:
                        self.last_heartbeat[miner_id] = time.time()
                
                # Forward all messages to node
                await self.forward_to_node(data)
        
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if miner_id and miner_id in self.miner_connections:
                del self.miner_connections[miner_id]
                self.stats["miners_connected"] = len(self.miner_connections)
                print(f"[BRIDGE] Miner {miner_id[:16]}... disconnected | Total: {self.stats['miners_connected']}")
    
    async def heartbeat_checker(self):
        """Check for stale miner connections"""
        while True:
            current_time = time.time()
            stale_miners = []
            
            for miner_id, last_hb in self.last_heartbeat.items():
                if current_time - last_hb > 60:  # 60 seconds timeout
                    stale_miners.append(miner_id)
            
            for miner_id in stale_miners:
                if miner_id in self.miner_connections:
                    del self.miner_connections[miner_id]
                if miner_id in self.last_heartbeat:
                    del self.last_heartbeat[miner_id]
                print(f"[BRIDGE] Miner {miner_id[:16]}... timed out")
            
            self.stats["miners_connected"] = len(self.miner_connections)
            await asyncio.sleep(30)
    
    async def handle_node(self):
        """Handle connection to main node with auto-reconnect"""
        while True:
            try:
                async with websockets.connect(NODE_WS_URL) as websocket:
                    self.node_connection = websocket
                    print(f"[BRIDGE] Connected to node at {NODE_WS_URL}")
                    self.stats["last_reconnect"] = time.time()
                    
                    # Register as bridge
                    await websocket.send(json.dumps({
                        "type": "bridge_register",
                        "bridge_id": f"bridge_{id(self)}",
                        "timestamp": time.time()
                    }))
                    
                    async for message in websocket:
                        data = json.loads(message)
                        
                        # Forward to specific miner or broadcast
                        miner_id = data.get("validator_id")
                        if miner_id and miner_id in self.miner_connections:
                            await self.forward_to_miner(miner_id, data)
                        else:
                            # Broadcast to all miners
                            await self.broadcast_to_miners(data)
            
            except Exception as e:
                print(f"[BRIDGE] Node connection failed: {e}. Reconnecting in 5s...")
                self.node_connection = None
                await asyncio.sleep(5)
    
    async def status_reporter(self):
        """Report bridge status periodically"""
        while True:
            await asyncio.sleep(60)
            print(f"[BRIDGE STATUS] Miners: {self.stats['miners_connected']} | "
                  f"Messages: {self.stats['messages_forwarded']} | "
                  f"Node: {'Connected' if self.node_connection else 'Disconnected'}")
    
    async def run(self):
        """Run both miner and node handlers"""
        miner_server = await websockets.serve(self.handle_miner, BRIDGE_HOST, BRIDGE_PORT)
        
        print("=" * 50)
        print("MICROCOIN WIFI BRIDGE")
        print("=" * 50)
        print(f"Listening for miners on ws://{BRIDGE_HOST}:{BRIDGE_PORT}")
        print(f"Forwarding to node at {NODE_WS_URL}")
        print("=" * 50 + "\n")
        
        await asyncio.gather(
            miner_server.wait_closed(),
            self.handle_node(),
            self.heartbeat_checker(),
            self.status_reporter()
        )

# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    bridge = WiFiBridge()
    asyncio.run(bridge.run())
