// i2c_master_esp32.ino
// ESP32/ESP8266 as I2C Master for MicroCoin mining
// Communicates with ATtiny85/Arduino slaves

#include <Wire.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

// ==================== I2C CONFIGURATION ====================
#define I2C_SDA 21  // ESP32 SDA pin (D2 on ESP8266)
#define I2C_SCL 22  // ESP32 SCL pin (D1 on ESP8266)
#define MASTER_ADDRESS 0x08
#define MAX_SLAVES 10

// Slave addresses (ATtiny85 or Arduino)
const uint8_t slaveAddresses[] = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12};
int slaveCount = 0;
bool slavePresent[10] = {false};

// ==================== WIFI CONFIGURATION ====================
const char* WIFI_SSID = "your_wifi";
const char* WIFI_PASSWORD = "your_password";
const char* NODE_IP = "192.168.1.100";
const int NODE_PORT = 8080;

// ==================== MICROCOIN CONFIGURATION ====================
const char* WALLET_ADDRESS = "MC_YourWalletAddress";
uint32_t currentStake = 100;
int currentLevel = 1;

// ==================== GLOBALS ====================
WebSocketsClient webSocket;
uint32_t lastI2CScan = 0;
uint32_t lastUptimePing = 0;
uint32_t uptimeCounter = 0;
bool isValidator = false;
uint32_t lastChallengeTime = 0;
String currentChallenge;
uint32_t currentBlockId = 0;
uint32_t totalRewards = 0;
uint32_t blocksSigned = 0;

// ==================== I2C FUNCTIONS ====================
void initI2C() {
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(100000);  // 100kHz standard speed
    Serial.println("[I2C] Master initialized");
}

void scanI2CSlaves() {
    Serial.println("[I2C] Scanning for slaves...");
    slaveCount = 0;
    
    for (int i = 0; i < MAX_SLAVES; i++) {
        Wire.beginTransmission(slaveAddresses[i]);
        byte error = Wire.endTransmission();
        
        if (error == 0) {
            slavePresent[i] = true;
            slaveCount++;
            Serial.printf("[I2C] Slave found at address 0x%02X\n", slaveAddresses[i]);
        } else {
            slavePresent[i] = false;
        }
    }
    
    Serial.printf("[I2C] Total slaves: %d\n", slaveCount);
}

void sendToSlave(uint8_t address, const char* command, uint32_t value) {
    Wire.beginTransmission(address);
    Wire.write((uint8_t*)command, strlen(command));
    Wire.write((uint8_t*)&value, sizeof(value));
    Wire.endTransmission();
}

String readFromSlave(uint8_t address) {
    String response = "";
    Wire.requestFrom(address, 32);
    while (Wire.available()) {
        response += (char)Wire.read();
    }
    return response;
}

void sendChallengeToSlaves(const char* challenge, uint32_t blockId) {
    for (int i = 0; i < MAX_SLAVES; i++) {
        if (slavePresent[i]) {
            sendToSlave(slaveAddresses[i], "CHLG", blockId);
            delay(5);
            sendToSlave(slaveAddresses[i], challenge, 0);
        }
    }
    Serial.printf("[I2C] Challenge sent to %d slaves\n", slaveCount);
}

void getSignaturesFromSlaves() {
    for (int i = 0; i < MAX_SLAVES; i++) {
        if (slavePresent[i]) {
            String sig = readFromSlave(slaveAddresses[i]);
            if (sig.length() > 0) {
                Serial.printf("[I2C] Slave 0x%02X signature: %s\n", slaveAddresses[i], sig.c_str());
                // Send signature to node
                sendSignatureToNode(sig.c_str());
            }
        }
    }
}

// ==================== NETWORK FUNCTIONS ====================
void calculateLevel() {
    currentLevel = ((currentStake - 1) / 100) + 1;
    if (currentLevel < 1) currentLevel = 1;
    if (currentLevel > 100) currentLevel = 100;
}

void sendRegistration() {
    StaticJsonDocument<256> doc;
    doc["type"] = "register";
    doc["validator_id"] = WiFi.macAddress().c_str();
    doc["wallet"] = WALLET_ADDRESS;
    doc["stake"] = currentStake;
    doc["level"] = currentLevel;
    doc["rewards"] = totalRewards;
    doc["blocks"] = blocksSigned;
    doc["has_slaves"] = slaveCount;
    
    String output;
    serializeJson(doc, output);
    webSocket.sendTXT(output);
    Serial.println("[NET] Registration sent");
}

