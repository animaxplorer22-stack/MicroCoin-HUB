// microcoin_miner_esp32_full.ino
// FULL PRODUCTION CODE FOR ESP32/ESP8266
// WITH REAL ECDSA CRYPTOGRAPHY (secp256k1 - same as Bitcoin)

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <NTPClient.h>
#include <WiFiUdp.h>
#include <EEPROM.h>
#include <mbedtls/ecdsa.h>
#include <mbedtls/entropy.h>
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/sha256.h>
#include <mbedtls/base64.h>

// ==================== USER CONFIGURATION ====================
// EDIT THESE BEFORE FLASHING

const char* WIFI_SSID = "your_wifi_ssid";
const char* WIFI_PASSWORD = "your_wifi_password";

const char* NODE_IP = "192.168.1.100";
const int NODE_PORT = 8080;

// GENERATE THIS USING: openssl ecparam -name secp256k1 -genkey -noout -out private.pem
// THEN: openssl ec -in private.pem -text -noout | grep -A 3 "priv:" | tail -3 | tr -d ' \n:'
const char* PRIVATE_KEY_HEX = "your_64_char_private_key_hex_here";

const char* WALLET_ADDRESS = "MC_YourWalletAddressHere";
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
#define EEPROM_STAKE_ADDR 0
#define EEPROM_REWARDS_ADDR 4
#define EEPROM_BLOCKS_ADDR 8
#define EEPROM_UPTIME_ADDR 12
#define EEPROM_CHECKSUM_ADDR 16

// ==================== CRYPTOGRAPHY CONTEXT ====================
mbedtls_ecdsa_context ecdsa;
mbedtls_entropy_context entropy;
mbedtls_ctr_drbg_context ctr_drbg;
mbedtls_sha256_context sha256_ctx;
mbedtls_mpi r, s;

// ==================== GLOBAL VARIABLES ====================
WebSocketsClient webSocket;
WiFiUDP ntpUDP;
NTPClient timeClient(ntpUDP, "pool.ntp.org", 0, 60000);

uint32_t currentStake;
uint32_t totalRewards;
uint32_t totalBlocksSigned;
uint32_t uptimeSeconds;
uint32_t lastUptimePing;
uint32_t lastDistributionCheck;
uint32_t lastRewardTime;
uint32_t uptimeCounter;
uint32_t lastBlockTime;
uint32_t consecutiveMisses;

char validatorID[65];
char publicKeyHex[130];
char walletAddress[70];
bool isValidator = false;
uint32_t lastChallengeTime = 0;
char currentChallenge[65];
char currentChallengeHash[65];
char signatureHex[130];
uint32_t currentBlockId = 0;
int currentLevel = 1;
bool isRegistered = false;
uint32_t lastReconnect = 0;
uint32_t reconnectAttempts = 0;

// ==================== CRYPTO UTILITIES ====================
void hexToBytes(const char* hex, unsigned char* bytes, size_t len) {
    for (size_t i = 0; i < len; i++) {
        sscanf(hex + 2*i, "%02hhx", &bytes[i]);
    }
}

void bytesToHex(const unsigned char* bytes, size_t len, char* hex) {
    for (size_t i = 0; i < len; i++) {
        sprintf(hex + 2*i, "%02x", bytes[i]);
    }
    hex[2*len] = '\0';
}

void computeSHA256(const char* input, char* output) {
    unsigned char hash[32];
    mbedtls_sha256_init(&sha256_ctx);
    mbedtls_sha256_starts(&sha256_ctx, 0);
    mbedtls_sha256_update(&sha256_ctx, (const unsigned char*)input, strlen(input));
    mbedtls_sha256_finish(&sha256_ctx, hash);
    bytesToHex(hash, 32, output);
}

void computeDoubleSHA256(const char* input, char* output) {
    char firstHash[65];
    computeSHA256(input, firstHash);
    computeSHA256(firstHash, output);
}

