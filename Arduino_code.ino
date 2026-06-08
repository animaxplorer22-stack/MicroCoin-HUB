// microcoin_arduino_bridge.ino
// COMPLETE ARDUINO UNO CODE WITH ETHERNET SHIELD
// Connects to WiFi Bridge (not directly to node)

#include <SPI.h>
#include <Ethernet.h>
#include <ArduinoJson.h>
#include <EEPROM.h>

// ==================== USER CONFIGURATION ====================
// EDIT THESE BEFORE FLASHING

// Ethernet shield MAC address
byte mac[] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0xED};

// WiFi Bridge IP and port (NOT the main node)
IPAddress bridgeIP(192, 168, 1, 200);  // IP of computer running wifi_bridge.py
int bridgePort = 8081;

// Static IP for this Arduino (change to match your network)
IPAddress localIP(192, 168, 1, 150);
IPAddress dns(8, 8, 8, 8);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);

// Your MicroCoin wallet address (generate from wallet.html)
const char* WALLET_ADDRESS = "MC_YourWalletAddressHere";

// Initial stake (Level 1 = 100 MC)
uint32_t INITIAL_STAKE = 100;

// ==================== CONSTANTS ====================
#define BLOCK_REWARD 6000
#define LEVEL_STAKE_RANGE 100
#define SIGNING_WINDOW_MS 2500
#define SLASH_RATE 0.10
#define VALIDATOR_SHARE 0.75
#define NODE_SHARE 0.08
#define UPTIME_SHARE 0.07
#define LP_SHARE 0.10

// EEPROM addresses
#define EEPROM_STAKE_ADDR 0
#define EEPROM_REWARDS_ADDR 4
#define EEPROM_BLOCKS_ADDR 8
#define EEPROM_UPTIME_ADDR 12
#define EEPROM_CHECKSUM_ADDR 16

// ==================== GLOBAL VARIABLES ====================
EthernetClient client;

uint32_t currentStake;
uint32_t totalRewards;
uint32_t totalBlocksSigned;
uint32_t uptimeSeconds;
uint32_t lastUptimePing;
uint32_t lastChallengeTime;
uint32_t lastReconnect;
uint32_t uptimeCounter;
uint32_t consecutiveMisses;

char validatorID[18];
char currentChallenge[65];
bool isValidator = false;
bool isRegistered = false;
uint32_t currentBlockId = 0;
int currentLevel = 1;

// Response buffer
char responseBuffer[1024];
uint16_t responseIndex = 0;