void sendUptimePing() {
    StaticJsonDocument<128> doc;
    doc["type"] = "uptime_ping";
    doc["validator_id"] = WiFi.macAddress().c_str();
    doc["uptime_seconds"] = uptimeCounter;
    doc["stake"] = currentStake;
    doc["level"] = currentLevel;
    doc["slaves"] = slaveCount;
    
    String output;
    serializeJson(doc, output);
    webSocket.sendTXT(output);
}

void sendSignatureToNode(const char* signature) {
    StaticJsonDocument<256> doc;
    doc["type"] = "block_signature";
    doc["validator_id"] = WiFi.macAddress().c_str();
    doc["challenge"] = currentChallenge;
    doc["signature"] = signature;
    doc["level"] = currentLevel;
    doc["stake"] = currentStake;
    doc["block_id"] = currentBlockId;
    
    String output;
    serializeJson(doc, output);
    webSocket.sendTXT(output);
    Serial.println("[NET] Signature sent to node");
}

void addReward(uint32_t reward) {
    totalRewards += reward;
    currentStake += reward;
    blocksSigned++;
    calculateLevel();
    Serial.printf("[REWARD] +%lu MC | Total: %lu | Level: %d\n", reward, totalRewards, currentLevel);
}

// ==================== WEBSOCKET EVENTS ====================
void webSocketEvent(WStype_t type, uint8_t* payload, size_t len) {
    switch(type) {
        case WStype_CONNECTED:
            Serial.println("[WS] Connected to node");
            sendRegistration();
            break;
            
        case WStype_TEXT: {
            StaticJsonDocument<512> doc;
            deserializeJson(doc, payload);
            String msgType = doc["type"];
            
            if (msgType == "challenge") {
                currentChallenge = doc["challenge"].as<String>();
                currentBlockId = doc["block_id"];
                lastChallengeTime = millis();
                isValidator = true;
                
                // Send challenge to I2C slaves
                sendChallengeToSlaves(currentChallenge.c_str(), currentBlockId);
                // Wait for slaves to sign
                delay(50);
                getSignaturesFromSlaves();
            }
            else if (msgType == "block_accepted") {
                addReward(doc["reward"]);
                isValidator = false;
            }
            else if (msgType == "block_rejected") {
                isValidator = false;
            }
            else if (msgType == "slash") {
                uint32_t slashAmount = currentStake * 0.1;
                currentStake -= slashAmount;
                calculateLevel();
                Serial.printf("[SLASH] Lost %lu MC, new stake: %lu\n", slashAmount, currentStake);
                isValidator = false;
            }
            break;
        }
        default:
            break;
    }
}

// ==================== SETUP ====================
void setup() {
    Serial.begin(115200);
    delay(1000);
    
    Serial.println("\n=== MICROCOIN I2C MASTER (ESP32) ===");
    
    // Initialize I2C
    initI2C();
    scanI2CSlaves();
    
    // Connect WiFi
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nWiFi connected");
    
    // Setup WebSocket
    webSocket.begin(NODE_IP, NODE_PORT, "/");
    webSocket.onEvent(webSocketEvent);
    webSocket.setReconnectInterval(5000);
    
    calculateLevel();
    lastI2CScan = millis();
    lastUptimePing = millis();
    
    Serial.printf("Master ready | Level: %d | Stake: %lu | Slaves: %d\n", currentLevel, currentStake, slaveCount);
}

// ==================== LOOP ====================
void loop() {
    webSocket.loop();
    
    // Scan I2C every 60 seconds
    if (millis() - lastI2CScan > 60000) {
        scanI2CSlaves();
        lastI2CScan = millis();
    }
    
    // Send uptime ping every 30 seconds
    if (millis() - lastUptimePing > 30000) {
        uptimeCounter++;
        sendUptimePing();
        lastUptimePing = millis();
    }
    
    // Check signing timeout
    if (isValidator && (millis() - lastChallengeTime > 2500)) {
        Serial.println("[TIMEOUT] Missed signing window");
        isValidator = false;
    }
    
    delay(10);
}