// ==================== ECDSA SIGNATURE ====================
void initCrypto() {
    mbedtls_ecdsa_init(&ecdsa);
    mbedtls_entropy_init(&entropy);
    mbedtls_ctr_drbg_init(&ctr_drbg);
    mbedtls_mpi_init(&r);
    mbedtls_mpi_init(&s);
    
    const char* personalization = "microcoin_validator_v1";
    mbedtls_ctr_drbg_seed(&ctr_drbg, mbedtls_entropy_func, &entropy,
                          (const unsigned char*)personalization, strlen(personalization));
    
    // Load private key
    unsigned char privateKeyBytes[32];
    hexToBytes(PRIVATE_KEY_HEX, privateKeyBytes, 32);
    
    // Setup secp256k1 curve
    mbedtls_ecp_group_id grp_id = MBEDTLS_ECP_DP_SECP256K1;
    mbedtls_ecp_keypair keypair;
    mbedtls_ecp_keypair_init(&keypair);
    mbedtls_ecp_group_load(&keypair.grp, grp_id);
    mbedtls_mpi_read_binary(&keypair.d, privateKeyBytes, 32);
    mbedtls_ecp_mul(&keypair.grp, &keypair.Q, &keypair.d, &keypair.grp.G, NULL, NULL);
    mbedtls_ecdsa_from_keypair(&ecdsa, &keypair);
    
    // Generate public key from private
    unsigned char publicKeyBytes[65];
    size_t publicKeyLen = 65;
    mbedtls_ecp_point_write_binary(&keypair.grp, &keypair.Q, MBEDTLS_ECP_PF_UNCOMPRESSED,
                                    &publicKeyLen, publicKeyBytes, sizeof(publicKeyBytes));
    bytesToHex(publicKeyBytes, publicKeyLen, publicKeyHex);
    
    Serial.println("[CRYPTO] ECDSA secp256k1 initialized");
}

bool signMessage(const char* message, char* signatureOut) {
    unsigned char hash[32];
    mbedtls_sha256_init(&sha256_ctx);
    mbedtls_sha256_starts(&sha256_ctx, 0);
    mbedtls_sha256_update(&sha256_ctx, (const unsigned char*)message, strlen(message));
    mbedtls_sha256_finish(&sha256_ctx, hash);
    
    unsigned char signature[64];
    size_t sigLen;
    
    int ret = mbedtls_ecdsa_sign(&ecdsa, MBEDTLS_MD_SHA256, hash, sizeof(hash),
                                  signature, &sigLen, mbedtls_ctr_drbg_random, &ctr_drbg);
    
    if (ret != 0) {
        Serial.printf("[CRYPTO] Sign failed: %d\n", ret);
        return false;
    }
    
    bytesToHex(signature, sigLen, signatureOut);
    return true;
}

// ==================== WALLET ADDRESS GENERATION ====================
void generateWalletAddress() {
    // Wallet address = MC_ + SHA256(public key) first 32 chars
    char publicKeyHash[65];
    computeSHA256(publicKeyHex, publicKeyHash);
    snprintf(walletAddress, sizeof(walletAddress), "MC_%.32s", publicKeyHash);
    Serial.printf("[WALLET] Address: %s\n", walletAddress);
}

void generateValidatorID() {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    char macStr[13];
    snprintf(macStr, sizeof(macStr), "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    
    // Combine MAC + wallet address for unique ID
    char combined[100];
    snprintf(combined, sizeof(combined), "%s%s", macStr, walletAddress);
    computeSHA256(combined, validatorID);
}

// ==================== STAKING & LEVELS ====================
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
    
    if (currentStake < 100 || currentStake > 10000000 || storedChecksum != calculatedChecksum) {
        currentStake = INITIAL_STAKE;
        totalRewards = 0;
        totalBlocksSigned = 0;
        uptimeSeconds = 0;
        consecutiveMisses = 0;
        Serial.println("[EEPROM] Invalid data, resetting to defaults");
        saveToEEPROM();
    }
    
    calculateLevel();
}

// ==================== SLASHING ====================
void handleSlashing() {
    uint32_t slashAmount = (uint32_t)(currentStake * SLASH_RATE);
    if (slashAmount < LEVEL_STAKE_RANGE) slashAmount = LEVEL_STAKE_RANGE;
    if (slashAmount > currentStake) slashAmount = currentStake;
    
    currentStake -= slashAmount;
    if (currentStake < LEVEL_STAKE_RANGE) currentStake = LEVEL_STAKE_RANGE;
    
    calculateLevel();
    saveToEEPROM();
    consecutiveMisses++;
    
    char slashMsg[200];
    snprintf(slashMsg, sizeof(slashMsg), "SLASHED: Lost %lu MC. New stake: %lu MC. Level: %d. Miss count: %lu",
             slashAmount, currentStake, currentLevel, consecutiveMisses);
    Serial.println(slashMsg);
    
    if (consecutiveMisses >= 5) {
        Serial.println("[WARNING] 5 consecutive misses. Reducing stake further...");
        currentStake = currentStake * 0.9;
        if (currentStake < LEVEL_STAKE_RANGE) currentStake = LEVEL_STAKE_RANGE;
        calculateLevel();
        saveToEEPROM();
    }
}