// ==================== HELPER FUNCTIONS ====================
void generateValidatorID() {
    uint8_t mac[6];
    // Use Ethernet MAC as ID
    Ethernet.MACAddress(mac);
    snprintf(validatorID, sizeof(validatorID), "%02X%02X%02X%02X%02X%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void calculateLevel() {
    currentLevel = ((currentStake - 1) / LEVEL_STAKE_RANGE) + 1;
    if (currentLevel < 1) currentLevel = 1;
    if (currentLevel > 100) currentLevel = 100;
}

uint32_t computeChecksum() {
    uint32_t sum = currentStake + totalRewards + totalBlocksSigned + uptimeSeconds;
    return sum ^ 0x5A5A5A5A;
}

void saveToEEPROM() {
    EEPROM.begin(512);
    EEPROM.put(EEPROM_STAKE_ADDR, currentStake);
    EEPROM.put(EEPROM_REWARDS_ADDR, totalRewards);
    EEPROM.put(EEPROM_BLOCKS_ADDR, totalBlocksSigned);
    EEPROM.put(EEPROM_UPTIME_ADDR, uptimeSeconds);
    uint32_t checksum = computeChecksum();
    EEPROM.put(EEPROM_CHECKSUM_ADDR, checksum);
    EEPROM.commit();
    EEPROM.end();
}

void loadFromEEPROM() {
    EEPROM.begin(512);
    EEPROM.get(EEPROM_STAKE_ADDR, currentStake);
    EEPROM.get(EEPROM_REWARDS_ADDR, totalRewards);
    EEPROM.get(EEPROM_BLOCKS_ADDR, totalBlocksSigned);
    EEPROM.get(EEPROM_UPTIME_ADDR, uptimeSeconds);
    
    uint32_t storedChecksum;
    EEPROM.get(EEPROM_CHECKSUM_ADDR, storedChecksum);
    EEPROM.end();
    
    uint32_t calculatedChecksum = computeChecksum();
    
    if (currentStake < 100 || currentStake > 1000000 || storedChecksum != calculatedChecksum) {
        currentStake = INITIAL_STAKE;
        totalRewards = 0;
        totalBlocksSigned = 0;
        uptimeSeconds = 0;
        consecutiveMisses = 0;
        saveToEEPROM();
        Serial.println("[EEPROM] Reset to defaults");
    }
    
    calculateLevel();
}

void handleSlashing() {
    uint32_t slashAmount = (uint32_t)(currentStake * SLASH_RATE);
    if (slashAmount < LEVEL_STAKE_RANGE) slashAmount = LEVEL_STAKE_RANGE;
    if (slashAmount > currentStake) slashAmount = currentStake;
    
    currentStake -= slashAmount;
    if (currentStake < LEVEL_STAKE_RANGE) currentStake = LEVEL_STAKE_RANGE;
    
    calculateLevel();
    saveToEEPROM();
    consecutiveMisses++;
    
    Serial.print("[SLASH] -");
    Serial.print(slashAmount);
    Serial.print(" MC | New stake: ");
    Serial.print(currentStake);
    Serial.print(" | Level: ");
    Serial.println(currentLevel);
}

void addReward(uint32_t rewardAmount) {
    totalRewards += rewardAmount;
    currentStake += rewardAmount;
    totalBlocksSigned++;
    consecutiveMisses = 0;
    calculateLevel();
    saveToEEPROM();
    
    Serial.print("[REWARD] +");
    Serial.print(rewardAmount);
    Serial.print(" MC | Total: ");
    Serial.print(totalRewards);
    Serial.print(" | Stake: ");
    Serial.print(currentStake);
    Serial.print(" | Level: ");
    Serial.print(currentLevel);
    Serial.print(" | Blocks: ");
    Serial.println(totalBlocksSigned);
}

// ==================== NETWORK COMMUNICATION ====================
bool connectToBridge() {
    if (client.connected()) return true;
    
    Serial.println("[NET] Connecting to WiFi bridge...");
    if (client.connect(bridgeIP, bridgePort)) {
        Serial.println("[NET] Connected to bridge");
        return true;
    } else {
        Serial.println("[NET] Bridge connection failed");
        return false;
    }
}

void disconnectFromBridge() {
    client.stop();
    isRegistered = false;
    Serial.println("[NET] Disconnected from bridge");
}

void sendToBridge(const char* jsonString) {
    if (!connectToBridge()) return;
    
    client.print("POST / HTTP/1.1\r\n");
    client.print("Host: ");
    client.print(bridgeIP);
    client.print("\r\n");
    client.print("Content-Type: application/json\r\n");
    client.print("Content-Length: ");
    client.print(strlen(jsonString));
    client.print("\r\n");
    client.print("Connection: close\r\n");
    client.print("\r\n");
    client.print(jsonString);
}

void sendRegister() {
    StaticJsonDocument<512> doc;
    doc["type"] = "register";
    doc["validator_id"] = validatorID;
    doc["wallet"] = WALLET_ADDRESS;
    doc["stake"] = currentStake;
    doc["level"] = currentLevel;
    doc["rewards"] = totalRewards;
    doc["blocks"] = totalBlocksSigned;
    doc["uptime"] = uptimeSeconds;
    doc["timestamp"] = millis() / 1000;
    
    String output;
    serializeJson(doc, output);
    sendToBridge(output.c_str());
    Serial.println("[REG] Registration sent to bridge");
}

void sendUptimePing() {
    StaticJsonDocument<256> doc;
    doc["type"] = "uptime_ping";
    doc["validator_id"] = validatorID;
    doc["uptime_seconds"] = uptimeCounter;
    doc["stake"] = currentStake;
    doc["level"] = currentLevel;
    doc["timestamp"] = millis() / 1000;
    
    String output;
    serializeJson(doc, output);
    sendToBridge(output.c_str());
}

void sendBlockSignature() {
    StaticJsonDocument<384> doc;
    doc["type"] = "block_signature";
    doc["validator_id"] = validatorID;
    doc["challenge"] = currentChallenge;
    doc["signature"] = "sig_arduino_";
    doc["signature"] += String(millis());
    doc["level"] = currentLevel;
    doc["stake"] = currentStake;
    doc["block_id"] = currentBlockId;
    doc["timestamp"] = millis() / 1000;
    
    String output;
    serializeJson(doc, output);
    sendToBridge(output.c_str());
    Serial.println("[SIGN] Block signature sent");
}

// ==================== RESPONSE PARSING ====================
void parseResponse(const String& response) {
    // Find JSON body after HTTP headers
    int bodyStart = response.indexOf("\r\n\r\n");
    if (bodyStart == -1) return;
    
    String jsonBody = response.substring(bodyStart + 4);
    
    StaticJsonDocument<1024> doc;
    DeserializationError error = deserializeJson(doc, jsonBody);
    if (error) return;
    
    const char* type = doc["type"];
    
    if (strcmp(type, "registered") == 0) {
        isRegistered = true;
        int nodeLevel = doc["level"];
        uint32_t nodeStake = doc["stake"];
        Serial.print("[REGISTERED] Level: ");
        Serial.print(nodeLevel);
        Serial.print(", Stake: ");
        Serial.println(nodeStake);
    }
    else if (strcmp(type, "challenge") == 0) {
        const char* challenge = doc["challenge"];
        if (challenge) {
            strncpy(currentChallenge, challenge, 64);
            currentChallenge[64] = '\0';
            currentBlockId = doc["block_id"];
            lastChallengeTime = millis();
            isValidator = true;
            sendBlockSignature();
            Serial.println("[CHALLENGE] Received, signing...");
        }
    }
    else if (strcmp(type, "block_accepted") == 0) {
        uint32_t reward = doc["reward"];
        addReward(reward);
        isValidator = false;
        Serial.print("[BLOCK ACCEPTED] Block ");
        Serial.println(doc["block_id"].as<uint32_t>());
    }
    else if (strcmp(type, "block_rejected") == 0) {
        const char* reason = doc["reason"];
        Serial.print("[BLOCK REJECTED] ");
        Serial.println(reason);
        isValidator = false;
    }
    else if (strcmp(type, "slash") == 0) {
        handleSlashing();
        isValidator = false;
    }
    else if (strcmp(type, "level_update") == 0) {
        uint32_t newStake = doc["stake"];
        if (newStake != currentStake) {
            currentStake = newStake;
            calculateLevel();
            saveToEEPROM();
            Serial.print("[LEVEL UPDATE] New stake: ");
            Serial.print(currentStake);
            Serial.print(", Level: ");
            Serial.println(currentLevel);
        }
    }
    else if (strcmp(type, "reward_distribution") == 0) {
        uint32_t uptimeReward = doc["uptime_reward"];
        if (uptimeReward > 0) {
            addReward(uptimeReward);
            Serial.print("[UPTIME REWARD] +");
            Serial.println(uptimeReward);
        }
    }
}

void readResponses() {
    if (!client.available()) return;
    
    while (client.available()) {
        char c = client.read();
        if (responseIndex < sizeof(responseBuffer) - 1) {
            responseBuffer[responseIndex++] = c;
        }
        if (c == '\n' && responseIndex > 0) {
            String line(responseBuffer, responseIndex);
            responseIndex = 0;
            
            if (line.startsWith("HTTP/") || line.startsWith("{") || line.startsWith("POST")) {
                // Accumulate until we have full response
                static String fullResponse = "";
                fullResponse += line;
                if (line.length() == 1 && line[0] == '\n') {
                    parseResponse(fullResponse);
                    fullResponse = "";
                }
            }
        }
    }
}

// ==================== SETUP ====================
void setup() {
    Serial.begin(115200);
    delay(1000);
    
    Serial.println("\n=== MICROCOIN ARDUINO MINER ===");
    Serial.println("Connecting via WiFi Bridge");
    
    // Load saved state
    loadFromEEPROM();
    generateValidatorID();
    calculateLevel();
    
    Serial.print("Validator ID: ");
    Serial.println(validatorID);
    Serial.print("Wallet: ");
    Serial.println(WALLET_ADDRESS);
    Serial.print("Initial stake: ");
    Serial.print(currentStake);
    Serial.print(" MC, Level: ");
    Serial.println(currentLevel);
    Serial.print("Total rewards: ");
    Serial.print(totalRewards);
    Serial.println(" MC");
    
    // Configure Ethernet
    Ethernet.begin(mac, localIP, dns, gateway, subnet);
    Serial.print("Local IP: ");
    Serial.println(Ethernet.localIP());
    Serial.print("Bridge IP: ");
    Serial.println(bridgeIP);
    
    // Register with bridge
    delay(1000);
    sendRegister();
    
    // Initialize timers
    lastUptimePing = millis();
    lastReconnect = millis();
    uptimeCounter = 0;
    isValidator = false;
    responseIndex = 0;
    
    Serial.println("\n[READY] Waiting for challenges...\n");
}

// ==================== MAIN LOOP ====================
void loop() {
    // Read incoming responses
    readResponses();
    
    // Periodic reconnection
    if (!isRegistered && (millis() - lastReconnect > 30000)) {
        if (client.connected()) {
            sendRegister();
        } else {
            connectToBridge();
            if (client.connected()) {
                sendRegister();
            }
        }
        lastReconnect = millis();
    }
    
    // Send uptime ping every 30 seconds
    if (millis() - lastUptimePing >= 30000) {
        uptimeCounter++;
        sendUptimePing();
        lastUptimePing = millis();
        
        // Periodic status update
        if (uptimeCounter % 2 == 0) {
            Serial.print("[STATUS] Stake: ");
            Serial.print(currentStake);
            Serial.print(" MC, Level: ");
            Serial.print(currentLevel);
            Serial.print(", Blocks: ");
            Serial.print(totalBlocksSigned);
            Serial.print(", Rewards: ");
            Serial.print(totalRewards);
            Serial.print(" MC, Uptime: ");
            Serial.println(uptimeCounter);
        }
    }
    
    // Check for challenge timeout (2.5 seconds)
    if (isValidator && (millis() - lastChallengeTime >= SIGNING_WINDOW_MS)) {
        Serial.println("[TIMEOUT] Failed to sign within window");
        handleSlashing();
        isValidator = false;
    }
    
    // Check if connection is still alive
    static uint32_t lastHeartbeat = 0;
    if (millis() - lastHeartbeat >= 60000) {
        if (!client.connected()) {
            Serial.println("[NET] Connection lost, reconnecting...");
            connectToBridge();
            if (client.connected()) {
                sendRegister();
            }
        }
        lastHeartbeat = millis();
    }
    
    delay(10);
}
