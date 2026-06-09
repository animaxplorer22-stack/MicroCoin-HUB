// i2c_slave_attiny85.ino
// ATtiny85 or Arduino as I2C Slave for MicroCoin mining
// Receives challenges from ESP32 master and returns signatures

#include <Wire.h>
#include <avr/pgmspace.h>

// ==================== CONFIGURATION ====================
#define SLAVE_ADDRESS 0x09  // Change for each slave (0x09 to 0x12)
#define LED_PIN 1           // Onboard LED (ATtiny85 pin 1)
#define SIGNING_DELAY_MS 20 // Simulated signing time

// ==================== GLOBALS ====================
char currentChallenge[65];
uint32_t currentBlockId = 0;
bool hasChallenge = false;
uint32_t challengeReceivedTime = 0;
uint32_t signaturesSent = 0;

// Simple pseudo-random generator for signature (ATtiny85 has no crypto)
unsigned int simpleRand(unsigned int seed) {
    seed = seed * 1103515245 + 12345;
    return (seed >> 16) & 0x7FFF;
}

void generateSignature(char* output, const char* challenge, uint32_t blockId, uint32_t deviceId) {
    // Simple signature generation (for production, use proper crypto)
    // ATtiny85 cannot do ECDSA, so this is a placeholder
    uint32_t seed = 0;
    
    // Combine challenge hash with device info
    for (int i = 0; i < 64 && challenge[i]; i++) {
        seed += challenge[i];
    }
    seed += blockId;
    seed += deviceId;
    seed += millis();
    
    // Generate 32-byte signature
    for (int i = 0; i < 64; i += 4) {
        unsigned int r = simpleRand(seed + i);
        sprintf(&output[i], "%04X", r);
    }
    output[64] = '\0';
}

// ==================== I2C REQUEST HANDLER ====================
void requestEvent() {
    char signature[65] = {0};
    
    if (hasChallenge) {
        generateSignature(signature, currentChallenge, currentBlockId, SLAVE_ADDRESS);
        signaturesSent++;
        
        // Blink LED to show activity
        digitalWrite(LED_PIN, HIGH);
        delay(5);
        digitalWrite(LED_PIN, LOW);
        
        // Send signature
        Wire.write(signature);
        hasChallenge = false;
    } else {
        // No challenge, send status
        char status[16];
        sprintf(status, "IDLE:%lu", signaturesSent);
        Wire.write(status);
    }
}

// ==================== I2C RECEIVE HANDLER ====================
void receiveEvent(int bytes) {
    char buffer[64];
    int idx = 0;
    
    while (Wire.available() && idx < 63) {
        buffer[idx++] = Wire.read();
    }
    buffer[idx] = '\0';
    
    // Check command
    if (strncmp(buffer, "CHLG", 4) == 0) {
        // Command to expect a challenge
        hasChallenge = false;
        challengeReceivedTime = millis();
    }
    else if (idx >= 32) {
        // This is the actual challenge (64 hex chars)
        strncpy(currentChallenge, buffer, 64);
        currentChallenge[64] = '\0';
        hasChallenge = true;
        challengeReceivedTime = millis();
        
        // Blink pattern for challenge received
        for (int i = 0; i < 2; i++) {
            digitalWrite(LED_PIN, HIGH);
            delay(10);
            digitalWrite(LED_PIN, LOW);
            delay(10);
        }
    }
}

// ==================== SETUP ====================
void setup() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
    
    // Blink to show startup
    for (int i = 0; i < 3; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(100);
        digitalWrite(LED_PIN, LOW);
        delay(100);
    }
    
    // Initialize I2C slave
    Wire.begin(SLAVE_ADDRESS);
    Wire.onReceive(receiveEvent);
    Wire.onRequest(requestEvent);
    
    // Small delay for master to detect
    delay(100);
    
    // Rapid blink to show ready
    for (int i = 0; i < 5; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(20);
        digitalWrite(LED_PIN, LOW);
        delay(20);
    }
}

// ==================== LOOP ====================
void loop() {
    // Idle - I2C is interrupt-driven
    // Optional: heartbeat LED every 5 seconds
    static uint32_t lastHeartbeat = 0;
    if (millis() - lastHeartbeat > 5000) {
        digitalWrite(LED_PIN, HIGH);
        delay(50);
        digitalWrite(LED_PIN, LOW);
        lastHeartbeat = millis();
    }
    
    delay(10);
}