void addReward(uint32_t rewardAmount) {
    totalRewards += rewardAmount;
    currentStake += rewardAmount;
    totalBlocksSigned++;
    consecutiveMisses = 0;
    calculateLevel();
    saveToEEPROM();
    
    char rewardMsg[200];
    snprintf(rewardMsg, sizeof(rewardMsg), "REWARD: +%lu MC. Total rewards: %lu MC. Stake: %lu MC. Level: %d. Blocks: %lu",
             rewardAmount, totalRewards, currentStake, currentLevel, totalBlocksSigned);
    Serial.println(rewardMsg);
}

// ==================== WEBSOCKET COMMUNICATION ====================
void sendRegister() {
    StaticJsonDocument<512> doc;
    doc["type"] = "register";
    doc["validator_id"] = validatorID;
    doc["public_key"] = publicKeyHex;
    doc["wallet"] = walletAddress;
    doc["stake"] = currentStake;
    doc["level"] = currentLevel;
    doc["rewards"] = totalRewards;
    doc["blocks"] = totalBlocksSigned;
    doc["uptime"] = uptimeSeconds;
    
    char timestamp[32];
    snprintf(timestamp, sizeof(timestamp), "%lu", timeClient.getEpochTime());
    doc["timestamp"] = timestamp;
    
    char messageToSign[256];
    snprintf(messageToSign, sizeof(messageToSign), "%s%s%lu%s", validatorID, walletAddress, currentStake, timestamp);
    char signature[130];
    if (signMessage(messageToSign, signature)) {
        doc["signature"] = signature;
    }
    
    String output;
    serializeJson(doc, output);
    webSocket.sendTXT(output);
    Serial.println("[WS] Registration sent");
}

void sendUptimePing() {
    StaticJsonDocument<256> doc;
    doc["type"] = "uptime_ping";
    doc["validator_id"] = validatorID;
    doc["uptime_seconds"] = uptimeCounter;
    doc["stake"] = currentStake;
    doc["level"] = currentLevel;
    doc["timestamp"] = timeClient.getEpochTime();
    
    String output;
    serializeJson(doc, output);
    webSocket.sendTXT(output);
}

void sendBlockSignature() {
    char messageToSign[256];
    snprintf(messageToSign, sizeof(messageToSign), "%s%s%lu", currentChallenge, validatorID, currentBlockId);
    
    char signature[130];
    if (!signMessage(messageToSign, signature)) {
        Serial.println("[ERROR] Failed to sign challenge");
        return;
    }
    
    StaticJsonDocument<512> doc;
    doc["type"] = "block_signature";
    doc["validator_id"] = validatorID;
    doc["challenge"] = currentChallenge;
    doc["signature"] = signature;
    doc["level"] = currentLevel;
    doc["stake"] = currentStake;
    doc["timestamp"] = timeClient.getEpochTime();
    doc["block_id"] = currentBlockId;
    
    String output;
    serializeJson(doc, output);
    webSocket.sendTXT(output);
    Serial.printf("[SIGN] Block %lu signed\n", currentBlockId);
}

// ==================== WEBSOCKET EVENT HANDLER ====================
void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch(type) {
        case WStype_DISCONNECTED:
            Serial.println("[WS] Disconnected");
            isValidator = false;
            isRegistered = false;
            reconnectAttempts++;
            break;
            
        case WStype_CONNECTED:
            Serial.println("[WS] Connected to node");
            reconnectAttempts = 0;
            sendRegister();
            break;
            
        case WStype_TEXT: {
            StaticJsonDocument<1024> doc;
            DeserializationError error = deserializeJson(doc, payload);
            if (error) {
                Serial.printf("[ERROR] JSON parse: %s\n", error.c_str());
                break;
            }
            
            const char* type = doc["type"];
            
            if (strcmp(type, "registered") == 0) {
                isRegistered = true;
                int nodeLevel = doc["level"];
                uint32_t nodeStake = doc["stake"];
                Serial.printf("[REGISTERED] Level: %d, Stake: %lu\n", nodeLevel, nodeStake);
            }
            else if (strcmp(type, "challenge") == 0) {
                strncpy(currentChallenge, doc["challenge"], 64);
                currentChallenge[64] = '\0';
                currentBlockId = doc["block_id"];
                lastChallengeTime = millis();
                isValidator = true;
                sendBlockSignature();
            }
            else if (strcmp(type, "block_accepted") == 0) {
                uint32_t reward = doc["reward"];
                addReward(reward);
                isValidator = false;
                lastRewardTime = millis();
                Serial.printf("[BLOCK ACCEPTED] Block %lu, reward %lu MC\n", (uint32_t)doc["block_id"], reward);
            }
            else if (strcmp(type, "block_rejected") == 0) {
                const char* reason = doc["reason"];
                Serial.printf("[BLOCK REJECTED] %s\n", reason);
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
                    Serial.printf("[LEVEL UPDATE] New stake: %lu, Level: %d\n", currentStake, currentLevel);
                }
            }
            else if (strcmp(type, "reward_distribution") == 0) {
                uint32_t uptimeReward = doc["uptime_reward"];
                if (uptimeReward > 0) {
                    addReward(uptimeReward);
                    Serial.printf("[UPTIME REWARD] +%lu MC\n", uptimeReward);
                }
            }
            break;
        }
        
        default:
            break;
    }
}

// ==================== NETWORK CONNECTION ====================
void connectWiFi() {
    Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\nWiFi connected. IP: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\nWiFi connection failed! Restarting...");
        ESP.restart();
    }
}

// ==================== SETUP ====================
void setup() {
    Serial.begin(115200);
    delay(1000);
    
    Serial.println("\n=== MICROCOIN MINER v1.0 ===");
    Serial.println("Real ECDSA Cryptography - secp256k1");
    
    // Initialize crypto first
    initCrypto();
    
    // Load saved state
    loadFromEEPROM();
    generateWalletAddress();
    generateValidatorID();
    calculateLevel();
    
    Serial.printf("Validator ID: %.16s...\n", validatorID);
    Serial.printf("Wallet: %s\n", walletAddress);
    Serial.printf("Public Key: %.32s...\n", publicKeyHex);
    Serial.printf("Initial Stake: %lu MC\n", currentStake);
    Serial.printf("Level: %d\n", currentLevel);
    Serial.printf("Total Rewards: %lu MC\n", totalRewards);
    Serial.printf("Blocks Signed: %lu\n", totalBlocksSigned);
    
    // Connect to network
    connectWiFi();
    
    // Initialize NTP
    timeClient.begin();
    timeClient.update();
    Serial.printf("NTP time: %s\n", timeClient.getFormattedTime().c_str());
    
    // Setup WebSocket
    webSocket.begin(NODE_IP, NODE_PORT, "/");
    webSocket.onEvent(webSocketEvent);
    webSocket.setReconnectInterval(5000);
    
    lastUptimePing = millis();
    lastDistributionCheck = millis();
    lastReconnect = millis();
    
    Serial.println("\n[READY] Waiting for challenges...\n");
}

// ==================== MAIN LOOP ====================
void loop() {
    webSocket.loop();
    timeClient.update();
    
    // Periodic reconnection check
    if (!isRegistered && (millis() - lastReconnect > 30000)) {
        if (webSocket.isConnected()) {
            sendRegister();
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
            Serial.printf("[STATUS] Stake: %lu MC, Level: %d, Blocks: %lu, Rewards: %lu MC, Uptime: %lu\n",
                          currentStake, currentLevel, totalBlocksSigned, totalRewards, uptimeCounter);
        }
    }
    
    // Check for challenge timeout (2.5 seconds)
    if (isValidator && (millis() - lastChallengeTime >= SIGNING_WINDOW_MS)) {
        Serial.println("[TIMEOUT] Failed to sign within window");
        handleSlashing();
        isValidator = false;
    }
    
    // Periodic EEPROM save (every hour)
    static uint32_t lastSave = 0;
    if (millis() - lastSave >= 3600000) {
        saveToEEPROM();
        lastSave = millis();
    }
    
    delay(10);
}
